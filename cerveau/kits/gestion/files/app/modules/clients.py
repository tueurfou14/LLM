"""Clients."""

from app.core.framework import Champ, Module

module = Module(
    nom="clients",
    libelle="Clients",
    objet="client",
    ordre=10,
    titre="nom",
    champs=[
        Champ("nom", "Nom", requis=True),
        Champ("email", "Email", type="email"),
        Champ("telephone", "Téléphone", type="telephone"),
        Champ("adresse", "Adresse", type="texte_long", liste=False),
        Champ("notes", "Notes", type="texte_long", liste=False),
    ],
)
