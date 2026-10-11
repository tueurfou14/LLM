# Application de gestion (ERP) avec FastAPI et SQLite
mots-clés: api, fastapi, rest, backend, endpoint, route, crud, sqlite, sqlalchemy, base de données, erp, gestion, facture, client, produit, stock, crm, garage, devis, planning, module
description: Construire ou étendre une application de gestion : base qui tourne, puis un module à la fois.

Structure fixe, quel que soit le nom du dossier. Tout se lance depuis la racine, les imports partent de `app` :
- `app/main.py` : application, `app.include_router(...)` de chaque module
- `app/database.py` : SQLite, `Base`, `get_db` ; `app/models.py` : une classe par table
- `app/web.py` : liste `MODULES` du menu ; `app/routers/<module>.py` ; `app/templates/<module>/liste.html` et `form.html`
- `tests/conftest.py` (fixture `http`, base en mémoire) ; `tests/test_<module>.py`

Démarrage, dossier vide : `scaffold_web_app` avec le nom de l'application. Ne réécris jamais cette base à la main.

Ajouter un module, par exemple vehicules :
1. `read_file` sur `app/routers/clients.py`, `app/templates/clients/liste.html`, `form.html` et `tests/test_clients.py`.
2. `edit_file` sur `app/models.py` : classe `Vehicule` copiée de `Client`. Lien vers un client : `client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))`.
3. `write_file` de `app/routers/vehicules.py`, des deux templates et de `tests/test_vehicules.py`, copiés de clients en changeant noms et champs.
4. `edit_file` sur `app/main.py` (import + `include_router`) et `app/web.py` (ligne dans `MODULES`).
5. `run_command` « uv run pytest -q ». Corrige jusqu'au vert avant le module suivant.

Ordre conseillé pour un ERP : clients, produits ou véhicules, devis, factures, stock, tableau de bord.

Diagnostics :
- `ModuleNotFoundError: No module named 'models'` : l'import doit être `from app.models import ...`.
- 404 sur une route qui existe : routeur absent de `app/main.py`, ou `prefix` différent de l'URL testée.
- 422 : un champ obligatoire du formulaire (`Form(...)`) manque dans les données envoyées.
- `no such column` : champ ajouté à une table existante ; en développement, supprime `data/app.db`.
- Montrer l'application : `start_server` « uv run uvicorn app.main:app --reload --port 8000 », puis donne l'adresse. Pour déboguer, préfère les tests.
