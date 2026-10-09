# Livraison de niveau professionnel
mots-clés: pro, professionnel, production, qualité, livrable, entreprise, client final, commercialiser, vendre, robuste, complet, industriel
priorité: 5
description: Quand l'utilisateur veut un résultat professionnel : analyser tous les contextes avant d'écrire, puis livrer complet.

Un livrable professionnel n'est pas un prototype qui marche : c'est un logiciel qu'un tiers peut installer, utiliser, maintenir et faire évoluer sans toi. Avant d'écrire une ligne de code, tu analyses, tu écris le plan dans `PLAN.md`, puis tu exécutes phase par phase.

## 1. Analyse des contextes (écris le résultat dans PLAN.md)
Passe chacun de ces points, même brièvement :
- **Métier** : qui utilise le logiciel, quels rôles (ex. garage : réceptionniste, mécanicien, comptable, gérant), quels flux quotidiens, quelles règles (TVA, numérotation des factures, archivage légal).
- **Données** : entités, relations, contraintes d'unicité, historisation, suppression logique plutôt que physique pour ce qui a une valeur légale.
- **Sécurité** : authentification, rôles et permissions, validation stricte des entrées, protection OWASP, secrets hors du code, journalisation des actions sensibles.
- **Fiabilité** : gestion des erreurs cohérente, transactions, migrations de schéma (Alembic), sauvegardes.
- **Exploitation** : configuration par variables d'environnement, journaux structurés, Docker et docker-compose, procédure de déploiement et de mise à jour.
- **Qualité** : tests unitaires et d'intégration, couverture des cas d'erreur, linter et formateur, intégration continue (GitHub Actions).
- **Expérience** : interface ou API cohérente, messages en français, pagination, recherche, exports (CSV, PDF pour les factures).
- **Documentation** : README d'installation, guide utilisateur court, documentation d'API générée, CHANGELOG, licence.
- **Inconnues** : ce que tu ne sais pas ou dont tu n'es pas sûr (versions de bibliothèques, règles légales, formats). Pour chaque inconnue, utilise `web_search` puis `web_fetch` pour vérifier à la source avant de décider. Note la source dans PLAN.md.

## 2. Plan
`PLAN.md` contient : le périmètre, les décisions d'architecture et leurs raisons, la pile technique avec versions vérifiées, et la liste des phases sous forme de cases à cocher. Chaque phase se termine par des tests verts.

## 3. Exécution
- Une phase à la fois. Après chaque phase : `uv run pytest -q`, correction, puis coche la case dans PLAN.md avec `edit_file`.
- Code lisible : noms explicites, fonctions courtes, docstrings pour ce qui n'est pas évident, aucun secret en dur, aucun TODO laissé sans ticket dans PLAN.md.
- Quand une bibliothèque est en cause, lis sa documentation officielle avec `web_fetch` plutôt que de deviner.

## 4. Livraison
Termine par un rapport : ce qui est livré, comment l'installer et le lancer, les tests et leur résultat, les limites connues, les étapes suivantes recommandées.
