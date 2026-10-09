# Démarrer un nouveau projet
mots-clés: créer, nouveau, projet, structure, initialiser, démarrer, squelette, application, erp, logiciel, dossier
description: Mettre en place la structure d'un projet depuis un dossier vide.

Tu travailles dans le dossier de travail courant, qui est la racine du projet. Tu ne peux pas écrire ailleurs : si l'utilisateur demande un dossier hors de cette racine (par exemple C:\Dev ou /home/x), explique-lui de relancer `cerveau chat <ce dossier>` et continue dans la racine actuelle.

Procédure :
1. Commence par `list_files` pour voir si le dossier est vide ou contient déjà du code.
2. Décide la pile technique avec ce que l'utilisateur a dit. Par défaut : Python 3.12, FastAPI pour une API, SQLite pour la base, pytest pour les tests. Annonce ce choix en une ligne.
3. Crée la structure avec `create_directory` puis `write_file`, dans cet ordre :
   - `README.md` : but du projet, comment installer, comment lancer, comment tester.
   - `pyproject.toml` ou `requirements.txt` avec les dépendances.
   - `.gitignore` adapté au langage.
   - un dossier source avec le nom du projet, contenant `__init__.py`.
   - un dossier `tests/` avec un premier test qui passe.
4. Écris un premier module qui fonctionne réellement, pas un fichier vide. Pour un ERP : commence par le module le plus central, souvent les clients ou les produits, avec son modèle de données, ses opérations de base et ses tests.
5. Propose `run_command` pour installer les dépendances puis lancer les tests. Attends la confirmation de l'utilisateur.
6. Termine par un résumé : ce qui existe, comment le lancer, et les trois prochaines étapes que tu proposes.

Un projet complet se construit module par module. Ne tente jamais de tout écrire en une seule réponse : fais une étape solide, vérifie-la, puis demande à l'utilisateur s'il veut continuer avec la suivante.
