"""Services proposés : page /services et accueil."""

from app.core.framework import Champ, Module

module = Module(
    nom="services",
    libelle="Services",
    objet="service",
    ordre=10,
    titre="titre",
    tri="position",
    champs=[
        Champ("titre", "Titre", requis=True),
        Champ("resume", "Résumé", type="texte_long", requis=True, aide="Deux lignes, affichées sur l'accueil."),
        Champ("detail", "Description complète", type="texte_long", liste=False,
              aide="Affichée sur la page Services. Une ligne vide sépare deux paragraphes."),
        Champ("prix", "Prix indicatif", aide="Texte libre, ex. « à partir de 49 € ». Laisser vide pour ne rien afficher."),
        Champ("image", "Photo", type="image", liste=False),
        Champ("position", "Position", type="entier", defaut=10,
              aide="Ordre d'affichage : les plus petits nombres d'abord."),
        Champ("publie", "Affiché sur le site", type="booleen", defaut=True),
    ],
)
