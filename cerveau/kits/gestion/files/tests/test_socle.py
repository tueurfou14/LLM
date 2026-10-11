"""Socle : connexion, droits, utilisateurs, journal, paramètres, numérotation, documents."""

from decimal import Decimal

from app.core.framework import REGISTRE, creer_exemple
from app.core.modeles import Journal


def test_sante(anonyme):
    assert anonyme.get("/sante").json() == {"ok": True}


def test_mauvais_mot_de_passe(anonyme):
    reponse = anonyme.post("/connexion", data={"identifiant": "admin", "mot_de_passe": "faux"})
    assert reponse.status_code == 401 and "incorrect" in reponse.text


def test_freinage_apres_cinq_echecs(anonyme):
    for _ in range(5):
        anonyme.post("/connexion", data={"identifiant": "admin", "mot_de_passe": "faux"})
    reponse = anonyme.post("/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe"})
    assert "Réessayez dans une minute" in reponse.text


def test_redirection_apres_connexion_reste_sur_le_site(anonyme):
    reponse = anonyme.post("/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe",
                                               "suite": "//exemple.com"}, follow_redirects=False)
    assert reponse.headers["location"] == "/"


def test_tableau_de_bord(http):
    assert http.get("/").status_code == 200


def test_utilisateur_sans_role_admin(http, anonyme):
    http.post("/utilisateurs/nouveau", data={"identifiant": "marc", "mot_de_passe": "motdepasse-marc",
                                             "roles": "terrain", "actif": "on"})
    http.get("/deconnexion")
    anonyme.post("/connexion", data={"identifiant": "marc", "mot_de_passe": "motdepasse-marc"})
    assert anonyme.get("/").status_code == 200
    assert anonyme.get("/utilisateurs").status_code == 403
    assert anonyme.get("/parametres").status_code == 403


def test_pages_administration(http):
    for page in ("/utilisateurs", "/utilisateurs/nouveau", "/journal", "/parametres", "/mon-compte"):
        assert http.get(page).status_code == 200, page


def test_parametres_enregistres(http):
    http.post("/parametres", data={"societe_nom": "Garage Dupont", "societe_siret": "123 456 789 00012"})
    assert "Garage Dupont" in http.get("/").text


def test_changement_de_mot_de_passe(http):
    reponse = http.post("/mon-compte", data={"actuel": "admin-mot-de-passe", "nouveau": "nouveau-mot-de-passe",
                                             "confirmation": "nouveau-mot-de-passe"}, follow_redirects=False)
    assert reponse.status_code == 303


def test_journal_des_actions(http, db):
    http.post("/clients/nouveau", data={"nom": "Garage Dupont"})
    assert db.query(Journal).filter_by(action="création", module="clients").count() == 1


def test_numerotation_continue_des_factures(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    numeros = []
    for _ in range(2):
        adresse = http.post("/factures/nouveau", data={"date": "2026-10-11", "client": client.id},
                            follow_redirects=False).headers["location"]
        numeros.append(db.get(REGISTRE["factures"].modele, int(adresse.rsplit("/", 1)[1])).numero)
    assert numeros[0].endswith("-0001") and numeros[1].endswith("-0002")


def test_facture_jamais_supprimable_et_verrouillee_une_fois_emise(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    adresse = http.post("/factures/nouveau", data={
        "date": "2026-10-11", "client": client.id, "statut": "Émise",
        "lignes-0-designation": "Vidange", "lignes-0-quantite": "1", "lignes-0-prix_unitaire": "80",
        "lignes-0-taux_tva": "20"}, follow_redirects=False).headers["location"]
    assert http.post(f"{adresse}/supprimer").status_code in (404, 405)
    http.post(adresse, data={"statut": "Payée", "lignes-0-designation": "Autre", "lignes-0-prix_unitaire": "1"})
    facture = db.get(REGISTRE["factures"].modele, int(adresse.rsplit("/", 1)[1]))
    db.refresh(facture)
    assert facture.statut == "Payée"
    assert [l.designation for l in facture.lignes] == ["Vidange"]


def test_devis_transforme_en_facture_avec_totaux(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    adresse = http.post("/devis/nouveau", data={
        "date": "2026-10-11", "client": client.id,
        "lignes-0-designation": "Main d'œuvre", "lignes-0-quantite": "2", "lignes-0-prix_unitaire": "45,50",
        "lignes-0-taux_tva": "20",
        "lignes-1-designation": "Pièce", "lignes-1-quantite": "1", "lignes-1-prix_unitaire": "100",
        "lignes-1-taux_tva": "20"}, follow_redirects=False).headers["location"]
    reponse = http.post(f"{adresse}/action/facturer", follow_redirects=False)
    facture = db.get(REGISTRE["factures"].modele, int(reponse.headers["location"].rsplit("/", 1)[1]))
    module = REGISTRE["factures"]
    assert module.valeur(facture, module.champ("total_ht")) == Decimal("191.00")
    assert module.valeur(facture, module.champ("total_ttc")) == Decimal("229.20")
    assert "229,20" in http.get(f"/factures/{facture.id}/imprimer").text


def test_client_utilise_non_supprimable(http, db):
    client = creer_exemple(REGISTRE["clients"], db)
    http.post("/devis/nouveau", data={"date": "2026-10-11", "client": client.id})
    reponse = http.post(f"/clients/{client.id}/supprimer", follow_redirects=True)
    assert "Suppression impossible" in reponse.text
    assert db.get(REGISTRE["clients"].modele, client.id) is not None


def test_saisie_invalide_reaffiche_le_formulaire(http):
    reponse = http.post("/clients/nouveau", data={"nom": "X", "email": "pas-un-email"})
    assert reponse.status_code == 422 and "adresse email invalide" in reponse.text
