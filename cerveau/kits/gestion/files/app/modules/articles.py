"""Catalogue des articles et prestations."""

from app.core.framework import Champ, Module
from app.modules._documents import TAUX_TVA

module = Module(
    nom="articles",
    libelle="Catalogue",
    objet="article",
    ordre=20,
    titre="designation",
    champs=[
        Champ("reference", "Référence"),
        Champ("designation", "Désignation", requis=True),
        Champ("prix_unitaire", "Prix unitaire HT", type="monnaie"),
        Champ("taux_tva", "TVA %", type="choix", choix=TAUX_TVA, defaut="20"),
        Champ("unite", "Unité", aide="h, u, m², forfait…"),
    ],
)
