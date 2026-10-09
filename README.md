# Cerveau

Noyau de mémoire et d'orchestration pour un assistant de code local, capable
d'auditer la sécurité d'un projet. Il tourne sur Mac et Windows, parle à
n'importe quel modèle servi par LM Studio ou Ollama, et ne dépend d'aucune
bibliothèque externe.

## Principe

Le modèle de langage fait le raisonnement. Il reste petit pour rester fluide.
Tout le reste vit à côté de lui et ne dépend pas de lui :

| Brique | Rôle | Où |
|---|---|---|
| `cerveau/hardware.py` | Sonde la machine et choisit le palier de modèle | RAM, GPU, mémoire unifiée |
| `cerveau/llm.py` | Client OpenAI-compatible avec streaming et appels d'outils | LM Studio ou Ollama |
| `cerveau/memory.py` | Mémoire de projet : épisodes et faits, recherche vectorielle et plein texte | SQLite |
| `cerveau/tools/` | Outils que le modèle appelle : lecture, écriture et édition de fichiers, commandes avec confirmation, Semgrep, gitleaks, Trivy | Projet et scanners locaux |
| `cerveau/skills/` | Fiches de procédure en Markdown, injectées selon la demande : nouveau projet, API Python, audit de sécurité, tests | Les vôtres dans `~/.cerveau/skills/` |
| `cerveau/agent.py` | La boucle : rappel, skills, modèle, outils, mémorisation | |

Changer de modèle revient à changer une ligne de configuration. Le cerveau,
lui, ne change pas.

## Installation

```bash
uv venv --python 3.12
source .venv/bin/activate      # Windows : .venv\Scripts\activate
uv pip install -e ".[dev]"
```

Après un `git pull`, relancez `uv pip install -e ".[dev]"` si les dépendances
ont changé.

Puis un serveur d'inférence :

