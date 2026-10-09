"""Exécution de commandes dans le projet, avec confirmation de l'utilisateur.

Le modèle peut lancer des tests, installer des dépendances ou démarrer un
script. Chaque commande est soumise à l'utilisateur avant d'être exécutée :
le registre appelle la fonction de confirmation parce que l'outil est marqué
`dangerous`. La commande tourne dans la racine du projet, avec un délai.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .registry import Tool, truncate


def run_command(root: Path, command: str, timeout: int = 300) -> str:
    """Lance une commande shell dans le dossier du projet et renvoie sa sortie."""
    try:
        proc = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True,  # noqa: S602
                              timeout=min(int(timeout), 1800), errors="replace")
    except subprocess.TimeoutExpired:
        return f"commande interrompue après {timeout} s : {command}"
    out = proc.stdout
    if proc.stderr:
        out += ("\n[stderr]\n" if out else "[stderr]\n") + proc.stderr
    header = f"code de sortie {proc.returncode}\n"
    return truncate(header + (out or "(aucune sortie)"))


TOOLS = [
    Tool(
        name="run_command",
        description=(
            "Exécute une commande shell dans le dossier du projet (tests, installation, build, git). "
            "L'utilisateur doit confirmer avant l'exécution."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Commande complète, ex: pytest -q"},
                "timeout": {"type": "integer", "description": "Délai maximal en secondes", "default": 300},
            },
            "required": ["command"],
        },
        run=run_command,
        dangerous=True,
    ),
]
