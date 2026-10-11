"""Kits, modules déclaratifs, points de reprise git et autonomie vérifiée par le cerveau."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cerveau.tools import projets


def test_kits_livres():
    kits = projets.kits_disponibles()
    assert {"gestion", "site"} <= set(kits)
    for meta in kits.values():
        assert meta["test"] and "{port}" in meta["start"]


def test_create_project_gestion(tmp_path):
    out = projets.create_project(tmp_path, "gestion", 'Garage "Dupont" & Fils')
    assert out.startswith("projet créé"), out
    assert (tmp_path / ".gitignore").is_file() and not (tmp_path / "_gitignore").exists()
    config = (tmp_path / "app/config.py").read_text(encoding="utf-8")
    assert "APP_TITLE = \"Garage 'Dupont' & Fils\"" in config
    for py in tmp_path.rglob("*.py"):
        compile(py.read_text(encoding="utf-8"), str(py), "exec")
    assert not any("__TITRE__" in f.read_text(encoding="utf-8", errors="ignore") for f in tmp_path.rglob("*") if f.is_file())
    assert projets.kit_du_projet(tmp_path)["nom"] == "gestion"
    assert projets.create_project(tmp_path, "gestion", "x").startswith("échec")


def test_create_project_site_garde_un_json_valide(tmp_path):
    assert projets.create_project(tmp_path, "site", 'Menuiserie "Maire"').startswith("projet créé")
    json.loads((tmp_path / "contenu/site.json").read_text(encoding="utf-8"))


def test_kit_inconnu(tmp_path):
    assert "Kits disponibles" in projets.create_project(tmp_path, "nimporte", "x")


@pytest.fixture
def gestion(tmp_path, monkeypatch):
    projets.create_project(tmp_path, "gestion", "Garage")
    monkeypatch.setattr(projets, "_charger_application", lambda root: "")
    return tmp_path


def test_add_module_ecrit_un_module_declaratif(gestion):
    out = projets.add_module(gestion, "vehicules", "Véhicules", objet="véhicule", titre="immatriculation", champs=[
        {"nom": "immatriculation", "type": "texte", "requis": True},
        {"nom": "client", "libelle": "Client", "type": "ref", "ref": "clients", "requis": True},
        {"nom": "mise_en_service", "type": "date", "defaut": "aujourdhui"},
        {"nom": "energie", "type": "choix", "choix": ["Diesel", "Essence"]},
        {"nom": "km_par_an", "type": "calcul", "calcul": "obj.km or 0", "affichage": "entier"},
        {"nom": "km", "type": "entier"},
    ])
    assert out.startswith("module créé"), out
    code = (gestion / "app/modules/vehicules.py").read_text(encoding="utf-8")
    compile(code, "vehicules.py", "exec")
    assert "ref='clients'" in code and "defaut=date.today" in code and "lambda obj: obj.km or 0" in code


def test_add_module_document(gestion):
    out = projets.add_module(gestion, "commandes", "Commandes", objet="commande", feminin=True, document=True,
                             champs=[{"nom": "numero", "type": "numero", "format": "C-{annee}-{n:04d}"},
                                     {"nom": "client", "type": "ref", "ref": "clients", "requis": True}])
    code = (gestion / "app/modules/commandes.py").read_text(encoding="utf-8")
    assert out.startswith("module créé")
    assert "lignes=lignes_standard()" in code and "*champs_totaux()" in code and "feminin=True" in code


def test_add_module_refuse_sans_rien_ecrire(gestion):
    out = projets.add_module(gestion, "pieces", "Pièces", champs=[
        {"nom": "Désignation", "type": "texte"},
        {"nom": "fournisseur", "type": "ref", "ref": "fournisseurs"},
        {"nom": "etat", "type": "choix"},
        {"nom": "prix", "type": "float"}])
    assert out.startswith("échec") and not (gestion / "app/modules/pieces.py").exists()
    for attendu in ("nom de champ invalide", "fournisseurs", "choix", "float"):
        assert attendu in out


def test_add_module_retire_un_module_qui_ne_se_charge_pas(gestion, monkeypatch):
    monkeypatch.setattr(projets, "_charger_application", lambda root: "Traceback… ValueError: boum")
    out = projets.add_module(gestion, "pieces", "Pièces", champs=[{"nom": "nom", "type": "texte"}])
    assert out.startswith("échec") and "boum" in out and not (gestion / "app/modules/pieces.py").exists()


def test_add_module_hors_kit_gestion(tmp_path):
    assert projets.add_module(tmp_path, "x", "X", champs=[]).startswith("échec")


def test_verify_sans_kit(tmp_path):
    assert projets.verify_project(tmp_path).startswith("pas de kit")


git = pytest.mark.skipif(not shutil.which("git"), reason="git absent")


@git
def test_points_de_reprise_et_mise_de_cote(tmp_path):
    (tmp_path / "a.txt").write_text("v1")
    assert projets.checkpoint(tmp_path, "v1")
    (tmp_path / "a.txt").write_text("cassé")
    (tmp_path / "nouveau.txt").write_text("x")
    assert projets.set_aside(tmp_path, "bloqué")
    assert (tmp_path / "a.txt").read_text() == "v1" and not (tmp_path / "nouveau.txt").exists()
    stash = subprocess.run(["git", "stash", "list"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "bloqué" in stash   # rien n'est perdu


@git
def test_projet_range_dans_un_autre_depot_n_est_pas_touche(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    projet = tmp_path / "sous-projet"
    projet.mkdir()
    (projet / "a.txt").write_text("x")
    assert not projets.checkpoint(projet, "x")
    assert not (projet / ".git").exists()


@pytest.mark.parametrize("pack", sorted(projets.packs_du_kit("gestion")))
def test_packs_metier(tmp_path, monkeypatch, pack):
    monkeypatch.setattr(projets, "_charger_application", lambda root: "")
    out = projets.create_project(tmp_path, "gestion", "Essai", pack=pack)
    assert f"pack « {pack} » appliqué" in out, out
    for spec in projets.packs_du_kit("gestion")[pack]["modules"]:
        compile((tmp_path / f"app/modules/{spec['nom']}.py").read_text(encoding="utf-8"), spec["nom"], "exec")
    for retire in projets.packs_du_kit("gestion")[pack].get("retirer", []):
        assert not (tmp_path / f"app/modules/{retire}.py").exists()


def test_pack_optique_ajoute_le_role(tmp_path, monkeypatch):
    monkeypatch.setattr(projets, "_charger_application", lambda root: "")
    projets.create_project(tmp_path, "gestion", "Optic", pack="optique")
    config = (tmp_path / "app/config.py").read_text(encoding="utf-8")
    assert '"optique": "Opticien"' in config
    compile(config, "config.py", "exec")
    assert "journal_consultations=True" in (tmp_path / "app/modules/ordonnances.py").read_text(encoding="utf-8")
