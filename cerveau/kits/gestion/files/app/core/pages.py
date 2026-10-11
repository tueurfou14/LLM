"""Pages du socle : connexion, tableau de bord, mon compte, utilisateurs, journal, paramètres."""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.core.database import DATA, get_db
from app.core.framework import modules_visibles
from app.core.modeles import (PARAMETRES_SOCIETE, Journal, Utilisateur, ecrire_parametre, journaliser,
                              lire_parametres)
from app.core.securite import frein, hacher, verifier
from app.core.web import exiger_admin, flash, rendre, utilisateur_connecte

router = APIRouter()
LOGOS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _suite_sure(suite: str) -> str:
    return suite if suite.startswith("/") and not suite.startswith("//") else "/"


@router.get("/sante")
def sante():
    return {"ok": True}


@router.get("/connexion")
def connexion(request: Request, suite: str = "/", db: Session = Depends(get_db)):
    return rendre(request, db, "connexion.html", {"suite": _suite_sure(suite), "erreur": ""})


@router.post("/connexion")
def connexion_envoi(request: Request, identifiant: str = Form(""), mot_de_passe: str = Form(""),
                    suite: str = Form("/"), db: Session = Depends(get_db)):
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
    return RedirectResponse("/connexion", status_code=303)


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
        return RedirectResponse("/mon-compte", status_code=303)
    return rendre(request, db, "mon_compte.html", {"erreur": erreur}, utilisateur, 422)


# --- administration -------------------------------------------------------------

@router.get("/utilisateurs")
def utilisateurs(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    tous = db.scalars(select(Utilisateur).order_by(Utilisateur.identifiant)).all()
    return rendre(request, db, "admin/utilisateurs.html", {"utilisateurs": tous, "roles": config.ROLES}, admin)


@router.get("/utilisateurs/nouveau")
def utilisateur_nouveau(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    return rendre(request, db, "admin/utilisateur.html", {"cible": None, "roles": config.ROLES, "erreur": ""}, admin)


@router.get("/utilisateurs/{uid:int}")
def utilisateur_fiche(request: Request, uid: int, db: Session = Depends(get_db),
                      admin: Utilisateur = Depends(exiger_admin)):
    cible = db.get(Utilisateur, uid)
    if cible is None:
        return rendre(request, db, "erreur.html", {"message": "Utilisateur introuvable."}, admin, 404)
    return rendre(request, db, "admin/utilisateur.html", {"cible": cible, "roles": config.ROLES, "erreur": ""}, admin)


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
        return rendre(request, db, "admin/utilisateur.html", {"cible": cible, "roles": config.ROLES, "erreur": erreur},
                      admin, 422)
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
    return RedirectResponse("/utilisateurs", status_code=303)


@router.get("/journal")
def journal(request: Request, module: str = "", page: int = 1, db: Session = Depends(get_db),
            admin: Utilisateur = Depends(exiger_admin)):
    requete = select(Journal)
    if module:
        requete = requete.where(Journal.module == module)
    page = max(page, 1)
    lignes = db.scalars(requete.order_by(Journal.id.desc()).offset((page - 1) * 100).limit(100)).all()
    return rendre(request, db, "admin/journal.html", {"lignes": lignes, "module": module, "page": page}, admin)


@router.get("/parametres")
def parametres(request: Request, db: Session = Depends(get_db), admin: Utilisateur = Depends(exiger_admin)):
    return rendre(request, db, "admin/parametres.html", {"champs": PARAMETRES_SOCIETE}, admin)


@router.post("/parametres")
async def parametres_envoi(request: Request, logo: UploadFile | None = File(None), db: Session = Depends(get_db),
                           admin: Utilisateur = Depends(exiger_admin)):
    formulaire = await request.form()
    for cle, _ in PARAMETRES_SOCIETE:
        ecrire_parametre(db, cle, str(formulaire.get(cle, "")).strip())
    if logo is not None and logo.filename:
        extension = "." + logo.filename.rsplit(".", 1)[-1].lower() if "." in logo.filename else ""
        contenu = await logo.read()
        if extension not in LOGOS:
            flash(request, "Logo refusé : PNG, JPG ou WEBP seulement.", "erreur")
        elif len(contenu) > 2_000_000:
            flash(request, "Logo refusé : 2 Mo au plus.", "erreur")
        else:
            dossier = DATA / "uploads"
            dossier.mkdir(parents=True, exist_ok=True)
            for ancien in dossier.glob("logo.*"):
                ancien.unlink()
            (dossier / f"logo{extension}").write_bytes(contenu)
    journaliser(db, admin, "paramètres modifiés", "parametres")
    db.commit()
    flash(request, "Paramètres enregistrés.")
    return RedirectResponse("/parametres", status_code=303)


@router.get("/fichiers/logo")
def fichier_logo(_: Utilisateur = Depends(utilisateur_connecte)):
    logo = next(iter((DATA / "uploads").glob("logo.*")), None) if (DATA / "uploads").exists() else None
    if logo is None or logo.suffix not in LOGOS:
        return Response(status_code=404)
    return FileResponse(logo, media_type=LOGOS[logo.suffix])


def parametres_societe(db: Session) -> dict[str, str]:
    return lire_parametres(db)
