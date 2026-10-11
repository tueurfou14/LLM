"""Serveurs de développement lancés en arrière-plan.

run_command attend la fin d'une commande ; un serveur web ne se termine
jamais. start_server le lance à part, attend que son port réponde, puis le
laisse tourner pour que l'utilisateur ouvre l'application dans son
navigateur. La sortie du serveur va dans `.cerveau/logs/<nom>.log`. Tous les
serveurs sont arrêtés à la sortie du cerveau.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .registry import Tool, truncate
from .shell import _kill_tree, command_env, workdir

STARTUP_TIMEOUT = 120   # la première fois, uv installe les dépendances avant de démarrer


@dataclass
class Server:
    name: str
    command: str
    port: int
    proc: subprocess.Popen
    log_path: Path
    log_file: IO[bytes]


_SERVERS: dict[str, Server] = {}


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _http_status(url: str) -> int | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # jamais de proxy pour localhost
    try:
        with opener.open(url, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:  # noqa: BLE001
        return None


def _tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _stop(server: Server) -> None:
    if server.proc.poll() is None:
        _kill_tree(server.proc)
        try:
            server.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    server.log_file.close()


def stop_all() -> None:
    for name in list(_SERVERS):
        _stop(_SERVERS.pop(name))


atexit.register(stop_all)


def start_server(root: Path, command: str, port: int = 8000, name: str = "app", cwd: str = ".") -> str:
    """Lance une commande serveur en arrière-plan et attend que le port réponde."""
    port = int(port)
    if name in _SERVERS:
        _stop(_SERVERS.pop(name))
        time.sleep(0.5)
    if _port_open(port):
        return (f"échec : le port {port} est déjà occupé par un autre programme. Relance avec un autre port, "
                f"en le mettant à la fois dans la commande (--port) et dans le paramètre port.")
    logs = root / ".cerveau" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{name}.log"
    log_file = open(log_path, "wb")  # noqa: SIM115 - fermé par _stop
    env = command_env(root)
    env["PYTHONUNBUFFERED"] = "1"
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, shell=True, cwd=workdir(root, cwd), stdout=log_file,  # noqa: S602
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **kwargs)
    server = Server(name, command, port, proc, log_path, log_file)
    shown_log = log_path.relative_to(root).as_posix()

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_file.close()
            return truncate(f"échec : le serveur s'est arrêté au démarrage (code {proc.returncode}). "
                            f"Lis l'erreur ci-dessous, corrige le code, puis relance start_server.\n"
                            f"--- {shown_log} ---\n{_tail(log_path)}")
        if _port_open(port):
            break
        time.sleep(0.5)
    else:
        _stop(server)
        return truncate(f"échec : aucune réponse sur le port {port} après {STARTUP_TIMEOUT} s ; le serveur a été "
                        f"arrêté. Vérifie que la commande écoute bien sur ce port.\n--- {shown_log} ---\n{_tail(log_path)}")

    _SERVERS[name] = server
    url = f"http://127.0.0.1:{port}/"
    status = _http_status(url)
    text = (f"serveur « {name} » démarré : {url}\n"
            f"Il tourne en arrière-plan jusqu'à stop_server ou la fin de la session. Journal : {shown_log}. "
            f"Donne cette adresse à l'utilisateur. Vérifie une page avec web_fetch sur {url}…")
    if status is None:
        text += "\nLa page d'accueil n'a pas répondu en HTTP : consulte server_logs."
    elif status >= 500:
        text += (f"\nATTENTION : la page d'accueil renvoie une erreur {status}. Corrige-la avec le journal "
                 f"ci-dessous :\n{_tail(log_path)}")
    else:
        text += f"\nRéponse de la page d'accueil : HTTP {status}."
    return truncate(text)


def stop_server(root: Path, name: str = "app") -> str:
    server = _SERVERS.pop(name, None)
    if server is None:
        running = ", ".join(_SERVERS) or "aucun"
        return f"aucun serveur nommé « {name} ». Serveurs en cours : {running}"
    _stop(server)
    return f"serveur « {name} » arrêté (port {server.port} libéré)"


def server_logs(root: Path, name: str = "app", lines: int = 60) -> str:
    server = _SERVERS.get(name)
    log_path = server.log_path if server else root / ".cerveau" / "logs" / f"{name}.log"
    if not log_path.exists():
        return f"aucun journal pour le serveur « {name} »"
    if server is None:
        state = "arrêté"
    elif server.proc.poll() is None:
        state = f"en cours sur http://127.0.0.1:{server.port}/"
    else:
        state = f"arrêté de lui-même (code {server.proc.returncode})"
    return truncate(f"serveur « {name} » : {state}\n{_tail(log_path, max(1, min(int(lines), 400)))}")


TOOLS = [
    Tool(
        name="start_server",
        description=(
            "Lance l'application (serveur web) en arrière-plan et la laisse tourner, pour que l'utilisateur "
            "l'ouvre dans son navigateur. Attend que le port réponde et renvoie l'adresse, ou l'erreur de "
            "démarrage. À utiliser pour uvicorn, flask, npm run dev, etc. ; jamais run_command pour un serveur."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "Commande de lancement, ex: uv run uvicorn app.main:app --reload --port 8000"},
                "port": {"type": "integer", "description": "Port sur lequel le serveur écoute", "default": 8000},
                "name": {"type": "string", "description": "Nom du serveur, pour l'arrêter ou lire son journal",
                         "default": "app"},
                "cwd": {"type": "string", "description": "Sous-dossier où lancer la commande, relatif au projet",
                        "default": "."},
            },
            "required": ["command"],
        },
        run=start_server,
        dangerous=True,
    ),
    Tool(
        name="stop_server",
        description="Arrête un serveur lancé avec start_server.",
        parameters={"type": "object", "properties": {"name": {"type": "string", "default": "app"}}},
        run=stop_server,
    ),
    Tool(
        name="server_logs",
        description="Affiche l'état et les dernières lignes du journal d'un serveur lancé avec start_server "
                    "(erreurs 500, traces Python).",
        parameters={"type": "object", "properties": {"name": {"type": "string", "default": "app"},
                                                     "lines": {"type": "integer", "default": 60}}},
        run=server_logs,
    ),
]
