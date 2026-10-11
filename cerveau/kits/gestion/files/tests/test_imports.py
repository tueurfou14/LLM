"""Import CSV et Excel : reconnaissance des colonnes, liens par nom, tout ou rien."""

import io

from openpyxl import Workbook

from app.core.framework import REGISTRE, creer_exemple
from sqlalchemy import func, select


def _nombre(db, module):
    return db.scalar(select(func.count()).select_from(REGISTRE[module].modele))


def test_import_csv_excel_francais(http, db):
    contenu = "Nom;Téléphone;EMAIL\nGarage Dupont;03 81 00 00 00;a@b.fr\nÉlodie Martin;;\n".encode("cp1252")
    reponse = http.post("/clients/importer", files={"fichier": ("clients.csv", contenu, "text/csv")},
                        follow_redirects=False)
    assert reponse.status_code == 303
    assert _nombre(db, "clients") == 2


def test_import_tout_ou_rien(http, db):
    contenu = "nom;email\nBon;ok@exemple.fr\n;pas-un-email\n".encode()
    reponse = http.post("/clients/importer", files={"fichier": ("c.csv", contenu, "text/csv")})
    assert reponse.status_code == 422
    assert "ligne 3, Nom : obligatoire" in reponse.text and "ligne 3, Email" in reponse.text
    assert _nombre(db, "clients") == 0


def test_import_lien_par_nom_et_date_francaise(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    contenu = f"Date;Client;Objet\n15/10/2026;{client.nom};Révision\n".encode()
    reponse = http.post("/devis/importer", files={"fichier": ("devis.csv", contenu, "text/csv")},
                        follow_redirects=False)
    assert reponse.status_code == 303, reponse.text[:2000]
    devis = db.scalars(select(REGISTRE["devis"].modele)).one()
    assert devis.client_id == client.id and devis.date.isoformat() == "2026-10-15" and devis.numero


def test_import_lien_introuvable(http, db):
    contenu = "Date;Client\n2026-10-15;Inconnu SARL\n".encode()
    reponse = http.post("/devis/importer", files={"fichier": ("devis.csv", contenu, "text/csv")})
    assert reponse.status_code == 422 and "Inconnu SARL" in reponse.text


def test_import_excel(http, db):
    classeur = Workbook()
    feuille = classeur.active
    feuille.append(["Désignation", "Prix unitaire HT", "TVA %"])
    feuille.append(["Vidange", 89.9, "20"])
    feuille.append(["Main d'œuvre", 68, "20"])
    tampon = io.BytesIO()
    classeur.save(tampon)
    reponse = http.post("/articles/importer", files={"fichier": ("articles.xlsx", tampon.getvalue(), "application/octet-stream")},
                        follow_redirects=False)
    assert reponse.status_code == 303
    assert _nombre(db, "articles") == 2


def test_pages_import(http):
    assert http.get("/clients/importer").status_code == 200
    modele = http.get("/clients/modele.csv")
    assert modele.status_code == 200 and "Nom;Email" in modele.text
