"""Contenu du site : contenu/site.json et ce que le client enregistre dans l'administration.

contenu/site.json contient trois parties :
- « parametres » : nom, slogan, coordonnées, horaires, couleurs, réseaux, mentions légales… (page Paramètres) ;
- « textes » : les textes des pages (titres, accroches, paragraphes) (page Textes des pages) ;
  chaque réglage est {"cle", "groupe", "libelle", "type", "valeur", "aide"} ; « valeur » est la valeur par
  défaut, remplacée par ce que le client enregistre (table parametres, clés « site.<cle> » et « texte.<cle> ») ;
- « exemples » : objets insérés une seule fois au premier démarrage, module par module (services, actualités…).

Dans les gabarits du site : {{ site.telephone }}, {{ textes.accueil_titre }}.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import medias
from app.core.database import RACINE
from app.core.framework import REGISTRE, convertir
from app.core.modeles import Parametre, ecrire_parametre, effacer_parametre, numero_suivant

FICHIER = RACINE / "contenu" / "site.json"
IMAGES = RACINE / "app" / "static" / "site" / "img"
SECTIONS = {"parametres": "site.", "textes": "texte."}
TYPES = ("texte", "texte_long", "couleur", "url", "email", "telephone", "image")

_cache: dict = {"signature": None, "donnees": None}


class ErreurContenu(Exception):
    """contenu/site.json est invalide : le message dit quoi corriger."""


# --- lecture de contenu/site.json ---------------------------------------------------------

def charger() -> dict:
    """Contenu de contenu/site.json, relu automatiquement quand le fichier change."""
    try:
        etat = FICHIER.stat()
    except FileNotFoundError:
        raise ErreurContenu("fichier manquant : contenu/site.json") from None
    signature = (etat.st_mtime_ns, etat.st_size)
    if _cache["signature"] != signature:
        try:
            donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ErreurContenu(
                f"contenu/site.json n'est pas un JSON valide (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}. "
                "Vérifie les virgules (pas après le dernier élément), les guillemets doubles et les accolades.") from None
        verifier(donnees)
        _cache.update(signature=signature, donnees=donnees)
    return _cache["donnees"]


def verifier(donnees) -> None:
    """Lève ErreurContenu avec un message précis si la structure de contenu/site.json est incorrecte."""
    if not isinstance(donnees, dict):
        raise ErreurContenu("contenu/site.json doit contenir un objet { ... }")
    for section in SECTIONS:
        reglages_ = donnees.get(section)
        if not isinstance(reglages_, list):
            raise ErreurContenu(f"contenu/site.json : « {section} » doit être une liste [ {{...}}, ... ]")
        vues = set()
        for i, reglage in enumerate(reglages_):
            ou = f"contenu/site.json : {section}[{i}]"
            if not isinstance(reglage, dict):
                raise ErreurContenu(f"{ou} doit être un objet {{\"cle\": ..., \"groupe\": ..., \"libelle\": ..., \"valeur\": ...}}")
            cle = reglage.get("cle")
            if not isinstance(cle, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", cle):
                raise ErreurContenu(f"{ou} : « cle » doit être en minuscules, chiffres et _ (ex. accueil_titre), "
                                    f"valeur actuelle : {cle!r}")
            if cle in vues:
                raise ErreurContenu(f"{ou} : la clé « {cle} » est déclarée deux fois dans « {section} »")
            vues.add(cle)
            for champ in ("groupe", "libelle"):
                if not isinstance(reglage.get(champ), str) or not reglage[champ].strip():
                    raise ErreurContenu(f"{ou} ({cle}) : « {champ} » doit être un texte non vide")
            type_ = reglage.get("type", "texte")
            if type_ not in TYPES:
                raise ErreurContenu(f"{ou} ({cle}) : type « {type_} » inconnu. Types : {', '.join(TYPES)}")
            if not isinstance(reglage.get("valeur", ""), str):
                raise ErreurContenu(f"{ou} ({cle}) : « valeur » doit être un texte entre guillemets")
            try:
                verifier_valeur(type_, reglage.get("valeur", ""))
            except ValueError as exc:
                raise ErreurContenu(f"{ou} ({cle}) : valeur incorrecte : {exc}") from None
    exemples = donnees.get("exemples", {})
    if not isinstance(exemples, dict) or not all(isinstance(v, list) for v in exemples.values()):
        raise ErreurContenu("contenu/site.json : « exemples » doit être un objet {\"services\": [ {...} ], ...}")


def reglages(section: str) -> list[dict]:
    return charger()[section]


def valeurs(db: Session, section: str) -> dict[str, str]:
    """Valeurs à utiliser : celles de contenu/site.json, remplacées par celles enregistrées dans l'administration."""
    prefixe = SECTIONS[section]
    resultat = {r["cle"]: r.get("valeur", "") for r in reglages(section)}
    for parametre in db.scalars(select(Parametre).where(Parametre.cle.startswith(prefixe))):
        cle = parametre.cle[len(prefixe):]
        if cle in resultat:
            resultat[cle] = parametre.valeur
    return resultat


def verifier_valeur(type_: str, texte: str) -> str:
    """Contrôle d'une valeur saisie. Lève ValueError avec un message pour l'utilisateur."""
    if type_ == "couleur":
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", texte):
            raise ValueError("couleur attendue, ex. #1f3d36")
        return texte.lower()
    if texte == "":
        return texte
    if type_ == "url" and not re.match(r"https?://[^\s]+$", texte):
        raise ValueError("adresse attendue, commençant par https://")
    if type_ == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", texte):
        raise ValueError("adresse email invalide")
    if type_ in ("texte", "telephone", "url", "email") and len(texte) > 300:
        raise ValueError("300 caractères au plus")
    return texte


