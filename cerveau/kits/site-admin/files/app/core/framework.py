"""Moteur des modules (repris du kit gestion) : un module se déclare, le moteur fait le reste.

    module = Module(nom="vehicules", libelle="Véhicules", objet="véhicule", champs=[
        Champ("immatriculation", "Immatriculation", requis=True),
        Champ("client", "Client", type="ref", ref="clients", requis=True),
    ])

donne une table en base, un menu, une liste avec recherche et pagination, un formulaire de
création et de modification, la suppression (refusée si l'objet est utilisé ailleurs), l'export
CSV, le journal des actions, et selon les options : lignes de document avec totaux, impression,
planning, numérotation automatique, actions métier et verrouillage.

Ici, tout le back-office est sous le préfixe /admin : un module « services » a sa liste sur /admin/services/.
Le type « image » (envoi d'un fichier PNG, JPG ou WEBP) est propre à ce kit : voir app/core/medias.py.
"""

from __future__ import annotations

import csv
import io
import re
from calendar import Calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import UploadFile
from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func, or_,
                        select)
from sqlalchemy.orm import Session, relationship

from app.core import medias
from app.core.database import Base, get_db
from app.core.modeles import Utilisateur, journaliser, numero_suivant
from app.core.web import ADMIN, Interdit, date_fr, flash, monnaie, nombre, rendre, utilisateur_connecte

TYPES = ("texte", "texte_long", "entier", "decimal", "monnaie", "date", "booleen", "choix", "email", "telephone",
         "ref", "numero", "calcul", "image")
TEXTUELS = ("texte", "texte_long", "email", "telephone", "choix", "numero")
PAR_PAGE = 50

REGISTRE: dict[str, "Module"] = {}
# Noms pris par le socle (tables, pages de /admin) ou par les variables des pages du site public.
RESERVES = ("utilisateurs", "journal", "sequences", "parametres", "textes", "connexion", "deconnexion", "mon_compte",
            "static", "medias", "sante", "site", "page", "menu", "article", "request", "annee", "url_base",
            "aujourdhui", "admin", "sitemap", "robots")


@dataclass
class Champ:
    """Un champ d'un module.

    type : texte, texte_long, entier, decimal, monnaie, date, booleen, choix, email, telephone,
           ref (lien vers un autre module, ref="clients"), numero (attribué à la création selon
           format, ex. "F-{annee}-{n:04d}"), calcul (lecture seule, calcul=fonction(objet) -> valeur),
           image (fichier PNG, JPG ou WEBP de 5 Mo au plus ; la colonne garde le nom du fichier,
           l'image est servie sur /medias/<nom>).
    defaut : valeur ou fonction sans argument (ex. date.today).
    liste : affiché comme colonne dans la liste.
    affichage : pour un calcul, comment l'afficher (monnaie, decimal, entier, texte).
    """
    nom: str
    libelle: str
    type: str = "texte"
    requis: bool = False
    choix: list[str] = field(default_factory=list)
    ref: str = ""
    defaut: Any = None
    liste: bool = True
    format: str = ""
    calcul: Callable[[Any], Any] | None = None
    affichage: str = "texte"
    aide: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.nom):
            raise ValueError(f"champ « {self.nom} » : nom en minuscules, chiffres et _ seulement")
        if self.type not in TYPES:
            raise ValueError(f"champ « {self.nom} » : type inconnu « {self.type} ». Types : {', '.join(TYPES)}")
        if self.type == "choix" and not self.choix:
            raise ValueError(f"champ « {self.nom} » : un type choix demande choix=[...]")
        if self.type == "ref" and not self.ref:
            raise ValueError(f"champ « {self.nom} » : un type ref demande ref=\"<module>\"")
        if self.type == "numero" and not self.format:
            raise ValueError(f"champ « {self.nom} » : un type numero demande format, ex. \"F-{{annee}}-{{n:04d}}\"")
        if self.type == "calcul" and self.calcul is None:
            raise ValueError(f"champ « {self.nom} » : un type calcul demande calcul=fonction(objet)")

    @property
    def colonne(self) -> str:
        return f"{self.nom}_id" if self.type == "ref" else self.nom

    @property
    def saisissable(self) -> bool:
        return self.type not in ("calcul", "numero")

    def valeur_defaut(self):
        return self.defaut() if callable(self.defaut) else self.defaut


