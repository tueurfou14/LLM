"""Réglages de l'application. Le contenu du site (textes, coordonnées, couleurs) est dans contenu/site.json
et se modifie ensuite dans l'administration (/admin)."""

APP_TITLE = "__TITRE__"

# Rôles proposés aux utilisateurs de l'administration. « admin » a tous les droits
# (utilisateurs, journal, paramètres du site). « redaction » modifie les contenus et les textes.
ROLES = {
    "admin": "Administrateur",
    "redaction": "Rédaction",
}
