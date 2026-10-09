# Tester et corriger
mots-clés: test, tests, pytest, tester, bug, erreur, corriger, réparer, plante, exception, échoue, debug, déboguer
description: Reproduire un problème, le corriger et prouver la correction par un test.

Procédure :
1. Reproduis d'abord : lance la commande qui échoue avec `run_command` (souvent `uv run pytest -q` ou `uv run python <script>`). Lis la trace complète.
2. Localise : `search_code` sur le nom de la fonction ou le message d'erreur, puis `read_file` sur le fichier en cause. Ne modifie rien avant d'avoir lu le code.
3. Explique la cause en une ou deux phrases avant de corriger.
4. Corrige avec `edit_file`, en changeant le minimum nécessaire.
5. Ajoute ou adapte un test qui aurait échoué avant la correction et passe après.
6. Relance les tests avec `run_command` et montre le résultat. Si ça échoue encore, reprends à l'étape 2 avec la nouvelle trace, ne devine pas.
7. Résume : cause, correction, test ajouté.
