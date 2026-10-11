"""Vérifie contenu/site.json : JSON valide, clés obligatoires, images et liens existants."""
from __future__ import annotations

import json
import re

import pytest

from conftest import RACINE

FICHIER = "contenu/site.json"


@pytest.fixture(scope="module")
def site():
    texte = (RACINE / "contenu" / "site.json").read_text(encoding="utf-8")
    try:
        return json.loads(texte)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"{FICHIER} n'est pas un JSON valide : ligne {e.lineno}, colonne {e.colno} : {e.msg}. "
            "Vérifie les virgules (pas de virgule après le dernier élément), les guillemets doubles et les accolades.",
            pytrace=False,
        )


def _texte_non_vide(valeur) -> bool:
    return isinstance(valeur, str) and valeur.strip() != ""


def test_cles_principales(site):
    for cle in ("nom", "slogan", "description"):
        assert _texte_non_vide(site.get(cle)), f"{FICHIER} : la clé « {cle} » doit être un texte non vide"
    for cle in ("couleurs", "coordonnees", "accueil", "a_propos", "bandeau_contact", "mentions"):
        assert isinstance(site.get(cle), dict), f"{FICHIER} : la clé « {cle} » doit exister et être un objet {{...}}"
    for cle in ("menu", "services", "realisations"):
        assert isinstance(site.get(cle), list) and site[cle], f"{FICHIER} : la clé « {cle} » doit être une liste non vide [...]"
    url = site.get("url_site", "")
    assert url == "" or re.match(r"^https?://", url), (
        f"{FICHIER} : « url_site » doit être vide ou commencer par https:// (valeur actuelle : {url!r})"
    )


def test_coordonnees(site):
    c = site["coordonnees"]
    for cle in ("adresse", "telephone", "email"):
        assert _texte_non_vide(c.get(cle)), f"{FICHIER} : « coordonnees.{cle} » doit être un texte non vide"
    assert "@" in c["email"], f"{FICHIER} : « coordonnees.email » n'est pas une adresse e-mail ({c['email']!r})"
    assert isinstance(c.get("horaires"), list), (
        f"{FICHIER} : « coordonnees.horaires » doit être une liste de lignes, ex. [\"Lundi – vendredi : 8 h – 18 h\"]"
    )


def test_couleurs(site):
    for cle in ("primaire", "accent", "fond", "texte"):
        valeur = site["couleurs"].get(cle)
        assert isinstance(valeur, str) and re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", valeur), (
            f"{FICHIER} : « couleurs.{cle} » doit être une couleur hexadécimale comme \"#1f3d36\" (valeur actuelle : {valeur!r})"
        )


def test_textes_de_l_accueil_et_du_bandeau(site):
    for cle in ("accroche", "intro", "image", "image_alt", "bouton"):
        assert _texte_non_vide(site["accueil"].get(cle)), f"{FICHIER} : « accueil.{cle} » doit être un texte non vide"
    for cle in ("titre", "texte", "bouton"):
        assert _texte_non_vide(site["bandeau_contact"].get(cle)), f"{FICHIER} : « bandeau_contact.{cle} » doit être un texte non vide"
    assert _texte_non_vide(site["a_propos"].get("titre")), f"{FICHIER} : « a_propos.titre » doit être un texte non vide"
    assert isinstance(site["a_propos"].get("paragraphes"), list), f"{FICHIER} : « a_propos.paragraphes » doit être une liste de textes"


def test_menu(site):
    for i, item in enumerate(site["menu"]):
        assert isinstance(item, dict) and _texte_non_vide(item.get("titre")) and _texte_non_vide(item.get("lien")), (
            f"{FICHIER} : menu[{i}] doit être un objet {{\"titre\": \"...\", \"lien\": \"page.html\"}}"
        )
        lien = item["lien"]
        if lien.startswith(("http://", "https://")):
            continue
        assert (RACINE / "pages" / lien).is_file(), (
            f"{FICHIER} : menu[{i}] pointe vers « {lien} » mais le fichier pages/{lien} n'existe pas. "
            "Crée la page dans pages/ ou corrige le lien."
        )


def test_services(site):
    for i, service in enumerate(site["services"]):
        for cle in ("titre", "texte"):
            assert isinstance(service, dict) and _texte_non_vide(service.get(cle)), (
                f"{FICHIER} : services[{i}] doit avoir un « {cle} » non vide"
            )


def test_realisations(site):
    for i, realisation in enumerate(site["realisations"]):
        for cle in ("titre", "image", "legende"):
            assert isinstance(realisation, dict) and _texte_non_vide(realisation.get(cle)), (
                f"{FICHIER} : realisations[{i}] doit avoir un « {cle} » non vide"
            )


def test_images_existent(site):
    images = [("accueil.image", site["accueil"].get("image")), ("a_propos.image", site["a_propos"].get("image"))]
    images += [(f"realisations[{i}].image", r.get("image")) for i, r in enumerate(site["realisations"])]
    for cle, chemin in images:
        if not chemin or chemin.startswith(("http://", "https://")):
            continue
        assert (RACINE / chemin).is_file(), (
            f"{FICHIER} : « {cle} » vaut « {chemin} » mais ce fichier n'existe pas. "
            "Dépose l'image dans static/img/ et écris le chemin sous la forme \"static/img/nom.jpg\"."
        )