def enregistrer(db: Session, section: str, formulaire) -> tuple[dict[str, str], list[str]]:
    """Enregistre le formulaire de la page Textes ou Paramètres.
    Renvoie (erreurs par clé, images remplacées à effacer après le commit). Rien n'est écrit s'il y a une erreur.
    Une valeur identique à celle de contenu/site.json n'est pas stockée : le fichier reste la référence."""
    prefixe = SECTIONS[section]
    actuelles = valeurs(db, section)
    nouvelles, erreurs = {}, {}
    for reglage in reglages(section):
        cle, type_ = reglage["cle"], reglage.get("type", "texte")
        if type_ == "image":
            try:
                image = medias.lire(formulaire.get(cle))
            except ValueError as exc:
                erreurs[cle] = str(exc)
                continue
            if image is not None:
                nouvelles[cle] = image
            elif formulaire.get(f"{cle}__retirer") == "on":
                nouvelles[cle] = ""
            continue
        brut = formulaire.get(cle)
        if brut is None:
            continue   # absent du formulaire : inchangé
        try:
            nouvelles[cle] = verifier_valeur(type_, str(brut).replace("\r\n", "\n").strip())
        except ValueError as exc:
            erreurs[cle] = str(exc)
    if erreurs:
        return erreurs, []
    defauts = {r["cle"]: r.get("valeur", "") for r in reglages(section)}
    anciennes = []
    for cle, valeur in nouvelles.items():
        if isinstance(valeur, medias.ImageRecue):
            valeur = medias.enregistrer(valeur)
        if valeur != actuelles[cle] and medias.NOM_SUR.fullmatch(actuelles[cle] or ""):
            anciennes.append(actuelles[cle])
        if valeur == defauts[cle]:
            effacer_parametre(db, prefixe + cle)
        else:
            ecrire_parametre(db, prefixe + cle, valeur)
    return {}, anciennes


# --- exemples insérés au premier démarrage -------------------------------------------------

def installer_exemples(db: Session) -> list[str]:
    """Insère les « exemples » de contenu/site.json, une seule fois par module et seulement si sa table est vide.
    Un module vidé ensuite par le client n'est pas re-rempli (repère « exemples.<module> »)."""
    installes = []
    for nom, objets in charger().get("exemples", {}).items():
        module = REGISTRE.get(nom)
        if module is None:
            raise ErreurContenu(f"contenu/site.json : exemples.{nom} : aucun module « {nom} » dans app/modules/ "
                                f"(modules : {', '.join(sorted(REGISTRE))})")
        repere = f"exemples.{nom}"
        if db.get(Parametre, repere) is not None:
            continue
        if not db.scalar(select(func.count()).select_from(module.modele)):
            for i, donnees in enumerate(objets):
                db.add(objet_exemple(module, donnees, f"contenu/site.json : exemples.{nom}[{i}]", db))
            installes.append(nom)
        ecrire_parametre(db, repere, "1")
    db.commit()
    return installes


def objet_exemple(module, donnees: dict, ou: str, db: Session):
    """Objet du module à partir d'un exemple. Dates : « 2026-10-01 », « 01/10/2026 » ou « J-7 » (il y a 7 jours).
    Image : nom d'un fichier PNG, JPG ou WEBP de app/static/site/img/ (copié dans les images envoyées)."""
    if not isinstance(donnees, dict):
        raise ErreurContenu(f"{ou} doit être un objet {{\"champ\": valeur, ...}}")
    noms = {c.nom: c for c in module.champs}
    objet = module.modele()
    for cle, brut in donnees.items():
        champ = noms.get(cle)
        if champ is None or not champ.saisissable:
            raise ErreurContenu(f"{ou} : « {cle} » n'est pas un champ du module {module.nom} "
                                f"(champs : {', '.join(c.nom for c in module.champs if c.saisissable)})")
        try:
            if champ.type == "booleen" and isinstance(brut, bool):
                valeur = brut
            elif champ.type == "date" and isinstance(brut, str) and re.fullmatch(r"J-\d+", brut.strip()):
                valeur = date.today() - timedelta(days=int(brut.strip()[2:]))
            elif champ.type == "image":
                valeur = _image_exemple(str(brut or ""), ou)
            else:
                valeur = convertir(champ, "" if brut is None else str(brut))
        except ValueError as exc:
            raise ErreurContenu(f"{ou} : « {cle} » : {exc}") from None
        setattr(objet, champ.colonne, valeur)
    for champ in module.champs:
        if champ.requis and champ.saisissable and getattr(objet, champ.colonne, None) in (None, ""):
            raise ErreurContenu(f"{ou} : le champ obligatoire « {champ.nom} » manque")
        if champ.type == "numero":
            setattr(objet, champ.colonne, numero_suivant(db, f"{module.nom}.{champ.nom}", champ.format))
        if champ.defaut is not None and champ.nom not in donnees and champ.saisissable:
            setattr(objet, champ.colonne, champ.valeur_defaut())
    return objet


def _image_exemple(nom: str, ou: str) -> str | None:
    if not nom:
        return None
    source = IMAGES / nom
    if not source.is_file():
        raise ErreurContenu(f"{ou} : image introuvable app/static/site/img/{nom}")
    contenu = source.read_bytes()
    format_ = medias.format_image(contenu)
    if format_ is None:
        raise ErreurContenu(f"{ou} : app/static/site/img/{nom} n'est pas une image PNG, JPG ou WEBP "
                            "(sans image, le site affiche une illustration par défaut : laisser \"\")")
    return medias.enregistrer(medias.ImageRecue(contenu, format_))
