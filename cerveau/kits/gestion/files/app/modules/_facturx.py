"""Factur-X : la facture PDF porte aussi ses données en XML (norme EN 16931, profil BASIC),
lisibles par le logiciel du client et par les plateformes de facturation électronique.

Le XML est vérifié contre le schéma officiel (XSD) à la création. Les règles métier de la norme
(schematron) et la conformité PDF/A ne sont pas contrôlées ici : validez un exemplaire avec un
validateur Factur-X avant la mise en production, et transmettez via une plateforme agréée.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape

from app.modules._documents import CENTIME, _d, montant_ligne

PROFIL = "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic"


def _montant(valeur) -> str:
    return str(_d(valeur).quantize(CENTIME, ROUND_HALF_UP))


def _date(valeur) -> str:
    return valeur.strftime("%Y%m%d")


def _adresse(texte: str | None) -> str:
    """« 12 rue des Artisans\\n25300 Pontarlier » → éléments CII (ordre imposé par le schéma)."""
    lignes = [l.strip() for l in (texte or "").splitlines() if l.strip()]
    code, ville, rue = "", "", []
    for ligne in lignes:
        trouve = re.match(r"^(\d{5})\s+(.+)$", ligne)
        if trouve and not code:
            code, ville = trouve.groups()
        else:
            rue.append(ligne)
    xml = ""
    if code:
        xml += f"<ram:PostcodeCode>{escape(code)}</ram:PostcodeCode>"
    if rue:
        xml += f"<ram:LineOne>{escape(rue[0])}</ram:LineOne>"
    if ville:
        xml += f"<ram:CityName>{escape(ville)}</ram:CityName>"
    return f"<ram:PostalTradeAddress>{xml}<ram:CountryID>FR</ram:CountryID></ram:PostalTradeAddress>"


def _siren(texte: str | None) -> str:
    chiffres = re.sub(r"\D", "", texte or "")
    return chiffres[:9] if len(chiffres) >= 9 else ""


def _categorie(taux: Decimal) -> str:
    return "S" if taux > 0 else "Z"


def xml_facture(facture, parametres: dict[str, str]) -> bytes:
    lignes_xml, ventilation = [], {}
    for numero, ligne in enumerate(facture.lignes, start=1):
        taux = _d(ligne.taux_tva)
        montant = montant_ligne(ligne)
        ventilation[taux] = ventilation.get(taux, Decimal("0")) + montant
        lignes_xml.append(
            "<ram:IncludedSupplyChainTradeLineItem>"
            f"<ram:AssociatedDocumentLineDocument><ram:LineID>{numero}</ram:LineID></ram:AssociatedDocumentLineDocument>"
            f"<ram:SpecifiedTradeProduct><ram:Name>{escape(ligne.designation or '')}</ram:Name></ram:SpecifiedTradeProduct>"
            "<ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice>"
            f"<ram:ChargeAmount>{_montant(ligne.prix_unitaire)}</ram:ChargeAmount>"
            "</ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>"
            f"<ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode=\"C62\">{_d(ligne.quantite).normalize():f}"
            "</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>"
            "<ram:SpecifiedLineTradeSettlement><ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode>"
            f"<ram:CategoryCode>{_categorie(taux)}</ram:CategoryCode>"
            f"<ram:RateApplicablePercent>{taux.normalize():f}</ram:RateApplicablePercent></ram:ApplicableTradeTax>"
            "<ram:SpecifiedTradeSettlementLineMonetarySummation>"
            f"<ram:LineTotalAmount>{_montant(montant)}</ram:LineTotalAmount>"
            "</ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement>"
            "</ram:IncludedSupplyChainTradeLineItem>")
    taxes_xml, total_tva = [], Decimal("0")
    for taux, base in sorted(ventilation.items()):
        tva = (base * taux / 100).quantize(CENTIME, ROUND_HALF_UP)
        total_tva += tva
        taxes_xml.append(
            "<ram:ApplicableTradeTax>"
            f"<ram:CalculatedAmount>{_montant(tva)}</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode>"
            + ("" if taux > 0 else "<ram:ExemptionReason>Taux zéro</ram:ExemptionReason>")
            + f"<ram:BasisAmount>{_montant(base)}</ram:BasisAmount>"
            f"<ram:CategoryCode>{_categorie(taux)}</ram:CategoryCode>"
            f"<ram:RateApplicablePercent>{taux.normalize():f}</ram:RateApplicablePercent></ram:ApplicableTradeTax>")
    total_ht = sum(ventilation.values(), Decimal("0"))
    client = facture.client
    siren_vendeur, siren_client = _siren(parametres.get("societe_siret")), _siren(getattr(client, "siren", ""))
    vendeur = (f"<ram:SellerTradeParty><ram:Name>{escape(parametres.get('societe_nom') or '')}</ram:Name>"
               + (f'<ram:SpecifiedLegalOrganization><ram:ID schemeID="0002">{siren_vendeur}</ram:ID>'
                  "</ram:SpecifiedLegalOrganization>" if siren_vendeur else "")
               + _adresse(parametres.get("societe_adresse"))
               + (f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{escape(parametres["societe_tva"].replace(" ", ""))}'
                  "</ram:ID></ram:SpecifiedTaxRegistration>" if parametres.get("societe_tva") else "")
               + "</ram:SellerTradeParty>")
    acheteur = (f"<ram:BuyerTradeParty><ram:Name>{escape(client.nom if client else '')}</ram:Name>"
                + (f'<ram:SpecifiedLegalOrganization><ram:ID schemeID="0002">{siren_client}</ram:ID>'
                   "</ram:SpecifiedLegalOrganization>" if siren_client else "")
                + _adresse(getattr(client, "adresse", "")) + "</ram:BuyerTradeParty>")
    echeance = (f"<ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><udt:DateTimeString format=\"102\">"
                f"{_date(facture.echeance)}</udt:DateTimeString></ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>"
                if facture.echeance else "")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
        'xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100" '
        'xmlns:qdt="urn:un:unece:uncefact:data:standard:QualifiedDataType:100" '
        'xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">'
        "<rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter>"
        f"<ram:ID>{PROFIL}</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>"
        f"<rsm:ExchangedDocument><ram:ID>{escape(facture.numero or '')}</ram:ID><ram:TypeCode>380</ram:TypeCode>"
        f"<ram:IssueDateTime><udt:DateTimeString format=\"102\">{_date(facture.date)}</udt:DateTimeString>"
        "</ram:IssueDateTime></rsm:ExchangedDocument>"
        "<rsm:SupplyChainTradeTransaction>" + "".join(lignes_xml)
        + "<ram:ApplicableHeaderTradeAgreement>" + vendeur + acheteur + "</ram:ApplicableHeaderTradeAgreement>"
        "<ram:ApplicableHeaderTradeDelivery/>"
        "<ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>"
        + "".join(taxes_xml) + echeance
        + "<ram:SpecifiedTradeSettlementHeaderMonetarySummation>"
        f"<ram:LineTotalAmount>{_montant(total_ht)}</ram:LineTotalAmount>"
        f"<ram:TaxBasisTotalAmount>{_montant(total_ht)}</ram:TaxBasisTotalAmount>"
        f"<ram:TaxTotalAmount currencyID=\"EUR\">{_montant(total_tva)}</ram:TaxTotalAmount>"
        f"<ram:GrandTotalAmount>{_montant(total_ht + total_tva)}</ram:GrandTotalAmount>"
        f"<ram:DuePayableAmount>{_montant(total_ht + total_tva)}</ram:DuePayableAmount>"
        "</ram:SpecifiedTradeSettlementHeaderMonetarySummation></ram:ApplicableHeaderTradeSettlement>"
        "</rsm:SupplyChainTradeTransaction></rsm:CrossIndustryInvoice>")
    return xml.encode("utf-8")


def facture_facturx(module, facture, parametres: dict[str, str]) -> bytes:
    """PDF de la facture avec son XML Factur-X intégré (vérifié contre le schéma XSD)."""
    from facturx import generate_from_binary

    from app.core.pdf import document_pdf

    pdf = document_pdf(module, facture, parametres)
    return generate_from_binary(pdf, xml_facture(facture, parametres), flavor="factur-x", level="basic",
                                check_xsd=True, lang="fr-FR",
                                pdf_metadata={"author": parametres.get("societe_nom") or "",
                                              "title": f"Facture {facture.numero}",
                                              "subject": f"Facture {facture.numero}", "keywords": "Factur-X"})
