"""Console interactive : texte en temps réel, appels d'outils visibles,
statistiques de tokens après chaque réponse.

Construite sur rich. Si rich manque, __main__ retombe sur le mode texte.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console as RichConsole
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from . import __version__, config, skills as skills_mod
from .agent import Agent, TurnStats
from .llm import Client, LLMError
from .memory import MemoryStore

HELP = """\
[bold]/help[/]            cette aide
[bold]/quit[/]            quitter
[bold]/note[/] texte      mémoriser un fait sur le projet
[bold]/memory[/]          derniers souvenirs du projet
[bold]/model[/] [nom]     afficher ou changer le modèle (enregistré dans la config)
[bold]/skills[/]          procédures disponibles
[bold]/verbose[/]         afficher le résultat complet des outils
[bold]/clear[/]           oublier la conversation en cours (la mémoire reste)
[bold]/stats[/]           statistiques de la dernière réponse
Ctrl+C pendant une réponse l'interrompt."""


class ChatConsole:
    def __init__(self, cfg: config.Config, root: Path):
        self.cfg = cfg
        self.root = root
        self.out = RichConsole(highlight=False)
        self.verbose = False
        self.live: Live | None = None
        self.buffer: list[str] = []
        self.waiting = False
        self.agent = Agent(cfg, root, on_token=self._on_token, on_tool_start=self._on_tool_start,
                           on_tool=self._on_tool, confirm=self._confirm)

    # --- affichage du flux ----------------------------------------------------

    def _start_live(self) -> None:
        if self.live is None:
            self.buffer = []
            self.live = Live(Text(""), console=self.out, refresh_per_second=15, transient=True,
                             vertical_overflow="visible")
            self.live.start()

    def _stop_live(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
            text = "".join(self.buffer).strip()
            if text:
                self.out.print(Markdown(text))
            self.buffer = []

    def _on_token(self, token: str) -> None:
        if self.waiting:
            self.waiting = False
        self._start_live()
        self.buffer.append(token)
        self.live.update(Text("".join(self.buffer)))  # type: ignore[union-attr]

    def _on_tool_start(self, name: str, args: dict) -> None:
        self._stop_live()
        self.waiting = False
        self.out.print(Text.assemble(("  ⚙ ", "yellow"), (name, "bold"), ("  " + _args_summary(args), "dim")))

    def _on_tool(self, name: str, args: dict, result: str) -> None:
        first = (result.splitlines() or [""])[0]
        failed = first.lower().startswith(("erreur", "fichier introuvable", "outil inconnu", "arguments invalides",
                                           "chemin hors", "l'utilisateur a refusé"))
        mark = ("    ✗ ", "red") if failed else ("    ✓ ", "green")
        self.out.print(Text.assemble(mark, (first[:120], "dim")))
        if self.verbose and len(result) > len(first):
            self.out.print(Panel(result[:4000], border_style="dim", title=name, title_align="left"))

    def _confirm(self, name: str, args: dict) -> bool:
        self._stop_live()
        self.out.print(Panel(Text(args.get("command", ""), style="bold"), title="Le modèle veut exécuter",
                             border_style="yellow", title_align="left"))
        try:
            answer = self.out.input("  Autoriser ? [bold][o/N][/] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("o", "oui", "y", "yes")

    # --- statistiques ---------------------------------------------------------

    def _print_stats(self, st: TurnStats) -> None:
        ctx = max(self.cfg.context_tokens, 1)
        pct = min(100, int(100 * st.prompt_tokens / ctx))
        bar = ProgressBar(total=ctx, completed=min(st.prompt_tokens, ctx), width=16,
                          complete_style="green" if pct < 70 else "yellow" if pct < 90 else "red")
        tilde = "~" if st.estimated else ""
        line = Table.grid(padding=(0, 1))
        line.add_row(
            Text(f"↑ {tilde}{st.prompt_tokens:,}".replace(",", " "), style="dim"),
            Text(f"↓ {tilde}{st.completion_tokens:,}".replace(",", " "), style="dim"),
            Text(f"{st.tokens_per_second:.0f} tok/s", style="bold" if st.tokens_per_second >= 20 else "yellow"),
            Text(f"{st.elapsed:.1f} s", style="dim"),
            Text(f"{st.tool_calls} outil{'s' if st.tool_calls > 1 else ''}", style="dim"),
            Text("contexte", style="dim"), bar, Text(f"{pct}%", style="dim"),
        )
        self.out.print(line)

    # --- commandes --------------------------------------------------------------

    def _command(self, line: str) -> bool:
        """Renvoie False pour quitter."""
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        if cmd in ("/quit", "/exit"):
            return False
        if cmd == "/help":
            self.out.print(Panel(HELP, border_style="dim"))
        elif cmd == "/note" and rest:
            self.agent.note(rest)
            self.out.print("  [green]souvenir enregistré[/]")
        elif cmd == "/memory":
            for m in self.agent.store.recent(self.agent.project, limit=10):
                self.out.print(f"  [dim][{m.id}] ({m.kind})[/] {m.content[:160].replace(chr(10), ' ')}")
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
        elif cmd == "/verbose":
            self.verbose = not self.verbose
            self.out.print(f"  résultats complets des outils : {'oui' if self.verbose else 'non'}")
        elif cmd == "/clear":
            self.agent.history.clear()
            self.out.print("  conversation oubliée")
        elif cmd == "/stats":
            self._print_stats(self.agent.last_stats)
        else:
            self.out.print("  [red]commande inconnue[/], /help pour la liste")
        return True

    # --- boucle principale -----------------------------------------------------

    def run(self) -> int:
        header = Table.grid(padding=(0, 2))
        header.add_row(Text(f"Cerveau v{__version__}", style="bold"),
                       Text(f"modèle {self.cfg.model}", style="cyan"),
                       Text(f"projet {self.agent.project}", style="green"),
                       Text(str(self.root), style="dim"))
        self.out.print(Panel(header, border_style="blue"))
        self.out.print("[dim]/help pour les commandes, /quit pour sortir.[/]\n")

        while True:
            try:
                line = self.out.input("[bold cyan]vous ›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                self.out.print()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                if not self._command(line):
                    return 0
                continue

            self.out.print()
            self.waiting = True
            try:
                with self.out.status("[dim]réflexion…[/]", spinner="dots"):
                    # Le status s'efface dès le premier token ou le premier outil.
                    self.agent.ask(line)
            except KeyboardInterrupt:
                self._stop_live()
                self.out.print("\n  [yellow]interrompu[/]")
                continue
            except LLMError as exc:
                self._stop_live()
                self.out.print(f"\n  [red]✗ {exc}[/]")
                continue
            finally:
                self.waiting = False
            self._stop_live()
            self._print_stats(self.agent.last_stats)
            self.out.print()


def _args_summary(args: dict) -> str:
    parts = []
    for key, value in args.items():
        if key == "content":
            parts.append(f"{len(str(value))} caractères")
        else:
            text = str(value)
            parts.append(text if len(text) <= 70 else text[:70] + "…")
    return "  ".join(parts)


def run(cfg: config.Config, root: Path) -> int:
    return ChatConsole(cfg, root).run()
