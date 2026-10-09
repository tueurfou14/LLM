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
- Après écriture, propose `run_command` avec `pytest -q` et corrige ce qui échoue avant de passer à la suite.

Dépendances minimales : `fastapi`, `uvicorn`, `sqlalchemy`, `pydantic`, `pytest`, `httpx`.
Lancement : `uvicorn <paquet>.main:app --reload`.
