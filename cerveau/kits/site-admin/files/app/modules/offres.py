"""Offres du moment (promotions) : affichées sur l'accueil jusqu'à leur date de fin."""

from app.core.framework import Champ, Module

module = Module(
    nom="offres",
    libelle="Offres du moment",
    objet="offre",
    feminin=True,
    ordre=40,
    titre="titre",
    tri="position",
    champs=[
        Champ("titre", "Titre", requis=True),
        Champ("texte", "Texte", type="texte_long", liste=False),
        Champ("prix", "Prix ou remise", aide="Texte libre, ex. « -20 % » ou « 59 € au lieu de 79 € »."),
        Champ("image", "Photo", type="image", liste=False),
        Champ("fin", "Affichée jusqu'au", type="date", aide="Laisser vide pour une offre sans date de fin."),
        Champ("position", "Position", type="entier", defaut=10),
        Champ("publie", "Affichée sur le site", type="booleen", defaut=True),
    ],
)
