"""Mots de passe, clé de session, compte administrateur initial, freinage des essais de connexion."""

import hashlib
import hmac
import os
import secrets
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import DATA, EN_MEMOIRE
from app.core.modeles import Utilisateur

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hacher(mot_de_passe: str) -> str:
    sel = os.urandom(16)
    empreinte = hashlib.scrypt(mot_de_passe.encode("utf-8"), salt=sel, **_SCRYPT)
    return f"scrypt${sel.hex()}${empreinte.hex()}"


def verifier(mot_de_passe: str, stocke: str) -> bool:
    try:
        _, sel, empreinte = stocke.split("$")
        calcul = hashlib.scrypt(mot_de_passe.encode("utf-8"), salt=bytes.fromhex(sel), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calcul.hex(), empreinte)


def cle_secrete() -> str:
    """Clé de signature des sessions : SECRET_KEY, sinon data/secret.key créée au premier lancement."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    DATA.mkdir(exist_ok=True)
    fichier = DATA / "secret.key"
    if not fichier.exists():
        fichier.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return fichier.read_text(encoding="utf-8").strip()


def creer_admin_initial(db: Session) -> str | None:
    """Sans aucun utilisateur, crée « admin ». Le mot de passe vient de ADMIN_PASSWORD ou est tiré au
    hasard, affiché dans le journal du serveur et écrit dans data/admin-initial.txt."""
    if db.scalar(select(func.count()).select_from(Utilisateur)):
        return None
    mot_de_passe = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    db.add(Utilisateur(identifiant="admin", nom="Administrateur", mot_de_passe=hacher(mot_de_passe), roles="admin"))
    db.commit()
    if not EN_MEMOIRE:
        DATA.mkdir(exist_ok=True)
        (DATA / "admin-initial.txt").write_text(
            f"identifiant : admin\nmot de passe : {mot_de_passe}\n"
            "Changez-le dans « Mon compte » puis supprimez ce fichier.\n", encoding="utf-8")
    ou = " (aussi dans data/admin-initial.txt)" if not EN_MEMOIRE else ""
    print(f"Compte administrateur créé : admin / {mot_de_passe}{ou}", flush=True)
    return mot_de_passe


class Frein:
    """Après 5 échecs sur un identifiant, les essais sont refusés pendant une minute."""

    ECHECS_MAX = 5
    DUREE = 60

    def __init__(self) -> None:
        self._echecs: dict[str, tuple[int, float]] = {}

    def bloque(self, identifiant: str) -> bool:
        nombre, depuis = self._echecs.get(identifiant.lower(), (0, 0.0))
        if nombre >= self.ECHECS_MAX and time.monotonic() - depuis < self.DUREE:
            return True
        if nombre >= self.ECHECS_MAX:
            self._echecs.pop(identifiant.lower(), None)
        return False

    def echec(self, identifiant: str) -> None:
        nombre, _ = self._echecs.get(identifiant.lower(), (0, 0.0))
        self._echecs[identifiant.lower()] = (nombre + 1, time.monotonic())

    def succes(self, identifiant: str) -> None:
        self._echecs.pop(identifiant.lower(), None)


frein = Frein()
