"""Pages du socle de l'administration, toutes sous /admin : connexion, tableau de bord, mon compte,
utilisateurs, journal. (Textes des pages et paramètres du site : app/site/reglages.py.)"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.core.database import get_db
from app.core.framework import modules_visibles
from app.core.modeles import Journal, Utilisateur, journaliser
from app.core.securite import frein, hacher, verifier
from app.core.web import ADMIN, exiger_admin, flash, rendre, utilisateur_connecte

router = APIRouter(prefix=ADMIN)


def _suite_sure(suite: str) -> str:
    """Adresse où revenir après la connexion : une page de ce site, jamais un autre site."""
    if suite.startswith("/") and not suite.startswith(("//", "/\\")):
        return suite
    return f"{ADMIN}/"


@router.get("")
def racine():
    return RedirectResponse(f"{ADMIN}/", status_code=303)


@router.get("/connexion")
def connexion(request: Request, suite: str = f"{ADMIN}/", db: Session = Depends(get_db)):
    return rendre(request, db, "connexion.html", {"suite": _suite_sure(suite), "erreur": ""})


@router.post("/connexion")
def connexion_envoi(request: Request, identifiant: str = Form(""), mot_de_passe: str = Form(""),
                    suite: str = Form(f"{ADMIN}/"), db: Session = Depends(get_db)):
    identifiant = identifiant.strip()
    if frein.bloque(identifiant):
        erreur = "Trop d'essais. Réessayez dans une minute."
    else:
        utilisateur = db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant))
        if utilisateur and utilisateur.actif and verifier(mot_de_passe, utilisateur.mot_de_passe):
            frein.succes(identifiant)
            request.session.clear()
            request.session["uid"] = utilisateur.id
            journaliser(db, utilisateur, "connexion")
            db.commit()
            return RedirectResponse(_suite_sure(suite), status_code=303)
        frein.echec(identifiant)
        erreur = "Identifiant ou mot de passe incorrect."
    return rendre(request, db, "connexion.html", {"suite": _suite_sure(suite), "erreur": erreur}, statut=401)


@router.get("/deconnexion")
def deconnexion(request: Request):
    request.session.clear()
    return RedirectResponse(f"{ADMIN}/connexion", status_code=303)


@router.get("/")
def accueil(request: Request, db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(utilisateur_connecte)):
    cartes = [(m, db.scalar(select(func.count()).select_from(m.modele))) for m in modules_visibles(utilisateur)]
    recents = []
    if utilisateur.est_admin:
        recents = db.scalars(select(Journal).order_by(Journal.id.desc()).limit(10)).all()
    return rendre(request, db, "accueil.html", {"cartes": cartes, "recents": recents}, utilisateur)


@router.get("/mon-compte")
def mon_compte(request: Request, db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(utilisateur_connecte)):
    return rendre(request, db, "mon_compte.html", {"erreur": ""}, utilisateur)


@router.post("/mon-compte")
def mon_compte_envoi(request: Request, actuel: str = Form(""), nouveau: str = Form(""), confirmation: str = Form(""),
                     db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(utilisateur_connecte)):
    if not verifier(actuel, utilisateur.mot_de_passe):
        erreur = "Mot de passe actuel incorrect."
    elif len(nouveau) < 10:
        erreur = "Le nouveau mot de passe doit faire au moins 10 caractères."
    elif nouveau != confirmation:
        erreur = "La confirmation ne correspond pas."
    else:
        utilisateur.mot_de_passe = hacher(nouveau)
        journaliser(db, utilisateur, "changement de mot de passe")
        db.commit()
        flash(request, "Mot de passe changé.")
        return RedirectResponse(f"{ADMIN}/mon-compte", status_code=303)
    return rendre(request, db, "mon_compte.html", {"erreur": erreur}, utilisateur, 422)


# --- utilisateurs et journal (administrateurs) ---------------------------------------

@router.get("/utilisateurs")
def utilisateurs(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    tous = db.scalars(select(Utilisateur).order_by(Utilisateur.identifiant)).all()
    return rendre(request, db, "socle/utilisateurs.html", {"utilisateurs": tous, "roles": config.ROLES}, admin)


@router.get("/utilisateurs/nouveau")
def utilisateur_nouveau(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    return rendre(request, db, "socle/utilisateur.html", {"cible": None, "roles": config.ROLES, "erreur": ""}, admin)


@router.get("/utilisateurs/{uid:int}")
def utilisateur_fiche(request: Request, uid: int, db: Session = Depends(get_db),
                      admin: Utilisateur = Depends(exiger_admin)):
    cible = db.get(Utilisateur, uid)
    if cible is None:
        return rendre(request, db, "erreur.html", {"message": "Utilisateur introuvable."}, admin, 404)
    return rendre(request, db, "socle/utilisateur.html", {"cible": cible, "roles": config.ROLES, "erreur": ""}, admin)


@router.post("/utilisateurs/nouveau")
@router.post("/utilisateurs/{uid:int}")
async def utilisateur_enregistrer(request: Request, uid: int | None = None, db: Session = Depends(get_db),
                                  admin: Utilisateur = Depends(exiger_admin)):
    formulaire = await request.form()
    cible = db.get(Utilisateur, uid) if uid else None
    identifiant = str(formulaire.get("identifiant", "")).strip()
    mot_de_passe = str(formulaire.get("mot_de_passe", ""))
    roles = [r for r in formulaire.getlist("roles") if r in config.ROLES]
    actif = formulaire.get("actif") == "on"
    erreur = ""
    if cible is None and not identifiant:
        erreur = "L'identifiant est obligatoire."
    elif cible is None and db.scalar(select(Utilisateur).where(Utilisateur.identifiant == identifiant)):
        erreur = "Cet identifiant existe déjà."
    elif (cible is None or mot_de_passe) and len(mot_de_passe) < 10:
        erreur = "Le mot de passe doit faire au moins 10 caractères."
    elif cible is not None and cible.id == admin.id and ("admin" not in roles or not actif):
        erreur = "Vous ne pouvez pas retirer vos propres droits d'administrateur."
    if erreur:
        return rendre(request, db, "socle/utilisateur.html",
                      {"cible": cible, "roles": config.ROLES, "erreur": erreur}, admin, 422)
    if cible is None:
        cible = Utilisateur(identifiant=identifiant, mot_de_passe="")
        db.add(cible)
    cible.nom = str(formulaire.get("nom", "")).strip()
    cible.roles = ",".join(roles)
    cible.actif = actif
    if mot_de_passe:
        cible.mot_de_passe = hacher(mot_de_passe)
    db.flush()
    journaliser(db, admin, "utilisateur enregistré", "utilisateurs", cible.id, f"{cible.identifiant} ({cible.roles})")
    db.commit()
    flash(request, f"Utilisateur {cible.identifiant} enregistré.")
    return RedirectResponse(f"{ADMIN}/utilisateurs", status_code=303)


@router.get("/journal")
def journal(request: Request, module: str = "", page: int = 1, db: Session = Depends(get_db),
            admin: Utilisateur = Depends(exiger_admin)):
    requete = select(Journal)
    if module:
        requete = requete.where(Journal.module == module)
    page = max(page, 1)
    lignes = db.scalars(requete.order_by(Journal.id.desc()).offset((page - 1) * 100).limit(100)).all()
    return rendre(request, db, "socle/journal.html", {"lignes": lignes, "module": module, "page": page}, admin)
