"""Application : socle (connexion, utilisateurs, journal, paramètres) et modules de app/modules/."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import quote

from app import config, modules
from app.core import pages
from app.core.database import SessionLocal, mettre_a_jour_schema
from app.core.framework import REGISTRE
from app.core.securite import cle_secrete, creer_admin_initial
from app.core.web import APP_DIR, Interdit, NonConnecte, rendre

modules.charger()


@asynccontextmanager
async def demarrage(_: FastAPI):
    ajouts = mettre_a_jour_schema()
    if ajouts:
        print(f"Colonnes ajoutées à la base : {', '.join(ajouts)}", flush=True)
    with SessionLocal() as db:
        creer_admin_initial(db)
    yield


app = FastAPI(title=config.APP_TITLE, lifespan=demarrage, docs_url=None, redoc_url=None)
# Cookie au nom du projet : deux applications sur 127.0.0.1 ne se volent pas leur session.
app.add_middleware(SessionMiddleware, secret_key=cle_secrete(), session_cookie="session-__SLUG__", same_site="lax",
                   max_age=12 * 3600)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(pages.router)
for module in REGISTRE.values():
    app.include_router(module.router)


@app.exception_handler(NonConnecte)
async def non_connecte(request: Request, _: NonConnecte):
    return RedirectResponse(f"/connexion?suite={quote(request.url.path)}", status_code=303)


@app.exception_handler(Interdit)
async def interdit(request: Request, _: Interdit):
    with SessionLocal() as db:
        return rendre(request, db, "erreur.html", {"message": "Vous n'avez pas accès à cette page."}, statut=403)
