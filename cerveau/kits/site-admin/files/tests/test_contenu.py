"""contenu/site.json : JSON valide, structure correcte, et chaque {{ site.x }} / {{ textes.x }} des gabarits
est déclaré dans le fichier. Les exemples correspondent aux modules de app/modules."""

import json
import re
from pathlib import Path

import pytest

from app.core.framework import REGISTRE
from app.site import contenu

RACINE = Path(__file__).resolve().parent.parent
GABARITS = sorted((RACINE / "app" / "templates" / "site").glob("*.html"))
FICHIER = "contenu/site.json"


@pytest.fixture(scope="module")
def donnees():
    texte = (RACINE / "contenu" / "site.json").read_text(encoding="utf-8")
    try:
        donnees = json.loads(texte)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{FICHIER} n'est pas un JSON valide : ligne {exc.lineno}, colonne {exc.colno} : {exc.msg}. "
                    "Vérifie les virgules (pas après le dernier élément), les guillemets doubles et les accolades.",
                    pytrace=False)
    try:
        contenu.verifier(donnees)
    except contenu.ErreurContenu as exc:
        pytest.fail(str(exc), pytrace=False)
    return donnees


def _cles(donnees, section):
    return {r["cle"] for r in donnees[section]}


def test_parametres_indispensables(donnees):
    cles = _cles(donnees, "parametres")
    for cle in ("nom", "slogan", "description", "adresse", "telephone", "email", "horaires", "couleur_primaire",
                "couleur_accent", "couleur_fond", "couleur_texte"):
        assert cle in cles, f"{FICHIER} : le paramètre « {cle} » est indispensable (section « parametres »)"
    valeurs = {r["cle"]: r.get("valeur", "") for r in donnees["parametres"]}
    for cle in ("nom", "telephone", "email"):
        assert valeurs[cle].strip(), f"{FICHIER} : le paramètre « {cle} » doit avoir une valeur"


@pytest.mark.parametrize("gabarit", GABARITS, ids=lambda g: g.name)
def test_gabarits_utilisent_des_cles_declarees(donnees, gabarit):
    texte = gabarit.read_text(encoding="utf-8")
    for section, variable in (("parametres", "site"), ("textes", "textes")):
        declarees = _cles(donnees, section)
        for cle in sorted(set(re.findall(rf"\b{variable}\.([a-z_][a-z0-9_]*)", texte))):
            assert cle in declarees, (
                f"app/templates/site/{gabarit.name} utilise {{{{ {variable}.{cle} }}}} mais « {cle} » n'est pas dans "
                f"« {section} » de {FICHIER}. Ajoute {{\"cle\": \"{cle}\", \"groupe\": \"...\", \"libelle\": \"...\", "
                f"\"valeur\": \"...\"}} dans {section}, ou corrige le nom.")


def test_exemples_correspondent_aux_modules(donnees):
    for nom, objets in donnees.get("exemples", {}).items():
        assert nom in REGISTRE, (
            f"{FICHIER} : exemples.{nom} ne correspond à aucun module de app/modules/ ({', '.join(sorted(REGISTRE))})")
        module = REGISTRE[nom]
        champs = {c.nom for c in module.champs if c.saisissable}
        for i, objet in enumerate(objets):
            inconnus = set(objet) - champs
            assert not inconnus, (
                f"{FICHIER} : exemples.{nom}[{i}] : {', '.join(sorted(inconnus))} n'existe pas dans le module "
                f"{nom} (champs : {', '.join(sorted(champs))})")


def test_exemples_installables(db):
    """Chaque exemple se convertit sans erreur (dates, nombres, images, champs obligatoires)."""
    for nom, objets in contenu.charger().get("exemples", {}).items():
        for i, objet in enumerate(objets):
            try:
                contenu.objet_exemple(REGISTRE[nom], objet, f"{FICHIER} : exemples.{nom}[{i}]", db)
            except contenu.ErreurContenu as exc:
                pytest.fail(str(exc), pytrace=False)


def test_verifier_signale_les_erreurs_clairement():
    for mauvais, attendu in (
        ({"parametres": [], "textes": [{"cle": "Titre", "groupe": "A", "libelle": "B", "valeur": ""}]}, "minuscules"),
        ({"parametres": [{"cle": "couleur_primaire", "groupe": "C", "libelle": "C", "type": "couleur",
                          "valeur": "rouge"}], "textes": []}, "couleur attendue"),
        ({"parametres": [], "textes": [{"cle": "a", "groupe": "A", "libelle": "B", "valeur": ""},
                                       {"cle": "a", "groupe": "A", "libelle": "B", "valeur": ""}]}, "deux fois"),
    ):
        with pytest.raises(contenu.ErreurContenu, match=attendu):
            contenu.verifier(mauvais)
