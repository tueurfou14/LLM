# Démarrer un nouveau projet
mots-clés: créer, nouveau, projet, structure, initialiser, démarrer, squelette, application, erp, logiciel, dossier, site
description: Mettre en place un projet depuis un dossier vide.

La racine du projet est ton dossier de travail. Ne crée jamais de sous-dossier au nom du projet. Si l'utilisateur veut un autre dossier, dis-lui de relancer `cerveau chat <ce dossier>`.

1. Regarde le « Contenu actuel du projet » de ta consigne : vide ou existant ?
2. Vide, et un kit correspond : `create_project` (gestion : ERP, CRM, stock, facturation, GMAO, planning ; site : site vitrine). Puis `verify_project`, puis `start_server` avec la commande donnée et l'adresse à l'utilisateur.
3. Vide, sans kit adapté : `pyproject.toml`, `.gitignore`, `README.md`, code dans `app/`, tests dans `tests/`, puis « uv run pytest -q ».
4. Termine par : ce qui existe, l'adresse, les trois prochaines étapes.

Une étape solide et vérifiée à la fois.
