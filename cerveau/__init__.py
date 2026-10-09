"""Cerveau : noyau de mémoire et d'orchestration pour un assistant de code local.

Le noyau ne dépend d'aucun modèle en particulier. Il parle à un serveur
d'inférence local (LM Studio ou Ollama) via l'API compatible OpenAI, gère la
mémoire de projet dans SQLite, et expose des outils d'audit au modèle.
"""

__version__ = "0.1.0"
