"""Devis, transformables en facture."""

from datetime import date

from app.core.framework import Action, Champ, Module
from app.modules._documents import champs_totaux, copier_document, lignes_standard


def facturer(devis, db, utilisateur):
    if not devis.lignes:
        raise ValueError("Le devis n'a aucune ligne.")
    facture = copier_document(devis, "factures", db, date=date.today(), statut="Brouillon")
    devis.statut = "Accepté"
    return f"/factures/{facture.id}"


module = Module(
    nom="devis",
    libelle="Devis",
    objet="devis",
    ordre=30,
    imprimable=True,
    champs=[
        Champ("numero", "N°", type="numero", format="D-{annee}-{n:04d}"),
        Champ("date", "Date", type="date", requis=True, defaut=date.today),
        Champ("client", "Client", type="ref", ref="clients", requis=True),
        Champ("objet", "Objet"),
        Champ("statut", "Statut", type="choix", choix=["Brouillon", "Envoyé", "Accepté", "Refusé"], defaut="Brouillon"),
        Champ("conditions", "Conditions", type="texte_long", liste=False),
        *champs_totaux(),
    ],
    lignes=lignes_standard(),
    actions=[Action("facturer", "Transformer en facture", facturer, visible=lambda d: d.statut != "Refusé")],
)
