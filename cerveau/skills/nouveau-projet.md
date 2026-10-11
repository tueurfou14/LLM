# Démarrer un nouveau projet
mots-clés: créer, nouveau, projet, structure, initialiser, démarrer, squelette, application, erp, logiciel, dossier
description: Mettre en place la structure d'un projet depuis un dossier vide.

Tu travailles dans la racine du projet et nulle part ailleurs. Si l'utilisateur veut un autre dossier, dis-lui de relancer `cerveau chat <ce dossier>` et continue ici. Ne crée pas de sous-dossier au nom du projet : la racine est déjà le projet.

1. Regarde le « Contenu actuel du projet » de ta consigne : dossier vide ou code existant ?
2. Application de gestion ou application web (ERP, CRM, stock, facturation, réservation…) : `scaffold_web_app`, puis `run_command` « uv run pytest -q », puis `start_server`. La suite se fait module par module (skill application de gestion).
3. Autre projet Python : `pyproject.toml` (dépendances), `.gitignore`, `README.md` (installer, lancer, tester), le code dans `app/`, un premier test dans `tests/`, puis « uv run pytest -q ».
4. Termine par : ce qui existe, l'adresse ou la commande pour le lancer, les trois prochaines étapes.

Ne tente jamais de tout écrire en une seule réponse : une étape solide et vérifiée, puis la suivante.
