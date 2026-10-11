"""Base d'application de gestion prête à tourner.

Un petit modèle écrit mal une application entière d'un coup, mais copie très
bien un module qui marche. scaffold_web_app pose une base FastAPI + SQLite +
pages HTML avec un module Clients complet et testé ; le modèle ajoute
ensuite les autres modules (véhicules, devis, factures…) en le recopiant.

Structure fixe, quel que soit le nom du dossier : le code est dans `app/`,
les tests dans `tests/`, et tout se lance depuis la racine.
"""

from __future__ import annotations

import re
from pathlib import Path

from .registry import Tool

PYPROJECT = """\
[project]
name = "{slug}"
version = "0.1.0"
description = "{title}"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
filterwarnings = ["ignore:Using .httpx. with .starlette.testclient.:UserWarning"]
"""

GITIGNORE = """\
.venv/
__pycache__/
*.pyc
data/
.cerveau/
.pytest_cache/
"""

README = """\
# {title}

Application de gestion : FastAPI, SQLite, pages HTML.

## Lancer

```
uv run uvicorn app.main:app --reload --port 8000
```

Puis ouvrir http://127.0.0.1:8000. La base est le fichier `data/app.db`, créé au premier lancement.
`uv run` installe seul les dépendances la première fois.

## Tester

```
uv run pytest -q
```

## Structure

```
app/main.py              application, menu, routeurs inclus
app/database.py          connexion SQLite, Base, get_db
app/models.py            une classe par table
app/web.py               moteur de pages et liste MODULES du menu
app/routers/<module>.py  pages d'un module : liste, création, modification, suppression
app/templates/<module>/  liste.html et form.html du module
tests/test_<module>.py   tests du module
```

Les imports partent toujours de `app` : `from app.models import Client`. Toutes les commandes se lancent
depuis la racine du projet.

## Ajouter un module (exemple : vehicules)

1. `app/models.py` : ajouter la classe `Vehicule` (copier `Client`), `__tablename__ = "vehicules"`.
   Une relation vers un client : `client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))`.
2. `app/routers/vehicules.py` : copier `app/routers/clients.py`, remplacer client/Client/clients
   par vehicule/Vehicule/vehicules et adapter les champs du formulaire.
3. `app/templates/vehicules/liste.html` et `form.html` : copier ceux de clients et adapter les colonnes et champs.
4. `app/main.py` : importer le routeur et ajouter `app.include_router(vehicules.router)`.
5. `app/web.py` : ajouter `("/vehicules/", "Véhicules")` dans `MODULES`.
6. `tests/test_vehicules.py` : copier `tests/test_clients.py` et adapter.
7. `uv run pytest -q`.

Une nouvelle table est créée au démarrage. Un champ ajouté à une table existante ne l'est pas :
en développement, supprimer `data/app.db`.
"""

DATABASE = '''\
"""Connexion à la base SQLite : le fichier data/app.db à la racine du projet."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
'''

MODELS = '''\
"""Tables de la base. Une classe par table."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    nom: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120), default="")
    telephone: Mapped[str] = mapped_column(String(30), default="")
    adresse: Mapped[str] = mapped_column(String(255), default="")
    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
'''

WEB = '''\
"""Moteur de pages HTML et menu de l'application."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
APP_TITLE = "{title}"

# Menu : (adresse, libellé). Ajouter ici chaque nouveau module.
MODULES = [
    ("/clients/", "Clients"),
]

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["MODULES"] = MODULES
templates.env.globals["APP_TITLE"] = APP_TITLE
'''

MAIN = '''\
"""Application : pages, menu et routeurs de chaque module."""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  (déclare les tables avant create_all)
from app.database import Base, engine
from app.routers import clients
from app.web import APP_TITLE, BASE_DIR, templates

Base.metadata.create_all(engine)

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(clients.router)


@app.get("/")
def accueil(request: Request):
    return templates.TemplateResponse(request, "accueil.html", {{}})


@app.get("/sante")
def sante():
    return {{"ok": True}}
'''

