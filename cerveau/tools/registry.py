from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[..., str]
    dangerous: bool = False   # demande une confirmation à l'utilisateur avant d'agir

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.read_paths: set[str] = set()   # fichiers lus ou écrits pendant la session

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def call(self, name: str, arguments: dict, root: Path,
             confirm: Callable[[str, dict], bool] | None = None) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Outil inconnu : {name}. Outils disponibles : {', '.join(self._tools)}"
        if tool.dangerous:
            from .shell import is_destructive

            if name == "run_command" and confirm is None and is_destructive(str(arguments.get("command", ""))):
                return ("Commande refusée : elle est potentiellement destructrice (suppression hors du projet, "
                        "réécriture d'historique git, arrêt de la machine) et personne n'est là pour la valider. "
                        "Trouve une approche qui reste dans le projet.")
            if confirm is not None and not confirm(name, arguments):
                return f"L'utilisateur a refusé l'exécution de {name}. Propose une autre approche ou demande-lui pourquoi."
        path = str(arguments.get("path", "")) if isinstance(arguments, dict) else ""
        if name == "write_file" and path:
            target = root / path
            if target.is_file() and _norm(path) not in self.read_paths:
                return (f"{path} existe déjà et tu ne l'as pas lu dans cette session. Lis-le avec read_file, "
                        "puis modifie-le avec edit_file ou remplace-le avec write_file en connaissance de cause.")
        try:
            result = tool.run(root=root, **arguments)
        except TypeError as exc:
            return f"Arguments invalides pour {name} : {exc}"
        except Exception as exc:  # noqa: BLE001 - l'erreur doit revenir au modèle, pas planter l'agent
            return f"Erreur dans {name} : {type(exc).__name__}: {exc}"
        if name in ("read_file", "write_file", "edit_file") and path:
            self.read_paths.add(_norm(path))
        return result


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip("/").lower()


def default_registry() -> Registry:
    from . import files, security, shell

    reg = Registry()
    for tool in (*files.TOOLS, *shell.TOOLS, *security.TOOLS):
        reg.add(tool)
    return reg


def truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [tronqué, {len(text) - limit} caractères de plus]"


def to_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)
