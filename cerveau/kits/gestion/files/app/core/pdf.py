"""PDF d'un document (devis, facture…) fabriqué côté serveur, pour l'envoi par mail.
Même contenu que la page d'impression : émetteur, destinataire, informations, lignes, totaux."""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.database import DATA

NOMBRES = ("monnaie", "decimal", "entier")


def _p(texte, style) -> Paragraph:
    return Paragraph(escape(str(texte or "")).replace("\n", "<br/>"), style)


def document_pdf(module, objet, parametres: dict[str, str]) -> bytes:
    from app.core.framework import REGISTRE, formater

    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontName="Helvetica", fontSize=9.5, leading=12.5)
    gras = ParagraphStyle("gras", parent=normal, fontName="Helvetica-Bold")
    discret = ParagraphStyle("discret", parent=normal, textColor=colors.HexColor("#697386"), fontSize=8.5)
    droite = ParagraphStyle("droite", parent=normal, alignment=TA_RIGHT)
    titre = ParagraphStyle("titre", parent=normal, fontName="Helvetica-Bold", fontSize=16, leading=20, spaceAfter=8)

    elements = []
    logo = next(iter((DATA / "uploads").glob("logo.*")), None) if (DATA / "uploads").exists() else None
    emetteur = []
    if logo is not None:
        try:
            image = Image(str(logo))
            ratio = min(45 * mm / image.imageWidth, 16 * mm / image.imageHeight)
            image.drawWidth, image.drawHeight = image.imageWidth * ratio, image.imageHeight * ratio
            image.hAlign = "LEFT"
            emetteur.append(image)
        except Exception:  # noqa: BLE001 - un logo illisible ne bloque pas le document
            pass
    emetteur += [_p(parametres.get("societe_nom"), gras), _p(parametres.get("societe_adresse"), normal),
                 _p(parametres.get("societe_telephone"), normal), _p(parametres.get("societe_email"), normal)]
    destinataire = []
    for champ in module.champs:
        cible = getattr(objet, champ.nom, None) if champ.type == "ref" else None
        if cible is not None:
            destinataire.append(_p(REGISTRE[champ.ref].libelle_objet(cible), gras))
            if getattr(cible, "adresse", None):
                destinataire.append(_p(cible.adresse, normal))
            if getattr(cible, "siren", None):
                destinataire.append(_p(f"SIREN {cible.siren}", discret))
    entete = Table([[emetteur, destinataire]], colWidths=[95 * mm, 85 * mm])
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#e3e6eb")),
                                ("LEFTPADDING", (1, 0), (1, 0), 8), ("TOPPADDING", (1, 0), (1, 0), 6)]))
    elements += [entete, Spacer(1, 10 * mm)]
    elements.append(_p(f"{module.objet.capitalize()} {module.libelle_objet(objet)}", titre))

    infos = [[_p(c.libelle, discret), _p(module.afficher(objet, c), normal)] for c in module.champs
             if c.type not in ("ref", "calcul", "numero", "texte_long") and module.valeur(objet, c) not in (None, "")]
    if infos:
        table = Table(infos, colWidths=[40 * mm, 140 * mm], hAlign="LEFT")
        table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                   ("TOPPADDING", (0, 0), (-1, -1), 1)]))
        elements += [table, Spacer(1, 6 * mm)]

    if module.lignes:
        champs = module.lignes.champs
        lignes = [[_p(c.libelle, gras) for c in champs]]
        for ligne in getattr(objet, module.lignes.nom):
            lignes.append([_p(formater(c, module.valeur(ligne, c)),
                              droite if c.type in NOMBRES or c.affichage in NOMBRES else normal) for c in champs])
        largeurs = [180 * mm - 25 * mm * (len(champs) - 1)] + [25 * mm] * (len(champs) - 1)
        table = Table(lignes, colWidths=largeurs, repeatRows=1)
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1c2230")),
                                   ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#e3e6eb")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements += [table, Spacer(1, 5 * mm)]

    calculs = [c for c in module.champs if c.type == "calcul"]
    droite_gras = ParagraphStyle("droite_gras", parent=droite, fontName="Helvetica-Bold")
    totaux = [[_p(c.libelle, gras if i == len(calculs) - 1 else normal),
               _p(module.afficher(objet, c), droite_gras if i == len(calculs) - 1 else droite)]
              for i, c in enumerate(calculs)]
    if totaux:
        table = Table(totaux, colWidths=[45 * mm, 35 * mm], hAlign="RIGHT")
        table.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#1c2230"))]))
        elements.append(table)
    for champ in module.champs:
        if champ.type == "texte_long" and module.valeur(objet, champ):
            elements += [Spacer(1, 5 * mm), _p(champ.libelle, gras), _p(module.valeur(objet, champ), normal)]

    pied = " · ".join(filter(None, [
        parametres.get("societe_nom"),
        f"SIRET {parametres['societe_siret']}" if parametres.get("societe_siret") else "",
        f"TVA {parametres['societe_tva']}" if parametres.get("societe_tva") else "",
        f"IBAN {parametres['societe_iban']}" if parametres.get("societe_iban") else ""]))

    def bas_de_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#697386"))
        y = 12 * mm
        for ligne in reversed([*(parametres.get("mentions_documents") or "").splitlines(), pied]):
            canvas.drawString(15 * mm, y, ligne[:160])
            y += 3.6 * mm
        if getattr(objet, "statut", None) == "Brouillon":
            canvas.setFont("Helvetica-Bold", 90)
            canvas.setFillColor(colors.Color(0.76, 0.25, 0.05, alpha=0.08))
            canvas.translate(105 * mm, 150 * mm)
            canvas.rotate(30)
            canvas.drawCentredString(0, 0, "BROUILLON")
        canvas.restoreState()

    tampon = io.BytesIO()
    document = SimpleDocTemplate(tampon, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                                 bottomMargin=28 * mm, title=f"{module.objet} {module.libelle_objet(objet)}",
                                 author=parametres.get("societe_nom") or "")
    document.build(elements, onFirstPage=bas_de_page, onLaterPages=bas_de_page)
    return tampon.getvalue()
