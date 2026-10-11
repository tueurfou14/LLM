"""Générateur du site vitrine.

    python site.py build              rend pages/ dans dist/ (avec static/, sitemap.xml, robots.txt)
    python site.py serve --port 8000  reconstruit puis sert dist/ ; reconstruit à chaque
                                      rechargement de page si une source a changé

Le contenu est dans contenu/site.json, les gabarits Jinja dans pages/
(les fichiers qui commencent par « _ » ne produisent pas de page).
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import shutil
import sys
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    TemplateSyntaxError,
    select_autoescape,
)

RACINE = Path(__file__).resolve().parent
DOSSIERS_SOURCES = ("contenu", "pages", "static")


class ErreurSite(Exception):
    """Erreur de construction, avec un message en français qui dit quoi corriger."""


# --- Contenu -----------------------------------------------------------------

def charger_contenu(racine: Path = RACINE) -> dict:
    chemin = racine / "contenu" / "site.json"
    try:
        texte = chemin.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ErreurSite("fichier manquant : contenu/site.json") from None
    try:
        contenu = json.loads(texte)
    except json.JSONDecodeError as e:
        raise ErreurSite(
            f"contenu/site.json n'est pas un JSON valide (ligne {e.lineno}, colonne {e.colno}) : {e.msg}. "
            "Vérifie les virgules, les guillemets doubles et les accolades."
        ) from None
    if not isinstance(contenu, dict):
        raise ErreurSite("contenu/site.json doit contenir un objet JSON { ... }")
    return contenu


def pages_sources(racine: Path = RACINE) -> list[Path]:
    """Gabarits qui produisent une page (pages/*.html sauf ceux qui commencent par « _ »)."""
    return sorted(p for p in (racine / "pages").glob("*.html") if not p.name.startswith("_"))


# --- Filtres Jinja -----------------------------------------------------------

def filtre_tel(numero) -> str:
    """« 01 23 45 67 89 » -> « 0123456789 » pour un lien tel:."""
    return re.sub(r"[^\d+]", "", str(numero))


def filtre_carte(adresse) -> str:
    """Lien de recherche OpenStreetMap pour une adresse."""
    return "https://www.openstreetmap.org/search?query=" + urllib.parse.quote(str(adresse))


def environnement(racine: Path = RACINE) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(racine / "pages"), encoding="utf-8"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["tel"] = filtre_tel
    env.filters["carte"] = filtre_carte
    return env


def _position_gabarit(exc: BaseException) -> str:
    """Retrouve « fichier.html ligne N » dans la pile d'une erreur de rendu."""
    trouve = ""
    tb = exc.__traceback__
    while tb is not None:
        nom = tb.tb_frame.f_code.co_filename
        if nom.endswith(".html"):
            trouve = f"pages/{Path(nom).name} ligne {tb.tb_lineno}"
        tb = tb.tb_next
    return trouve


# --- Construction ------------------------------------------------------------

def _ecrire(chemin: Path, donnees: bytes, produits: set[Path]) -> None:
    """Écrit seulement si le contenu a changé (évite les verrous de fichiers sous Windows)."""
    produits.add(chemin.resolve())
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if chemin.is_file() and chemin.read_bytes() == donnees:
        return
    chemin.write_bytes(donnees)


def _copier(source: Path, cible: Path, produits: set[Path]) -> None:
    produits.add(cible.resolve())
    cible.parent.mkdir(parents=True, exist_ok=True)
    if cible.is_file():
        s, c = source.stat(), cible.stat()
        if s.st_size == c.st_size and int(s.st_mtime) == int(c.st_mtime):
            return
    shutil.copy2(source, cible)


def _nettoyer(sortie: Path, produits: set[Path]) -> None:
    """Supprime de dist/ ce qui ne vient plus des sources (page renommée, image retirée...)."""
    for chemin in sorted(sortie.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if chemin.is_file() and chemin.resolve() not in produits:
            chemin.unlink()
        elif chemin.is_dir() and not any(chemin.iterdir()):
            chemin.rmdir()


def _sitemap(url_site: str, pages: list[str]) -> str:
    base = url_site.rstrip("/") + "/"
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for page in pages:
        adresse = base if page == "index.html" else base + page
        lignes.append(f"  <url><loc>{html.escape(adresse)}</loc></url>")
    lignes.append("</urlset>")
    return "\n".join(lignes) + "\n"


def construire(racine: Path = RACINE, sortie: Path | None = None) -> list[str]:
    """Construit le site dans `sortie` (dist/ par défaut). Renvoie la liste des pages produites."""
    racine = Path(racine).resolve()
    sortie = Path(sortie or racine / "dist").resolve()
    if sortie == racine or sortie in racine.parents:
        raise ErreurSite(f"dossier de sortie dangereux : {sortie}")

    site = charger_contenu(racine)
    env = environnement(racine)
    produits: set[Path] = set()
    sortie.mkdir(parents=True, exist_ok=True)
    annee = datetime.date.today().year

    sources = pages_sources(racine)
    if not sources:
        raise ErreurSite("aucune page dans pages/ (il faut au moins pages/index.html)")
    pages: list[str] = []
    for source in sources:
        try:
            gabarit = env.get_template(source.name)
            rendu = gabarit.render(site=site, page=source.name, annee=annee)
        except TemplateSyntaxError as e:
            fichier = Path(e.filename or e.name or source.name).name
            raise ErreurSite(f"pages/{fichier} ligne {e.lineno} : erreur de syntaxe Jinja : {e.message}") from None
        except TemplateNotFound as e:
            raise ErreurSite(
                f"pages/{source.name} : gabarit introuvable « {e.name} » (vérifie le extends ou include)"
            ) from None
        except Exception as e:  # noqa: BLE001 - message clair pour le modèle
            ou = _position_gabarit(e) or f"pages/{source.name}"
            raise ErreurSite(
                f"{ou} : erreur au rendu de pages/{source.name} : {type(e).__name__}: {e}. "
                "Vérifie que les clés utilisées existent dans contenu/site.json."
            ) from None
        _ecrire(sortie / source.name, rendu.encode("utf-8"), produits)
        pages.append(source.name)

    statique = racine / "static"
    if statique.is_dir():
        for fichier in statique.rglob("*"):
            if fichier.is_file():
                _copier(fichier, sortie / "static" / fichier.relative_to(statique), produits)

    url_site = str(site.get("url_site") or "").strip()
    if url_site:
        _ecrire(sortie / "sitemap.xml", _sitemap(url_site, pages).encode("utf-8"), produits)
        robots = f"User-agent: *\nAllow: /\nSitemap: {url_site.rstrip('/')}/sitemap.xml\n"
        _ecrire(sortie / "robots.txt", robots.encode("utf-8"), produits)

    _nettoyer(sortie, produits)
    return pages


# --- Serveur de développement --------------------------------------------------

TYPES_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def signature(racine: Path = RACINE) -> tuple:
    """Empreinte des sources : change dès qu'un fichier est modifié, ajouté ou supprimé."""
    elements = []
    for dossier in DOSSIERS_SOURCES:
        base = racine / dossier
        if base.is_dir():
            for p in base.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    elements.append((str(p), st.st_mtime_ns, st.st_size))
    return tuple(sorted(elements))


def page_erreur(message: str) -> bytes:
    return (
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\"><title>Erreur de construction</title></head>"
        "<body style=\"font-family:sans-serif;padding:2rem;max-width:60rem\">"
        "<h1>Le site ne se construit pas</h1>"
        f"<pre style=\"white-space:pre-wrap;background:#fee;padding:1rem\">{html.escape(message)}</pre>"
        "<p>Corrige le fichier indiqué puis recharge la page.</p></body></html>"
    ).encode("utf-8")


def servir(racine: Path = RACINE, port: int = 8000, hote: str = "127.0.0.1") -> None:
    sortie = racine / "dist"
    etat = {"signature": None, "erreur": None}
    verrou = threading.Lock()

    def actualiser() -> None:
        sig = signature(racine)
        if sig == etat["signature"]:
            return
        try:
            pages = construire(racine, sortie)
            etat["erreur"] = None
            print(f"Site construit : {len(pages)} page(s) dans dist/", flush=True)
        except ErreurSite as e:
            etat["erreur"] = str(e)
            print(f"ERREUR : {e}", file=sys.stderr, flush=True)
        etat["signature"] = sig

    class Gestionnaire(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **TYPES_MIME}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(sortie), **kwargs)

        def _est_page(self) -> bool:
            chemin = urllib.parse.urlsplit(self.path).path
            dernier = chemin.rsplit("/", 1)[-1]
            return chemin.endswith("/") or chemin.endswith(".html") or "." not in dernier

        def _repondre(self, envoyer_corps: bool) -> None:
            with verrou:
                if self._est_page():
                    actualiser()
                    if etat["erreur"]:
                        corps = page_erreur(etat["erreur"])
                        self.send_response(500)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(corps)))
                        self.end_headers()
                        if envoyer_corps:
                            self.wfile.write(corps)
                        return
                if envoyer_corps:
                    super().do_GET()
                else:
                    super().do_HEAD()

        def do_GET(self):
            self._repondre(True)

        def do_HEAD(self):
            self._repondre(False)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    with verrou:
        actualiser()
    try:
        serveur = ThreadingHTTPServer((hote, port), Gestionnaire)
    except OSError as e:
        print(f"ERREUR : impossible d'écouter sur {hote}:{port} ({e}). Port déjà utilisé ?",
              file=sys.stderr, flush=True)
        sys.exit(1)
    serveur.daemon_threads = True
    print(f"Site disponible sur http://{hote}:{port}/  (Ctrl+C pour arrêter)", flush=True)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()


# --- Ligne de commande ---------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Site vitrine : construction et serveur local")
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("build", help="construit le site dans dist/")
    p_serve = sous.add_parser("serve", help="construit puis sert dist/ en local")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    if args.commande == "build":
        try:
            pages = construire(RACINE)
        except ErreurSite as e:
            print(f"ERREUR : {e}", file=sys.stderr)
            return 1
        print(f"Site construit : {len(pages)} page(s) dans dist/ ({', '.join(pages)})")
        return 0
    servir(RACINE, args.port, args.host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
