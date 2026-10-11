"""Tests génériques : chaque module de app/modules est vérifié automatiquement, sans rien écrire.
Création, lecture, modification, liste, recherche, export, impression, planning, actions, suppression."""

import pytest

from app.core.framework import REGISTRE, formulaire_exemple

MODULES = sorted(REGISTRE.values(), key=lambda m: (m.ordre, m.nom))


def _creer(http, module, db):
    donnees = formulaire_exemple(module, db)
    reponse = http.post(f"/{module.nom}/nouveau", data=donnees, follow_redirects=False)
    assert reponse.status_code == 303, (
        f"création {module.nom} refusée ({reponse.status_code}) avec {donnees} : "
        f"{reponse.text[:1500] if reponse.status_code != 500 else 'erreur 500, voir la trace ci-dessus'}")
    return donnees, reponse.headers["location"]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_pages_du_module(module, http, db):
    assert http.get(f"/{module.nom}/").status_code == 200, f"liste /{module.nom}/ en erreur"
    assert http.get(f"/{module.nom}/nouveau").status_code == 200, f"formulaire /{module.nom}/nouveau en erreur"
    donnees, adresse = _creer(http, module, db)
    fiche = http.get(adresse)
    assert fiche.status_code == 200, f"fiche {adresse} en erreur"
    liste = http.get(f"/{module.nom}/")
    assert liste.status_code == 200
    assert "Aucun" not in liste.text, f"l'objet créé n'apparaît pas dans /{module.nom}/"
    assert http.get(f"/{module.nom}/", params={"q": "Exemple"}).status_code == 200
    export = http.get(f"/{module.nom}/export.csv")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    modif = http.post(adresse, data=donnees, follow_redirects=False)
    assert modif.status_code == 303, f"modification {adresse} refusée ({modif.status_code}) : {modif.text[:1500]}"
    if module.imprimable:
        assert http.get(f"{adresse}/imprimer").status_code == 200, f"impression {adresse} en erreur"
    if module.planning:
        assert http.get(f"/{module.nom}/planning").status_code == 200, f"planning /{module.nom}/ en erreur"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_champs_obligatoires(module, http, db):
    if any(c.requis for c in module.champs):
        reponse = http.post(f"/{module.nom}/nouveau", data={}, follow_redirects=False)
        assert reponse.status_code == 422, f"/{module.nom}/nouveau accepte un formulaire vide"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_actions_et_suppression(module, http, db):
    _, adresse = _creer(http, module, db)
    objet = db.get(module.modele, int(adresse.rsplit("/", 1)[1]))
    for action in module.actions:
        if action.visible is None or action.visible(objet):
            reponse = http.post(f"{adresse}/action/{action.nom}", follow_redirects=True)
            assert reponse.status_code == 200, f"action {action.nom} de {module.nom} en erreur"
    if module.suppression:
        assert http.post(f"{adresse}/supprimer", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.nom)
def test_acces_reserve_aux_connectes(module, anonyme):
    reponse = anonyme.get(f"/{module.nom}/", follow_redirects=False)
    assert reponse.status_code == 303 and reponse.headers["location"].startswith("/connexion")
