"""Factures. Numérotation continue, jamais supprimées ; une facture émise ne change plus,
seuls son statut et sa date de paiement restent modifiables."""

from datetime import date, timedelta

from app.core.framework import Champ, Module
from app.modules._documents import champs_totaux, lignes_standard

STATUTS = ["Brouillon", "Émise", "Payée", "Annulée"]


def dans_30_jours():
    return date.today() + timedelta(days=30)


module = Module(
    nom="factures",
    libelle="Factures",
    feminin=True,
    objet="facture",
    ordre=40,
    imprimable=True,
    suppression=False,
    champs=[
        Champ("numero", "N°", type="numero", format="F-{annee}-{n:04d}"),
        Champ("date", "Date", type="date", requis=True, defaut=date.today),
        Champ("echeance", "Échéance", type="date", defaut=dans_30_jours),
        Champ("client", "Client", type="ref", ref="clients", requis=True),
        Champ("objet", "Objet"),
        Champ("statut", "Statut", type="choix", choix=STATUTS, defaut="Brouillon"),
        Champ("date_paiement", "Payée le", type="date", liste=False),
        *champs_totaux(),
    ],
    lignes=lignes_standard(),
    verrouille=lambda f: f.statut != "Brouillon",
    modifiable_si_verrouille=["statut", "date_paiement"],
)
