"""Factures. Numérotation continue, jamais supprimées ; une facture émise ne change plus,
seuls son statut et sa date de paiement restent modifiables. PDF au format Factur-X,
relance des impayés par mail."""

from datetime import date, timedelta

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.framework import Champ, Module
from app.core.modeles import journaliser, lire_parametres
from app.core.web import Interdit, flash, rendre, utilisateur_connecte
from app.modules._documents import champs_totaux, lignes_standard
from app.modules._facturx import facture_facturx

STATUTS = ["Brouillon", "Émise", "Payée", "Annulée"]
RELANCE = ("Bonjour,\n\nSauf erreur de notre part, la facture {numero} du {date} d'un montant de {montant}, "
           "échue le {echeance}, reste impayée. Vous la trouverez ci-jointe.\n\nMerci de procéder à son règlement "
           "ou de nous contacter si elle a déjà été réglée.\n\nCordialement,\n{societe}")


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
        Champ("operation", "Nature de l'opération", type="choix", choix=["Prestation de services", "Livraison de biens", "Mixte"],
              defaut="Prestation de services", liste=False),
        Champ("statut", "Statut", type="choix", choix=STATUTS, defaut="Brouillon"),
        Champ("date_paiement", "Payée le", type="date", liste=False),
        Champ("relances", "Relances envoyées", type="entier", liste=False),
        Champ("derniere_relance", "Dernière relance", type="date", liste=False),
        *champs_totaux(),
    ],
    lignes=lignes_standard(),
    verrouille=lambda f: f.statut != "Brouillon",
    modifiable_si_verrouille=["statut", "date_paiement"],
    pdf=facture_facturx,
)


def impayees(db: Session):
    modele = module.modele
    return db.scalars(select(modele).where(modele.statut == "Émise", modele.echeance < date.today())
                      .order_by(modele.echeance)).all()


@module.router.get("/relances")
def relances(request: Request, db: Session = Depends(get_db), utilisateur=Depends(utilisateur_connecte)):
    if not module.peut_lire(utilisateur):
        raise Interdit()
    return rendre(request, db, "factures_relances.html", {"module": module, "factures": impayees(db),
                                                           "aujourdhui": date.today()}, utilisateur)


@module.router.post("/{objet_id:int}/relancer")
def relancer(request: Request, objet_id: int, db: Session = Depends(get_db), utilisateur=Depends(utilisateur_connecte)):
    from app.core.mail import envoyer

    if not module.peut_ecrire(utilisateur):
        raise Interdit()
    facture = db.get(module.modele, objet_id)
    if facture is None or facture.statut != "Émise":
        flash(request, "Seule une facture émise et non payée peut être relancée.", "erreur")
        return RedirectResponse("/factures/relances", status_code=303)
    parametres = lire_parametres(db)
    texte = (parametres.get("texte_relance") or RELANCE).format(
        numero=facture.numero, date=facture.date.strftime("%d/%m/%Y"),
        echeance=facture.echeance.strftime("%d/%m/%Y") if facture.echeance else "",
        montant=module.afficher(facture, module.champ("total_ttc")), societe=parametres.get("societe_nom") or "")
    destinataire = facture.client.email if facture.client else ""
    try:
        envoyer(db, destinataire or "", f"Relance : facture {facture.numero}", texte,
                [(f"facture-{facture.numero}.pdf", facture_facturx(module, facture, parametres), "application/pdf")])
    except ValueError as exc:
        flash(request, f"Relance de {facture.numero} : {exc}", "erreur")
        return RedirectResponse("/factures/relances", status_code=303)
    facture.relances = (facture.relances or 0) + 1
    facture.derniere_relance = date.today()
    journaliser(db, utilisateur, "relance", module.nom, facture.id, f"{facture.numero} → {destinataire}")
    db.commit()
    flash(request, f"Relance envoyée pour {facture.numero}.")
    return RedirectResponse("/factures/relances", status_code=303)
