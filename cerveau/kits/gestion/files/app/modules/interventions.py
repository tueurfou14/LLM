"""Interventions et rendez-vous, avec planning mensuel."""

from datetime import date

from app.core.framework import REGISTRE, Action, Champ, Module
from app.core.modeles import numero_suivant


def facturer(intervention, db, utilisateur):
    factures = REGISTRE["factures"]
    facture = factures.modele(date=date.today(), client_id=intervention.client_id, objet=intervention.titre,
                              statut="Brouillon",
                              numero=numero_suivant(db, "factures.numero", factures.champ("numero").format))
    facture.lignes.append(factures.modele_ligne(position=0, designation=intervention.titre, quantite=1,
                                                prix_unitaire=0, taux_tva="20"))
    db.add(facture)
    db.flush()
    intervention.statut = "Facturée"
    return f"/factures/{facture.id}"


module = Module(
    nom="interventions",
    libelle="Interventions",
    feminin=True,
    objet="intervention",
    ordre=50,
    planning="date",
    titre="titre",
    tri="-date",
    champs=[
        Champ("date", "Date", type="date", requis=True, defaut=date.today),
        Champ("heure", "Heure", aide="ex. 14h30"),
        Champ("client", "Client", type="ref", ref="clients", requis=True),
        Champ("titre", "Intitulé", requis=True),
        Champ("intervenant", "Intervenant"),
        Champ("statut", "Statut", type="choix",
              choix=["À planifier", "Planifiée", "En cours", "Terminée", "Facturée"], defaut="Planifiée"),
        Champ("compte_rendu", "Compte rendu", type="texte_long", liste=False),
    ],
    actions=[Action("facturer", "Créer la facture", facturer, visible=lambda i: i.statut == "Terminée")],
)
