"""Tables du socle : utilisateurs, journal des actions, numérotation, paramètres (clé → valeur)."""

from datetime import date, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.database import Base


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(60), unique=True)
    nom: Mapped[str] = mapped_column(String(120), default="")
    mot_de_passe: Mapped[str] = mapped_column(String(255))
    roles: Mapped[str] = mapped_column(String(255), default="")   # séparés par des virgules
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def liste_roles(self) -> set[str]:
        return {r.strip() for r in (self.roles or "").split(",") if r.strip()}

    @property
    def est_admin(self) -> bool:
        return "admin" in self.liste_roles

    def a_un_role(self, roles: list[str] | None) -> bool:
        """Vrai si roles est vide (tout le monde) ou si l'utilisateur a l'un d'eux. L'admin a tout."""
        return not roles or self.est_admin or bool(self.liste_roles & set(roles))


class Journal(Base):
    __tablename__ = "journal"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    utilisateur: Mapped[str] = mapped_column(String(60), default="")
    action: Mapped[str] = mapped_column(String(40))
    module: Mapped[str] = mapped_column(String(60), default="", index=True)
    objet_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class Sequence(Base):
    __tablename__ = "sequences"

    cle: Mapped[str] = mapped_column(String(80), primary_key=True)
    valeur: Mapped[int] = mapped_column(Integer, default=0)


class Parametre(Base):
    """Valeurs enregistrées depuis l'administration (textes des pages, paramètres du site…)."""
    __tablename__ = "parametres"

    cle: Mapped[str] = mapped_column(String(80), primary_key=True)
    valeur: Mapped[str] = mapped_column(Text, default="")


def journaliser(db: Session, utilisateur: Utilisateur | None, action: str, module: str = "",
                objet_id: int | None = None, detail: str = "") -> None:
    db.add(Journal(utilisateur=utilisateur.identifiant if utilisateur else "", action=action, module=module,
                   objet_id=objet_id, detail=detail[:2000]))


def numero_suivant(db: Session, cle: str, modele: str) -> str:
    """Numéro suivant d'une série, sans trou ni doublon : « F-{annee}-{n:04d} » → F-2026-0001.
    Avec {annee} dans le modèle, la série repart à 1 chaque année."""
    annee = date.today().year
    cle_complete = f"{cle}.{annee}" if "{annee}" in modele else cle
    sequence = db.get(Sequence, cle_complete)
    if sequence is None:
        sequence = Sequence(cle=cle_complete, valeur=0)
        db.add(sequence)
    sequence.valeur += 1
    return modele.format(annee=annee, n=sequence.valeur)


def ecrire_parametre(db: Session, cle: str, valeur: str) -> None:
    parametre = db.get(Parametre, cle)
    if parametre is None:
        db.add(Parametre(cle=cle, valeur=valeur))
    else:
        parametre.valeur = valeur


def effacer_parametre(db: Session, cle: str) -> None:
    parametre = db.get(Parametre, cle)
    if parametre is not None:
        db.delete(parametre)
