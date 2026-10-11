"""Réglages de l'application. Les coordonnées de la société se règlent dans l'application (Paramètres)."""

APP_TITLE = "__TITRE__"

# Rôles proposés aux utilisateurs. « admin » a tous les droits.
# Un module limite l'accès avec lecture=[...] et ecriture=[...] (noms de rôles ci-dessous).
ROLES = {
    "admin": "Administrateur",
    "gestion": "Gestion",
    "terrain": "Terrain",
}
