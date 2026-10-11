"""Construit le site et vérifie chaque page produite dans dist/."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from conftest import PAGES, RACINE, analyser, charger_generateur

EXTERNES = ("http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:", "#")


def _cible_interne(dist: Path, page: Path, lien: str) -> Path | None:
    """Fichier visé par un lien interne, ou None si le lien est externe."""
    if lien.startswith(EXTERNES) or lien.strip() == "":
        return None
    chemin = unquote(urlsplit(lien).path)
    if not chemin:
        return None
    cible = dist / chemin.lstrip("/") if chemin.startswith("/") else page.parent / chemin
    if chemin.endswith("/"):
        cible = cible / "index.html"
    return cible


def test_il_y_a_des_pages():
    assert "index.html" in PAGES, "pages/index.html est obligatoire (page d'accueil)"


@pytest.mark.parametrize("nom", PAGES)
def test_page_produite(dist, nom):
    assert (dist / nom).is_file(), f"pages/{nom} n'a pas produit dist/{nom}"


@pytest.mark.parametrize("nom", PAGES)
def test_titre_et_description(dist, nom):
    page = analyser(dist / nom)
    assert page.lang == "fr", f"pages/{nom} : la balise <html> doit avoir lang=\"fr\" (vient de pages/_base.html)"
    assert page.titre.strip(), (
        f"pages/{nom} : le <title> est vide. Ajoute {{% set titre = \"...\" %}} en haut de pages/{nom}."
    )
    assert page.description and page.description.strip(), (
        f"pages/{nom} : la meta description est vide. Ajoute {{% set description = \"...\" %}} en haut de pages/{nom}."
    )


@pytest.mark.parametrize("nom", PAGES)
def test_un_seul_h1(dist, nom):
    page = analyser(dist / nom)
    assert page.nb_h1 == 1, (
        f"pages/{nom} : il faut exactement un <h1> par page, il y en a {page.nb_h1}. "
        "Garde un seul <h1> (le titre principal) et utilise <h2>/<h3> pour les autres titres."
    )


@pytest.mark.parametrize("nom", PAGES)
def test_images_ont_un_alt(dist, nom):
    page = analyser(dist / nom)
    for img in page.images:
        assert img.get("alt") is not None and (img.get("alt") or "").strip(), (
            f"pages/{nom} : l'image « {img.get('src')} » n'a pas de texte alternatif (attribut alt). "
            "Ajoute alt=\"description de l'image\" (ou renseigne le champ correspondant dans contenu/site.json)."
        )


@pytest.mark.parametrize("nom", PAGES)
def test_liens_internes_existent(dist, nom):
    fichier = dist / nom
    page = analyser(fichier)
    for balise, attribut, lien in page.liens:
        cible = _cible_interne(dist, fichier, lien)
        if cible is None:
            continue
        assert cible.is_file(), (
            f"pages/{nom} : <{balise} {attribut}=\"{lien}\"> pointe vers un fichier inexistant. "
            "Corrige le lien (pages/*.html, contenu/site.json) ou ajoute le fichier dans static/ ou pages/."
        )


def test_css_references_existent(dist):
    for css in (dist / "static").rglob("*.css"):
        texte = css.read_text(encoding="utf-8")
        for lien in re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", texte):
            cible = _cible_interne(dist, css, lien)
            if cible is None:
                continue
            assert cible.is_file(), f"{css.relative_to(dist)} : url({lien}) pointe vers un fichier inexistant"


def test_sitemap_et_robots(tmp_path):
    """Avec url_site renseignée, la construction produit sitemap.xml et robots.txt."""
    copie = tmp_path / "projet"
    for nom in ("contenu", "pages", "static"):
        shutil.copytree(RACINE / nom, copie / nom)
    shutil.copy2(RACINE / "site.py", copie / "site.py")
    chemin = copie / "contenu" / "site.json"
    site = json.loads(chemin.read_text(encoding="utf-8"))
    site["url_site"] = "https://www.exemple.fr"
    chemin.write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")

    generateur = charger_generateur(copie)
    generateur.construire(copie, copie / "dist")
    sitemap = (copie / "dist" / "sitemap.xml").read_text(encoding="utf-8")
    for nom in PAGES:
        attendu = "https://www.exemple.fr/" + ("" if nom == "index.html" else nom)
        assert f"<loc>{attendu}</loc>" in sitemap, f"site.py : sitemap.xml ne contient pas {attendu}"
    robots = (copie / "dist" / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://www.exemple.fr/sitemap.xml" in robots, "site.py : robots.txt doit indiquer le sitemap"