ROUTER_CLIENTS = '''\
"""Module Clients : liste avec recherche, création, modification, suppression.

Modèle à recopier pour chaque nouveau module (voir README, « Ajouter un module »).
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client
from app.web import templates

router = APIRouter(prefix="/clients", tags=["clients"])


def _get_or_404(db: Session, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client introuvable")
    return client


@router.get("/")
def liste(request: Request, q: str = "", db: Session = Depends(get_db)):
    query = select(Client).order_by(Client.nom)
    if q:
        query = query.where(Client.nom.ilike(f"%{q}%"))
    clients = db.scalars(query).all()
    return templates.TemplateResponse(request, "clients/liste.html", {"clients": clients, "q": q})


@router.get("/nouveau")
def nouveau(request: Request):
    return templates.TemplateResponse(request, "clients/form.html", {"client": None})


@router.post("/nouveau")
def creer(nom: str = Form(...), email: str = Form(""), telephone: str = Form(""), adresse: str = Form(""),
          db: Session = Depends(get_db)):
    db.add(Client(nom=nom.strip(), email=email.strip(), telephone=telephone.strip(), adresse=adresse.strip()))
    db.commit()
    return RedirectResponse("/clients/", status_code=303)


@router.get("/{client_id}")
def fiche(request: Request, client_id: int, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "clients/form.html", {"client": _get_or_404(db, client_id)})


@router.post("/{client_id}")
def modifier(client_id: int, nom: str = Form(...), email: str = Form(""), telephone: str = Form(""),
             adresse: str = Form(""), db: Session = Depends(get_db)):
    client = _get_or_404(db, client_id)
    client.nom, client.email = nom.strip(), email.strip()
    client.telephone, client.adresse = telephone.strip(), adresse.strip()
    db.commit()
    return RedirectResponse("/clients/", status_code=303)


@router.post("/{client_id}/supprimer")
def supprimer(client_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, client_id))
    db.commit()
    return RedirectResponse("/clients/", status_code=303)
'''

BASE_HTML = '''\
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}{{ APP_TITLE }}{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <a class="brand" href="/">{{ APP_TITLE }}</a>
    <nav>
      {% for url, label in MODULES %}<a href="{{ url }}">{{ label }}</a>{% endfor %}
    </nav>
  </header>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
'''

ACCUEIL_HTML = '''\
{% extends "base.html" %}
{% block content %}
<h1>Tableau de bord</h1>
<div class="cards">
  {% for url, label in MODULES %}
  <a class="card" href="{{ url }}">{{ label }}</a>
  {% endfor %}
</div>
{% endblock %}
'''

CLIENTS_LISTE_HTML = '''\
{% extends "base.html" %}
{% block title %}Clients · {{ APP_TITLE }}{% endblock %}
{% block content %}
<div class="toolbar">
  <h1>Clients</h1>
  <a class="button" href="/clients/nouveau">Nouveau client</a>
</div>
<form class="search" method="get">
  <input type="search" name="q" value="{{ q }}" placeholder="Rechercher un nom">
  <button type="submit">Rechercher</button>
</form>
<table>
  <thead><tr><th>Nom</th><th>Email</th><th>Téléphone</th><th></th></tr></thead>
  <tbody>
    {% for client in clients %}
    <tr>
      <td><a href="/clients/{{ client.id }}">{{ client.nom }}</a></td>
      <td>{{ client.email }}</td>
      <td>{{ client.telephone }}</td>
      <td class="actions">
        <form method="post" action="/clients/{{ client.id }}/supprimer" onsubmit="return confirm('Supprimer ce client ?')">
          <button class="danger" type="submit">Supprimer</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">Aucun client.</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
'''

CLIENTS_FORM_HTML = '''\
{% extends "base.html" %}
{% block title %}{{ client.nom if client else "Nouveau client" }} · {{ APP_TITLE }}{% endblock %}
{% block content %}
<h1>{{ client.nom if client else "Nouveau client" }}</h1>
<form class="form" method="post" action="{{ '/clients/%d' % client.id if client else '/clients/nouveau' }}">
  <label>Nom <input name="nom" required maxlength="120" value="{{ client.nom if client else '' }}"></label>
  <label>Email <input name="email" type="email" maxlength="120" value="{{ client.email if client else '' }}"></label>
  <label>Téléphone <input name="telephone" maxlength="30" value="{{ client.telephone if client else '' }}"></label>
  <label>Adresse <input name="adresse" maxlength="255" value="{{ client.adresse if client else '' }}"></label>
  <div class="buttons">
    <button type="submit">Enregistrer</button>
    <a href="/clients/">Annuler</a>
  </div>
</form>
{% endblock %}
'''

STYLE_CSS = """\
:root { --bg: #f6f7f9; --card: #fff; --text: #1d2330; --muted: #667085; --line: #e4e7ec; --accent: #2557d6; --danger: #c4320a; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
header { display: flex; align-items: center; gap: 24px; padding: 12px 24px; background: var(--card); border-bottom: 1px solid var(--line); }
header .brand { font-weight: 700; color: var(--text); text-decoration: none; }
nav { display: flex; gap: 16px; flex-wrap: wrap; }
nav a { color: var(--muted); text-decoration: none; }
nav a:hover { color: var(--accent); }
main { max-width: 1100px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 16px; }
a { color: var(--accent); }
.toolbar { display: flex; justify-content: space-between; align-items: center; }
.button, button { display: inline-block; padding: 8px 14px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; font: inherit; text-decoration: none; cursor: pointer; }
button.danger { background: transparent; color: var(--danger); padding: 4px 8px; }
.search { display: flex; gap: 8px; margin-bottom: 16px; }
input, select, textarea { width: 100%; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; font: inherit; background: #fff; }
.search input { max-width: 320px; }
table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }
th { background: #fafbfc; color: var(--muted); font-weight: 600; font-size: 13px; }
td.actions { text-align: right; }
td.actions form { display: inline; }
td.empty { color: var(--muted); text-align: center; }
.form { display: grid; gap: 14px; max-width: 520px; background: var(--card); padding: 20px; border: 1px solid var(--line); border-radius: 8px; }
.form label { display: grid; gap: 4px; color: var(--muted); font-size: 13px; }
.buttons { display: flex; gap: 16px; align-items: center; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }
.card { display: block; padding: 20px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; color: var(--text); font-weight: 600; text-decoration: none; }
.card:hover { border-color: var(--accent); }
"""

