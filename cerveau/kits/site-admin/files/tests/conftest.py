"""Base en mémoire vidée avant chaque test, images dans un dossier temporaire.
`http` est connecté en administrateur, `anonyme` ne l'est pas. `analyser(html)` relève titre, description,
<h1>, images et liens d'une page."""

import os
import tempfile
from html.parser import HTMLParser

os.environ["APP_DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "cle-de-test"
os.environ["ADMIN_PASSWORD"] = "admin-mot-de-passe"
os.environ["APP_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="images-tests-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.framework import Action, Champ, Module  # noqa: E402
from app.core.securite import frein  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = "/admin"

# Module d'essai, présent seulement pendant les tests : il exerce sous /admin les options du moteur que les
# modules du site n'utilisent pas (impression, planning, action, image obligatoire).
ESSAI = Module(
    nom="essai_moteur", libelle="Essai du moteur", objet="essai", ordre=999, titre="nom",
    imprimable=True, planning="jour",
    actions=[Action("valider", "Valider", lambda objet, db, utilisateur: None)],
    champs=[
        Champ("nom", "Nom", requis=True),
        Champ("jour", "Jour", type="date"),
        Champ("photo", "Photo", type="image", requis=True),
        Champ("montant", "Montant", type="monnaie"),
    ],
)
app.include_router(ESSAI.router)


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
    reponse = anonyme.post(f"{ADMIN}/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe"},
                           follow_redirects=False)
    assert reponse.status_code == 303, "la connexion admin des tests a échoué"
    return anonyme


class AnalyseurHTML(HTMLParser):
    """Relève ce que les tests vérifient : langue, titre, description, h1, images, liens, formulaires."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.titre = ""
        self.description = None
        self.nb_h1 = 0
        self.images = []        # attributs de chaque <img>
        self.liens = []         # (balise, attribut, valeur)
        self.formulaires = []   # attributs de chaque <form>
        self._dans_titre = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._dans_titre = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.description = a.get("content") or ""
        elif tag == "h1":
            self.nb_h1 += 1
        elif tag == "form":
            self.formulaires.append(a)
        if tag == "img":
            self.images.append(a)
        for attribut in ("href", "src", "action"):
            if a.get(attribut) is not None and tag not in ("form",):
                self.liens.append((tag, attribut, a[attribut]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._dans_titre = False

    def handle_data(self, data):
        if self._dans_titre:
            self.titre += data


def analyser(html: str) -> AnalyseurHTML:
    analyseur = AnalyseurHTML()
    analyseur.feed(html)
    return analyseur
