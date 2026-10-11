"""Base en mémoire, vidée avant chaque test. `http` est connecté en administrateur, `anonyme` ne l'est pas."""

import os

os.environ["APP_DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "cle-de-test"
os.environ["ADMIN_PASSWORD"] = "admin-mot-de-passe"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.securite import frein  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    frein._echecs.clear()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def anonyme(db):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def http(anonyme):
    reponse = anonyme.post("/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe"},
                           follow_redirects=False)
    assert reponse.status_code == 303, "la connexion admin des tests a échoué"
    return anonyme
