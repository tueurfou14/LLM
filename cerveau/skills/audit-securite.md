# Audit de sécurité d'un projet
mots-clés: audit, sécurité, faille, vulnérabilité, injection, owasp, secret, mot de passe, xss, csrf, cve, scanner, semgrep, pentest, sécuriser
description: Auditer le code du projet de façon méthodique et proposer des correctifs.

Cadre : tu audites le code du projet courant pour le corriger. Tu expliques les failles et tu proposes des correctifs, tu ne produis pas d'outil d'attaque contre des systèmes tiers.

Procédure :
1. `scanners_status` pour savoir quels scanners sont installés. S'il en manque, dis à l'utilisateur lesquels et comment les installer, puis continue avec ce qui existe.
2. Lance dans l'ordre : `secrets_scan` (secrets commis), `dependencies_scan` (CVE dans les dépendances), `semgrep_scan` (analyse statique du code).
3. Sans scanner, fais une revue manuelle ciblée avec `search_code` sur les motifs à risque :
   - injections : `execute(`, `cursor`, `f"SELECT`, `os.system`, `subprocess` avec `shell=True`, `eval(`, `exec(`
   - secrets : `password`, `secret`, `api_key`, `token`, chaînes longues en base64
   - fichiers : `open(` avec un chemin venant de l'utilisateur, `../`
   - web : `render_template_string`, `|safe`, `dangerouslySetInnerHTML`, CORS avec `*`
   - crypto : `md5`, `sha1`, `random.random` pour des jetons, `verify=False`
4. Pour chaque résultat, lis le code concerné avec `read_file` et décide : faille confirmée, faux positif, ou à vérifier. Ne rapporte jamais un résultat de scanner sans l'avoir lu.
5. Rapport final, classé par gravité (critique, haute, moyenne, basse) :
   - fichier et ligne
   - ce qui est vulnérable et comment ça s'exploite, en deux phrases
   - le correctif, avec le code corrigé
6. Propose d'appliquer les correctifs les plus graves avec `edit_file`, un par un, et de relancer les tests.
7. Mémorise ce qui a été trouvé et corrigé pour les prochains audits.
