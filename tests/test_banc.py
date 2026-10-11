"""Banc d'essai, testé avec un faux modèle : aucun serveur d'inférence requis."""

import json
import shutil
from pathlib import Path

import pytest

from cerveau import banc, config
from cerveau.config import Config
from cerveau.llm import LLMError, Reply, ToolCall
from cerveau.memory import MemoryStore
from cerveau.tools import projets, server

VERDICT_OK = "OK : tests verts (43 passed in 3.1s), l'application démarre et répond."


class FakeClient:
    """Joue un script de réponses ; une exception dans le script est levée à son tour."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.models = set()

    def chat(self, model, messages, tools=None, temperature=0.2, max_tokens=None):
        self.calls.append(messages)
        self.models.add(model)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def embed(self, model, texts):
        return [[1.0, 0.0] for _ in texts]


def script_garage():
    return [
        Reply(tool_calls=[
            ToolCall(id="1", name="create_project", arguments={"kit": "gestion", "title": "Garage Dupont"}),
            ToolCall(id="2", name="write_file", arguments={"path": "PLAN.md", "content": "- [x] Base\n- [x] Véhicules\n"}),
        ]),
        Reply(tool_calls=[ToolCall(id="3", name="add_module", arguments={
            "nom": "vehicules", "libelle": "Véhicules", "objet": "véhicule", "titre": "immatriculation",
            "champs": [{"nom": "immatriculation", "libelle": "Immatriculation", "type": "texte", "requis": True},
                       {"nom": "client", "libelle": "Client", "type": "ref", "ref": "clients"}]})]),
        Reply(content="ERP en place.\nÉTAT : TERMINÉ", usage={"prompt_tokens": 900, "completion_tokens": 40}),
    ]


SCENARIO_GARAGE = {
    "nom": "garage-test",
    "description": "test",
    "demande": "Fais un ERP pour mon garage. Sois autonome.",
    "autonome": True,
    "limite_etapes": 5,
    "criteres": [
        {"type": "verification", "poids": 3},
        {"type": "module", "motif": "vehicul", "ref": "clients", "poids": 2},
        {"type": "texte", "fichier": "app/config.py", "contient": ["garage"]},
        {"type": "fichier", "chemin": "PLAN.md"},
        {"type": "plan"},
        {"type": "hors_projet"},
        {"type": "module", "nom": "pieces"},
    ],
}


@pytest.fixture
def isole(tmp_path, monkeypatch):
    """Cerveau HOME à part, vérification et chargement d'application simulés (pas d'uv)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"base_url": "http://127.0.0.1:9/v1", "model": "faux"}),
                                      encoding="utf-8")
    monkeypatch.setattr(config, "HOME", home)
    monkeypatch.setattr(projets, "_charger_application", lambda root: "")
    monkeypatch.setattr(projets, "verify_project", lambda root: VERDICT_OK)
    outil = next(t for t in projets.TOOLS if t.name == "verify_project")
    monkeypatch.setattr(outil, "run", lambda root: VERDICT_OK)
    return home


def scenario(data=None, **changes):
    data = dict(data or SCENARIO_GARAGE, **changes)
    assert banc.valider(data) == []
    return banc.Scenario(nom=data["nom"], description=data.get("description", ""), demande=data["demande"],
                         criteres=data["criteres"], autonome=data.get("autonome", False),
                         limite_etapes=data.get("limite_etapes", 30), preparation=data.get("preparation"))


# --- scénarios -------------------------------------------------------------------------

def test_scenarios_livres_valides():
    scenarios, erreurs = banc.charger_scenarios([banc.SCENARIOS_LIVRES])
    assert erreurs == []
    assert {"erp-garage", "site-menuiserie", "module-ajout", "correction-bug", "espaces-verts"} <= set(scenarios)
    for s in scenarios.values():
        assert s.demande and s.criteres and s.description
    assert scenarios["erp-garage"].autonome and "Sois autonome" in scenarios["erp-garage"].demande
    assert scenarios["module-ajout"].preparation["kit"] == "gestion"


def test_validation_signale_les_erreurs():
    erreurs = banc.valider({"nom": "Mauvais Nom", "demande": "", "limite_etapes": 0,
                            "preparation": {"kit": "inconnu"},
                            "criteres": [{"type": "magie"}, {"type": "tests"}, {"type": "module"},
                                         {"type": "texte", "fichier": "a"}, {"type": "fichier", "chemin": "x", "poids": -1}]})
    texte = "\n".join(erreurs)
    for attendu in ("nom", "demande", "limite_etapes", "kit inconnu", "type inconnu", "min ou depart_plus",
                    "nom ou motif", "contient, absent ou motif", "poids"):
        assert attendu in texte, attendu
    assert banc.valider({"nom": "x", "demande": "y", "criteres": []})