CONFTEST = '''\
"""Chaque test a sa propre base SQLite en mémoire."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def http():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
'''

TEST_CLIENTS = '''\
def test_accueil(http):
    page = http.get("/")
    assert page.status_code == 200
    assert "Clients" in page.text


def test_creer_modifier_supprimer_un_client(http):
    r = http.post("/clients/nouveau", data={"nom": "Garage Dupont", "email": "contact@dupont.fr"},
                  follow_redirects=False)
    assert r.status_code == 303
    assert "Garage Dupont" in http.get("/clients/").text

    r = http.post("/clients/1", data={"nom": "Garage Martin"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Garage Martin" in http.get("/clients/").text

    http.post("/clients/1/supprimer", follow_redirects=False)
    assert "Garage Martin" not in http.get("/clients/").text


def test_recherche(http):
    http.post("/clients/nouveau", data={"nom": "Alpha"})
    http.post("/clients/nouveau", data={"nom": "Beta"})
    page = http.get("/clients/", params={"q": "alp"}).text
    assert "Alpha" in page and "Beta" not in page


def test_client_introuvable(http):
    assert http.get("/clients/999").status_code == 404


def test_nom_obligatoire(http):
    assert http.post("/clients/nouveau", data={"email": "x@y.fr"}).status_code == 422
'''


def _slug(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return text or "application"


def files_for(title: str) -> dict[str, str]:
    slug = _slug(title)
    safe_title = title.replace('"', "'")
    return {
        "pyproject.toml": PYPROJECT.format(slug=slug, title=safe_title),
        ".gitignore": GITIGNORE,
        "README.md": README.format(title=title),
        "app/__init__.py": "",
        "app/database.py": DATABASE,
        "app/models.py": MODELS,
        "app/web.py": WEB.format(title=safe_title),
        "app/main.py": MAIN.format(),
        "app/routers/__init__.py": "",
        "app/routers/clients.py": ROUTER_CLIENTS,
        "app/templates/base.html": BASE_HTML,
        "app/templates/accueil.html": ACCUEIL_HTML,
        "app/templates/clients/liste.html": CLIENTS_LISTE_HTML,
        "app/templates/clients/form.html": CLIENTS_FORM_HTML,
        "app/static/style.css": STYLE_CSS,
        "tests/conftest.py": CONFTEST,
        "tests/test_clients.py": TEST_CLIENTS,
    }


def scaffold_web_app(root: Path, title: str = "Application de gestion") -> str:
    """Crée la base d'une application de gestion qui tourne, avec un module Clients."""
    if (root / "app").exists():
        return ("échec : le dossier app/ existe déjà, la base est en place. Lis app/main.py et "
                "app/routers/clients.py, puis ajoute les modules en suivant README.md (« Ajouter un module »).")
    created, kept = [], []
    for rel, content in files_for(title).items():
        target = root / rel
        if target.exists():
            kept.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(rel)
    text = (f"base créée ({len(created)} fichiers) : {', '.join(created)}.\n"
            "Elle fonctionne telle quelle : un module Clients complet (liste, recherche, création, modification, "
            "suppression) avec ses tests.\n"
            "Étapes suivantes :\n"
            "1. run_command « uv run pytest -q » (uv installe les dépendances la première fois) ;\n"
            "2. start_server « uv run uvicorn app.main:app --reload --port 8000 » et donne l'adresse ;\n"
            "3. pour chaque module demandé, lis d'abord app/routers/clients.py et ses templates, puis suis "
            "README.md, section « Ajouter un module ». Un module à la fois, tests verts avant le suivant.\n"
            "Les imports partent toujours de app : « from app.models import Client ».")
    if kept:
        text += f"\nFichiers déjà présents, laissés tels quels : {', '.join(kept)}."
    return text


TOOLS = [
    Tool(
        name="scaffold_web_app",
        description=(
            "Crée d'un coup la base d'une application de gestion (ERP, CRM, gestion de stock, facturation…) qui "
            "tourne : FastAPI + SQLite + pages HTML, un module Clients complet et ses tests. À appeler en premier "
            "pour une nouvelle application de ce type, puis ajouter les modules un par un en copiant Clients."
        ),
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Nom affiché de l'application, ex: Garage Dupont"}},
        },
        run=scaffold_web_app,
    ),
]
