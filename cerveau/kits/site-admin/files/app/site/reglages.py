"""Administration du site : « Textes des pages » (/admin/textes) et « Paramètres du site » (/admin/parametres).

Les champs affichés viennent de contenu/site.json (« textes » et « parametres ») : ajouter un réglage dans ce
fichier le fait apparaître ici, regroupé par « groupe ».
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core import medias
from app.core.database import get_db
from app.core.modeles import Utilisateur, journaliser
from app.core.web import ADMIN, exiger_admin, flash, rendre, utilisateur_connecte
from app.site import contenu

router = APIRouter(prefix=ADMIN)

PAGES = {
    "textes": ("Textes des pages", "Les titres et paragraphes affichés sur chaque page du site."),
    "parametres": ("Paramètres du site", "Nom, coordonnées, horaires, couleurs, réseaux sociaux et mentions légales."),
}


def _groupes(section: str) -> list[tuple[str, list[dict]]]:
    groupes: dict[str, list[dict]] = {}
    for reglage in contenu.reglages(section):
        groupes.setdefault(reglage["groupe"], []).append(reglage)
    return list(groupes.items())


def _page(request, db, utilisateur, section, saisies=None, erreurs=None, statut=200):
    titre, intro = PAGES[section]
    return rendre(request, db, "socle/reglages.html", {
        "section": section, "titre": titre, "intro": intro, "groupes": _groupes(section),
        "valeurs": contenu.valeurs(db, section), "saisies": saisies or {}, "erreurs": erreurs or {},
    }, utilisateur, statut)


async def _enregistrer(request: Request, db: Session, utilisateur: Utilisateur, section: str):
    formulaire = await request.form()
    erreurs, anciennes = contenu.enregistrer(db, section, formulaire)
    if erreurs:
        db.rollback()
        saisies = {cle: v for cle, v in formulaire.items() if isinstance(v, str)}
        return _page(request, db, utilisateur, section, saisies, erreurs, 422)
    journaliser(db, utilisateur, "modification", section, None, PAGES[section][0])
    db.commit()
    medias.supprimer(anciennes)
    flash(request, f"{PAGES[section][0]} enregistrés.")
    return RedirectResponse(f"{ADMIN}/{section}", status_code=303)


@router.get("/textes")
def textes(request: Request, db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(utilisateur_connecte)):
    return _page(request, db, utilisateur, "textes")


@router.post("/textes")
async def textes_envoi(request: Request, db: Session = Depends(get_db),
                       utilisateur: Utilisateur = Depends(utilisateur_connecte)):
    return await _enregistrer(request, db, utilisateur, "textes")


@router.get("/parametres")
def parametres(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    return _page(request, db, admin, "parametres")


@router.post("/parametres")
async def parametres_envoi(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    return await _enregistrer(request, db, admin, "parametres")
