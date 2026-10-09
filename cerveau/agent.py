"""Boucle de l'agent : rappel de mémoire, appel du modèle, exécution des
outils, écriture des souvenirs.

Le modèle est interchangeable. Tout ce qui le concerne passe par le client
OpenAI-compatible ; la mémoire et les outils sont indépendants de lui.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import skills as skills_mod
from .config import HOME, Config
from .llm import Client, Reply, ToolCall
from .memory import GLOBAL, MemoryStore
from .tools import Registry, default_registry
from .tools.shell import describe_environment

SYSTEM_PROMPT = """Tu es Cerveau, un développeur senior et auditeur de sécurité. Tu travailles dans le dossier du projet « {project} », qui est ta racine : tous les chemins sont relatifs à ce dossier.

Tu disposes des outils suivants, à appeler via le mécanisme d'appel d'outils (jamais en décrivant l'appel dans ta réponse) :
{tools}

Utilise-les : tu agis, tu ne te contentes pas de décrire. Tu ne peux pas écrire en dehors de la racine du projet ; si l'utilisateur veut travailler ailleurs, dis-lui de relancer « cerveau chat <dossier> ».

Environnement d'exécution : {environment}.
Pour un projet Python : crée l'environnement une fois avec « uv venv », installe avec « uv pip install -e . » ou « uv pip install <paquet> », lance avec « uv run pytest -q » ou « uv run python … ». N'utilise jamais « pip » seul. Ne lance les scanners de sécurité que si l'utilisateur demande un audit.

Méthode :
- Pour une tâche de code, annonce en deux lignes ce que tu vas faire, puis crée ou modifie les fichiers avec write_file et edit_file. Un projet complet se construit par étapes : commence par la structure et un premier module qui fonctionne, puis étends. Ne demande pas de permission pour écrire dans le projet.
- Tu restes dans le périmètre demandé. Tu ne crées pas de modules, fonctionnalités ou fichiers que l'utilisateur n'a pas demandés. Si quelque chose manque pour que ça fonctionne, fais le strict minimum et signale-le dans ton résumé.
- Tu n'inventes jamais le contenu d'un fichier existant : tu le lis d'abord.
- Après avoir écrit du code, vérifie-le quand c'est possible avec run_command (tests, compilation, lancement).
- Pour un audit de sécurité, lance d'abord les scanners disponibles, puis lis le code concerné pour confirmer ou écarter chaque résultat, classe les failles par gravité avec fichier et ligne, et propose un correctif concret.
- Tu réponds en français, de façon précise et concise. Tu ne refuses jamais une tâche de développement ou d'audit sur ce projet ; si quelque chose est hors de portée de tes outils, dis exactement ce qui manque.
- Quand une tâche est terminée, résume ce qui a été fait et ce qui reste à faire.
{skills}
Souvenirs pertinents sur ce projet :
{memories}
"""
LEARN_PROMPT = """Voici un échange entre un utilisateur et son assistant de code.

Utilisateur : {question}
Assistant : {answer}

Extrais ce qui mérite d'être retenu durablement, et rien d'autre : une préférence de l'utilisateur, une décision prise sur le projet, une convention à respecter. Ignore le contenu temporaire, les détails de code et les salutations. S'il n'y a rien à retenir, réponds exactement : RIEN.

Sinon, une ligne par élément, au format :
PORTEE | TYPE | phrase courte à la troisième personne
où PORTEE vaut « global » si cela vaut pour tous ses projets (goûts, outils, langue, façon de travailler) ou « projet » si c'est propre à ce projet, et TYPE vaut « preference », « fact » ou « decision ». Trois lignes au maximum."""

MAX_TOOL_ROUNDS = 60   # tours modèle+outils par réponse ; surchargé par config.max_tool_rounds
RESERVE_TOKENS = 2048  # place gardée pour la réponse du modèle
KEEP_RECENT_TOOL_RESULTS = 6


@dataclass
class TurnStats:
    """Ce qu'a coûté la dernière réponse, pour l'affichage en console."""
    prompt_tokens: int = 0        # taille du contexte envoyé au dernier appel
    completion_tokens: int = 0    # tokens générés sur tous les tours
    rounds: int = 0
    tool_calls: int = 0
    elapsed: float = 0.0
    generation_time: float = 0.0  # temps de génération pure, prefill exclu
    first_token: float = 0.0      # attente avant le premier token du dernier tour
    estimated: bool = False       # True si le serveur n'a pas fourni usage

    @property
    def tokens_per_second(self) -> float:
        return self.completion_tokens / self.generation_time if self.generation_time > 0 else 0.0


class Agent:
    def __init__(self, config: Config, project_root: Path, client: Client | None = None,
                 store: MemoryStore | None = None, registry: Registry | None = None,
                 on_token: Callable[[str], None] | None = None,
                 on_tool: Callable[[str, dict, str], None] | None = None,
                 confirm: Callable[[str, dict], bool] | None = None,
                 on_tool_start: Callable[[str, dict], None] | None = None,
                 on_thinking: Callable[[str], None] | None = None,
                 on_learned: Callable[[str, str, str], None] | None = None):
        self.config = config
        self.root = project_root.resolve()
        self.project = self.root.name
        self.client = client or Client(config.base_url, config.api_key,
                                       timeout=getattr(config, "timeout_seconds", 600))
        self.store = store or MemoryStore(config.db_path, embedder=self._embed)
        self.registry = registry or default_registry()
        self.on_token = on_token
        self.on_tool = on_tool
        self.confirm = confirm
        self.on_tool_start = on_tool_start
        self.on_thinking = on_thinking
        self.on_learned = on_learned
        self.history: list[dict] = []
        self.last_stats = TurnStats()
        self.skills = skills_mod.load_all([HOME / "skills"])
        if not getattr(config, "web_enabled", True):
            self.registry.remove("web_search")
            self.registry.remove("web_fetch")
        self.debug = bool(os.environ.get("CERVEAU_DEBUG"))

    # --- embeddings -----------------------------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed(self.config.embedding_model, texts)

    # --- prompt ---------------------------------------------------------------

    def _system(self, user_message: str) -> str:
        memories = self.store.recall(self.project, user_message, limit=self.config.max_memories)
        if memories:
            text = "\n".join(f"- [{m.kind}] {m.content}" for m in memories)
        else:
            text = "(aucun pour l'instant)"
        chosen = skills_mod.select(self.skills, user_message, limit=3)
        skills_text = ""
        if chosen:
            skills_text = "\nProcédures à suivre pour cette demande :\n\n" + skills_mod.render(chosen) + "\n"
            if self.debug:
                print(f"[debug] skills : {', '.join(s.name for s in chosen)}", file=sys.stderr)
        tools_text = "\n".join(f"- {t['function']['name']} : {t['function']['description']}"
                               for t in self.registry.schemas())
        return SYSTEM_PROMPT.format(project=self.project, tools=tools_text, skills=skills_text, memories=text,
                                    environment=describe_environment(self.root))

    # --- boucle ---------------------------------------------------------------

    def ask(self, user_message: str, remember: bool = True) -> str:
        messages = [{"role": "system", "content": self._system(user_message)}, *self.history,
                    {"role": "user", "content": user_message}]
        tools = self.registry.schemas()
        stats = TurnStats()
        t0 = time.perf_counter()

        final_text = ""
        actions: list[str] = []
        limit = getattr(self.config, "max_tool_rounds", MAX_TOOL_ROUNDS) or MAX_TOOL_ROUNDS
        for _ in range(limit):
            stats.rounds += 1
            compact_messages(messages, self.config.context_tokens - RESERVE_TOKENS)
            reply = self._round(messages, tools, stats)
            if self.debug:
                print(f"[debug] réponse brute : {reply!r}"[:1500], file=sys.stderr)
            if not reply.tool_calls:
                final_text = reply.content
                break
            messages.append(_assistant_message(reply))
            for call in reply.tool_calls:
                stats.tool_calls += 1
                actions.append(_describe_call(call))
                if self.on_tool_start:
                    self.on_tool_start(call.name, call.arguments)
                result = self.registry.call(call.name, call.arguments, self.root, confirm=self.confirm)
                if self.on_tool:
                    self.on_tool(call.name, call.arguments, result)
                messages.append({"role": "tool", "tool_call_id": call.id or call.name, "name": call.name,
                                 "content": result})
        else:
            # Plafond atteint : on demande un bilan sans outils plutôt que d'abandonner.
            messages.append({"role": "user", "content": (
                "Tu as atteint la limite d'actions pour cette réponse. Sans appeler d'outil, résume ce que tu as "
                "fait, ce qui reste à faire, et dis à l'utilisateur d'écrire « continue » pour poursuivre.")})
            stats.rounds += 1
            reply = self._round(messages, [], stats)
            final_text = reply.content or "Limite d'actions atteinte. Écrivez « continue » pour poursuivre."

        stats.elapsed = time.perf_counter() - t0
        self.last_stats = stats

        recorded = final_text
        if actions:
            shown = actions[:40]
            more = f" … et {len(actions) - 40} autres" if len(actions) > 40 else ""
            recorded += "\n\n(Actions effectuées : " + " ; ".join(shown) + more + ")"
        self.history.extend([{"role": "user", "content": user_message},
                             {"role": "assistant", "content": recorded}])
        self.history = self.history[-12:]

        if remember and final_text:
            self.store.remember(self.project, f"Question : {user_message[:300]}\nRéponse : {final_text[:600]}")
            if getattr(self.config, "auto_learn", True):
                self.learn(user_message, final_text)
        return final_text

    # --- autonomie -----------------------------------------------------------------

    AUTONOMY_TRIGGERS = ("autonome", "autonomie", "tout seul", "sans t'arrêter", "sans t'arreter", "jusqu'au bout",
                         "pendant que je dors", "continue sans me demander", "fais tout")
    DONE_MARKER = re.compile(r"ÉTAT\s*:\s*TERMIN", re.IGNORECASE)
    CONTINUE_MARKER = re.compile(r"ÉTAT\s*:\s*EN COURS", re.IGNORECASE)
    CONTINUE_PROMPT = ("Continue en autonomie : relis PLAN.md, prends la première phase non cochée, réalise-la, "
                       "vérifie avec les tests, coche-la. Termine par la ligne ÉTAT.")

    @classmethod
    def wants_autonomy(cls, message: str) -> bool:
        low = message.lower()
        return any(t in low for t in cls.AUTONOMY_TRIGGERS)

    def ask_autonomous(self, task: str, on_step: Callable[[int, int], None] | None = None) -> str:
        """Enchaîne les étapes jusqu'à « ÉTAT : TERMINÉ », à la limite ou à une
        réponse sans marqueur deux fois de suite (le modèle a fini sans le dire)."""
        limit = getattr(self.config, "max_autonomous_steps", 40)
        answer = ""
        missing_marker = 0
        for step in range(1, limit + 1):
            if on_step:
                on_step(step, limit)
            answer = self.ask(task if step == 1 else self.CONTINUE_PROMPT)
            if self.DONE_MARKER.search(answer):
                return answer
            if self.CONTINUE_MARKER.search(answer):
                missing_marker = 0
                continue
            missing_marker += 1
            if missing_marker >= 2:
                return answer
        return answer + "\n\n(limite d'étapes autonomes atteinte ; écrivez « continue en autonomie » pour reprendre)"

    def learn(self, question: str, answer: str) -> list[tuple[str, str, str]]:
        """Demande au modèle ce qui mérite d'être retenu et l'enregistre.
        Renvoie les (portée, type, contenu) retenus. Jamais bloquant : une erreur
        est ignorée, la réponse principale a déjà été rendue."""
        prompt = LEARN_PROMPT.format(question=question[:1500], answer=answer[:2500])
        try:
            reply = self.client.chat(self.config.model, [{"role": "user", "content": prompt}],
                                     temperature=0.0, max_tokens=200)
        except Exception:  # noqa: BLE001
            return []
        learned = []
        for line in (reply.content or "").splitlines():
            parts = [x.strip() for x in line.split("|")]
            if len(parts) != 3 or not parts[2] or parts[2].upper() == "RIEN":
                continue
            scope, kind, content = parts[0].lower(), parts[1].lower(), parts[2]
            if kind not in ("preference", "fact", "decision"):
                continue
            project = GLOBAL if scope.startswith("glob") else self.project
            stored_kind = "preference" if kind == "preference" else "fact"
            if kind == "decision":
                content = "Décision : " + content
            try:
                self.store.remember(project, content, kind=stored_kind)
            except ValueError:
                continue
            learned.append((scope, kind, content))
            if self.on_learned:
                self.on_learned(scope, kind, content)
        return learned

    def _round(self, messages: list[dict], tools: list[dict], stats: TurnStats) -> Reply:
        """Un appel au modèle, en streaming si le client le permet. Les tokens
        de texte sont transmis à on_token au fil de l'eau."""
        t0 = time.perf_counter()
        first: float | None = None
        chars = 0
        if hasattr(self.client, "chat_events"):
            reply = Reply()
            for kind, payload in self.client.chat_events(self.config.model, messages, tools=tools,
                                                         temperature=self.config.temperature):
                if kind in ("token", "thinking") and first is None:
                    first = time.perf_counter() - t0
                if kind == "token":
                    chars += len(payload)  # type: ignore[arg-type]
                    if self.on_token:
                        self.on_token(payload)  # type: ignore[arg-type]
                elif kind == "thinking":
                    if self.on_thinking:
                        self.on_thinking(payload)  # type: ignore[arg-type]
                elif kind == "done":
                    reply = payload  # type: ignore[assignment]
        else:
            reply = self.client.chat(self.config.model, messages, tools=tools, temperature=self.config.temperature)
            chars = len(reply.content)
            if reply.content and self.on_token:
                self.on_token(reply.content)
        total = time.perf_counter() - t0
        # Les appels d'outils purs n'émettent aucun token texte : on ne peut pas isoler le prefill,
        # on garde le temps total pour ce tour.
        stats.first_token = first if first is not None else 0.0
        stats.generation_time += (total - first) if first is not None else total
        usage = reply.usage or {}
        if usage.get("completion_tokens") is not None:
            stats.completion_tokens += int(usage.get("completion_tokens") or 0)
            stats.prompt_tokens = int(usage.get("prompt_tokens") or stats.prompt_tokens)
        else:
            stats.estimated = True
            stats.completion_tokens += _estimate_tokens(reply.content) + sum(
                _estimate_tokens(json.dumps(c.arguments)) for c in reply.tool_calls)
            stats.prompt_tokens = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)
        return reply

    def note(self, content: str, kind: str = "fact") -> int:
        """Ajoute un souvenir à la main, par exemple une convention du projet."""
        return self.store.remember(self.project, content, kind=kind)


