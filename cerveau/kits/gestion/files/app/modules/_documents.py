"""Aides partagées des documents commerciaux (devis, factures, bons de commande…) :
lignes standard, totaux HT / TVA / TTC, copie d'un document vers un autre."""

from decimal import ROUND_HALF_UP, Decimal

from app.core.framework import REGISTRE, Champ, Lignes
from app.core.modeles import numero_suivant

TAUX_TVA = ["20", "10", "5.5", "2.1", "0"]
CENTIME = Decimal("0.01")


def _d(valeur) -> Decimal:
    return Decimal(str(valeur)) if valeur not in (None, "") else Decimal("0")


def montant_ligne(ligne) -> Decimal:
    return (_d(ligne.quantite) * _d(ligne.prix_unitaire)).quantize(CENTIME, ROUND_HALF_UP)


def total_ht(document) -> Decimal:
    return sum((montant_ligne(l) for l in document.lignes), Decimal("0"))


def total_tva(document) -> Decimal:
    tva = sum((montant_ligne(l) * _d(l.taux_tva) / 100 for l in document.lignes), Decimal("0"))
    return tva.quantize(CENTIME, ROUND_HALF_UP)


def total_ttc(document) -> Decimal:
    return total_ht(document) + total_tva(document)


def lignes_standard() -> Lignes:
    return Lignes("Lignes", [
        Champ("designation", "Désignation", requis=True),
        Champ("quantite", "Quantité", type="decimal", defaut=1),
        Champ("prix_unitaire", "Prix unitaire HT", type="monnaie"),
        Champ("taux_tva", "TVA %", type="choix", choix=TAUX_TVA, defaut="20"),
        Champ("montant", "Montant HT", type="calcul", calcul=montant_ligne, affichage="monnaie"),
    ])


def champs_totaux() -> list[Champ]:
    return [
        Champ("total_ht", "Total HT", type="calcul", calcul=total_ht, affichage="monnaie"),
        Champ("total_tva", "TVA", type="calcul", calcul=total_tva, affichage="monnaie", liste=False),
        Champ("total_ttc", "Total TTC", type="calcul", calcul=total_ttc, affichage="monnaie"),
    ]


def copier_document(source, cible: str, db, **valeurs):
    """Crée un document du module `cible` à partir de `source` : mêmes champs communs, mêmes lignes.
    Les numéros sont attribués selon le format du module cible."""
    module = REGISTRE[cible]
    noms_source = {c.colonne for c in REGISTRE[source.__tablename__].champs}
    document = module.modele()
    for champ in module.champs:
        if champ.saisissable and champ.colonne in noms_source:
            setattr(document, champ.colonne, getattr(source, champ.colonne))
        elif champ.type == "numero":
            setattr(document, champ.colonne, numero_suivant(db, f"{module.nom}.{champ.nom}", champ.format))
        elif champ.saisissable and champ.defaut is not None:
            setattr(document, champ.colonne, champ.valeur_defaut())
    for colonne, valeur in valeurs.items():
        setattr(document, colonne, valeur)
    colonnes = [c.colonne for c in module.lignes.champs if c.saisissable]
    for position, ligne in enumerate(source.lignes):
        document.lignes.append(module.modele_ligne(position=position,
                                                   **{c: getattr(ligne, c, None) for c in colonnes}))
    db.add(document)
    db.flush()
    return document