@dataclass
class Lignes:
    """Lignes d'un document (devis, facture, bon de commande…), saisies dans un tableau."""
    libelle: str
    champs: list[Champ]
    nom: str = "lignes"


@dataclass
class Action:
    """Bouton métier sur la fiche d'un objet, ex. « Transformer en facture ».
    fonction(objet, db, utilisateur) fait le travail et renvoie l'adresse où aller ensuite (ou None).
    Elle lève ValueError("message") pour refuser."""
    nom: str
    libelle: str
    fonction: Callable[[Any, Session, Utilisateur], str | None]
    visible: Callable[[Any], bool] | None = None
    roles: list[str] | None = None


_SQL = {
    "texte": lambda: String(255), "email": lambda: String(255), "telephone": lambda: String(40),
    "choix": lambda: String(80), "numero": lambda: String(40), "texte_long": lambda: Text(),
    "entier": lambda: Integer(), "decimal": lambda: Numeric(14, 3), "monnaie": lambda: Numeric(14, 2),
    "date": lambda: Date(), "booleen": lambda: Boolean(), "image": lambda: String(255),
}


def _classe(nom: str) -> str:
    return "".join(part.capitalize() for part in nom.split("_"))


def _colonnes(nom_classe: str, champs: list[Champ]) -> dict:
    attributs: dict = {}
    for champ in champs:
        if champ.type == "calcul":
            continue
        if champ.type == "ref":
            attributs[champ.colonne] = Column(Integer, ForeignKey(f"{champ.ref}.id"), index=True)
            attributs[champ.nom] = relationship(_classe(champ.ref), foreign_keys=f"{nom_classe}.{champ.colonne}")
        else:
            attributs[champ.nom] = Column(_SQL[champ.type](), index=champ.type == "numero")
    return attributs


