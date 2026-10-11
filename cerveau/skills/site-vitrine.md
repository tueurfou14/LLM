# Site vitrine (kit site)
mots-clés: site, vitrine, site web, site internet, landing, page d'accueil, artisan, entreprise, présentation, refonte
description: Créer ou adapter un site vitrine à partir du kit site.

Dossier vide : `create_project` avec le nom du client. Kit « site » : site statique que toi seul modifies. Kit « site-admin » si le client veut changer lui-même ses textes, photos, actualités ou promotions (espace /admin) : suis alors la suite affichée par create_project, les points ci-dessous valent pour le kit « site ».
1. Adapte d'abord `contenu/site.json` au client : nom, slogan, description, coordonnées, horaires, services, réalisations, à propos, couleurs, menu. N'invente pas de coordonnées : laisse « [À COMPLÉTER] » et signale-le.
2. Pages : `pages/*.html` (Jinja, étendent `_base.html`), chacune avec `{% set titre %}`, `{% set description %}` et un seul `<h1>`. Nouvelle page : copier `pages/services.html` et l'ajouter au menu de site.json.
3. Images dans `static/img/`, référencées « static/img/nom.jpg », toujours avec un texte alternatif.
4. Animations : `data-anim` sur un élément ; `static/js/main.js`. Jamais de ScrollTrigger pin ni de section épinglée : les animations se jouent seules à l'arrivée.
5. `verify_project`, puis `start_server` et l'adresse. Le site se reconstruit à chaque rechargement.
Rien côté client ne mentionne qu'un outil automatique a produit le site.
