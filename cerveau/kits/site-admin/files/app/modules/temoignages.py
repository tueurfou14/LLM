"""Témoignages de clients : section « Ils nous font confiance » de l'accueil.
N'y mettre que des avis réels, avec l'accord de leur auteur."""

from app.core.framework import Champ, Module

module = Module(
    nom="temoignages",
    libelle="Témoignages",
    objet="témoignage",
    ordre=50,
    titre="auteur",
    tri="position",
    champs=[
        Champ("auteur", "Auteur", requis=True, aide="Ex. « Marie D., Besançon »."),
        Champ("texte", "Avis", type="texte_long", requis=True),
        Champ("note", "Note", type="choix", choix=["5", "4", "3"], defaut="5"),
        Champ("position", "Position", type="entier", defaut=10),
        Champ("publie", "Affiché sur le site", type="booleen", defaut=False,
              aide="Cocher pour l'afficher. Seulement des avis réels, avec l'accord de leur auteur."),
    ],
)
