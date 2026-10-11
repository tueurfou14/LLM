# __TITRE__ — site vitrine

Site statique multi-pages généré par `site.py` (Python + Jinja2) dans `dist/`.

## Modifier le contenu

Presque tout se change dans `contenu/site.json` : nom, slogan, description, coordonnées,
horaires, services, réalisations, texte « à propos », réseaux sociaux, couleurs, police,
menu et mentions légales. Les images vont dans `static/img/` et se référencent par
`"static/img/nom.jpg"`.

Les gabarits sont dans `pages/` (ils étendent `pages/_base.html`). Le style est dans
`static/css/style.css`, les animations (GSAP) dans `static/js/main.js` : ajouter
l'attribut `data-anim` à un élément pour qu'il apparaisse en douceur. Jamais de `pin`.

## Ajouter une page

1. Copier `pages/services.html` en `pages/ma-page.html`, changer `titre`, `description`
   et le contenu (un seul `<h1>`).
2. Ajouter `{"titre": "Ma page", "lien": "ma-page.html"}` dans `menu` de `contenu/site.json`.

Les fichiers de `pages/` qui commencent par `_` ne produisent pas de page (gabarits partagés).

## Lancer

```
uv run python site.py serve --port 8000
```

Ouvrir http://127.0.0.1:8000/ : le site est reconstruit à chaque rechargement de page si
une source a changé. `uv run python site.py build` construit seulement `dist/`.

## Tester

```
uv run pytest -q
```

Les tests vérifient la construction, les titres, descriptions, `<h1>`, textes alternatifs,
liens internes et le contenu de `site.json`.

## Déployer

Renseigner `url_site` dans `contenu/site.json` (pour `sitemap.xml` et `robots.txt`),
lancer `uv run python site.py build`, puis copier le contenu de `dist/` à la racine du
site sur le serveur (nginx, Caddy, hébergement mutualisé). Aucun programme n'est nécessaire
côté serveur.
