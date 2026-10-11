"""Actualités : page /actualites, une page par actualité (/actualites/<numéro>-<titre>) et accueil."""

from datetime import date

from app.core.framework import Champ, Module
from app.core.web import slug

module = Module(
    nom="actualites",
    libelle="Actualités",
    objet="actualité",
    feminin=True,
    ordre=30,
    titre="titre",
    tri="-date",
    public=lambda a: f"/actualites/{a.id}-{slug(a.titre)}",
    champs=[
        Champ("titre", "Titre", requis=True),
        Champ("date", "Date", type="date", requis=True, defaut=date.today),
        Champ("image", "Photo", type="image", aide="Sans photo, une illustration par défaut est affichée."),
        Champ("chapo", "Résumé", type="texte_long", requis=True, liste=False,
              aide="Deux ou trois lignes, affichées dans la liste des actualités."),
        Champ("texte", "Texte", type="texte_long", liste=False, aide="Une ligne vide sépare deux paragraphes."),
        Champ("publie", "Publiée sur le site", type="booleen", defaut=True,
              aide="Décocher pour préparer une actualité sans la montrer."),
    ],
)