- **Mac Apple Silicon** : [LM Studio](https://lmstudio.ai), moteur MLX. Chargez
  `qwen2.5-coder-7b-instruct` et `text-embedding-nomic-embed-text-v1.5`, et
  démarrez le serveur local (port 1234).
- **Windows ou Linux** : [Ollama](https://ollama.com), puis
  `ollama pull qwen2.5-coder:7b` et `ollama pull nomic-embed-text`.

Scanners de sécurité, optionnels mais recommandés :

```bash
pip install semgrep
brew install gitleaks trivy        # Mac
winget install gitleaks trivy      # Windows
```

## Utilisation

```bash
cerveau info                 # matériel détecté et modèle conseillé
cerveau check                # le serveur répond-il, les modèles sont-ils chargés
cerveau chat ./mon-projet    # dialogue, développement et audit sur ce dossier
cerveau audit ./mon-projet   # audit de sécurité guidé
cerveau memory ./mon-projet  # souvenirs enregistrés
cerveau note ./mon-projet "On utilise SQLAlchemy, jamais de SQL brut."
cerveau model                # modèle actif et modèles disponibles sur le serveur
cerveau model qwen3-coder:30b   # change de modèle principal
cerveau use lmstudio         # bascule le serveur (ollama ou lmstudio)
cerveau bench qwen2.5-coder:7b qwen3-coder:30b   # compare les débits réels
cerveau skills               # procédures disponibles
cerveau context 32768        # taille de contexte, variante Ollama créée au besoin
```

`CERVEAU_DEBUG=1` affiche les skills retenus et les réponses brutes du modèle,
utile quand il n'appelle pas un outil alors qu'il le devrait.

## Skills

Un skill est un fichier Markdown avec un titre, une ligne `mots-clés:` et une
ligne `description:`, suivis de la procédure. Quand une demande contient ces
mots-clés, la procédure est ajoutée à la consigne du modèle. Un petit modèle
suit bien une marche à suivre explicite. Ajoutez les vôtres dans
`~/.cerveau/skills/` ; un fichier du même nom qu'un skill livré le remplace.

Le dossier passé à `chat` ou `audit` est la racine de travail : le modèle
lit, crée et modifie des fichiers uniquement dedans. S'il n'existe pas,
`cerveau` propose de le créer. Toute commande que le modèle veut exécuter
(tests, installation, build) vous est soumise avant de tourner.

La configuration est écrite au premier lancement dans `~/.cerveau/config.json`.
Les variables `CERVEAU_BASE_URL`, `CERVEAU_MODEL` et `CERVEAU_EMBEDDING_MODEL`
la surchargent.

## Contexte et longues sessions

Il n'y a aucun quota : tout tourne en local. La seule limite est la fenêtre
de contexte du modèle, c'est-à-dire la quantité de conversation qu'il voit à
la fois. Trois mécanismes la gèrent :

- **`cerveau context 32768`** fixe la taille. Avec Ollama, la commande crée
  une variante du modèle (`qwen3-coder:30b-ctx32k`) servie avec ce contexte,
  car Ollama applique sinon son défaut, souvent 4096, et tronque en silence.
  `cerveau check` avertit quand c'est le cas.
- **Compaction automatique** : avant chaque appel, la conversation est ramenée
  sous le budget en raccourcissant les anciens résultats d'outils puis en
  retirant les plus vieux échanges. La consigne et le dernier message restent
  toujours entiers.
- **Mémoire de projet** : ce qui a été décidé et fait est mémorisé dans SQLite
  et rappelé selon la demande, même après `/clear` ou un redémarrage.

Ordres de grandeur du cache de contexte, en plus du modèle lui-même :

| Modèle | 16k tokens | 32k tokens |
|---|---|---|
| Qwen2.5-Coder 7B | 0,9 Go | 1,8 Go |
| Qwen3-Coder 30B-A3B | 1,6 Go | 3,2 Go |

Deux variables d'environnement d'Ollama réduisent ce coût de moitié :
`OLLAMA_FLASH_ATTENTION=1` et `OLLAMA_KV_CACHE_TYPE=q8_0`, à définir avant de
relancer Ollama.

## Console

`cerveau chat <dossier>` ouvre une console interactive :

- le texte du modèle s'affiche au fil des tokens, avec le débit en direct,
  puis est rendu en Markdown avec coloration des blocs de code ;
- chaque appel d'outil apparaît au moment où il part, avec un chrono pendant
  l'exécution et son résultat ;
- un fichier créé est montré avec coloration syntaxique, un fichier modifié
  avec son diff avant/après ;
- une commande que le modèle veut exécuter est affichée et attend votre `o` ;
  si elle échoue, sa sortie est montrée ;
- la réflexion des modèles qui raisonnent est repliée en une ligne,
  dépliable avec `/verbose` ;
- après chaque réponse : tokens de contexte, tokens générés, débit, durée,
  nombre d'outils et jauge de remplissage du contexte. Un `~` devant les
  compteurs signale une estimation, quand le serveur ne fournit pas `usage` ;
- à la sortie, un bilan de la session avec les fichiers touchés.

Saisie : flèches haut/bas pour l'historique, Tab complète les commandes,
Alt+Entrée ajoute une ligne. Commandes : `/help`, `/note`, `/memory`,
`/model [nom]`, `/skills`, `/files`, `/audit`, `/verbose`, `/clear`, `/stats`,
`/quit`. Ctrl+C interrompt une réponse en cours. `cerveau chat --plain` donne
le mode texte simple.

## Paliers de modèle

| Budget mémoire pour le modèle | Palier | Modèle | Débit attendu |
|---|---|---|---|
| 22 Go et plus | large | Qwen2.5-Coder 32B | 15 à 40 tok/s |
| 12 à 22 Go | medium | Qwen2.5-Coder 14B | 10 à 25 tok/s |
| 5 à 12 Go | small | Qwen2.5-Coder 7B | 20 à 60 tok/s |
| moins de 5 Go | tiny | Qwen2.5-Coder 1.5B | variable |

Un MacBook Air M3 16 Go tombe dans le palier `small`.

## Cadre d'usage des outils de sécurité

Les scanners servent à auditer du code que vous avez le droit d'auditer :
vos projets ou ceux pour lesquels vous avez un mandat. Ils trouvent et
expliquent des failles pour les corriger.

## Tests

```bash
pytest
```

Les tests n'ont besoin d'aucun serveur d'inférence : l'agent est testé avec un
faux client.

## Suite prévue

- Bac à sable Docker pour exécuter les tests du projet audité.
- Modèle brouillon pour le décodage spéculatif.
- Consolidation des souvenirs : fusion, oubli, détection de contradictions.
- Adaptateurs LoRA par domaine, chargés selon la tâche.
