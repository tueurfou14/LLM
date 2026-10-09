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
| `cerveau/agent.py` | La boucle : rappel, modèle, outils, mémorisation | |

Changer de modèle revient à changer une ligne de configuration. Le cerveau,
lui, ne change pas.

## Installation

```bash
pip install -e ".[dev]"
```

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
```

Le dossier passé à `chat` ou `audit` est la racine de travail : le modèle
lit, crée et modifie des fichiers uniquement dedans. S'il n'existe pas,
`cerveau` propose de le créer. Toute commande que le modèle veut exécuter
(tests, installation, build) vous est soumise avant de tourner.

La configuration est écrite au premier lancement dans `~/.cerveau/config.json`.
Les variables `CERVEAU_BASE_URL`, `CERVEAU_MODEL` et `CERVEAU_EMBEDDING_MODEL`
la surchargent.

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
