# API Python avec FastAPI et SQLite
mots-clés: api, fastapi, rest, backend, endpoint, route, crud, sqlite, sqlalchemy, base de données, erp, gestion, facture, client, produit, stock
description: Construire ou étendre une API Python propre avec base de données et tests.

Structure attendue dans le dossier source du projet :
- `main.py` : création de l'application FastAPI et inclusion des routeurs.
- `database.py` : moteur SQLAlchemy sur SQLite, session, fonction `get_db`.
- `models.py` : modèles SQLAlchemy, une classe par table.
- `schemas.py` : modèles Pydantic d'entrée et de sortie, jamais les modèles SQLAlchemy directement.
- `routers/<module>.py` : un routeur par domaine métier (clients, produits, factures...), avec les opérations lister, lire, créer, modifier, supprimer.
- `tests/test_<module>.py` : tests avec `TestClient` et une base SQLite en mémoire.

Règles :
- Un domaine métier à la fois. Pour un ERP, l'ordre raisonnable est : clients, produits, stock, factures, puis les rapports.
- Valide les entrées avec Pydantic : types, longueurs, valeurs positives pour les quantités et prix.
- Jamais de SQL brut construit par concaténation : utilise l'ORM ou des requêtes paramétrées.
- Les identifiants sont des entiers auto-incrémentés ; renvoie 404 quand un objet n'existe pas.
- Chaque routeur ajouté est testé : au minimum création, lecture, et un cas d'erreur.
- Après écriture, propose `run_command` avec `uv run pytest -q` et corrige ce qui échoue avant de passer à la suite.

Dépendances minimales : `fastapi`, `uvicorn`, `sqlalchemy`, `pydantic`, `pytest`, `httpx`, déclarées dans `pyproject.toml`.
Installation : `uv venv` une fois, puis `uv pip install -e .`. Lancement : `uv run uvicorn <paquet>.main:app --reload`.


## Diagnostics fréquents (lis ceci avant de relancer les tests une troisième fois)
- **404 sur une route qui existe** : le routeur n'est pas inclus dans `main.py` (`app.include_router(clients.router)`), ou son `prefix` ne correspond pas à l'URL testée (`/clients` vs `/api/clients`), ou le test importe une autre `app`. Ouvre `main.py` et le routeur, compare les chemins exacts.
- **`KeyError: 'id'` dans un test** : la réponse n'est pas celle attendue, presque toujours à cause d'un 404 ou d'un 422 juste avant. Affiche `response.json()` dans l'assertion.
- **422** : le corps envoyé ne respecte pas le schéma Pydantic ; compare les champs du test et de `schemas.py`.
- **`detail` en anglais (« Not Found »)** : c'est le 404 par défaut de FastAPI, la route n'a pas été atteinte ; voir le premier point.
- **Base vide entre les tests** : utiliser une base SQLite en mémoire par test ou `Base.metadata.create_all` dans une fixture.
- **Ne lance jamais `uvicorn` ou un serveur pour déboguer** : il ne se termine pas. Les tests avec `TestClient` couvrent le même chemin, sans serveur.
