"""Exécution de commandes dans le projet, avec confirmation de l'utilisateur.

Le modèle peut lancer des tests, installer des dépendances ou démarrer un
script. Chaque commande est soumise à l'utilisateur avant d'être exécutée :
le registre appelle la fonction de confirmation parce que l'outil est marqué
`dangerous`. La commande tourne dans la racine du projet, avec un délai.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .registry import Tool, truncate


def project_venv(root: Path) -> Path | None:
    """L'environnement virtuel du projet, s'il existe."""
    for name in (".venv", "venv", "env"):
        candidate = root / name
        bindir = candidate / ("Scripts" if os.name == "nt" else "bin")
        if (bindir / ("python.exe" if os.name == "nt" else "python")).exists():
            return candidate
    return None


def command_env(root: Path) -> dict[str, str]:
    """Environnement des commandes : celui du projet, pas celui du cerveau.
    L'env virtuel du projet passe en tête du PATH ; celui du cerveau est retiré."""
    env = dict(os.environ)
    sep = os.pathsep
    paths = env.get("PATH", "").split(sep)
    own_venv = env.pop("VIRTUAL_ENV", None)
    if own_venv:
        own_bin = str(Path(own_venv) / ("Scripts" if os.name == "nt" else "bin"))
        paths = [p for p in paths if os.path.normcase(p) != os.path.normcase(own_bin)]
    venv = project_venv(root)
    if venv:
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        paths.insert(0, str(bindir))
        env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = sep.join(paths)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def describe_environment(root: Path) -> str:
    """Résumé pour la consigne du modèle : ce qu'il peut utiliser pour exécuter du code."""
    venv = project_venv(root)
    parts = [
        f"système {platform.system()} {platform.release()}",
        _shell_description(),
        f"uv {'disponible' if shutil.which('uv') else 'absent'}",
        f"python {platform.python_version()} ({'uv' if shutil.which('uv') else sys.executable})",
        f"environnement virtuel du projet : {venv.name if venv else 'aucun (crée-le avec « uv venv »)'}",
        f"docker {'disponible' if shutil.which('docker') else 'absent'}",
    ]
    return ", ".join(parts)


def _shell_description() -> str:
    if os.name == "nt":
        return ("shell cmd.exe de Windows, ni bash ni PowerShell : « && » enchaîne deux commandes, mais ls, cat, rm, "
                "touch, mkdir -p, export et source n'existent pas ; pour lire, créer ou modifier des fichiers et "
                "dossiers, utilise les outils de fichiers, pas des commandes")
    return "shell sh"


def workdir(root: Path, cwd: str = ".") -> Path:
    """Dossier où lancer une commande : la racine, ou un sous-dossier du projet."""
    from .files import safe_path

    target = safe_path(root, cwd or ".")
    if not target.is_dir():
        raise FileNotFoundError(f"dossier introuvable : {cwd}")
    return target


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", _oem_codepage(), "cp1252"):
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _oem_codepage() -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None


import re

DESTRUCTIVE = [
    r"\brm\s+(-\w*r\w*\s+|-\w*f\w*\s+)+(/|~|\$HOME|\.\.|[A-Za-z]:\\?)(\s|$)",   # rm -rf / ~ .. C:\
    r"\brmdir\s+/s",                       # rmdir /s (Windows)
    r"\bdel\s+.*(/s|/q).*\b[A-Za-z]:\\",  # del /s C:\
    r"remove-item\s+.*-recurse.*(\\|/)?([A-Za-z]:\\?|~|\$home)(\s|$)",
    r"\bformat\s+[A-Za-z]:",
    r"\bmkfs\b", r"\bdiskpart\b", r"\bdd\s+if=",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r"git\s+push\s+.*(--force|-f)\b", r"git\s+reset\s+--hard", r"git\s+clean\s+-\w*f",
    r":\(\)\s*\{\s*:\|:&\s*\};:",         # fork bomb
    r"\bcurl\b.*\|\s*(ba)?sh\b", r"\biex\b.*downloadstring",   # exécution de script distant
]


def is_destructive(command: str) -> str | None:
    """Renvoie le motif reconnu si la commande peut détruire des données hors du projet."""
    low = command.lower()
    for pattern in DESTRUCTIVE:
        if re.search(pattern, low):
            return pattern
    return None


