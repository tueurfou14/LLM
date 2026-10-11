"""Réalisations (galerie de travaux) : page /realisations et accueil."""

from datetime import date

from app.core.framework import Champ, Module

module = Module(
    nom="realisations",
    libelle="Réalisations",
    objet="réalisation",
    feminin=True,
    ordre=20,
    titre="titre",
    tri="-date",
    champs=[
        Champ("titre", "Titre", requis=True),
        Champ("image", "Photo", type="image", aide="Sans photo, une illustration par défaut est affichée."),
        Champ("legende", "Légende", type="texte_long", liste=False),
        Champ("lieu", "Lieu"),
        Champ("date", "Date", type="date", defaut=date.today),
        Champ("publie", "Affichée sur le site", type="booleen", defaut=True),
    ],
)
