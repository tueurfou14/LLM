"""Site public : pages, actualités, images envoyées (/medias), sitemap.xml, robots.txt, /sante.

Ajouter une page : créer app/templates/site/ma-page.html (sur le modèle de services.html : titre, description,
un seul <h1>), puis l'ajouter à PAGES ci-dessous. Le menu, sitemap.xml et les tests la prennent en compte.

Variables disponibles dans tous les gabarits du site :
  site       paramètres du site (site.nom, site.telephone, site.couleur_primaire…)
  textes     textes des pages (textes.accueil_titre…)
  menu       pages du menu ; url_base ; annee ; aujourdhui
  <module>   objets affichables de chaque module de app/modules (services, realisations, actualites…) :
             seulement ceux dont la case « publie » est cochée et dont la date « fin » n'est pas passée.
Fonctions : media(image, "illustration.svg") donne l'adresse d'une image envoyée, ou de l'illustration par
défaut de app/static/site/img/ ; lien(objet) donne l'adresse publique d'un objet (ex. une actualité).
"""

import re
import urllib.parse
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape as echapper
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import medias
from app.core.database import SessionLocal, get_db
from app.core.framework import REGISTRE
from app.core.web import ADMIN, APP_DIR
from app.site import contenu


@dataclass
class Page:
    adresse: str        # ex. "/services"
    gabarit: str        # fichier de app/templates/site/
    titre: str          # libellé dans le menu
    menu: bool = True


PAGES = [
    Page("/", "index.html", "Accueil"),
    Page("/services", "services.html", "Services"),
    Page("/realisations", "realisations.html", "Réalisations"),
    Page("/actualites", "actualites.html", "Actualités"),
    Page("/a-propos", "a-propos.html", "À propos"),
    Page("/contact", "contact.html", "Contact"),
    Page("/mentions-legales", "mentions-legales.html", "Mentions légales", menu=False),
]

MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre")

# StrictUndefined : une variable mal écrite dans un gabarit provoque une erreur claire au lieu d'un vide.
templates = Jinja2Templates(env=Environment(
    loader=FileSystemLoader(APP_DIR / "templates" / "site", encoding="utf-8"),
    autoescape=select_autoescape(["html", "xml"]), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True))
router = APIRouter()


# --- filtres et fonctions des gabarits -------------------------------------------------------

def filtre_tel(numero) -> str:
    """« 01 23 45 67 89 » → « 0123456789 » pour un lien tel:."""
    return re.sub(r"[^\d+]", "", str(numero or ""))


def filtre_carte(adresse) -> str:
    """Lien de recherche OpenStreetMap pour une adresse."""
    return "https://www.openstreetmap.org/search?query=" + urllib.parse.quote(" ".join(str(adresse or "").split()))


def filtre_paragraphes(texte) -> Markup:
    """Texte saisi → paragraphes <p> (ligne vide = nouveau paragraphe, retour à la ligne = <br>)."""
    blocs = [b.strip() for b in re.split(r"\n\s*\n", str(texte or "").replace("\r\n", "\n")) if b.strip()]
    paragraphes = [Markup("<p>{}</p>").format(Markup("<br>").join(echapper(l) for l in bloc.split("\n")))
                   for bloc in blocs]
    return Markup("\n").join(paragraphes)


def filtre_lignes(texte) -> list[str]:
    """Texte saisi → liste de lignes non vides (horaires, chiffres clés…)."""
    return [l.strip() for l in str(texte or "").splitlines() if l.strip()]


def filtre_paires(texte) -> list[tuple[str, str]]:
    """Lignes « valeur | libellé » → [(valeur, libellé), ...]."""
    return [(a.strip(), b.strip()) for a, _, b in (l.partition("|") for l in filtre_lignes(texte))]


def filtre_date_longue(valeur) -> str:
    return f"{valeur.day} {MOIS[valeur.month - 1]} {valeur.year}" if isinstance(valeur, date) else ""


def media(nom, defaut: str) -> str:
    """Adresse d'une image envoyée depuis l'administration, sinon de l'illustration app/static/site/img/<defaut>."""
    return f"/medias/{nom}" if nom and medias.NOM_SUR.fullmatch(str(nom)) else f"/static/site/img/{defaut}"


def lien(objet) -> str:
    """Adresse publique d'un objet dont le module déclare public=..., ex. une actualité."""
    module = REGISTRE[objet.__tablename__]
    return module.public(objet) if module.public else "/"