def test_scenarios_utilisateur_prioritaires_et_invalides_signales(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    (tmp_path / "banc").mkdir()
    perso = dict(SCENARIO_GARAGE, nom="erp-garage", description="ma version")
    (tmp_path / "banc" / "erp-garage.json").write_text(json.dumps(perso), encoding="utf-8")
    (tmp_path / "banc" / "casse.json").write_text("{pas du json", encoding="utf-8")
    scenarios, erreurs = banc.charger_scenarios()
    assert scenarios["erp-garage"].description == "ma version"
    assert "site-menuiserie" in scenarios
    assert len(erreurs) == 1 and "casse.json" in erreurs[0]


def test_preparation_injecte_le_bug(tmp_path):
    scenarios, _ = banc.charger_scenarios([banc.SCENARIOS_LIVRES])
    s = scenarios["correction-bug"]
    root = tmp_path / "projet"
    banc.preparer(s, root)
    assert "total_ht(document) - total_tva(document)" in (root / "app/modules/_documents.py").read_text(encoding="utf-8")
    assert projets.kit_du_projet(root)["nom"] == "gestion"
    critere_bug = next(c for c in s.criteres if c["type"] == "texte")
    ctx = banc.Contexte(root=root, dossier=tmp_path, empreintes=banc.empreintes(root, s.criteres))
    assert ctx.empreintes and all(k.startswith("tests/") for k in ctx.empreintes)
    assert banc.evaluer([critere_bug], ctx)[0]["ok"] is False


def test_preparation_echoue_si_l_injection_ne_trouve_rien(tmp_path):
    s = scenario(preparation={"kit": "gestion", "injections": [
        {"fichier": "app/config.py", "remplacer": "texte introuvable", "par": "x"}]})
    with pytest.raises(RuntimeError, match="introuvable"):
        banc.preparer(s, tmp_path / "p")


# --- critères ------------------------------------------------------------------------------

def test_criteres_sur_un_projet_gestion(tmp_path, monkeypatch):
    monkeypatch.setattr(projets, "_charger_application", lambda root: "")
    root = tmp_path / "projet"
    root.mkdir()
    projets.create_project(root, "gestion", "Garage")
    projets.add_module(root, "ordres", "Ordres", document=True, planning="date", champs=[
        {"nom": "date", "libelle": "Date", "type": "date"},
        {"nom": "client", "libelle": "Client", "type": "ref", "ref": "clients"}])
    (root / "PLAN.md").write_text("- [x] Base\n- [ ] Ordres\n", encoding="utf-8")
    criteres = [
        {"type": "module", "motif": "ordre", "document": True, "planning": True, "ref": "clients"},
        {"type": "module", "nom": "clients", "document": True},
        {"type": "module", "nom": ["clients", "fournisseurs"], "tous": True},
        {"type": "module", "nom": ["clients", "factures"], "tous": True},
        {"type": "texte", "fichier": "app/modules/*.py", "contient": ["CLIENTS", "ordres"], "absent": ["__TITRE__"]},
        {"type": "texte", "fichier": "app/config.py", "absent": ["Garage"]},
        {"type": "plan"},
        {"type": "fichier", "chemin": "app/modules/ordres.py"},
        {"type": "fichier", "chemin": "inexistant/*.py"},
    ]
    resultats = banc.evaluer(criteres, banc.Contexte(root=root, dossier=tmp_path))
    assert [r["ok"] for r in resultats] == [True, False, False, True, True, False, False, True, False]
    assert "lignes et totaux" in resultats[1]["detail"]
    assert "fournisseurs" in resultats[2]["detail"]
    assert "1/2" in resultats[6]["detail"] and "Ordres" in resultats[6]["detail"]
    assert banc.score(resultats) == round(100 * 4 / 9, 1)


def test_hors_projet_et_inchange(tmp_path):
    dossier = tmp_path / "exec"
    root = dossier / "projet"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    criteres = [{"type": "hors_projet"}, {"type": "inchange", "fichiers": ["tests/*.py"]}]
    ctx = banc.Contexte(root=root, dossier=dossier, empreintes=banc.empreintes(root, criteres),
                        instantane=banc.instantane_hors_projet(dossier, root))
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")          # dans le projet : permis
    (dossier / "journal.jsonl").write_text("{}\n", encoding="utf-8")   # fichier du banc : ignoré
    assert all(r["ok"] for r in banc.evaluer(criteres, ctx))
    (dossier / "fuite.txt").write_text("x", encoding="utf-8")
    (root / "tests" / "test_a.py").write_text("", encoding="utf-8")
    hors, inchange = banc.evaluer(criteres, ctx)
    assert not hors["ok"] and "fuite.txt" in hors["detail"]
    assert not inchange["ok"] and "tests/test_a.py modifié" in inchange["detail"]


def test_un_critere_qui_plante_compte_en_echec(tmp_path, monkeypatch):
    monkeypatch.setattr(projets, "verify_project", lambda root: 1 / 0)
    (r,) = banc.evaluer([{"type": "verification"}], banc.Contexte(root=tmp_path, dossier=tmp_path))
    assert not r["ok"] and "ZeroDivisionError" in r["detail"]


@pytest.mark.skipif(not shutil.which("uv"), reason="uv absent")
def test_criteres_qui_lancent_l_application(tmp_path):
    """Réel : uv run pytest et démarrage de l'application du kit gestion (une dizaine de secondes)."""
    root = tmp_path / "projet"
    root.mkdir()
    projets.create_project(root, "gestion", "Garage")
    criteres = [
        {"type": "tests", "min": 30},
        {"type": "tests", "min": 10000},
        {"type": "tests", "depart_plus": 2},
        {"type": "http", "pages": [{"chemin": "/sante"}, {"chemin": "/connexion", "contient": ["Garage"]},
                                   {"chemin": "/inexistant"}]},
    ]
    try:
        resultat = banc.compter_tests(root)
        passes = resultat[0]
        ctx = banc.Contexte(root=root, dossier=tmp_path, tests_depart=passes - 2, cache={"tests": resultat})
        tests_ok, tests_ko, relatif, http = banc.evaluer(criteres, ctx)
    finally:
        server.stop_all()
    assert tests_ok["ok"], tests_ok["detail"]
    assert not tests_ko["ok"] and "réussi" in tests_ko["detail"]
    assert relatif["ok"] and f"(minimum {passes})" in relatif["detail"], relatif["detail"]
    assert not http["ok"] and http["detail"] == "/inexistant répond 404", http["detail"]
    assert "banc-evaluation" not in server._SERVERS


# --- exécution -----------------------------------------------------------------------------

def test_execution_de_bout_en_bout_avec_memoire_isolee(tmp_path, isole):
    cfg = config.load()
    dossier = tmp_path / "runs" / "garage-test-faux-1"
    client = FakeClient(script_garage())
    res = banc.executer(scenario(), cfg, banc.Combinaison("faux", 8192), dossier, client=client)

    assert res["statut"] == "termine", res["erreur"]
    assert (res["etapes"], res["appels_outils"], res["appels_llm"], res["verifications"]) == (1, 3, 3, 1)
    assert res["tours"] == 3 and res["tokens_generes"] > 40 and res["contexte_max"] > 0
    assert [c["ok"] for c in res["criteres"]] == [True, True, True, True, True, True, False]
    assert res["score"] == 90.0 and not res["reussi"]   # poids : 9 sur 10
    assert "TERMINÉ" in res["reponse_finale"]
    # le modèle a bien travaillé dans le dossier du projet
    assert (dossier / "projet" / "app" / "modules" / "vehicules.py").is_file()
    # modèle et contexte surchargés, apprentissage coupé (aucun appel en plus des trois tours)
    assert client.models == {"faux"} and res["contexte"] == 8192 and len(client.calls) == 3
    assert str(dossier / "projet") in client.calls[0][0]["content"]
    # mémoire du banc à part, celle de l'utilisateur jamais créée
    assert not (isole / "memoire.sqlite").exists()
    store = MemoryStore(dossier / "memoire-banc.sqlite")
    assert store.count("projet") >= 1
    store.close()
    journal = [json.loads(l) for l in (dossier / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["evenement"] for e in journal][:2] == ["debut", "etape"] and journal[-1]["evenement"] == "fin"


def test_plafond_de_temps(tmp_path, isole):
    res = banc.executer(scenario(), config.load(), banc.Combinaison("faux", 8192), tmp_path / "e",
                        temps_max=0, client=FakeClient(script_garage()))
    assert res["statut"] == "temps_depasse" and res["appels_llm"] == 0
    assert res["criteres"] and not res["criteres"][1]["ok"]   # rien n'a été fait, mais tout est évalué


def test_erreurs_du_serveur_relancees_puis_abandon(tmp_path, isole):
    client = FakeClient([LLMError("serveur tombé")] * 3)
    res = banc.executer(scenario(), config.load(), banc.Combinaison("faux", 8192), tmp_path / "e", client=client)
    assert res["statut"] == "erreur_llm" and res["erreurs_llm"] == 3 and "serveur tombé" in res["erreur"]
    assert "Reprise après une coupure" in client.calls[-1][-1]["content"]


def test_preparation_impossible_ne_fait_rien_tourner(tmp_path, isole):
    s = scenario(preparation={"kit": "gestion", "injections": [
        {"fichier": "app/config.py", "remplacer": "introuvable", "par": "x"}]})
    client = FakeClient(script_garage())
    res = banc.executer(s, config.load(), banc.Combinaison("faux", 8192), tmp_path / "e", client=client)
    assert res["statut"] == "erreur" and "introuvable" in res["erreur"]
    assert client.calls == [] and res["criteres"] == [] and res["score"] == 0


# --- banc complet, rapports, comparaison, interruption -------------------------------------

def _ecrire_scenario(home, data):
    (home / "banc").mkdir(exist_ok=True)
    (home / "banc" / f"{data['nom']}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_main_banc_produit_les_rapports(tmp_path, isole, capsys):
    _ecrire_scenario(isole, SCENARIO_GARAGE)
    rapport = tmp_path / "rapports" / "r1.json"
    code = banc.main_banc(["garage-test", "--modele", "faux-a", "faux-b", "--dossier", str(tmp_path / "runs"),
                           "--rapport", str(rapport)], client_factory=lambda: FakeClient(script_garage()))
    assert code == 0
    data = json.loads(rapport.read_text(encoding="utf-8"))
    assert [e["modele"] for e in data["executions"]] == ["faux-a", "faux-b"]
    assert {s["configuration"] for s in data["synthese"]} == {"faux-a · 16k", "faux-b · 16k"}
    assert not data["interrompu"] and data["scenarios"]["garage-test"]["autonome"]
    dossiers = sorted(p.name for p in next((tmp_path / "runs").iterdir()).iterdir())
    assert dossiers == ["garage-test-faux-a-1", "garage-test-faux-b-1"]
    md = rapport.with_suffix(".md").read_text(encoding="utf-8")
    assert "| garage-test |" in md and "faux-a · 16k" in md and "module pieces" in md


def test_comparaison(tmp_path, isole, capsys):
    def rapport(score, debit):
        e = {"scenario": "s", "configuration": "m · 16k", "modele": "m", "contexte": 16384, "score": score,
             "reussi": score == 100, "duree": 60, "debit": debit, "etapes": 2, "tokens_generes": 100,
             "verifications_echec": 0, "erreurs_llm": 0}
        return {"executions": [e]}
    lignes = banc.comparer(rapport(50, 20), rapport(75, 25))
    assert lignes[0]["delta"] == 25 and lignes[0]["debit_apres"] == 25
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(rapport(50, 20)), encoding="utf-8")
    b.write_text(json.dumps(rapport(40, 20)), encoding="utf-8")
    assert banc.main_banc(["--comparer", str(a), str(b)]) == 0
    assert "-10" in capsys.readouterr().out


def test_interruption_arrete_proprement(tmp_path, isole, monkeypatch):
    _ecrire_scenario(isole, SCENARIO_GARAGE)
    arrets = []
    monkeypatch.setattr(server, "stop_all", lambda: arrets.append(1))
    script = script_garage()[:1] + [KeyboardInterrupt()]
    rapport = tmp_path / "r.json"
    code = banc.main_banc(["garage-test", "--repetitions", "3", "--dossier", str(tmp_path / "runs"),
                           "--rapport", str(rapport)], client_factory=lambda: FakeClient(list(script)))
    assert code == 130 and arrets
    data = json.loads(rapport.read_text(encoding="utf-8"))
    assert data["interrompu"] and len(data["executions"]) == 1
    assert data["executions"][0]["statut"] == "interrompu" and data["executions"][0]["criteres"] == []
    assert "interrompu" in rapport.with_suffix(".md").read_text(encoding="utf-8")


def test_liste_et_scenario_inconnu(isole, capsys):
    assert banc.main_banc(["--liste"]) == 0
    assert "erp-garage" in capsys.readouterr().out
    assert banc.main_banc(["nimporte"], client_factory=lambda: None) == 1