def compact_messages(messages: list[dict], budget_tokens: int) -> int:
    """Garde la conversation sous le budget, sur place. D'abord on raccourcit
    les résultats d'outils anciens, puis on retire les plus vieux échanges
    après la consigne. Renvoie le nombre de messages touchés. Sans ça, Ollama
    tronque le début en silence et le modèle perd sa consigne."""
    touched = 0
    if _messages_tokens(messages) <= budget_tokens:
        return 0
    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_indexes[:-KEEP_RECENT_TOOL_RESULTS]:
        content = messages[i].get("content") or ""
        if len(content) > 400:
            messages[i]["content"] = content[:300] + "\n… [résultat raccourci pour tenir dans le contexte]"
            touched += 1
        if _messages_tokens(messages) <= budget_tokens:
            return touched
    # Retirer les plus anciens échanges, en gardant la consigne (index 0) et le dernier message utilisateur.
    while len(messages) > 2 and _messages_tokens(messages) > budget_tokens:
        victim = 1
        # Ne jamais laisser un résultat d'outil orphelin : on retire l'assistant et ses outils ensemble.
        messages.pop(victim)
        touched += 1
        while len(messages) > 2 and messages[victim].get("role") == "tool":
            messages.pop(victim)
            touched += 1
    return touched


def _messages_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += _estimate_tokens(str(m.get("content") or "")) + 4
        for call in m.get("tool_calls") or []:
            total += _estimate_tokens(json.dumps(call.get("function", {})))
    return total


def _describe_call(call: ToolCall) -> str:
    """Résumé court d'un appel, gardé dans l'historique pour qu'un « continue » sache où on en est."""
    args = call.arguments
    key = args.get("path") or args.get("command") or args.get("pattern") or ""
    return f"{call.name}({str(key)[:60]})" if key else call.name


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _assistant_message(reply: Reply) -> dict:
    return {
        "role": "assistant",
        "content": reply.content or None,
        "tool_calls": [{
            "id": c.id or c.name, "type": "function",
            "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
        } for c in reply.tool_calls],
    }
