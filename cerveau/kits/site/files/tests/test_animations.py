"""Règles du client sur les animations : jamais d'épinglage, mouvement réduit respecté,
site lisible sans JavaScript."""
from __future__ import annotations

import re

from conftest import RACINE

# Toute option d'épinglage ScrollTrigger : pin:, pinSpacing:, "pin": ...
EPINGLAGE = re.compile(r"""\bpin\w*["']?\s*:""")


def test_aucun_epinglage_dans_les_scripts():
    fichiers = list((RACINE / "static").rglob("*.js")) + list((RACINE / "pages").glob("*.html"))
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        trouve = EPINGLAGE.search(texte)
        assert not trouve, (
            f"{fichier.relative_to(RACINE).as_posix()} : option d'épinglage interdite « {trouve.group(0)} ». "
            "Le client refuse toute section épinglée qui bloque le défilement : supprime pin/pinSpacing "
            "et laisse les animations se déclencher seules à l'arrivée à l'écran."
        )


def test_mouvement_reduit_respecte():
    js = (RACINE / "static" / "js" / "main.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in js, (
        "static/js/main.js : il faut tester prefers-reduced-motion et ne rien animer dans ce cas"
    )
    css = (RACINE / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css, "static/css/style.css : il manque le bloc @media (prefers-reduced-motion: reduce)"


def test_gsap_en_version_exacte():
    base = (RACINE / "pages" / "_base.html").read_text(encoding="utf-8")
    sources = re.findall(r"""<script[^>]+src=["']([^"']*gsap[^"']*)["']""", base)
    for src in sources:
        assert re.search(r"/\d+\.\d+\.\d+/", src), (
            f"pages/_base.html : le script « {src} » doit viser une version exacte de GSAP (ex. .../gsap/3.12.5/gsap.min.js)"
        )


def test_contenu_visible_sans_javascript():
    """Les éléments animés ne doivent être masqués en CSS que sous la classe .js."""
    css = (RACINE / "static" / "css" / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # sans les commentaires
    for selecteurs, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "data-anim" not in selecteurs:
            continue
        masque = re.search(r"opacity\s*:\s*0(\.0*)?\s*(;|$)", corps) or re.search(r"visibility\s*:\s*hidden", corps)
        if not masque:
            continue
        for selecteur in selecteurs.split(","):
            if "data-anim" in selecteur:
                assert selecteur.strip().startswith(".js "), (
                    f"static/css/style.css : « {selecteur.strip()} » masque du contenu même sans JavaScript. "
                    "Préfixe le sélecteur par « .js » pour que le site reste lisible si le JS ne charge pas."
                )
