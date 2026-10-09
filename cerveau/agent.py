"""Boucle de l'agent : rappel de mémoire, appel du modèle, exécution des
outils, écriture des souvenirs.

Le modèle est interchangeable. Tout ce qui le concerne passe par le client
OpenAI-compatible ; la mémoire et les outils sont indépendants de lui.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .llm import Client, Reply
from .memory import MemoryStore
from .tools import Registry, default_registry

SYSTEM_PROMPT = """Tu es Cerveau, un développeur senior et auditeur de sécurité. Tu travailles dans le dossier du projet « {project} », qui est ta racine : tous les chemins sont relatifs à ce dossier.

Tu disposes d'outils pour lire, chercher, créer et modifier des fichiers, créer des dossiers, exécuter des commandes (tests, installation, build, git) et lancer des scanners de sécurité. Utilise-les : tu agis, tu ne te contentes pas de décrire.

Méthode :
- Pour une tâche de code, annonce en deux lignes ce que tu vas faire, puis crée ou modifie les fichiers avec write_file et edit_file. Un projet complet se construit par étapes : commence par la structure et un premier module qui fonctionne, puis étends. Ne demande pas de permission pour écrire dans le projet.
- Tu n'inventes jamais le contenu d'un fichier existant : tu le lis d'abord.
- Après avoir écrit du code, vérifie-le quand c'est possible avec run_command (tests, compilation, lancement).
- Pour un audit de sécurité, lance d'abord les scanners disponibles, puis lis le code concerné pour confirmer ou écarter chaque résultat, classe les failles par gravité avec fichier et ligne, et propose un correctif concret.
- Tu réponds en français, de façon précise et concise. Tu ne refuses jamais une tâche de développement ou d'audit sur ce projet ; si quelque chose est hors de portée de tes outils, dis exactement ce qui manque.
- Quand une tâche est terminée, résume ce qui a été fait et ce qui reste à faire.

Souvenirs pertinents sur ce projet :
{memories}
"""
MAX_TOOL_ROUNDS = 12


class Agent:
    def __init__(self, config: Config, project_root: Path, client: Client | None = None,
                 store: MemoryStore | None = None, registry: Registry | None = None,
                 on_token: Callable[[str], None] | None = None,
                 on_tool: Callable[[str, dict, str], None] | None = None,
                 confirm: Callable[[str, dict], bool] | None = None):
        self.config = config
        self.root = project_root.resolve()
        self.project = self.root.name
        self.client = client or Client(config.base_url, config.api_key)
        self.store = store or MemoryStore(config.db_path, embedder=self._embed)
        self.registry = registry or default_registry()
        self.on_token = on_token
        self.on_tool = on_tool
        self.confirm = confirm
        self.history: list[dict] = []

    # --- embeddings -----------------------------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed(self.config.embedding_model, texts)

    # --- prompt ---------------------------------------------------------------

    def _system(self, query: str) -> str:
        memories = self.store.recall(self.project, query, limit=self.config.max_memories)
        if memories:
            text = "\n".join(f"- [{m.kind}] {m.content}" for m in memories)
        else:
            text = "(aucun pour l'instant)"
        return SYSTEM_PROMPT.format(project=self.project, memories=text)

    # --- boucle ---------------------------------------------------------------

    def ask(self, user_message: str, remember: bool = True) -> str:
        messages = [{"role": "system", "content": self._system(user_message)}, *self.history,
                    {"role": "user", "content": user_message}]
        tools = self.registry.schemas()

        final_text = ""
        for _ in range(MAX_TOOL_ROUNDS):
            reply = self.client.chat(self.config.model, messages, tools=tools, temperature=self.config.temperature)
            if not reply.tool_calls:
                final_text = reply.content
                break
            messages.append(_assistant_message(reply))
            for call in reply.tool_calls:
                result = self.registry.call(call.name, call.arguments, self.root, confirm=self.confirm)
                if self.on_tool:
                    self.on_tool(call.name, call.arguments, result)
                messages.append({"role": "tool", "tool_call_id": call.id or call.name, "name": call.name,
                                 "content": result})
        else:
            final_text = reply.content or "Limite d'appels d'outils atteinte sans réponse finale."

        if not final_text:
            # Dernier tour en streaming pour la réponse texte, affichée au fil de l'eau.
            final_text = self._stream_final(messages)
        elif self.on_token:
            self.on_token(final_text)

        self.history.extend([{"role": "user", "content": user_message},
                             {"role": "assistant", "content": final_text}])
        self.history = self.history[-12:]

        if remember and final_text:
            self.store.remember(self.project, f"Question : {user_message[:300]}\nRéponse : {final_text[:600]}")
        return final_text

    def _stream_final(self, messages: list[dict]) -> str:
        parts: list[str] = []
        for token in self.client.chat_stream(self.config.model, messages, temperature=self.config.temperature):
            parts.append(token)
            if self.on_token:
                self.on_token(token)
        return "".join(parts)

    def note(self, content: str, kind: str = "fact") -> int:
        """Ajoute un souvenir à la main, par exemple une convention du projet."""
        return self.store.remember(self.project, content, kind=kind)


def _assistant_message(reply: Reply) -> dict:
    return {
        "role": "assistant",
        "content": reply.content or None,
        "tool_calls": [{
            "id": c.id or c.name, "type": "function",
            "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
        } for c in reply.tool_calls],
    }
