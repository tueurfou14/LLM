"""PDF, envoi par mail, relances d'impayés et Factur-X."""

from datetime import date, timedelta

import pytest

from app.core import mail
from app.core.framework import REGISTRE, creer_exemple


class FauxSMTP:
    envoyes: list = []

    def __init__(self, hote, port, timeout=None, context=None):
        self.hote = hote

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        pass

    def login(self, utilisateur, mot_de_passe):
        pass

    def send_message(self, message):
        FauxSMTP.envoyes.append(message)


@pytest.fixture
def smtp(http, monkeypatch):
    FauxSMTP.envoyes = []
    monkeypatch.setattr(mail.smtplib, "SMTP", FauxSMTP)
    http.post("/parametres", data={
        "societe_nom": "Garage Dupont", "societe_adresse": "12 rue des Artisans\n25300 Pontarlier",
        "societe_siret": "123 456 789 00012", "societe_tva": "FR12123456789",
        "smtp_hote": "smtp.exemple.fr", "smtp_port": "587", "smtp_utilisateur": "contact@garage.fr",
        "smtp_mot_de_passe": "secret-smtp", "smtp_expediteur": "contact@garage.fr"})
    return FauxSMTP


def _facture(http, db, statut="Émise", echeance=None):
    client = REGISTRE["clients"].modele(nom="Transports Martin", email="compta@martin.fr", siren="987654321",
                                        adresse="4 zone des Grands Prés\n25300 Doubs")
    db.add(client)
    db.commit()
    adresse = http.post("/factures/nouveau", data={
        "date": "2026-10-01", "echeance": echeance or "2026-10-31", "client": client.id, "statut": statut,
        "lignes-0-designation": "Révision", "lignes-0-quantite": "3,5", "lignes-0-prix_unitaire": "68",
        "lignes-0-taux_tva": "20",
        "lignes-1-designation": "Livre technique", "lignes-1-quantite": "1", "lignes-1-prix_unitaire": "30",
        "lignes-1-taux_tva": "5.5"}, follow_redirects=False).headers["location"]
    return adresse


def test_pdf_d_un_devis(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    adresse = http.post("/devis/nouveau", data={"date": "2026-10-11", "client": client.id,
                                                "lignes-0-designation": "Pièce", "lignes-0-prix_unitaire": "10"},
                        follow_redirects=False).headers["location"]
    reponse = http.get(f"{adresse}/pdf")
    assert reponse.status_code == 200 and reponse.content.startswith(b"%PDF")


def test_facture_au_format_facturx(http, db, smtp):
    from facturx import get_xml_from_pdf

    adresse = _facture(http, db)
    reponse = http.get(f"{adresse}/pdf")
    assert reponse.status_code == 200
    nom, xml = get_xml_from_pdf(reponse.content, check_xsd=True)
    assert nom == "factur-x.xml"
    texte = xml.decode()
    for attendu in ("<ram:ID>F-", "<ram:GrandTotalAmount>317.25</ram:GrandTotalAmount>", ">123456789<", ">987654321<",
                    "<ram:PostcodeCode>25300</ram:PostcodeCode>", "<ram:RateApplicablePercent>5.5"):
        assert attendu in texte, attendu


def test_envoi_par_mail(http, db, smtp):
    adresse = _facture(http, db)
    page = http.get(f"{adresse}/envoyer")
    assert page.status_code == 200 and "compta@martin.fr" in page.text
    reponse = http.post(f"{adresse}/envoyer", data={"a": "compta@martin.fr", "sujet": "Votre facture",
                                                    "texte": "Bonjour"}, follow_redirects=False)
    assert reponse.status_code == 303
    message = smtp.envoyes[0]
    assert message["To"] == "compta@martin.fr"
    pieces = list(message.iter_attachments())
    assert pieces and pieces[0].get_content_type() == "application/pdf"


def test_envoi_sans_configuration(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    adresse = http.post("/devis/nouveau", data={"date": "2026-10-11", "client": client.id},
                        follow_redirects=False).headers["location"]
    reponse = http.post(f"{adresse}/envoyer", data={"a": "x@y.fr", "sujet": "s", "texte": "t"})
    assert reponse.status_code == 422 and "pas configuré" in reponse.text


def test_mot_de_passe_smtp_jamais_reaffiche(http, smtp):
    assert "secret-smtp" not in http.get("/parametres").text
    http.post("/parametres", data={"societe_nom": "Garage Dupont", "smtp_hote": "smtp.exemple.fr",
                                   "smtp_expediteur": "contact@garage.fr"})
    from app.core.database import SessionLocal
    from app.core.modeles import lire_parametres

    with SessionLocal() as session:
        assert lire_parametres(session)["smtp_mot_de_passe"] == "secret-smtp"


def test_relance_des_impayes(http, db, smtp):
    hier = (date.today() - timedelta(days=1)).isoformat()
    adresse = _facture(http, db, echeance=hier)
    page = http.get("/factures/relances")
    assert page.status_code == 200 and "Transports Martin" in page.text
    ident = int(adresse.rsplit("/", 1)[1])
    assert http.post(f"/factures/{ident}/relancer", follow_redirects=False).status_code == 303
    assert "Relance : facture F-" in smtp.envoyes[0]["Subject"]
    facture = db.get(REGISTRE["factures"].modele, ident)
    db.refresh(facture)
    assert facture.relances == 1 and facture.derniere_relance == date.today()


def test_facture_payee_n_est_pas_relancee(http, db, smtp):
    adresse = _facture(http, db, statut="Payée", echeance="2026-01-01")
    assert "Aucune facture impayée" in http.get("/factures/relances").text
    http.post(f"/factures/{adresse.rsplit('/', 1)[1]}/relancer")
    assert smtp.envoyes == []