class Module:
    def __init__(self, nom: str, libelle: str, champs: list[Champ], *, objet: str = "", feminin: bool = False,
                 lignes: Lignes | None = None,
                 titre: str = "", ordre: int = 100, tri: str = "", lecture: list[str] | None = None,
                 ecriture: list[str] | None = None, suppression: bool = True, imprimable: bool = False,
                 planning: str = "", actions: list[Action] | None = None,
                 avant_enregistrement: Callable[[Any, Session], None] | None = None,
                 verrouille: Callable[[Any], bool] | None = None, modifiable_si_verrouille: list[str] | None = None,
                 public: Callable[[Any], str] | None = None):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", nom):
            raise ValueError(f"module « {nom} » : nom en minuscules, chiffres et _ seulement")
        if nom in REGISTRE:
            raise ValueError(f"module « {nom} » déclaré deux fois")
        if nom in RESERVES:
            raise ValueError(f"module « {nom} » : ce nom est réservé au socle ({', '.join(RESERVES)})")
        noms = [c.nom for c in champs]
        if len(noms) != len(set(noms)):
            raise ValueError(f"module « {nom} » : deux champs portent le même nom")
        self.nom, self.libelle, self.champs = nom, libelle, champs
        self.objet = objet or libelle.lower().rstrip("s")
        # Accords : « Nouvelle facture enregistrée », « Aucune intervention ».
        self.e = "e" if feminin else ""
        self.nouveau = f"{'Nouvelle' if feminin else 'Nouveau'} {self.objet}"
        self.aucun = f"{'Aucune' if feminin else 'Aucun'} {self.objet}"
        self.ce = f"{'Cette' if feminin else 'Ce'} {self.objet}"
        self.lignes = lignes
        self.titre = titre or next((c.nom for c in champs if c.type == "numero"), "") or next(
            (c.nom for c in champs if c.type == "texte"), champs[0].nom)
        self.ordre, self.tri = ordre, tri
        self.lecture, self.ecriture = lecture, ecriture
        self.suppression, self.imprimable = suppression, imprimable
        self.planning = planning
        if planning and planning not in noms:
            raise ValueError(f"module « {nom} » : planning=« {planning} » n'est pas un champ")
        self.actions = actions or []
        self.avant_enregistrement = avant_enregistrement
        self.verrouille = verrouille
        self.modifiable_si_verrouille = modifiable_si_verrouille or []
        # Adresse de l'objet sur le site public (bouton « Voir sur le site »), ex. lambda a: f"/actualites/{a.id}".
        self.public = public
        self.modele = self._creer_modele()
        self.router = self._creer_router()
        REGISTRE[nom] = self

    # --- modèle ---------------------------------------------------------------

    def _creer_modele(self):
        nom_classe = _classe(self.nom)
        attributs = {
            "__tablename__": self.nom,
            "id": Column(Integer, primary_key=True),
            "cree_le": Column(DateTime, server_default=func.now()),
            "modifie_le": Column(DateTime, server_default=func.now(), onupdate=func.now()),
            **_colonnes(nom_classe, self.champs),
        }
        if self.lignes:
            nom_ligne = _classe(f"{self.nom}_{self.lignes.nom}")
            ligne_attributs = {
                "__tablename__": f"{self.nom}_{self.lignes.nom}",
                "id": Column(Integer, primary_key=True),
                "parent_id": Column(Integer, ForeignKey(f"{self.nom}.id"), index=True),
                "position": Column(Integer, default=0),
                **_colonnes(nom_ligne, self.lignes.champs),
            }
            self.modele_ligne = type(nom_ligne, (Base,), ligne_attributs)
            attributs[self.lignes.nom] = relationship(nom_ligne, cascade="all, delete-orphan",
                                                      order_by=f"{nom_ligne}.position")
        return type(nom_classe, (Base,), attributs)

    @property
    def a_des_images(self) -> bool:
        """Le formulaire envoie alors des fichiers (multipart/form-data)."""
        return any(c.type == "image" for c in self.champs)

    @property
    def adresse(self) -> str:
        """Adresse de la liste dans l'administration, ex. /admin/services."""
        return f"{ADMIN}/{self.nom}"

    # --- valeurs ----------------------------------------------------------------

    def champ(self, nom: str) -> Champ:
        return next(c for c in self.champs if c.nom == nom)

    @staticmethod
    def valeur(objet, champ: Champ):
        if champ.type == "calcul":
            try:
                return champ.calcul(objet)
            except Exception:  # noqa: BLE001 - un calcul sur un objet incomplet ne doit pas casser la page
                return None
        return getattr(objet, champ.colonne, None)

    def libelle_objet(self, objet) -> str:
        if objet is None:
            return ""
        valeur = getattr(objet, self.champ(self.titre).colonne, None) if self.titre else None
        return str(valeur) if valeur not in (None, "") else f"{self.objet} n° {objet.id}"

    def afficher(self, objet, champ: Champ) -> str:
        valeur = self.valeur(objet, champ)
        if champ.type == "ref":
            cible = getattr(objet, champ.nom, None)
            return REGISTRE[champ.ref].libelle_objet(cible) if cible is not None else ""
        return formater(champ, valeur)

    # --- droits -----------------------------------------------------------------

    def peut_lire(self, utilisateur: Utilisateur) -> bool:
        return utilisateur.a_un_role(self.lecture) or utilisateur.a_un_role(self.ecriture) and bool(self.ecriture)

    def peut_ecrire(self, utilisateur: Utilisateur) -> bool:
        return utilisateur.a_un_role(self.ecriture)

    def est_verrouille(self, objet) -> bool:
        return bool(objet is not None and getattr(objet, "id", None) is not None and self.verrouille
                    and self.verrouille(objet))

    # --- formulaire ---------------------------------------------------------------

    def lire_formulaire(self, formulaire, objet=None) -> tuple[dict, list[dict], dict[str, str]]:
        """Valeurs converties des champs, lignes, et erreurs par champ."""
        valeurs, erreurs = {}, {}
        verrouille = self.est_verrouille(objet)
        for champ in self.champs:
            if not champ.saisissable or verrouille and champ.nom not in self.modifiable_si_verrouille:
                continue
            if champ.type == "image":
                self._lire_image(champ, formulaire, objet, valeurs, erreurs)
                continue
            try:
                valeurs[champ.colonne] = convertir(champ, formulaire.get(champ.nom))
            except ValueError as exc:
                erreurs[champ.nom] = str(exc)
        lignes: list[dict] = []
        if self.lignes and not verrouille:
            prefixe = self.lignes.nom
            indices = sorted({int(m.group(1)) for cle in formulaire.keys()
                              if (m := re.fullmatch(rf"{prefixe}-(\d+)-\w+", cle))})
            for i in indices:
                brut = {c.nom: formulaire.get(f"{prefixe}-{i}-{c.nom}") for c in self.lignes.champs if c.saisissable}
                if not any((v or "").strip() for v in brut.values() if isinstance(v, str)):
                    continue   # ligne vide
                ligne = {}
                for champ in self.lignes.champs:
                    if not champ.saisissable:
                        continue
                    try:
                        valeur = convertir(champ, brut.get(champ.nom))
                        if valeur is None and champ.defaut is not None:
                            valeur = champ.valeur_defaut()
                        ligne[champ.colonne] = valeur
                    except ValueError as exc:
                        erreurs[f"{prefixe}-{i}-{champ.nom}"] = f"ligne {len(lignes) + 1}, {champ.libelle} : {exc}"
                lignes.append(ligne)
        return valeurs, lignes, erreurs

    @staticmethod
    def _lire_image(champ: Champ, formulaire, objet, valeurs: dict, erreurs: dict) -> None:
        """Nouveau fichier → ImageRecue ; case « Retirer l'image » → None ; sinon l'image actuelle est gardée
        (la colonne n'est pas touchée). Un texte envoyé à la place d'un fichier est ignoré."""
        try:
            image = medias.lire(formulaire.get(champ.nom))
        except ValueError as exc:
            erreurs[champ.nom] = str(exc)
            return
        actuelle = getattr(objet, champ.colonne, None) if objet is not None else None
        if image is not None:
            valeurs[champ.colonne] = image
        elif champ.requis and not actuelle:
            erreurs[champ.nom] = "obligatoire"
        elif formulaire.get(f"{champ.nom}__retirer") == "on" and not champ.requis:
            valeurs[champ.colonne] = None

    @staticmethod
    def saisie_brute(formulaire) -> dict:
        """Valeurs saisies à réafficher après une erreur (sans les fichiers, qu'il faut choisir de nouveau)."""
        return {cle: valeur for cle, valeur in formulaire.items() if not isinstance(valeur, UploadFile)}

    def verifier_references(self, db: Session, valeurs: dict, lignes: list[dict], erreurs: dict) -> None:
        for champ in self.champs + (self.lignes.champs if self.lignes else []):
            if champ.type != "ref":
                continue
            ids = [valeurs.get(champ.colonne)] + [ligne.get(champ.colonne) for ligne in lignes]
            for ident in ids:
                if ident is not None and db.get(REGISTRE[champ.ref].modele, ident) is None:
                    erreurs[champ.nom] = f"{REGISTRE[champ.ref].objet} introuvable"

    def options_refs(self, db: Session) -> dict[str, list[tuple[int, str]]]:
        options = {}
        for champ in self.champs + (self.lignes.champs if self.lignes else []):
            if champ.type == "ref":
                cible = REGISTRE[champ.ref]
                objets = db.scalars(select(cible.modele).order_by(cible.ordre_sql())).all()
                options[champ.nom] = [(o.id, cible.libelle_objet(o)) for o in objets]
        return options

    def ordre_sql(self):
        tri = self.tri or (f"-{self.titre}" if self.champ(self.titre).type == "numero" else self.titre)
        descendant = tri.startswith("-")
        nom = tri.lstrip("-")
        colonne = getattr(self.modele, self.champ(nom).colonne if nom in [c.nom for c in self.champs] else nom)
        return colonne.desc() if descendant else colonne

    def references_vers(self, db: Session, objet) -> list[str]:
        """Ce qui utilise cet objet ailleurs : « 3 devis », « 1 facture »…"""
        usages = []
        for autre in REGISTRE.values():
            sources = [(autre.modele, c) for c in autre.champs if c.type == "ref" and c.ref == self.nom]
            if autre.lignes:
                sources += [(autre.modele_ligne, c) for c in autre.lignes.champs if c.type == "ref" and c.ref == self.nom]
            for modele, champ in sources:
                nombre_ = db.scalar(select(func.count()).select_from(modele).where(
                    getattr(modele, champ.colonne) == objet.id))
                if nombre_:
                    usages.append(f"{nombre_} {autre.libelle.lower()}")
        return usages

    def enregistrer(self, db: Session, objet, valeurs: dict, lignes: list[dict]) -> list[str]:
        """Applique les valeurs à l'objet. Les nouvelles images sont écrites sur le disque ; renvoie les
        images remplacées ou retirées, à effacer après le commit (medias.supprimer)."""
        # Le verrouillage dépend de l'état d'avant : une facture créée « Émise » garde ses lignes.
        verrouille_avant = objet.id is not None and self.est_verrouille(objet)
        valeurs, nouvelles, anciennes = dict(valeurs), [], []
        for champ in self.champs:
            if champ.type == "image" and champ.colonne in valeurs:
                if isinstance(valeurs[champ.colonne], medias.ImageRecue):
                    valeurs[champ.colonne] = medias.enregistrer(valeurs[champ.colonne])
                    nouvelles.append(valeurs[champ.colonne])
                actuelle = getattr(objet, champ.colonne, None)
                if actuelle and actuelle != valeurs[champ.colonne]:
                    anciennes.append(actuelle)
        try:
            for colonne, valeur in valeurs.items():
                setattr(objet, colonne, valeur)
            if self.lignes and not verrouille_avant:
                collection = getattr(objet, self.lignes.nom)
                collection.clear()
                for position, ligne in enumerate(lignes):
                    collection.append(self.modele_ligne(position=position, **ligne))
            if self.avant_enregistrement:
                self.avant_enregistrement(objet, db)
        except Exception:
            medias.supprimer(nouvelles)   # refusé : les images tout juste écrites ne servent à rien
            raise
        return anciennes

    # --- pages ------------------------------------------------------------------

    def _creer_router(self) -> APIRouter:
        router = APIRouter(prefix=self.adresse, tags=[self.nom])
        module = self

        def lire(utilisateur: Utilisateur = Depends(utilisateur_connecte)) -> Utilisateur:
            if not module.peut_lire(utilisateur):
                raise Interdit()
            return utilisateur

        def ecrire(utilisateur: Utilisateur = Depends(utilisateur_connecte)) -> Utilisateur:
            if not module.peut_ecrire(utilisateur):
                raise Interdit()
            return utilisateur

        def charger(db: Session, objet_id: int):
            return db.get(module.modele, objet_id)

        def page_form(request, db, utilisateur, objet, valeurs=None, lignes=None, erreurs=None, statut=200):
            return rendre(request, db, "module/form.html", {
                "module": module, "objet": objet, "valeurs": valeurs, "lignes_saisies": lignes,
                "erreurs": erreurs or {}, "options": module.options_refs(db),
                "verrouille": module.est_verrouille(objet), "peut_ecrire": module.peut_ecrire(utilisateur),
                "actions": [a for a in module.actions if objet is not None and (a.visible is None or a.visible(objet))
                            and utilisateur.a_un_role(a.roles)],
            }, utilisateur, statut)

        def introuvable(request, db, utilisateur):
            return rendre(request, db, "erreur.html", {"message": f"{module.objet.capitalize()} introuvable."},
                          utilisateur, 404)

        @router.get("/")
        def liste(request: Request, q: str = "", page: int = 1, db: Session = Depends(get_db),
                  utilisateur: Utilisateur = Depends(lire)):
            requete = select(module.modele)
            if q:
                colonnes = [getattr(module.modele, c.colonne) for c in module.champs if c.type in TEXTUELS]
                if colonnes:
                    requete = requete.where(or_(*[col.ilike(f"%{q}%") for col in colonnes]))
            total = db.scalar(select(func.count()).select_from(requete.subquery()))
            pages = max(1, -(-total // PAR_PAGE))
            page = min(max(page, 1), pages)
            objets = db.scalars(requete.order_by(module.ordre_sql(), module.modele.id.desc())
                                .offset((page - 1) * PAR_PAGE).limit(PAR_PAGE)).all()
            return rendre(request, db, "module/liste.html", {
                "module": module, "objets": objets, "q": q, "page": page, "pages": pages, "total": total,
                "colonnes": [c for c in module.champs if c.liste], "peut_ecrire": module.peut_ecrire(utilisateur),
            }, utilisateur)

        @router.get("/export.csv")
        def export(db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(lire)):
            sortie = io.StringIO()
            ecrivain = csv.writer(sortie, delimiter=";")
            ecrivain.writerow(["id"] + [c.libelle for c in module.champs])
            for objet in db.scalars(select(module.modele).order_by(module.ordre_sql())):
                ecrivain.writerow([objet.id] + [module.afficher(objet, c) for c in module.champs])
            return Response("﻿" + sortie.getvalue(), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{module.nom}.csv"'})

        if module.planning:
            @router.get("/planning")
            def planning(request: Request, mois: str = "", db: Session = Depends(get_db),
                         utilisateur: Utilisateur = Depends(lire)):
                try:
                    debut = datetime.strptime(mois, "%Y-%m").date() if mois else date.today().replace(day=1)
                except ValueError:
                    debut = date.today().replace(day=1)
                suivant = (debut.replace(day=28) + (date.resolution * 4)).replace(day=1)
                precedent = (debut - date.resolution).replace(day=1)
                colonne = getattr(module.modele, module.champ(module.planning).colonne)
                objets = db.scalars(select(module.modele).where(colonne >= debut, colonne < suivant)
                                    .order_by(colonne)).all()
                par_jour: dict[date, list] = {}
                for objet in objets:
                    jour = getattr(objet, module.champ(module.planning).colonne)
                    jour = jour.date() if isinstance(jour, datetime) else jour
                    par_jour.setdefault(jour, []).append(objet)
                semaines = Calendar(firstweekday=0).monthdatescalendar(debut.year, debut.month)
                return rendre(request, db, "module/planning.html", {
                    "module": module, "debut": debut, "semaines": semaines, "par_jour": par_jour,
                    "precedent": precedent.strftime("%Y-%m"), "suivant": suivant.strftime("%Y-%m"),
                    "aujourdhui": date.today(), "peut_ecrire": module.peut_ecrire(utilisateur),
                }, utilisateur)

        @router.get("/nouveau")
        def nouveau(request: Request, db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(ecrire)):
            valeurs = {c.nom: c.valeur_defaut() for c in module.champs if c.defaut is not None}
            for champ in module.champs:   # pré-remplissage depuis l'adresse : /admin/devis/nouveau?client=3
                if champ.nom in request.query_params:
                    valeurs[champ.nom] = request.query_params[champ.nom]
            return page_form(request, db, utilisateur, None, valeurs=valeurs)

        @router.post("/nouveau")
        async def creer(request: Request, db: Session = Depends(get_db), utilisateur: Utilisateur = Depends(ecrire)):
            formulaire = await request.form()
            valeurs, lignes, erreurs = module.lire_formulaire(formulaire)
            module.verifier_references(db, valeurs, lignes, erreurs)
            objet = module.modele()
            anciennes: list[str] = []
            if not erreurs:
                try:
                    anciennes = module.enregistrer(db, objet, valeurs, lignes)
                except ValueError as exc:
                    erreurs["_"] = str(exc)
            if erreurs:
                db.rollback()
                return page_form(request, db, utilisateur, None, valeurs=module.saisie_brute(formulaire),
                                 lignes=lignes, erreurs=erreurs, statut=422)
            for champ in module.champs:
                if champ.type == "numero":
                    setattr(objet, champ.colonne, numero_suivant(db, f"{module.nom}.{champ.nom}", champ.format))
            db.add(objet)
            db.flush()
            journaliser(db, utilisateur, "création", module.nom, objet.id, module.libelle_objet(objet))
            db.commit()
            medias.supprimer(anciennes)
            flash(request, f"{module.objet.capitalize()} enregistré{module.e}.")
            return RedirectResponse(f"{module.adresse}/{objet.id}", status_code=303)

        @router.get("/{objet_id:int}")
        def fiche(request: Request, objet_id: int, db: Session = Depends(get_db),
                  utilisateur: Utilisateur = Depends(lire)):
            objet = charger(db, objet_id)
            if objet is None:
                return introuvable(request, db, utilisateur)
            return page_form(request, db, utilisateur, objet)

        @router.post("/{objet_id:int}")
        async def modifier(request: Request, objet_id: int, db: Session = Depends(get_db),
                           utilisateur: Utilisateur = Depends(ecrire)):
            objet = charger(db, objet_id)
            if objet is None:
                return introuvable(request, db, utilisateur)
            formulaire = await request.form()
            avant = {c.nom: module.afficher(objet, c) for c in module.champs if c.saisissable}
            valeurs, lignes, erreurs = module.lire_formulaire(formulaire, objet)
            module.verifier_references(db, valeurs, lignes, erreurs)
            anciennes: list[str] = []
            if not erreurs:
                try:
                    anciennes = module.enregistrer(db, objet, valeurs, lignes)
                except ValueError as exc:
                    erreurs["_"] = str(exc)
            if erreurs:
                db.rollback()
                return page_form(request, db, utilisateur, charger(db, objet_id),
                                 valeurs=module.saisie_brute(formulaire), lignes=lignes, erreurs=erreurs, statut=422)
            db.flush()
            changes = [c.libelle for c in module.champs if c.saisissable and module.afficher(objet, c) != avant[c.nom]]
            journaliser(db, utilisateur, "modification", module.nom, objet.id,
                        module.libelle_objet(objet) + (" : " + ", ".join(changes) if changes else ""))
            db.commit()
            medias.supprimer(anciennes)
            flash(request, f"{module.objet.capitalize()} enregistré{module.e}.")
            return RedirectResponse(f"{module.adresse}/{objet.id}", status_code=303)

        if module.suppression:
            @router.post("/{objet_id:int}/supprimer")
            def supprimer(request: Request, objet_id: int, db: Session = Depends(get_db),
                          utilisateur: Utilisateur = Depends(ecrire)):
                objet = charger(db, objet_id)
                if objet is None:
                    return introuvable(request, db, utilisateur)
                if module.est_verrouille(objet):
                    flash(request, f"{module.ce} est verrouillé{module.e} : suppression impossible.", "erreur")
                    return RedirectResponse(f"{module.adresse}/{objet_id}", status_code=303)
                usages = module.references_vers(db, objet)
                if usages:
                    flash(request, f"Suppression impossible : utilisé par {', '.join(usages)}.", "erreur")
                    return RedirectResponse(f"{module.adresse}/{objet_id}", status_code=303)
                libelle = module.libelle_objet(objet)
                images = [getattr(objet, c.colonne) for c in module.champs if c.type == "image"]
                db.delete(objet)
                journaliser(db, utilisateur, "suppression", module.nom, objet_id, libelle)
                db.commit()
                medias.supprimer(images)
                flash(request, f"{module.objet.capitalize()} supprimé{module.e}.")
                return RedirectResponse(f"{module.adresse}/", status_code=303)

        if module.imprimable:
            @router.get("/{objet_id:int}/imprimer")
            def imprimer(request: Request, objet_id: int, db: Session = Depends(get_db),
                         utilisateur: Utilisateur = Depends(lire)):
                objet = charger(db, objet_id)
                if objet is None:
                    return introuvable(request, db, utilisateur)
                return rendre(request, db, "module/imprimer.html", {"module": module, "objet": objet,
                                                                     "REGISTRE": REGISTRE}, utilisateur)

        @router.post("/{objet_id:int}/action/{nom_action}")
        def action(request: Request, objet_id: int, nom_action: str, db: Session = Depends(get_db),
                   utilisateur: Utilisateur = Depends(ecrire)):
            objet = charger(db, objet_id)
            choisie = next((a for a in module.actions if a.nom == nom_action), None)
            if objet is None or choisie is None:
                return introuvable(request, db, utilisateur)
            if not utilisateur.a_un_role(choisie.roles) or (choisie.visible and not choisie.visible(objet)):
                raise Interdit()
            try:
                suite = choisie.fonction(objet, db, utilisateur)
            except ValueError as exc:
                db.rollback()
                flash(request, str(exc), "erreur")
                return RedirectResponse(f"{module.adresse}/{objet_id}", status_code=303)
            journaliser(db, utilisateur, choisie.libelle, module.nom, objet_id, module.libelle_objet(objet))
            db.commit()
            flash(request, f"{choisie.libelle} : fait.")
            return RedirectResponse(suite or f"{module.adresse}/{objet_id}", status_code=303)

        return router


# --- conversions et formats -------------------------------------------------------

def convertir(champ: Champ, brut) -> Any:
    """Texte saisi → valeur Python. Lève ValueError avec un message pour l'utilisateur."""
    texte = brut.strip() if isinstance(brut, str) else ("" if brut is None else str(brut))
    if champ.type == "booleen":
        return texte.lower() in ("on", "1", "true", "oui")
    if texte == "":
        if champ.requis:
            raise ValueError("obligatoire")
        return None
    if champ.type in ("texte", "email", "telephone", "numero") and len(texte) > 255:
        raise ValueError("255 caractères au plus")
    if champ.type == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", texte):
        raise ValueError("adresse email invalide")
    if champ.type == "choix" and texte not in champ.choix:
        raise ValueError(f"choisir parmi : {', '.join(champ.choix)}")
    if champ.type == "entier" or champ.type == "ref":
        try:
            return int(texte)
        except ValueError:
            raise ValueError("nombre entier attendu") from None
    if champ.type in ("decimal", "monnaie"):
        nettoye = texte.replace(" ", "").replace("\xa0", "").replace(" ", "").replace("€", "").replace(",", ".")
        try:
            valeur = Decimal(nettoye)
        except InvalidOperation:
            raise ValueError("nombre attendu, ex. 12,50") from None
        if not valeur.is_finite():
            raise ValueError("nombre attendu, ex. 12,50")
        return valeur
    if champ.type == "date":
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", texte):   # saisie à la française : 11/10/2026
            jour, mois, annee = texte.split("/")
            texte = f"{annee}-{int(mois):02d}-{int(jour):02d}"
        try:
            return date.fromisoformat(texte[:10])
        except ValueError:
            raise ValueError("date attendue (AAAA-MM-JJ)") from None
    return texte


def formater(champ: Champ, valeur) -> str:
    if valeur is None:
        return ""
    genre = champ.affichage if champ.type == "calcul" else champ.type
    if genre == "monnaie":
        return monnaie(valeur)
    if genre == "decimal":
        texte = nombre(valeur, 3).rstrip("0").rstrip(",")
        return texte
    if genre == "entier":
        return nombre(valeur, 0)
    if genre == "booleen" or isinstance(valeur, bool):
        return "Oui" if valeur else "Non"
    if isinstance(valeur, (date, datetime)):
        return date_fr(valeur)
    return str(valeur)


def valeur_saisie(champ: Champ, valeur) -> str:
    """Valeur → texte à mettre dans un champ de formulaire."""
    if valeur is None:
        return ""
    if isinstance(valeur, Decimal):
        texte = format(valeur, "f")
        if champ.type == "monnaie":
            return format(valeur.quantize(Decimal("0.01")), "f").replace(".", ",")
        return (texte.rstrip("0").rstrip(".") if "." in texte else texte).replace(".", ",")
    if isinstance(valeur, date):
        return valeur.isoformat()
    return str(valeur)


def modules_visibles(utilisateur: Utilisateur) -> list[Module]:
    return sorted((m for m in REGISTRE.values() if m.peut_lire(utilisateur)), key=lambda m: (m.ordre, m.libelle))


# --- données d'exemple pour les tests génériques ------------------------------------

def exemple_valeur(champ: Champ, db: Session, index: int = 1):
    if champ.type == "image":
        return None   # une image s'envoie comme fichier : voir fichiers_exemple
    if champ.type == "ref":
        return creer_exemple(REGISTRE[champ.ref], db).id
    return {
        "texte": f"Exemple {champ.libelle} {index}", "texte_long": f"Texte {champ.libelle}",
        "email": f"contact{index}@exemple.fr", "telephone": "03 81 00 00 00", "entier": 3,
        "decimal": Decimal("2.5"), "monnaie": Decimal("120.00"), "date": date.today(), "booleen": True,
        "choix": champ.valeur_defaut() or (champ.choix[0] if champ.choix else ""),
    }.get(champ.type)


def creer_exemple(module: Module, db: Session):
    objet = module.modele()
    for champ in module.champs:
        if champ.saisissable:
            setattr(objet, champ.colonne, exemple_valeur(champ, db))
        elif champ.type == "numero":
            setattr(objet, champ.colonne, numero_suivant(db, f"{module.nom}.{champ.nom}", champ.format))
    db.add(objet)
    db.commit()
    return objet


def formulaire_exemple(module: Module, db: Session) -> dict[str, str]:
    """Données de formulaire valides pour ce module ; les objets liés sont créés en base."""
    donnees = {}
    for champ in module.champs:
        if champ.saisissable and champ.type != "image":
            valeur = exemple_valeur(champ, db)
            if champ.type == "booleen":
                if valeur:
                    donnees[champ.nom] = "on"
            else:
                donnees[champ.nom] = valeur_saisie(champ, valeur)
    if module.lignes:
        for champ in module.lignes.champs:
            if champ.saisissable:
                valeur = exemple_valeur(champ, db)
                if champ.type != "booleen" or valeur:
                    donnees[f"{module.lignes.nom}-0-{champ.nom}"] = "on" if champ.type == "booleen" else valeur_saisie(champ, valeur)
    return donnees


def fichiers_exemple(module: Module) -> dict:
    """Fichiers d'exemple pour les champs image (à envoyer avec files=... dans les tests)."""
    return {c.nom: ("exemple.png", medias.png_exemple(), "image/png") for c in module.champs if c.type == "image"}


def saisie(champ: Champ, valeur) -> str:
    """Pour les gabarits : texte brut déjà saisi, ou valeur convertie pour un champ de formulaire."""
    return valeur if isinstance(valeur, str) else valeur_saisie(champ, valeur)


from app.core.web import templates  # noqa: E402

templates.env.globals.update(saisie=saisie, formater=formater)