def carte_osm(latitude, longitude) -> str:
    """Adresse de la carte OpenStreetMap à intégrer, ou "" si les coordonnées sont absentes ou fausses."""
    try:
        lat, lon = float(str(latitude).replace(",", ".")), float(str(longitude).replace(",", "."))
    except ValueError:
        return ""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return ""
    return ("https://www.openstreetmap.org/export/embed.html?bbox="
            f"{lon - 0.012:.5f},{lat - 0.006:.5f},{lon + 0.012:.5f},{lat + 0.006:.5f}&layer=mapnik&marker={lat:.5f},{lon:.5f}")


templates.env.filters.update(tel=filtre_tel, carte=filtre_carte, paragraphes=filtre_paragraphes,
                             lignes=filtre_lignes, paires=filtre_paires, date_longue=filtre_date_longue)
templates.env.globals.update(media=media, lien=lien, carte_osm=carte_osm)


# --- données et rendu -------------------------------------------------------------------------

def publies(module, db: Session) -> list:
    """Objets affichables d'un module : case « publie » cochée et date « fin » non passée (si ces champs existent)."""
    requete = select(module.modele)
    types = {c.nom: c.type for c in module.champs}
    if types.get("publie") == "booleen":
        requete = requete.where(module.modele.publie.is_(True))
    if types.get("fin") == "date":
        requete = requete.where(or_(module.modele.fin.is_(None), module.modele.fin >= date.today()))
    return list(db.scalars(requete.order_by(module.ordre_sql(), module.modele.id.desc())))


def url_base(request: Request, site: dict) -> str:
    return (site.get("url_site") or str(request.base_url)).rstrip("/")


def rendre(request: Request, db: Session, gabarit: str, contexte: dict | None = None, statut: int = 200):
    site = contenu.valeurs(db, "parametres")
    donnees = {
        "site": site, "textes": contenu.valeurs(db, "textes"), "menu": [p for p in PAGES if p.menu],
        "url_base": url_base(request, site), "annee": date.today().year, "aujourdhui": date.today(),
        **{nom: publies(module, db) for nom, module in REGISTRE.items()},
        **(contexte or {}),
    }
    return templates.TemplateResponse(request, gabarit, donnees, status_code=statut)


def page_introuvable(request: Request):
    with SessionLocal() as db:
        return rendre(request, db, "404.html", statut=404)


# --- routes -----------------------------------------------------------------------------------

def _vue(page: Page):
    def vue(request: Request, db: Session = Depends(get_db)):
        return rendre(request, db, page.gabarit)
    return vue


for _page in PAGES:
    router.add_api_route(_page.adresse, _vue(_page), methods=["GET"], include_in_schema=False,
                         name=f"page:{_page.adresse}")


@router.get("/actualites/{adresse}")
def actualite(request: Request, adresse: str, db: Session = Depends(get_db)):
    module = REGISTRE["actualites"]
    ident = adresse.split("-", 1)[0]
    article = db.get(module.modele, int(ident)) if ident.isdigit() else None
    if article is None or not article.publie:
        raise HTTPException(status_code=404)
    canonique = lien(article)
    if request.url.path != canonique:
        return RedirectResponse(canonique, status_code=301)
    return rendre(request, db, "actualite.html", {"article": article})


@router.get("/medias/{nom}")
def image(nom: str):
    fichier = medias.chemin(nom)
    if fichier is None:
        raise HTTPException(status_code=404)
    return FileResponse(fichier, media_type=medias.type_mime(nom),
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_db)):
    base = url_base(request, contenu.valeurs(db, "parametres"))
    adresses = [p.adresse for p in PAGES]
    for module in REGISTRE.values():
        if module.public:
            adresses += [module.public(objet) for objet in publies(module, db)]
    lignes = [f"  <url><loc>{escape(base + adresse)}</loc></url>" for adresse in adresses]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(lignes) + "\n</urlset>\n")
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt")
def robots(request: Request, db: Session = Depends(get_db)):
    base = url_base(request, contenu.valeurs(db, "parametres"))
    return PlainTextResponse(f"User-agent: *\nDisallow: {ADMIN}/\nSitemap: {base}/sitemap.xml\n")


@router.get("/sante")
def sante():
    return {"ok": True}
