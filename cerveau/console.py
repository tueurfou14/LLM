"""Console interactive : texte en temps réel, appels d'outils visibles,
diffs des fichiers modifiés, statistiques de tokens, saisie avec historique.

Construite sur rich et prompt_toolkit. Si l'un manque, __main__ retombe sur
le mode texte.
"""

from __future__ import annotations

import difflib
import time
from pathlib import Path

from rich.console import Console as RichConsole, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.status import Status
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import __version__, config, skills as skills_mod
from .agent import Agent, TurnStats
from .llm import Client, LLMError
from .memory import GLOBAL

COMMANDS = {
    "/help": "cette aide",
    "/quit": "quitter (bilan de la session)",
    "/note": "texte : mémoriser un fait sur le projet ; « /note global … » pour tous les projets",
    "/memory": "derniers souvenirs du projet et globaux",
    "/forget": "id : supprimer un souvenir",
    "/model": "[nom] : afficher ou changer le modèle",
    "/skills": "procédures disponibles",
    "/files": "fichiers touchés pendant la session",
    "/audit": "lancer l'audit de sécurité du projet",
    "/autonome": "tâche : enchaîner les étapes sans s'arrêter (aussi déclenché par le mot « autonome »)",
    "/mode": "[confirm|auto] : confirm demande un o avant chaque commande, auto ne demande rien",
    "/verbose": "résultats complets des outils et diffs entiers",
    "/clear": "oublier la conversation en cours (la mémoire reste)",
    "/stats": "statistiques de la dernière réponse",
}

AUDIT_PROMPT = (
    "Fais un audit de sécurité de ce projet. Commence par scanners_status, puis lance les scanners "
    "disponibles, lis le code concerné pour confirmer chaque faille, et termine par un rapport classé "
    "par gravité avec fichier, ligne, explication et correctif proposé."
)

DIFF_LIMIT = 60
LEXERS = {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
          ".html": "html", ".css": "css", ".json": "json", ".md": "markdown", ".toml": "toml",
          ".yml": "yaml", ".yaml": "yaml", ".sh": "bash", ".sql": "sql", ".go": "go", ".rs": "rust",
          ".java": "java", ".cs": "csharp", ".php": "php", ".rb": "ruby"}


