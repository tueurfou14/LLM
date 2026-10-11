"""Règles du client sur les animations : jamais d'épinglage, mouvement réduit respecté,
site lisible sans JavaScript, GSAP en version exacte."""

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
STATIQUE = RACINE / "app" / "static" / "site"
GABARITS = RACINE / "app" / "templates" / "site"

# Toute option d'épinglage ScrollTrigger : pin:, pinSpacing:, "pin": ...
EPINGLAGE = re.compile(r"""\bpin\w*["']?\s*:""")


def test_aucun_epinglage_dans_les_scripts():
    fichiers = list((RACINE / "app" / "static").rglob("*.js")) + list(GABARITS.glob("*.html"))
    for fichier in fichiers:
        texte = fichier.read_text(encoding="utf-8")
        trouve = EPINGLAGE.search(texte)
        assert not trouve, (
            f"{fichier.relative_to(RACINE).as_posix()} : option d'épinglage interdite « {trouve.group(0)} ». "
            "Le client refuse toute section épinglée qui bloque le défilement : supprime pin/pinSpacing "
            "et laisse les animations se déclencher seules à l'arrivée à l'écran.")


def test_mouvement_reduit_respecte():
    js = (STATIQUE / "js" / "main.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in js, "app/static/site/js/main.js : il faut tester prefers-reduced-motion"
    css = (STATIQUE / "css" / "style.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css, (
        "app/static/site/css/style.css : il manque le bloc @media (prefers-reduced-motion: reduce)")


def test_gsap_en_version_exacte():
    base = (GABARITS / "_base.html").read_text(encoding="utf-8")
    sources = re.findall(r"""<script[^>]+src=["']([^"']*gsap[^"']*)["']""", base)
    assert sources, "app/templates/site/_base.html : GSAP n'est plus chargé"
    for src in sources:
        assert re.search(r"/\d+\.\d+\.\d+/", src), (
            f"app/templates/site/_base.html : « {src} » doit viser une version exacte de GSAP (ex. .../gsap/3.12.5/gsap.min.js)")


def test_contenu_visible_sans_javascript():
    """Les éléments animés ne sont masqués en CSS que sous la classe .js, posée par le script en tête de page."""
    base = (GABARITS / "_base.html").read_text(encoding="utf-8")
    assert 'classList.add("js")' in base, "app/templates/site/_base.html : le script qui pose la classe js a disparu"
    css = re.sub(r"/\*.*?\*/", "", (STATIQUE / "css" / "style.css").read_text(encoding="utf-8"), flags=re.S)
    for selecteurs, corps in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "data-anim" not in selecteurs:
            continue
        if not (re.search(r"opacity\s*:\s*0(\.0*)?\s*(;|$)", corps) or re.search(r"visibility\s*:\s*hidden", corps)):
            continue
        for selecteur in selecteurs.split(","):
            if "data-anim" in selecteur:
                assert selecteur.strip().startswith(".js "), (
                    f"app/static/site/css/style.css : « {selecteur.strip()} » masque du contenu même sans JavaScript. "
                    "Préfixe le sélecteur par « .js ».")
