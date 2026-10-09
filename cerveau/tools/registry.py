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

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def call(self, name: str, arguments: dict, root: Path) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Outil inconnu : {name}. Outils disponibles : {', '.join(self._tools)}"
        try:
            return tool.run(root=root, **arguments)
        except TypeError as exc:
            return f"Arguments invalides pour {name} : {exc}"
        except Exception as exc:  # noqa: BLE001 - l'erreur doit revenir au modèle, pas planter l'agent
            return f"Erreur dans {name} : {type(exc).__name__}: {exc}"


def default_registry() -> Registry:
    from . import files, security

    reg = Registry()
    for tool in (*files.TOOLS, *security.TOOLS):
        reg.add(tool)
    return reg


def truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [tronqué, {len(text) - limit} caractères de plus]"


def to_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)