class ChatConsole:
    def __init__(self, cfg: config.Config, root: Path):
        self.cfg = cfg
        self.root = root
        self.out = RichConsole(highlight=False)
        self.verbose = False
        self.mode = cfg.permission_mode if cfg.permission_mode in ("confirm", "auto") else "confirm"
        # Affichage en cours : un seul « live » rich à la fois.
        self.live: Live | None = None
        self.status: Status | None = None
        self.buffer: list[str] = []
        self.thinking: list[str] = []
        self.first_token_at: float | None = None
        self.tool_started_at = 0.0
        self.pending_old: str | None = None
        # Session
        self.files_touched: dict[str, str] = {}
        self.session_prompt = 0
        self.session_completion = 0
        self.session_turns = 0
        self.session_tools = 0
        self.session_started = time.perf_counter()
        self.agent = Agent(cfg, root, on_token=self._on_token, on_thinking=self._on_thinking,
                           on_tool_start=self._on_tool_start, on_tool=self._on_tool, confirm=self._confirm,
                           on_learned=self._on_learned)
        self._apply_mode()
        self.session = _make_prompt_session()

    def _apply_mode(self) -> None:
        # En mode auto, aucun rappel de confirmation : le registre applique alors le garde-fou destructif.
        self.agent.confirm = None if self.mode == "auto" else self._confirm

    # --- gestion des affichages vivants -----------------------------------------

    def _start_status(self, message: str) -> None:
        self._stop_live()
        self._stop_status()
        self.status = self.out.status(message, spinner="dots")
        self.status.start()

    def _stop_status(self) -> None:
        if self.status is not None:
            self.status.stop()
            self.status = None

    def _start_live(self) -> None:
        self._stop_status()
        if self.live is None:
            self.buffer = []
            self.first_token_at = time.perf_counter()
            self.live = Live(Text(""), console=self.out, refresh_per_second=15, transient=True,
                             vertical_overflow="visible")
            self.live.start()

    def _render_live(self) -> None:
        if self.live is None:
            return
        text = "".join(self.buffer)
        elapsed = time.perf_counter() - (self.first_token_at or time.perf_counter())
        est = max(1, len(text) // 4)
        footer = Text(f"  ↓ ~{est} tokens · {est / elapsed if elapsed > 0.2 else 0:.0f} tok/s", style="dim")
        self.live.update(Group(Text(text), footer))

    def _stop_live(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
            text = "".join(self.buffer).strip()
            if text:
                self.out.print(Markdown(text))
            self.buffer = []

    def _flush_thinking(self) -> None:
        if self.thinking:
            words = len("".join(self.thinking).split())
            self.out.print(Text(f"  ∴ réflexion ({words} mots)", style="dim italic"))
            if self.verbose:
                self.out.print(Panel("".join(self.thinking).strip()[:3000], border_style="dim", title="réflexion"))
            self.thinking = []

    # --- callbacks de l'agent ------------------------------------------------------

    def _on_learned(self, scope: str, kind: str, content: str) -> None:
        where = "partout" if scope.startswith("glob") else "ce projet"
        self.out.print(Text.assemble(("  ✎ retenu ", "magenta"), (f"({where}) ", "dim"), (content, "dim")))

    def _on_thinking(self, text: str) -> None:
        if not self.thinking:
            self._start_status("[dim]réflexion du modèle…[/]")
        self.thinking.append(text)

    def _on_token(self, token: str) -> None:
        self._flush_thinking()
        self._start_live()
        self.buffer.append(token)
        self._render_live()

    def _on_tool_start(self, name: str, args: dict) -> None:
        self._flush_thinking()
        self._stop_live()
        self._stop_status()
        self.out.print(Text.assemble(("  ⚙ ", "yellow"), (name, "bold"), ("  " + _args_summary(args), "dim")))
        self.pending_old = None
        if name in ("write_file", "edit_file") and args.get("path"):
            target = self.root / str(args["path"])
            if target.is_file():
                try:
                    self.pending_old = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    self.pending_old = None
        self.tool_started_at = time.perf_counter()
        if name == "run_command" and self.mode != "auto":
            return  # la confirmation prend la main ; le chrono démarre après
        label = f"exécution : {str(args.get('command', ''))[:60]}" if name == "run_command" else f"{name}…"
        self._start_status(f"[dim]{label}[/]")

    def _on_tool(self, name: str, args: dict, result: str) -> None:
        self._stop_status()
        elapsed = time.perf_counter() - self.tool_started_at
        first = (result.splitlines() or [""])[0]
        failed = first.lower().startswith(("erreur", "fichier introuvable", "outil inconnu", "arguments invalides",
                                           "chemin hors", "l'utilisateur a refusé", "commande interrompue"))
        if name == "run_command" and first.startswith("code de sortie") and not first.endswith(" 0"):
            failed = True
        mark = ("    ✗ ", "red") if failed else ("    ✓ ", "green")
        timing = f"  {elapsed:.1f} s" if elapsed >= 1 else ""
        self.out.print(Text.assemble(mark, (first[:120], "dim"), (timing, "dim")))
        if name in ("write_file", "edit_file", "create_directory") and not failed and args.get("path"):
            key = str(args["path"])
            created = "créé" in first or name == "create_directory"
            if created or key not in self.files_touched:
                self.files_touched[key] = "créé" if created else "modifié"
        if name in ("write_file", "edit_file") and not failed:
            self._show_diff(str(args.get("path", "")))
        elif name == "run_command" and (self.verbose or failed):
            body = "\n".join(result.splitlines()[1:])[-4000:]
            if body.strip():
                self.out.print(Panel(body, border_style="red" if failed else "dim", title=args.get("command", ""),
                                     title_align="left"))
        elif self.verbose and len(result) > len(first):
            self.out.print(Panel(result[:4000], border_style="dim", title=name, title_align="left"))

    def _show_diff(self, path: str) -> None:
        target = self.root / path
        try:
            new = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if self.pending_old is None:
            lines = new.count("\n")
            if self.verbose or lines <= 40:
                lexer = LEXERS.get(target.suffix.lower(), "text")
                self.out.print(Panel(Syntax(new.rstrip("\n"), lexer, line_numbers=True, word_wrap=True),
                                     title=f"{path}  (nouveau)", title_align="left", border_style="green"))
            return
        diff = list(difflib.unified_diff(self.pending_old.splitlines(), new.splitlines(),
                                         fromfile=f"{path} (avant)", tofile=f"{path} (après)", lineterm="", n=2))
        if not diff:
            return
        shown = diff if self.verbose or len(diff) <= DIFF_LIMIT else diff[:DIFF_LIMIT] + [f"… {len(diff) - DIFF_LIMIT} lignes de plus (/verbose)"]
        added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        self.out.print(Panel(Syntax("\n".join(shown), "diff", word_wrap=True),
                             title=f"{path}  [green]+{added}[/] [red]-{removed}[/]", title_align="left",
                             border_style="blue"))

    def _confirm(self, name: str, args: dict) -> bool:
        self._stop_live()
        self._stop_status()
        self.out.print(Panel(Text(args.get("command", ""), style="bold"), title="Le modèle veut exécuter",
                             border_style="yellow", title_align="left"))
        try:
            answer = self.out.input("  Autoriser ? [bold][o/N][/] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        ok = answer in ("o", "oui", "y", "yes")
        if ok:
            self.tool_started_at = time.perf_counter()
            self._start_status(f"[dim]exécution : {args.get('command', '')[:60]}[/]")
        return ok

    # --- statistiques ------------------------------------------------------------------

    def _print_stats(self, st: TurnStats) -> None:
        ctx = max(self.cfg.context_tokens, 1)
        pct = min(100, int(100 * st.prompt_tokens / ctx))
        bar = ProgressBar(total=ctx, completed=min(st.prompt_tokens, ctx), width=16,
                          complete_style="green" if pct < 70 else "yellow" if pct < 90 else "red")
        tilde = "~" if st.estimated else ""
        line = Table.grid(padding=(0, 1))
        line.add_row(
            Text(f"↑ {tilde}{_fmt(st.prompt_tokens)}", style="dim"),
            Text(f"↓ {tilde}{_fmt(st.completion_tokens)}", style="dim"),
            Text(f"{st.tokens_per_second:.0f} tok/s", style="bold" if st.tokens_per_second >= 20 else "yellow"),
            Text(f"⏱ {st.first_token:.1f} s + {st.elapsed - st.first_token:.1f} s", style="dim"),
            Text(f"{st.tool_calls} outil{'s' if st.tool_calls > 1 else ''}", style="dim"),
            Text("contexte", style="dim"), bar, Text(f"{pct}%", style="dim"),
        )
        self.out.print(line)
        if pct >= 85:
            self.out.print("  [yellow]contexte presque plein : /clear repart à zéro, la mémoire du projet reste.[/]")

    def _print_session_summary(self) -> None:
        if not self.session_turns:
            return
        minutes = (time.perf_counter() - self.session_started) / 60
        table = Table.grid(padding=(0, 2))
        table.add_row("réponses", str(self.session_turns))
        table.add_row("tokens générés", _fmt(self.session_completion))
        table.add_row("tokens de contexte envoyés", _fmt(self.session_prompt))
        table.add_row("appels d'outils", str(self.session_tools))
        table.add_row("durée", f"{minutes:.0f} min")
        if self.files_touched:
            table.add_row("fichiers", "\n".join(f"{v} : {k}" for k, v in sorted(self.files_touched.items())))
        self.out.print(Panel(table, title="bilan de la session", border_style="blue", title_align="left"))

    # --- commandes -----------------------------------------------------------------------

    def _command(self, line: str) -> bool:
        """Renvoie False pour quitter."""
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        if cmd in ("/quit", "/exit"):
            return False
        if cmd == "/help":
            table = Table.grid(padding=(0, 2))
            for name, desc in COMMANDS.items():
                table.add_row(Text(name, style="bold"), desc)
            table.add_row("", "Entrée envoie, Alt+Entrée ajoute une ligne, flèches haut/bas : historique.")
            table.add_row("", "Ctrl+C pendant une réponse l'interrompt.")
            self.out.print(Panel(table, border_style="dim"))
        elif cmd == "/note" and rest:
            if rest.startswith("global "):
                self.agent.store.remember(GLOBAL, rest[7:], kind="preference")
                self.out.print("  [green]préférence enregistrée pour tous les projets[/]")
            else:
                self.agent.note(rest)
                self.out.print("  [green]souvenir enregistré pour ce projet[/]")
        elif cmd == "/memory":
            for label, project in (("ce projet", self.agent.project), ("global", GLOBAL)):
                items = self.agent.store.recent(project, limit=10)
                self.out.print(f"  [bold]{label}[/] : {self.agent.store.count(project)} souvenir(s)")
                for m in items:
                    self.out.print(f"    [dim][{m.id}] ({m.kind})[/] {m.content[:150].replace(chr(10), ' ')}")
        elif cmd == "/forget" and rest.isdigit():
            self.agent.store.forget(int(rest))
            self.out.print(f"  souvenir {rest} supprimé")
        elif cmd == "/model":
            if rest:
                self.cfg.model = rest
                self.cfg.save()
            try:
                models = ", ".join(Client(self.cfg.base_url, self.cfg.api_key).models()) or "aucun"
            except LLMError:
                models = "serveur injoignable"
            self.out.print(f"  modèle : [bold]{self.cfg.model}[/]\n  disponibles : [dim]{models}[/]")
        elif cmd == "/skills":
            for s in skills_mod.load_all([config.HOME / "skills"]):
                self.out.print(f"  [bold]{s.name:<22}[/] {s.description}")
        elif cmd == "/files":
            if not self.files_touched:
                self.out.print("  aucun fichier touché pour l'instant")
            for path, what in sorted(self.files_touched.items()):
                self.out.print(f"  [dim]{what:<8}[/] {path}")
        elif cmd == "/audit":
            self._ask(AUDIT_PROMPT)
        elif cmd == "/autonome":
            self._ask(rest or "Continue en autonomie selon PLAN.md.", autonomous=True)
        elif cmd == "/mode":
            if rest in ("confirm", "auto"):
                self.mode = rest
                self.cfg.permission_mode = rest
                self.cfg.save()
                self._apply_mode()
            label = ("auto : rien n'est demandé, les commandes destructrices sont bloquées"
                     if self.mode == "auto" else "confirm : chaque commande attend votre o")
            self.out.print(f"  mode [bold]{label}[/]")
        elif cmd == "/verbose":
            self.verbose = not self.verbose
            self.out.print(f"  résultats complets et diffs entiers : {'oui' if self.verbose else 'non'}")
        elif cmd == "/clear":
            self.agent.history.clear()
            self.out.print("  conversation oubliée")
        elif cmd == "/stats":
            self._print_stats(self.agent.last_stats)
        else:
            self.out.print("  [red]commande inconnue[/], /help pour la liste")
        return True

    # --- boucle principale -------------------------------------------------------------

    def _on_step(self, step: int, limit: int) -> None:
        self._stop_status()
        self._stop_live()
        if step > 1:
            self._print_stats(self.agent.last_stats)
        self.out.rule(f"[magenta]autonome · étape {step}/{limit}[/]", style="magenta")
        self._start_status("[dim]réflexion…[/]")

    def _ask(self, line: str, autonomous: bool | None = None) -> None:
        if autonomous is None:
            autonomous = Agent.wants_autonomy(line)
        self.out.print()
        if autonomous and self.mode != "auto":
            self.out.print("  [yellow]mode autonome : les commandes demanderont encore votre o ; « /mode auto » pour ne rien demander[/]")
        self._start_status("[dim]réflexion…[/]")
        try:
            if autonomous:
                self.agent.ask_autonomous(line, on_step=self._on_step)
            else:
                self.agent.ask(line)
        except KeyboardInterrupt:
            self._stop_status()
            self._stop_live()
            self.out.print("\n  [yellow]interrompu[/]")
            return
        except LLMError as exc:
            self._stop_status()
            self._stop_live()
            self.out.print(f"\n  [red]✗ {exc}[/]")
            return
        except Exception as exc:  # noqa: BLE001 - une erreur inattendue ne doit pas tuer la session
            self._stop_status()
            self._stop_live()
            self.out.print(f"\n  [red]✗ erreur interne : {type(exc).__name__}: {exc}[/]")
            self.out.print("  [dim]la session continue ; relancez avec CERVEAU_DEBUG=1 pour le détail[/]")
            if self.agent.debug:
                self.out.print_exception()
            return
        self._stop_status()
        self._flush_thinking()
        self._stop_live()
        st = self.agent.last_stats
        self.session_turns += 1
        self.session_prompt += st.prompt_tokens
        self.session_completion += st.completion_tokens
        self.session_tools += st.tool_calls
        self._print_stats(st)
        self.out.print()

    def _read(self) -> str | None:
        if self.session is not None:
            try:
                return self.session.prompt([("bold fg:ansicyan", "vous › ")])
            except (EOFError, KeyboardInterrupt):
                return None
        try:
            return self.out.input("[bold cyan]vous ›[/] ")
        except (EOFError, KeyboardInterrupt):
            return None

    def run(self) -> int:
        header = Table.grid(padding=(0, 2))
        header.add_row(Text(f"Cerveau v{__version__}", style="bold"),
                       Text(f"modèle {self.cfg.model}", style="cyan"),
                       Text(f"projet {self.agent.project}", style="green"),
                       Text("mode auto" if self.mode == "auto" else "mode confirm", style="magenta" if self.mode == "auto" else "dim"),
                       Text(str(self.root), style="dim"))
        self.out.print(Panel(header, border_style="blue"))
        self.out.print("[dim]/help pour les commandes, /quit pour sortir.[/]\n")

        while True:
            line = self._read()
            if line is None:
                self.out.print()
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self._command(line):
                    break
                continue
            self._ask(line)
        self._print_session_summary()
        return 0


def _make_prompt_session():
    """Saisie avec historique, complétion des commandes et multi-ligne.
    Renvoie None si prompt_toolkit manque ou si l'entrée n'est pas un terminal."""
    import sys

    if not sys.stdin.isatty():
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
    except ImportError:
        return None
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event):  # noqa: ANN001
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event):  # noqa: ANN001
        event.current_buffer.insert_text("\n")

    config.HOME.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(config.HOME / "history.txt")),
        completer=WordCompleter(list(COMMANDS), sentence=True),
        complete_while_typing=True,
        multiline=True,
        key_bindings=bindings,
        prompt_continuation=lambda width, line_number, is_soft_wrap: "      … ",
    )


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _args_summary(args: dict) -> str:
    parts = []
    for key, value in args.items():
        if key in ("content", "new", "old"):
            parts.append(f"{key}: {len(str(value))} car.")
        else:
            text = str(value)
            parts.append(text if len(text) <= 70 else text[:70] + "…")
    return "  ".join(parts)


def run(cfg: config.Config, root: Path) -> int:
    return ChatConsole(cfg, root).run()