# Serveurs web : (motif, port par défaut). run_command les confie à start_server.
WEB_SERVERS = [
    (r"\buvicorn\b", 8000), (r"\bgunicorn\b", 8000), (r"\bhypercorn\b", 8000), (r"\bfastapi\s+(run|dev)\b", 8000),
    (r"manage\.py\s+runserver", 8000), (r"\bflask\s+run\b", 5000), (r"\b(?:python[\d.]*|py)(?:\.exe)?[\"']?\s+-m\s+http\.server", 8000),
    (r"\bstreamlit\s+run\b", 8501), (r"\bnext\s+(dev|start)\b", 3000), (r"\bvite\b(?!\s+build)", 5173),
    (r"\bnpm\s+(run\s+)?(dev|start|serve)\b", 3000), (r"\byarn\s+(dev|start|serve)\b", 3000),
    (r"\bpnpm\s+(dev|start|serve)\b", 3000),
]
SERVER_PATTERNS = [p for p, _ in WEB_SERVERS] + [r"\bnode\s+\S*server", r"\btail\s+-f\b", r"--reload\b", r"\bwatch\b"]
SERVER_TIMEOUT = 15


def is_server_command(command: str) -> bool:
    low = command.lower()
    return any(re.search(p, low) for p in SERVER_PATTERNS)


def web_server_port(command: str) -> int | None:
    """Port d'un serveur web reconnu : celui de la commande, sinon le défaut de l'outil."""
    low = command.lower()
    for pattern, default in WEB_SERVERS:
        if re.search(pattern, low):
            explicit = re.search(r"(?:--port[=\s]+|\s-p\s+|runserver\s+(?:[\d.]+:)?|http\.server\s+)(\d{2,5})\b", low)
            return int(explicit.group(1)) if explicit else default
    return None


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        proc.kill()


def run_command(root: Path, command: str, timeout: int = 300, cwd: str = ".") -> str:
    """Lance une commande shell dans le dossier du projet et renvoie sa sortie.
    Un serveur web (uvicorn, npm run dev…) ne se termine jamais : il est confié
    à start_server, qui le laisse tourner en arrière-plan. Les autres commandes
    sans fin (tail -f, watch) sont arrêtées après quelques secondes."""
    port = web_server_port(command)
    if port is not None:
        from .server import start_server

        return start_server(root, command, port=port, cwd=cwd)
    server = is_server_command(command)
    limit = SERVER_TIMEOUT if server else min(int(timeout), 1800)
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, shell=True, cwd=workdir(root, cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,  # noqa: S602
                            stdin=subprocess.DEVNULL, env=command_env(root), **kwargs)
    timed_out = False
    try:
        raw_out, raw_err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            raw_out, raw_err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            raw_out, raw_err = b"", b""
    out = _decode(raw_out or b"")
    err = _decode(raw_err or b"")
    if err:
        out += ("\n[stderr]\n" if out else "[stderr]\n") + err
    if timed_out and server:
        header = (f"commande arrêtée par le cerveau après {limit} s (elle ne se termine pas seule ; c'est normal). "
                  "Sortie du début ci-dessous. Pour lancer une application et la laisser tourner, utilise "
                  "start_server.\n")
    elif timed_out:
        header = f"commande interrompue après {limit} s (elle ne s'est pas terminée) : {command}\n"
    else:
        header = f"code de sortie {proc.returncode}\n"
    return truncate(header + (out or "(aucune sortie)"))


TOOLS = [
    Tool(
        name="run_command",
        description=(
            "Exécute une commande shell et renvoie sa sortie quand elle est terminée (tests, installation, "
            "build, git). Elle part de la racine du projet ; pour un sous-dossier, passe cwd au lieu de « cd ». "
            "Pour lancer l'application et la laisser tourner, utilise start_server."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Commande complète, ex: pytest -q"},
                "timeout": {"type": "integer", "description": "Délai maximal en secondes", "default": 300},
                "cwd": {"type": "string", "description": "Sous-dossier où lancer la commande, relatif au projet",
                        "default": "."},
            },
            "required": ["command"],
        },
        run=run_command,
        dangerous=True,
    ),
]
