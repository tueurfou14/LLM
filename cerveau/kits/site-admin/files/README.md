# __TITRE__

Site vitrine avec espace d'administration : le site public est rendu par FastAPI (Jinja), le contenu
est dans une base SQLite modifiable depuis `/admin`.

## Lancer

```
uv run uvicorn app.main:app --reload --port 8000
```

- Site : http://127.0.0.1:8000/
- Administration : http://127.0.0.1:8000/admin/ — au premier démarrage, le compte `admin` est créé ;
  son mot de passe s'affiche dans la console et dans `data/admin-initial.txt` (ou vient de la variable
  `ADMIN_PASSWORD`). À changer dans « Mon compte », puis supprimer le fichier.

La base est `data/app.db`, les images envoyées sont dans `data/uploads/` (à sauvegarder ensemble).

## Tester

```
uv run pytest -q
```

Les tests vérifient chaque page publique (titre, description, un seul `<h1>`, textes alternatifs, liens),
le cycle complet de chaque module dans l'administration, l'envoi d'images, la cohérence de
`contenu/site.json` avec les gabarits et les règles d'animation. Les messages disent quel fichier corriger.

## Où modifier quoi

```
contenu/site.json            contenu de départ : paramètres, textes des pages, exemples
app/templates/site/          pages publiques (_base.html = en-tête, menu, pied de page)
app/static/site/             style.css, main.js (GSAP), illustrations SVG par défaut
app/site/public.py           liste des pages (PAGES), routes publiques, sitemap.xml, robots.txt
app/modules/<nom>.py         contenus modifiables par le client (services, réalisations, actualités…)
app/core/                    moteur de l'administration (repris du kit gestion) : ne pas modifier sans besoin
```

### contenu/site.json

- `parametres` (page « Paramètres du site ») et `textes` (page « Textes des pages ») : listes de
  `{"cle", "groupe", "libelle", "type", "valeur", "aide"}`. Types : `texte`, `texte_long`, `couleur`,
  `url`, `email`, `telephone`, `image`. Dans les gabarits : `{{ site.telephone }}`, `{{ textes.accueil_titre }}`.
  La valeur du fichier sert tant que le client ne l'a pas changée dans l'administration.
- `exemples` : objets insérés une seule fois au premier démarrage, par module (`"services": [...]`).
  Dates : `"2026-10-01"` ou `"J-7"` (il y a 7 jours). Image : nom d'un PNG/JPG/WEBP de `app/static/site/img/`,
  ou rien (une illustration SVG s'affiche). Pour réinstaller les exemples : supprimer `data/app.db`.

### Pages

Chaque gabarit commence par `{% extends "_base.html" %}`, `{% set titre = "..." %}`,
`{% set description = "..." %}` et contient exactement un `<h1>`. Variables : `site`, `textes`,
et pour chaque module ses objets affichables (`services`, `realisations`, `actualites`, `offres`,
`temoignages`) : case `publie` cochée et date `fin` non passée. Fonctions : `media(objet.image, "illustration.svg")`,
`lien(actualite)`. Filtres : `paragraphes`, `lignes`, `paires` (« valeur | libellé »), `tel`, `carte`,
`date_longue`. Une variable inconnue provoque une erreur claire (pas de vide silencieux).

Nouvelle page : créer `app/templates/site/ma-page.html` sur le modèle de `services.html`, puis ajouter
`Page("/ma-page", "ma-page.html", "Ma page")` dans `PAGES` de `app/site/public.py`.

Animations : ajouter `data-anim` à un élément pour qu'il apparaisse en douceur. Jamais de `pin`.
Sans JavaScript ou en « mouvement réduit », tout reste visible.

### Modules (contenus du client)

```python
from app.core.framework import Champ, Module

module = Module(
    nom="equipe", libelle="Équipe", objet="membre", ordre=60, titre="nom", tri="position",
    champs=[
        Champ("nom", "Nom", requis=True),
        Champ("poste", "Poste"),
        Champ("photo", "Photo", type="image"),
        Champ("position", "Position", type="entier", defaut=10),
        Champ("publie", "Affiché sur le site", type="booleen", defaut=True),
    ],
)
```

Le module a aussitôt sa table, son menu `/admin/equipe/`, sa liste, son formulaire, son export et ses
tests ; ses objets publiés sont la variable `equipe` des gabarits. Types : ceux du kit gestion (`texte`,
`texte_long`, `entier`, `decimal`, `monnaie`, `date`, `booleen`, `choix`, `email`, `telephone`, `ref`,
`numero`, `calcul`) et `image` (PNG, JPG ou WEBP, 5 Mo au plus, servie sur `/medias/<fichier>`).
Option `public=lambda obj: "/adresse"` : bouton « Voir sur le site » dans l'administration.

## Mettre en ligne

Sur le serveur : `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers` derrière un
proxy HTTPS (Caddy, nginx). Renseigner « Adresse publique du site » dans Paramètres du site (sitemap.xml,
partages). Sauvegarder `data/` (base, images envoyées, clé de session).
