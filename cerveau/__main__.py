"""Interface en ligne de commande.

  cerveau info                  matériel détecté et modèle recommandé
  cerveau check                 vérifie la connexion au serveur d'inférence
  cerveau chat [dossier]        dialogue avec mémoire sur un projet
  cerveau audit [dossier]       audit de sécurité guidé du projet
  cerveau memory [dossier]      affiche les derniers souvenirs
  cerveau note [dossier] texte  ajoute un souvenir
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config, hardware
from .agent import Agent
from .llm import Client, LLMError
from .memory import MemoryStore

AUDIT_PROMPT = (
    "Fais un audit de sécurité de ce projet. Commence par scanners_status, puis lance les scanners "
    "disponibles, lis le code concerné pour confirmer chaque faille, et termine par un rapport classé "
    "par gravité avec fichier, ligne, explication et correctif proposé."
)


def _print_token(token: str) -> None:
    sys.stdout.write(token)
    sys.stdout.flush()


def _print_tool(name: str, args: dict, result: str) -> None:
    short = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
    first = result.splitlines()[0] if result else ""
    sys.stdout.write(f"\n  ⚙ {name}({short}) → {first[:100]}\n")
    sys.stdout.flush()


def _short(value: object, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _confirm(name: str, args: dict) -> bool:
    command = args.get("command", "")
    sys.stdout.write(f"\n  ⚠ Le modèle veut exécuter : {command}\n  Autoriser ? [o/N] ")
    sys.stdout.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("o", "oui", "y", "yes")


def cmd_info(_args: argparse.Namespace) -> int:
    hw = hardware.detect()
    tier = hardware.pick_tier(hw)
    print(hardware.describe(hw, tier))
    cfg = config.load()
    print(f"\nConfig       : {config.HOME / 'config.json'}")
    print(f"Serveur      : {cfg.base_url}")
    print(f"Modèle actif : {cfg.model}")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    cfg = config.load()
    client = Client(cfg.base_url, cfg.api_key)
    try:
        models = client.models()
    except LLMError as exc:
        print(f"✗ {exc}")
        return 1
    print(f"✓ serveur joignable sur {cfg.base_url}")
    print(f"  modèles chargés : {', '.join(models) or 'aucun'}")
    for needed, label in ((cfg.model, "modèle principal"), (cfg.embedding_model, "modèle d'embedding")):
        ok = any(needed in m for m in models)
        print(f"  {'✓' if ok else '✗'} {label} « {needed} » {'disponible' if ok else 'absent'}")
    return 0


def _agent(args: argparse.Namespace) -> Agent:
    cfg = config.load()
    root = Path(args.project).resolve()
    if not root.is_dir():
        answer = input(f"Le dossier {root} n'existe pas. Le créer ? [o/N] ").strip().lower()
        if answer not in ("o", "oui", "y", "yes"):
            sys.exit("abandon")
        root.mkdir(parents=True)
    return Agent(cfg, root, on_token=_print_token, on_tool=_print_tool, confirm=_confirm)


def cmd_chat(args: argparse.Namespace) -> int:
    agent = _agent(args)
    print(f"Cerveau v{__version__} · projet « {agent.project} » · modèle {agent.config.model}")
    print(f"dossier de travail : {agent.root}")
    print("Tapez votre demande, « /note texte » pour mémoriser un fait, « /quit » pour sortir.\n")
    while True:
        try:
            line = input("vous > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        if line.startswith("/note "):
            agent.note(line[6:])
            print("  souvenir enregistré")
            continue
        print("cerveau > ", end="", flush=True)
        try:
            agent.ask(line)
        except LLMError as exc:
            print(f"\n✗ {exc}")
        print("\n")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    agent = _agent(args)
    print(f"Audit de « {agent.project} » avec {agent.config.model}\n")
    try:
        agent.ask(AUDIT_PROMPT)
    except LLMError as exc:
        print(f"\n✗ {exc}")
        return 1
    print()
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    cfg = config.load()
    project = Path(args.project).resolve().name
    store = MemoryStore(cfg.db_path)
    items = store.recent(project, limit=args.limit)
    print(f"{store.count(project)} souvenir(s) pour « {project} »\n")
    for m in items:
        print(f"[{m.id}] ({m.kind}) {m.content[:200].replace(chr(10), ' ')}")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    cfg = config.load()
    project = Path(args.project).resolve().name
    client = Client(cfg.base_url, cfg.api_key)
    store = MemoryStore(cfg.db_path, embedder=lambda t: client.embed(cfg.embedding_model, t))
    mid = store.remember(project, " ".join(args.text), kind="fact")
    print(f"souvenir {mid} enregistré pour « {project} »")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cerveau", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"cerveau {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="matériel détecté et modèle recommandé").set_defaults(fn=cmd_info)
    sub.add_parser("check", help="vérifie la connexion au serveur d'inférence").set_defaults(fn=cmd_check)

    for name, fn, help_ in (("chat", cmd_chat, "dialogue avec mémoire"), ("audit", cmd_audit, "audit de sécurité")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("project", nargs="?", default=".")
        p.set_defaults(fn=fn)

    p = sub.add_parser("memory", help="affiche les souvenirs d'un projet")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("note", help="ajoute un souvenir")
    p.add_argument("project")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_note)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
