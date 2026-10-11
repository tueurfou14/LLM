"""Outils communs aux tests : chargement de site.py, construction dans un dossier temporaire,
analyse des pages HTML produites."""
from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

# Pages à produire : pages/*.html sauf les fichiers qui commencent par « _ ».
PAGES = sorted(p.name for p in (RACINE / "pages").glob("*.html") if not p.name.startswith("_"))


def charger_generateur(racine: Path = RACINE):
    """Importe site.py sous un autre nom (« site » est déjà un module de Python)."""
    spec = importlib.util.spec_from_file_location("generateur_site", racine / "site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnalyseurHTML(HTMLParser):
    """Relève ce que les tests vérifient : titre, description, h1, images, liens."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.titre = ""
        self.description = None
        self.nb_h1 = 0
        self.images = []  # attributs de chaque <img>
        self.liens = []  # (balise, attribut, valeur)
        self._dans_titre = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._dans_titre = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.description = a.get("content") or ""
        elif tag == "h1":
            self.nb_h1 += 1
        if tag == "img":
            self.images.append(a)
        for attribut in ("href", "src"):
            if a.get(attribut) is not None:
                self.liens.append((tag, attribut, a[attribut]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._dans_titre = False

    def handle_data(self, data):
        if self._dans_titre:
            self.titre += data


def analyser(chemin: Path) -> AnalyseurHTML:
    analyseur = AnalyseurHTML()
    analyseur.feed(chemin.read_text(encoding="utf-8"))
    return analyseur


@pytest.fixture(scope="session")
def generateur():
    return charger_generateur()


@pytest.fixture(scope="session")
def dist(generateur, tmp_path_factory):
    """Construit le site une fois pour tous les tests, dans un dossier temporaire."""
    sortie = tmp_path_factory.mktemp("dist")
    try:
        generateur.construire(RACINE, sortie)
    except generateur.ErreurSite as e:
        pytest.fail(f"La construction du site échoue : {e}", pytrace=False)
    return sortie
