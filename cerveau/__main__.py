"""Interface en ligne de commande.

  cerveau info                  matériel détecté et modèle recommandé
  cerveau check                 vérifie la connexion au serveur d'inférence
  cerveau chat [dossier]        dialogue avec mémoire sur un projet
  cerveau audit [dossier]       audit de sécurité guidé du projet
  cerveau memory [dossier]      affiche les derniers souvenirs
  cerveau note [dossier] texte  ajoute un souvenir
  cerveau model [nom]           affiche ou change le modèle principal
  cerveau use ollama|lmstudio   bascule le serveur d'inférence
  cerveau bench [modèles...]    mesure le débit réel des modèles
  cerveau skills                liste les procédures disponibles

Variable CERVEAU_DEBUG=1 : affiche les skills choisis et les réponses brutes du modèle.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, config, hardware, skills as skills_mod
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


def _project_root(args: argparse.Namespace) -> Path:
    root = Path(args.project).resolve()
    if not root.is_dir():
        answer = input(f"Le dossier {root} n'existe pas. Le créer ? [o/N] ").strip().lower()
        if answer not in ("o", "oui", "y", "yes"):
            sys.exit("abandon")
        root.mkdir(parents=True)
    return root


def _agent(args: argparse.Namespace) -> Agent:
    cfg = config.load()
    root = _project_root(args)
    return Agent(cfg, root, on_token=_print_token, on_tool=_print_tool, confirm=_confirm)


def cmd_chat(args: argparse.Namespace) -> int:
    if not args.plain:
        try:
            from . import console
        except ImportError:
            console = None  # type: ignore[assignment]
        if console is not None:
            cfg = config.load()
            root = _project_root(args)
            return console.run(cfg, root)
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
        st = agent.last_stats
        print(f"\n  [{'~' if st.estimated else ''}{st.prompt_tokens} ctx, {st.completion_tokens} générés, "
              f"{st.tokens_per_second:.0f} tok/s, {st.elapsed:.1f} s, {st.tool_calls} outils]\n")
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


SERVERS = {
    "ollama": ("http://localhost:11434/v1", "nomic-embed-text"),
    "lmstudio": ("http://localhost:1234/v1", "text-embedding-nomic-embed-text-v1.5"),
}


def cmd_model(args: argparse.Namespace) -> int:
    cfg = config.load()
    if args.name:
        cfg.model = args.name
    if args.embedding:
        cfg.embedding_model = args.embedding
    if args.context:
        cfg.context_tokens = args.context
    if args.name or args.embedding or args.context:
        cfg.save()
        print("configuration enregistrée")
    print(f"modèle principal : {cfg.model}")
    print(f"embedding        : {cfg.embedding_model}")
    print(f"contexte         : {cfg.context_tokens} tokens")
    print(f"serveur          : {cfg.base_url}")
    try:
        models = Client(cfg.base_url, cfg.api_key).models()
        print("disponibles      : " + (", ".join(models) or "aucun"))
    except LLMError:
        print("disponibles      : serveur injoignable")
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    cfg = config.load()
    url, embedding = SERVERS[args.server]
    cfg.base_url = url
    cfg.embedding_model = embedding
    cfg.save()
    print(f"serveur : {args.server} ({url}), embedding : {embedding}")
    print("pensez à vérifier le nom du modèle principal avec « cerveau model »")
    return 0


BENCH_PROMPT = (
    "Écris une fonction Python qui lit un fichier CSV de ventes (date, produit, quantité, prix) "
    "et renvoie le chiffre d'affaires par mois, avec les tests pytest correspondants."
)


def cmd_bench(args: argparse.Namespace) -> int:
    cfg = config.load()
    client = Client(cfg.base_url, cfg.api_key)
    models = args.models or [cfg.model]
    messages = [{"role": "user", "content": BENCH_PROMPT}]
    print(f"serveur {cfg.base_url}, {args.tokens} tokens par modèle\n")
    print(f"{'modèle':<32} {'1er token':>10} {'débit':>12}")
    for model in models:
        start = time.perf_counter()
        first = None
        chars = 0
        try:
            for token in client.chat_stream(model, messages, max_tokens=args.tokens):
                if first is None:
                    first = time.perf_counter() - start
                chars += len(token)
        except LLMError as exc:
            print(f"{model:<32} erreur : {exc}")
            continue
        total = time.perf_counter() - start
        gen = total - (first or 0)
        # Approximation : un token de code fait environ 3,5 caractères.
        tps = (chars / 3.5) / gen if gen > 0 else 0
        print(f"{model:<32} {first or 0:>8.2f} s {tps:>8.1f} tok/s")
    print("\nLe débit est estimé à partir des caractères reçus ; comparez les modèles entre eux.")
    return 0


def cmd_skills(_args: argparse.Namespace) -> int:
    items = skills_mod.load_all([config.HOME / "skills"])
    print(f"{len(items)} skill(s). Les vôtres vont dans {config.HOME / 'skills'} (fichiers .md).\n")
    for s in items:
        print(f"{s.name:<24} {s.description}")
        print(f"{'':<24} mots-clés : {', '.join(s.keywords)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cerveau", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"cerveau {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="matériel détecté et modèle recommandé").set_defaults(fn=cmd_info)
    sub.add_parser("check", help="vérifie la connexion au serveur d'inférence").set_defaults(fn=cmd_check)

    for name, fn, help_ in (("chat", cmd_chat, "console interactive"), ("audit", cmd_audit, "audit de sécurité")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("project", nargs="?", default=".")
        p.add_argument("--plain", action="store_true", help="mode texte simple, sans la console rich")
        p.set_defaults(fn=fn)

    p = sub.add_parser("memory", help="affiche les souvenirs d'un projet")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("note", help="ajoute un souvenir")
    p.add_argument("project")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_note)

    p = sub.add_parser("model", help="affiche ou change le modèle principal")
    p.add_argument("name", nargs="?", help="nom du modèle, ex: qwen3-coder:30b")
    p.add_argument("--embedding", help="modèle d'embedding")
    p.add_argument("--context", type=int, help="taille du contexte en tokens")
    p.set_defaults(fn=cmd_model)

    p = sub.add_parser("use", help="bascule entre ollama et lmstudio")
    p.add_argument("server", choices=sorted(SERVERS))
    p.set_defaults(fn=cmd_use)

    p = sub.add_parser("bench", help="mesure le débit réel des modèles")
    p.add_argument("models", nargs="*", help="modèles à tester, par défaut le modèle actif")
    p.add_argument("--tokens", type=int, default=300)
    p.set_defaults(fn=cmd_bench)

    sub.add_parser("skills", help="liste les procédures disponibles").set_defaults(fn=cmd_skills)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
