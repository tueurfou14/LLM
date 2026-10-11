"""Base de données : SQLite dans data/app.db, ou l'adresse donnée par APP_DATABASE_URL."""

import os
import warnings
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

RACINE = Path(__file__).resolve().parents[2]
DATA = RACINE / "data"

# SQLite stocke les montants en texte décimal : l'avertissement ne nous concerne pas.
warnings.filterwarnings("ignore", r".*support Decimal objects natively", SAWarning)


def _adresse() -> str:
    adresse = os.environ.get("APP_DATABASE_URL")
    if adresse:
        return adresse
    DATA.mkdir(exist_ok=True)
    return f"sqlite:///{(DATA / 'app.db').as_posix()}"


URL = _adresse()
EN_MEMOIRE = URL in ("sqlite://", "sqlite:///:memory:")
_options: dict = {}
if URL.startswith("sqlite"):
    _options["connect_args"] = {"check_same_thread": False}
if EN_MEMOIRE:
    _options["poolclass"] = StaticPool

engine = create_engine(URL, **_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

if URL.startswith("sqlite") and not EN_MEMOIRE:
    @event.listens_for(engine, "connect")
    def _wal(connexion, _):  # noqa: ANN001
        connexion.execute("PRAGMA journal_mode=WAL")


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def mettre_a_jour_schema() -> list[str]:
    """Crée les tables manquantes et ajoute les colonnes manquantes.
    Ne supprime jamais rien : un champ retiré d'un module reste en base."""
    Base.metadata.create_all(engine)
    ajouts = []
    inspecteur = inspect(engine)
    with engine.begin() as connexion:
        for table in Base.metadata.sorted_tables:
            existantes = {c["name"] for c in inspecteur.get_columns(table.name)}
            for colonne in table.columns:
                if colonne.name not in existantes:
                    type_sql = colonne.type.compile(dialect=engine.dialect)
                    connexion.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{colonne.name}" {type_sql}'))
                    ajouts.append(f"{table.name}.{colonne.name}")
    return ajouts
