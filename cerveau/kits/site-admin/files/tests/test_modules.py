"""Tests génériques : chaque module de app/modules (et le module d'essai du moteur) est vérifié
automatiquement sous /admin, sans rien écrire : création, lecture, modification, liste, recherche, export,
impression, planning, actions, suppression, accès réservé."""

import pytest

from app.core.framework import REGISTRE, fichiers_exemple, formulaire_exemple

MODULES = sorted(REGISTRE.values(), key=lambda m: (m.ordre, m.nom))


def _creer(http, module, db):
    donnees = formulaire_exemple(module, db)
    reponse = http.post(f"{module.adresse}/nouveau", data=donnees, files=fichiers_exemple(module) or None,
                        follow_redirects=False)
    assert reponse.status_code == 303, (
        f"app/modules/{module.nom}.py : création refusée ({reponse.status_code}) avec {donnees} : "
        f"{reponse.text[:1500] if reponse.status_code != 500 else 'erreur 500, voir la trace ci-dessus'}")
    adresse = reponse.headers["location"]
    assert adresse.startswith(f"{module.adresse}/"), f"{module.nom} : après création, redirection hors de /admin : {adresse}"
    return donnees, adresse


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_pages_du_module(module, http, db):
    assert http.get(f"{module.adresse}/").status_code == 200, f"liste {module.adresse}/ en erreur"
    assert http.get(f"{module.adresse}/nouveau").status_code == 200, f"formulaire {module.adresse}/nouveau en erreur"
    donnees, adresse = _creer(http, module, db)
    fiche = http.get(adresse)
    assert fiche.status_code == 200, f"fiche {adresse} en erreur"
    liste = http.get(f"{module.adresse}/")
    assert liste.status_code == 200
    assert module.aucun not in liste.text, f"l'objet créé n'apparaît pas dans {module.adresse}/"
    assert http.get(f"{module.adresse}/", params={"q": "Exemple"}).status_code == 200
    export = http.get(f"{module.adresse}/export.csv")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    modif = http.post(adresse, data=donnees, follow_redirects=False)
    assert modif.status_code == 303, f"modification {adresse} refusée ({modif.status_code}) : {modif.text[:1500]}"
    assert modif.headers["location"] == adresse, f"{module.nom} : après modification, redirection vers {modif.headers['location']}"
    if module.imprimable:
        assert http.get(f"{adresse}/imprimer").status_code == 200, f"impression {adresse} en erreur"
    if module.planning:
        assert http.get(f"{module.adresse}/planning").status_code == 200, f"planning {module.adresse}/ en erreur"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_formulaire_envoie_les_images(module, http):
    page = http.get(f"{module.adresse}/nouveau").text
    if module.a_des_images:
        assert 'enctype="multipart/form-data"' in page, f"{module.nom} : le formulaire doit être en multipart/form-data"
        assert 'type="file"' in page


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_champs_obligatoires(module, http, db):
    if any(c.requis for c in module.champs):
        reponse = http.post(f"{module.adresse}/nouveau", data={}, follow_redirects=False)
        assert reponse.status_code == 422, f"{module.adresse}/nouveau accepte un formulaire vide"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_actions_et_suppression(module, http, db):
    _, adresse = _creer(http, module, db)
    objet = db.get(module.modele, int(adresse.rsplit("/", 1)[1]))
    for action in module.actions:
        if action.visible is None or action.visible(objet):
            reponse = http.post(f"{adresse}/action/{action.nom}", follow_redirects=True)
            assert reponse.status_code == 200, f"action {action.nom} de {module.nom} en erreur"
    if module.suppression:
        reponse = http.post(f"{adresse}/supprimer", follow_redirects=False)
        assert reponse.status_code == 303 and reponse.headers["location"] == f"{module.adresse}/"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_acces_reserve_aux_connectes(module, anonyme):
    reponse = anonyme.get(f"{module.adresse}/", follow_redirects=False)
    assert reponse.status_code == 303 and reponse.headers["location"].startswith("/admin/connexion"), (
        f"{module.adresse}/ est accessible sans connexion")
    reponse = anonyme.post(f"{module.adresse}/nouveau", data={}, follow_redirects=False)
    assert reponse.status_code == 303, f"{module.adresse}/nouveau accepte un envoi sans connexion"
