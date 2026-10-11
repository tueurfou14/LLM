"""Application : site public (app/site/public.py) et administration sous /admin (socle, textes, paramètres,
modules de app/modules/)."""

from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import config, modules
from app.core import pages
from app.core.database import SessionLocal, mettre_a_jour_schema
from app.core.framework import REGISTRE
from app.core.securite import cle_secrete, creer_admin_initial
from app.core.web import ADMIN, APP_DIR, Interdit, NonConnecte, rendre
from app.site import contenu, public, reglages

modules.charger()


@asynccontextmanager
async def demarrage(_: FastAPI):
    ajouts = mettre_a_jour_schema()
    if ajouts:
        print(f"Colonnes ajoutées à la base : {', '.join(ajouts)}", flush=True)
    with SessionLocal() as db:
        creer_admin_initial(db)
        installes = contenu.installer_exemples(db)
    if installes:
        print(f"Contenus d'exemple installés : {', '.join(installes)}", flush=True)
    print(f"Espace d'administration : <adresse du site>{ADMIN}/", flush=True)
    yield


app = FastAPI(title=config.APP_TITLE, lifespan=demarrage, docs_url=None, redoc_url=None, openapi_url=None)
# Le cookie de session n'est envoyé qu'à /admin : les visiteurs du site n'en reçoivent aucun. Son nom est propre
# au site : sur 127.0.0.1, les cookies sont communs à tous les ports et un cookie « session » d'une autre
# application locale empêcherait la connexion.
app.add_middleware(SessionMiddleware, secret_key=cle_secrete(), session_cookie="session-__SLUG__",
                   same_site="lax", max_age=12 * 3600, path=ADMIN)


@app.middleware("http")
async def entetes_de_securite(request: Request, suite):
    reponse = await suite(request)
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    reponse.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith(ADMIN):
        reponse.headers.setdefault("X-Frame-Options", "DENY")
        reponse.headers.setdefault("Cache-Control", "no-store")
    return reponse


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(pages.router)
app.include_router(reglages.router)
for module in REGISTRE.values():
    app.include_router(module.router)
app.include_router(public.router)


@app.exception_handler(NonConnecte)
async def non_connecte(request: Request, _: NonConnecte):
    return RedirectResponse(f"{ADMIN}/connexion?suite={quote(request.url.path)}", status_code=303)


@app.exception_handler(Interdit)
async def interdit(request: Request, _: Interdit):
    with SessionLocal() as db:
        return rendre(request, db, "erreur.html", {"message": "Vous n'avez pas accès à cette page."}, statut=403)


@app.exception_handler(StarletteHTTPException)
async def erreur_http(request: Request, exc: StarletteHTTPException):
    """Page « introuvable » aux couleurs du site pour les visiteurs ; réponse simple pour /static et /medias."""
    chemin = request.url.path
    if exc.status_code == 404 and not chemin.startswith(("/static/", "/medias/")):
        if chemin == ADMIN or chemin.startswith(ADMIN + "/"):
            with SessionLocal() as db:
                return rendre(request, db, "erreur.html", {"message": "Page introuvable."}, statut=404)
        return public.page_introuvable(request)
    return await http_exception_handler(request, exc)
