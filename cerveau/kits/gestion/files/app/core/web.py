"""Pages HTML : moteur de templates, formats français, utilisateur connecté, messages."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import config
from app.core.database import DATA, get_db
from app.core.modeles import Utilisateur, lire_parametres

APP_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


class NonConnecte(Exception):
    """Levée quand une page demande un utilisateur connecté : redirection vers /connexion."""


class Interdit(Exception):
    """Levée quand l'utilisateur n'a pas le rôle demandé : page 403."""


def utilisateur_connecte(request: Request, db: Session = Depends(get_db)) -> Utilisateur:
    uid = request.session.get("uid")
    utilisateur = db.get(Utilisateur, uid) if uid else None
    if utilisateur is None or not utilisateur.actif:
        raise NonConnecte()
    return utilisateur


def exiger_admin(utilisateur: Utilisateur = Depends(utilisateur_connecte)) -> Utilisateur:
    if not utilisateur.est_admin:
        raise Interdit()
    return utilisateur


def flash(request: Request, message: str, type_: str = "ok") -> None:
    """Message affiché en haut de la page suivante. type_ : ok ou erreur."""
    request.session.setdefault("flash", []).append([type_, message])


# --- formats français ---------------------------------------------------------

def nombre(valeur, decimales: int = 2) -> str:
    if valeur is None or valeur == "":
        return ""
    texte = f"{Decimal(str(valeur)):,.{decimales}f}"
    return texte.replace(",", " ").replace(".", ",")


def monnaie(valeur) -> str:
    return "" if valeur is None or valeur == "" else nombre(valeur, 2) + " €"


def date_fr(valeur) -> str:
    if isinstance(valeur, datetime):
        return valeur.strftime("%d/%m/%Y %H:%M")
    if isinstance(valeur, date):
        return valeur.strftime("%d/%m/%Y")
    return valeur or ""


templates.env.filters.update(nombre=nombre, monnaie=monnaie, date_fr=date_fr)


def rendre(request: Request, db: Session, gabarit: str, contexte: dict, utilisateur: Utilisateur | None = None,
           statut: int = 200):
    from app.core.framework import modules_visibles

    parametres = lire_parametres(db)
    contexte = {
        "utilisateur": utilisateur,
        "menu": modules_visibles(utilisateur) if utilisateur else [],
        "titre_app": parametres.get("societe_nom") or config.APP_TITLE,
        "parametres": parametres,
        "logo": any((DATA / "uploads").glob("logo.*")) if (DATA / "uploads").exists() else False,
        "flash": request.session.pop("flash", []),
        **contexte,
    }
    return templates.TemplateResponse(request, gabarit, contexte, status_code=statut)
