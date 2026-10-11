"""Banc d'essai : mesurer ce qui marche au lieu de deviner.

Un scénario (cerveau/banc_scenarios/*.json, ou ~/.cerveau/banc/*.json pour les vôtres) décrit une
demande telle qu'un utilisateur l'écrirait et des critères vérifiés automatiquement après coup,
sans modèle : vérification du projet, modules, fichiers, textes, pages HTTP, nombre de tests,
PLAN.md coché, rien de modifié hors du projet.

Chaque exécution part d'un dossier neuf, en mode auto (personne pour confirmer), avec une mémoire
à part et sans apprentissage automatique : la mémoire de l'utilisateur n'est jamais touchée.
Le rapport (JSON complet + Markdown lisible) permet de comparer modèles, contextes et consignes.

  cerveau banc                               tous les scénarios, modèle actif
  cerveau banc erp-garage --modele a b       deux modèles sur un scénario
  cerveau banc --contexte 16384 32768        deux tailles de contexte
  cerveau banc --comparer avant.json apres.json
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import os
import platform
import re
import socket
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__, config as config_mod, ollama
from .agent import Agent
from .llm import Client, LLMError
from .memory import MemoryStore
from .tools import projets, server, shell

SCENARIOS_LIVRES = Path(__file__).resolve().parent / "banc_scenarios"
FICHIERS_DU_BANC = ("memoire-banc.sqlite", "memoire-banc.sqlite-journal", "memoire-banc.sqlite-wal",
                    "memoire-banc.sqlite-shm", "journal.jsonl")
TEMPS_MAX_DEFAUT = 45.0     # minutes par exécution
REPRISES_APRES_ERREUR = 2   # relances d'un scénario autonome après une erreur du serveur d'inférence

CRITERES = {
    # type : clés obligatoires
    "verification": (),
    "fichier": ("chemin",),
    "module": (),
    "texte": ("fichier",),
    "http": (),
    "tests": (),          # min et/ou depart_plus
    "plan": (),
    "hors_projet": (),
    "inchange": ("fichiers",),
}


class ScenarioInvalide(ValueError):
    pass


class TempsDepasse(Exception):
    """Le plafond de temps de l'exécution est atteint."""


# --- scénarios -------------------------------------------------------------------------

@dataclass
class Scenario:
    nom: str
    description: str
    demande: str
    criteres: list[dict]
    autonome: bool = False
    limite_etapes: int = 30
    preparation: dict | None = None
    dossier_projet: str = "projet"
    source: str = ""

    def resume(self) -> dict:
        return {"description": self.description, "demande": self.demande, "autonome": self.autonome,
                "limite_etapes": self.limite_etapes, "preparation": self.preparation,
                "criteres": self.criteres, "source": self.source}


def valider(data: object) -> list[str]:
    """Erreurs d'un scénario lu en JSON ; liste vide s'il est valide."""
    if not isinstance(data, dict):
        return ["le scénario doit être un objet JSON"]
    erreurs = []
    nom = data.get("nom")
    if not isinstance(nom, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", nom):
        erreurs.append("nom : obligatoire, minuscules, chiffres, - et _")
    if not isinstance(data.get("demande"), str) or not data["demande"].strip():
        erreurs.append("demande : texte obligatoire")
    limite = data.get("limite_etapes", 30)
    if not isinstance(limite, int) or isinstance(limite, bool) or limite < 1:
        erreurs.append("limite_etapes : entier positif")
    if not isinstance(data.get("autonome", False), bool):
        erreurs.append("autonome : true ou false")
    dossier = data.get("dossier_projet", "projet")
    if not isinstance(dossier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", dossier):
        erreurs.append("dossier_projet : un simple nom de dossier")
    prep = data.get("preparation")
    if prep is not None:
        if not isinstance(prep, dict) or not isinstance(prep.get("kit"), str):
            erreurs.append("preparation : objet avec au moins « kit »")
        else:
            if prep["kit"] not in projets.kits_disponibles():
                erreurs.append(f"preparation.kit : kit inconnu « {prep['kit']} »")
            for i, inj in enumerate(prep.get("injections") or []):
                if not isinstance(inj, dict) or not all(isinstance(inj.get(k), str) for k in ("fichier", "remplacer", "par")):
                    erreurs.append(f"preparation.injections[{i}] : fichier, remplacer et par sont obligatoires")
            fichiers = prep.get("fichiers") or {}
            if not isinstance(fichiers, dict) or not all(isinstance(v, str) for v in fichiers.values()):
                erreurs.append("preparation.fichiers : objet {chemin: contenu}")
    criteres = data.get("criteres")
    if not isinstance(criteres, list) or not criteres:
        erreurs.append("criteres : liste non vide")
        return erreurs
    for i, critere in enumerate(criteres):
        if not isinstance(critere, dict) or critere.get("type") not in CRITERES:
            erreurs.append(f"criteres[{i}] : type inconnu ; types : {', '.join(CRITERES)}")
            continue
        manquantes = [k for k in CRITERES[critere["type"]] if k not in critere]
        if manquantes:
            erreurs.append(f"criteres[{i}] ({critere['type']}) : il manque {', '.join(manquantes)}")
        poids = critere.get("poids", 1)
        if not isinstance(poids, (int, float)) or isinstance(poids, bool) or poids <= 0:
            erreurs.append(f"criteres[{i}] : poids positif")
        if critere["type"] == "tests" and not any(isinstance(critere.get(k), int) for k in ("min", "depart_plus")):
            erreurs.append(f"criteres[{i}] (tests) : min ou depart_plus (entier) obligatoire")
        if critere["type"] == "module" and not (critere.get("nom") or critere.get("motif")):
            erreurs.append(f"criteres[{i}] (module) : nom ou motif obligatoire")
        if critere["type"] == "texte" and not any(critere.get(k) for k in ("contient", "absent", "motif")):
            erreurs.append(f"criteres[{i}] (texte) : contient, absent ou motif obligatoire")
        if critere.get("motif") is not None:
            try:
                re.compile(critere["motif"])
            except (re.error, TypeError):
                erreurs.append(f"criteres[{i}] : motif n'est pas une expression régulière valide")
    return erreurs


def charger_scenario(chemin: Path) -> Scenario:
    try:
        data = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioInvalide(f"{chemin} : illisible ({exc})") from exc
    erreurs = valider(data)
    if erreurs:
        raise ScenarioInvalide(f"{chemin} :\n- " + "\n- ".join(erreurs))
    return Scenario(nom=data["nom"], description=data.get("description", ""), demande=data["demande"].strip(),
                    criteres=data["criteres"], autonome=data.get("autonome", False),
                    limite_etapes=data.get("limite_etapes", 30), preparation=data.get("preparation"),
                    dossier_projet=data.get("dossier_projet", "projet"), source=str(chemin))


def dossiers_scenarios() -> list[Path]:
    return [SCENARIOS_LIVRES, config_mod.HOME / "banc"]


def charger_scenarios(dossiers: list[Path] | None = None) -> tuple[dict[str, Scenario], list[str]]:
    """Scénarios livrés puis ceux de l'utilisateur (prioritaires à nom égal), et les erreurs."""
    scenarios: dict[str, Scenario] = {}
    erreurs: list[str] = []
    for dossier in dossiers or dossiers_scenarios():
        if not dossier.is_dir():
            continue
        for chemin in sorted(dossier.glob("*.json")):
            try:
                scenario = charger_scenario(chemin)
            except ScenarioInvalide as exc:
                erreurs.append(str(exc))
                continue
            scenarios[scenario.nom] = scenario
    return scenarios, erreurs


# --- préparation ---------------------------------------------------------------------------

def preparer(scenario: Scenario, root: Path) -> None:
    """Crée le projet de départ : kit, fichiers imposés, bugs injectés, puis point de reprise git."""
    root.mkdir(parents=True, exist_ok=True)
    prep = scenario.preparation
    if not prep:
        return
    resultat = projets.create_project(root, prep["kit"], prep.get("titre") or scenario.nom)
    if resultat.startswith("échec"):
        raise RuntimeError(f"préparation impossible : {resultat}")
    for relatif, contenu in (prep.get("fichiers") or {}).items():
        cible = _dans(root, relatif)
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu, encoding="utf-8")
    for inj in prep.get("injections") or []:
        cible = _dans(root, inj["fichier"])
        texte = cible.read_text(encoding="utf-8")
        if inj["remplacer"] not in texte:
            raise RuntimeError(f"préparation impossible : « {inj['remplacer']} » introuvable dans {inj['fichier']}")
        cible.write_text(texte.replace(inj["remplacer"], inj["par"], 1), encoding="utf-8")
    projets.checkpoint(root, "banc : état de départ du scénario")


def _dans(root: Path, relatif: str) -> Path:
    cible = (root / relatif).resolve()
    if root.resolve() not in cible.parents:
        raise RuntimeError(f"chemin hors du projet dans le scénario : {relatif}")
    return cible


# --- critères ------------------------------------------------------------------------------

@dataclass
class Contexte:
    """Ce que les critères partagent pendant une évaluation."""
    root: Path
    dossier: Path
    empreintes: dict[str, str] = field(default_factory=dict)      # fichiers « inchange » au départ
    instantane: dict[str, tuple] = field(default_factory=dict)    # dossier d'exécution hors projet
    refus: int = 0
    tests_depart: int | None = None                                # tests réussis avant le travail du modèle
    cache: dict[str, object] = field(default_factory=dict)


def _fichiers(root: Path, motif: str) -> list[Path]:
    motif = motif.replace("\\", "/").lstrip("/")
    if any(c in motif for c in "*?["):
        return sorted(p for p in root.glob(motif) if p.is_file())
    chemin = root / motif
    return [chemin] if chemin.is_file() else []


def _empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def empreintes(root: Path, criteres: list[dict]) -> dict[str, str]:
    resultat = {}
    for critere in criteres:
        if critere.get("type") == "inchange":
            for motif in critere["fichiers"]:
                for chemin in _fichiers(root, motif):
                    resultat[chemin.relative_to(root).as_posix()] = _empreinte(chemin)
    return resultat


def instantane_hors_projet(dossier: Path, root: Path) -> dict[str, tuple]:
    """Taille et date de chaque fichier du dossier d'exécution, hors projet et fichiers du banc."""
    vus = {}
    projet = root.resolve()
    for parent, sous, noms in os.walk(dossier):
        sous[:] = [s for s in sous if (Path(parent) / s).resolve() != projet]
        for nom in noms:
            if nom in FICHIERS_DU_BANC:
                continue
            chemin = Path(parent) / nom
            try:
                st = chemin.stat()
            except OSError:
                continue
            vus[chemin.relative_to(dossier).as_posix()] = (st.st_size, st.st_mtime_ns)
    return vus


def contexte_de_depart(scenario: Scenario, dossier: Path, root: Path) -> Contexte:
    """État de référence pris juste après la préparation, avant que le modèle ne travaille."""
    ctx = Contexte(root=root, dossier=dossier, empreintes=empreintes(root, scenario.criteres))
    if any(c["type"] == "tests" and "depart_plus" in c for c in scenario.criteres):
        ctx.tests_depart = compter_tests(root)[0]
    ctx.instantane = instantane_hors_projet(dossier, root)
    return ctx


def _modules(root: Path) -> dict[str, str]:
    """Modules déclarés dans app/modules : nom -> code source."""
    trouves = {}
    for fichier in sorted((root / "app" / "modules").glob("*.py")):
        if fichier.name.startswith("_"):
            continue
        texte = fichier.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"""\bnom\s*=\s*["']([A-Za-z0-9_]+)["']""", texte)
        if match:
            trouves[match.group(1)] = texte
    return trouves


def _critere_verification(c: dict, ctx: Contexte) -> tuple[bool, str]:
    if "verification" not in ctx.cache:
        ctx.cache["verification"] = projets.verify_project(ctx.root)
    verdict = str(ctx.cache["verification"])
    return verdict.startswith("OK"), verdict


def _critere_fichier(c: dict, ctx: Contexte) -> tuple[bool, str]:
    trouves = _fichiers(ctx.root, c["chemin"])
    return bool(trouves), (f"{len(trouves)} fichier(s)" if trouves else f"{c['chemin']} absent")


def _critere_module(c: dict, ctx: Contexte) -> tuple[bool, str]:
    modules = _modules(ctx.root)
    if not modules:
        return False, "aucun module dans app/modules"
    if c.get("nom"):
        noms = [c["nom"]] if isinstance(c["nom"], str) else list(c["nom"])
        candidats = [n for n in noms if n in modules]
        manquants = [n for n in noms if n not in modules]
        if c.get("tous") and manquants:
            return False, f"manquant(s) : {', '.join(manquants)} ; présents : {', '.join(sorted(modules))}"
    else:
        motif = re.compile(c["motif"], re.IGNORECASE)
        candidats = [n for n in modules if motif.search(n)]
    if not candidats:
        return False, f"aucun module ne correspond ; présents : {', '.join(sorted(modules))}"
    exigences: list[tuple[str, Callable[[str], bool]]] = []
    if c.get("document"):
        exigences.append(("lignes et totaux", lambda t: "lignes=" in t or "lignes_standard" in t))
    if c.get("planning"):
        exigences.append(("planning", lambda t: re.search(r"\bplanning\s*=", t) is not None))
    if c.get("ref"):
        cible = re.escape(str(c["ref"]))
        exigences.append((f"lien vers {c['ref']}",
                          lambda t: re.search(rf"""\bref\s*=\s*["']{cible}["']""", t) is not None))
    retenus = [n for n in candidats if all(test(modules[n]) for _, test in exigences)]
    ok = len(retenus) == len(candidats) if c.get("tous") else bool(retenus)
    if not ok:
        rates = [n for n in candidats if n not in retenus]
        manque = [label for label, test in exigences if not all(test(modules[n]) for n in rates)]
        return False, f"{', '.join(rates)} sans : {', '.join(manque)}"
    return True, ", ".join(retenus)


def _critere_texte(c: dict, ctx: Contexte) -> tuple[bool, str]:
    fichiers = _fichiers(ctx.root, c["fichier"])
    if not fichiers:
        return False, f"{c['fichier']} absent"
    texte = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in fichiers)
    plier = c.get("insensible_casse", True)
    cherche = texte.casefold() if plier else texte
    norm = (lambda s: s.casefold()) if plier else (lambda s: s)
    contient = [c["contient"]] if isinstance(c.get("contient"), str) else list(c.get("contient") or [])
    absent = [c["absent"]] if isinstance(c.get("absent"), str) else list(c.get("absent") or [])
    manquants = [t for t in contient if norm(t) not in cherche]
    if contient and c.get("au_moins_un"):
        manquants = [] if len(manquants) < len(contient) else manquants
    presents = [t for t in absent if norm(t) in cherche]
    defauts = []
    if manquants:
        defauts.append("absent : " + ", ".join(manquants))
    if presents:
        defauts.append("encore présent : " + ", ".join(presents))
    if c.get("motif") and not re.search(c["motif"], texte, re.IGNORECASE if plier else 0):
        defauts.append(f"motif {c['motif']} introuvable")
    return not defauts, "; ".join(defauts) or "conforme"


def _port_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _lire_page(url: str) -> tuple[int | None, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _critere_http(c: dict, ctx: Contexte) -> tuple[bool, str]:
    kit = projets.kit_du_projet(ctx.root) or {}
    commande = c.get("commande") or kit.get("start")
    if not commande:
        return False, "pas de kit ni de commande de démarrage"
    pages = c.get("pages") or [{"chemin": kit.get("sante", "/")}]
    attendu = int(c.get("statut", 200))
    port = _port_libre()
    nom = "banc-evaluation"
    demarrage = server.start_server(ctx.root, commande.format(port=port), port=port, name=nom)
    if demarrage.startswith("échec"):
        return False, "l'application ne démarre pas : " + demarrage[:800]
    defauts = []
    try:
        for page in pages:
            page = {"chemin": page} if isinstance(page, str) else page
            statut, corps = _lire_page(f"http://127.0.0.1:{port}{page['chemin']}")
            if statut != attendu:
                defauts.append(f"{page['chemin']} répond {statut}")
                continue
            manquants = [t for t in page.get("contient") or [] if t.casefold() not in corps.casefold()]
            if manquants:
                defauts.append(f"{page['chemin']} sans « {', '.join(manquants)} »")
    finally:
        server.stop_server(ctx.root, nom)
    return not defauts, "; ".join(defauts) or f"{len(pages)} page(s) en {attendu}"


def compter_tests(root: Path) -> tuple[int, int, str]:
    """Lance les tests du projet : (réussis, en échec ou en erreur, sortie)."""
    kit = projets.kit_du_projet(root) or {}
    sortie = shell.run_command(root, kit.get("test", "uv run pytest -q"), timeout=900)
    compte: dict[str, int] = {}
    for n, k in re.findall(r"(\d+) (passed|failed|errors?)\b", sortie):
        compte[k.rstrip("s") if k.startswith("error") else k] = int(n)
    return compte.get("passed", 0), compte.get("failed", 0) + compte.get("error", 0), sortie


def _critere_tests(c: dict, ctx: Contexte) -> tuple[bool, str]:
    if "tests" not in ctx.cache:
        ctx.cache["tests"] = compter_tests(ctx.root)
    reussis, echoues, sortie = ctx.cache["tests"]  # type: ignore[misc]
    minimum = int(c.get("min", 0))
    if "depart_plus" in c:
        if ctx.tests_depart is None:
            return False, "nombre de tests de départ inconnu"
        minimum = max(minimum, ctx.tests_depart + int(c["depart_plus"]))
    detail = f"{reussis} réussi(s), {echoues} en échec (minimum {minimum})"
    if ctx.tests_depart is not None:
        detail += f", {ctx.tests_depart} au départ"
    if not reussis and not echoues:
        detail += " ; sortie : " + sortie[-400:]
    return reussis >= minimum, detail


def _critere_plan(c: dict, ctx: Contexte) -> tuple[bool, str]:
    plan = ctx.root / c.get("fichier", "PLAN.md")
    if not plan.is_file():
        return bool(c.get("facultatif")), f"{plan.name} absent"
    cases = re.findall(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+)$", plan.read_text(encoding="utf-8", errors="replace"), re.M)
    ouvertes = [texte.strip() for marque, texte in cases if marque == " "]
    if not cases:
        return False, f"{plan.name} sans cases à cocher"
    if ouvertes:
        return False, f"{len(ouvertes)}/{len(cases)} case(s) non cochée(s) : " + " ; ".join(ouvertes[:5])
    return True, f"{len(cases)} case(s) cochée(s)"


def _critere_hors_projet(c: dict, ctx: Contexte) -> tuple[bool, str]:
    apres = instantane_hors_projet(ctx.dossier, ctx.root)
    changes = sorted(k for k in set(apres) | set(ctx.instantane) if apres.get(k) != ctx.instantane.get(k))
    tentatives = f" ; {ctx.refus} tentative(s) refusée(s)" if ctx.refus else ""
    if changes:
        return False, "modifié hors du projet : " + ", ".join(changes[:10]) + tentatives
    return True, "rien hors du projet" + tentatives


def _critere_inchange(c: dict, ctx: Contexte) -> tuple[bool, str]:
    defauts = []
    attendus = {k: v for k, v in ctx.empreintes.items()
                if any(fnmatch.fnmatch(k, m.replace("\\", "/").lstrip("/")) for m in c["fichiers"])}
    for relatif, empreinte in sorted(attendus.items()):
        chemin = ctx.root / relatif
        if not chemin.is_file():
            defauts.append(f"{relatif} supprimé")
        elif _empreinte(chemin) != empreinte:
            defauts.append(f"{relatif} modifié")
    if not attendus:
        return True, "aucun fichier à surveiller au départ"
    return not defauts, "; ".join(defauts) or f"{len(attendus)} fichier(s) intact(s)"


EVALUATEURS: dict[str, Callable[[dict, Contexte], tuple[bool, str]]] = {
    "verification": _critere_verification, "fichier": _critere_fichier, "module": _critere_module,
    "texte": _critere_texte, "http": _critere_http, "tests": _critere_tests, "plan": _critere_plan,
    "hors_projet": _critere_hors_projet, "inchange": _critere_inchange,
}


def libelle(c: dict) -> str:
    if c.get("libelle"):
        return str(c["libelle"])
    t = c["type"]
    if t == "verification":
        return "verify_project OK"
    if t == "fichier":
        return f"fichier {c['chemin']}"
    if t == "module":
        nom = c.get("nom") or c.get("motif")
        return f"module {', '.join(nom) if isinstance(nom, list) else nom}"
    if t == "texte":
        return f"texte dans {c['fichier']}"
    if t == "http":
        return "pages HTTP"
    if t == "tests":
        if "depart_plus" in c:
            return f"au moins {c['depart_plus']} test(s) réussi(s) de plus qu'au départ"
        return f"au moins {c['min']} tests réussis"
    if t == "plan":
        return "PLAN.md entièrement coché"
    if t == "hors_projet":
        return "rien modifié hors du projet"
    return t


def evaluer(criteres: list[dict], ctx: Contexte, resultats: list[dict] | None = None) -> list[dict]:
    """Évalue les critères dans l'ordre ; `resultats` reçoit chaque verdict au fil de l'eau
    (utile si Ctrl+C interrompt l'évaluation)."""
    resultats = [] if resultats is None else resultats
    for c in criteres:
        try:
            ok, detail = EVALUATEURS[c["type"]](c, ctx)
        except Exception as exc:  # noqa: BLE001 - un critère cassé compte en échec, il n'arrête pas le banc
            ok, detail = False, f"erreur pendant l'évaluation : {type(exc).__name__}: {exc}"
        resultats.append({"type": c["type"], "libelle": libelle(c), "poids": c.get("poids", 1), "ok": bool(ok),
                          "detail": str(detail)[:2000]})
    return resultats


def score(criteres: list[dict]) -> float:
    total = sum(c["poids"] for c in criteres)
    return round(100 * sum(c["poids"] for c in criteres if c["ok"]) / total, 1) if total else 0.0


# --- exécution -----------------------------------------------------------------------------

class ClientSurveille:
    """Enveloppe du client : compte les appels et les erreurs, et coupe au plafond de temps."""

    def __init__(self, client, echeance: float):
        self.client = client
        self.echeance = echeance
        self.appels = 0
        self.erreurs = 0

    def _controle(self) -> None:
        if time.monotonic() >= self.echeance:
            raise TempsDepasse("plafond de temps atteint")

    def chat(self, *args, **kwargs):
        self._controle()
        self.appels += 1
        try:
            return self.client.chat(*args, **kwargs)
        except LLMError:
            self.erreurs += 1
            raise

    def _chat_events(self, *args, **kwargs):
        self._controle()
        self.appels += 1
        try:
            yield from self.client.chat_events(*args, **kwargs)
        except LLMError:
            self.erreurs += 1
            raise

    def embed(self, *args, **kwargs):
        return self.client.embed(*args, **kwargs)

    def __getattr__(self, nom: str):
        if nom == "chat_events" and hasattr(self.client, "chat_events"):
            return self._chat_events
        return getattr(self.client, nom)


class AgentBanc(Agent):
    """Agent qui cumule les statistiques de chaque réponse (ask_autonomous en enchaîne plusieurs)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cumul = {"tours": 0, "tokens_generes": 0, "temps_generation": 0.0, "contexte_max": 0,
                      "reponses": 0, "estime": False}

    def ask(self, user_message: str, remember: bool = True) -> str:
        reponse = super().ask(user_message, remember)
        st = self.last_stats
        self.cumul["tours"] += st.rounds
        self.cumul["tokens_generes"] += st.completion_tokens
        self.cumul["temps_generation"] += st.generation_time
        self.cumul["contexte_max"] = max(self.cumul["contexte_max"], st.prompt_tokens)
        self.cumul["reponses"] += 1
        self.cumul["estime"] = self.cumul["estime"] or st.estimated
        return reponse


def _slug(texte: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", texte).strip("-.") or "modele"


def _journal(chemin: Path, **evenement) -> None:
    evenement["t"] = round(time.time(), 2)
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(evenement, ensure_ascii=False, default=str) + "\n")


@dataclass
class Combinaison:
    modele: str              # modèle demandé
    contexte: int
    modele_servi: str = ""   # variante Ollama éventuelle
    avertissement: str = ""

    @property
    def etiquette(self) -> str:
        return f"{self.modele} · {_ctx(self.contexte)}"


def _ctx(contexte: int) -> str:
    return f"{contexte // 1024}k" if contexte % 1024 == 0 else str(contexte)


def executer(scenario: Scenario, cfg: config_mod.Config, combi: Combinaison, dossier: Path, repetition: int = 1,
             limite_etapes: int | None = None, temps_max: float = TEMPS_MAX_DEFAUT,
             client=None, console: Console | None = None) -> dict:
    """Une exécution d'un scénario dans `dossier` (neuf). Renvoie le résultat, même interrompu :
    le statut vaut alors « interrompu »."""
    console = console or Console(quiet=True)
    dossier.mkdir(parents=True, exist_ok=True)
    root = dossier / scenario.dossier_projet
    journal = dossier / "journal.jsonl"
    limite = limite_etapes or scenario.limite_etapes
    res: dict = {"scenario": scenario.nom, "modele": combi.modele, "modele_servi": combi.modele_servi or combi.modele,
                 "contexte": combi.contexte, "configuration": combi.etiquette, "repetition": repetition,
                 "dossier": str(dossier), "autonome": scenario.autonome, "limite_etapes": limite,
                 "statut": "termine", "erreur": None, "duree": 0.0, "etapes": 0, "tours": 0, "appels_outils": 0,
                 "appels_llm": 0, "tokens_generes": 0, "debit": 0.0, "contexte_max": 0, "tokens_estimes": False,
                 "verifications": 0, "verifications_echec": 0, "erreurs_llm": 0, "refus_outils": 0,
                 "criteres": [], "score": 0.0, "reussi": False, "reponse_finale": ""}
    if combi.avertissement:
        res["avertissement"] = combi.avertissement
    t0 = time.monotonic()
    ctx = Contexte(root=root, dossier=dossier)   # remplacé une fois le projet préparé
    agent = None
    surveille = None
    evaluer_criteres = True
    try:
        preparer(scenario, root)
        ctx = contexte_de_depart(scenario, dossier, root)

        run_cfg = dataclasses.replace(cfg, model=combi.modele_servi or combi.modele, context_tokens=combi.contexte,
                                      permission_mode="auto", auto_learn=False, max_autonomous_steps=limite)
        brut = client or Client(run_cfg.base_url, run_cfg.api_key, timeout=run_cfg.timeout_seconds)
        surveille = ClientSurveille(brut, t0 + temps_max * 60)
        store = MemoryStore(dossier / "memoire-banc.sqlite",
                            embedder=lambda textes: brut.embed(run_cfg.embedding_model, textes))

        def on_tool(nom: str, args: dict, resultat: str) -> None:
            res["appels_outils"] += 1
            premiere = resultat.splitlines()[0] if resultat else ""
            if premiere.startswith("Commande refusée") or "hors du projet" in premiere:
                res["refus_outils"] += 1
                ctx.refus += 1
            if nom == "verify_project":            # vérification demandée par le modèle lui-même
                res["verifications"] += 1
                res["verifications_echec"] += 0 if resultat.startswith("OK") else 1
            _journal(journal, evenement="outil", nom=nom, arguments=_court(args), resultat=resultat[:1500])
            console.print(f"    [dim]· {escape(nom)} → {escape(premiere[:90])}[/]")

        def on_step(etape: int, total: int) -> None:
            res["etapes"] += 1
            _journal(journal, evenement="etape", numero=etape, limite=total)
            console.print(f"  [bold]étape {res['etapes']}[/][dim]/{limite}[/]")

        def on_check(verdict: str) -> None:
            res["verifications"] += 1
            ok = verdict.startswith("OK")
            if not ok:
                res["verifications_echec"] += 1
            _journal(journal, evenement="verification", ok=ok, verdict=verdict[:3000])
            console.print(f"    {'[green]vérification OK[/]' if ok else '[red]vérification en échec[/]'}")

        agent = AgentBanc(run_cfg, root, client=surveille, store=store, on_tool=on_tool, confirm=None)
        _journal(journal, evenement="debut", scenario=scenario.nom, modele=run_cfg.model, contexte=run_cfg.context_tokens)
        reprises = 0
        demande = scenario.demande
        while True:
            try:
                if scenario.autonome:
                    agent.config.max_autonomous_steps = max(1, limite - res["etapes"])
                    reponse = agent.ask_autonomous(demande, on_step=on_step, on_check=on_check)
                else:
                    on_step(1, 1)
                    reponse = agent.ask(demande)
                res["reponse_finale"] = reponse[-3000:]
                break
            except LLMError as exc:
                res["erreur"] = str(exc)[:1000]
                _journal(journal, evenement="erreur_llm", message=str(exc))
                console.print(f"    [red]erreur du serveur d'inférence :[/] {escape(str(exc)[:200])}")
                if not scenario.autonome or reprises >= REPRISES_APRES_ERREUR or res["etapes"] >= limite:
                    res["statut"] = "erreur_llm"
                    break
                reprises += 1
                demande = (f"{scenario.demande}\n\n(Reprise après une coupure du serveur : relis PLAN.md et le "
                           "projet, puis continue là où tu t'es arrêté. Sois autonome.)")
    except TempsDepasse:
        res["statut"] = "temps_depasse"
        res["erreur"] = f"plafond de {temps_max:g} min atteint"
        console.print(f"    [yellow]plafond de {temps_max:g} min atteint[/]")
    except KeyboardInterrupt:
        res["statut"] = "interrompu"
        evaluer_criteres = False
    except Exception as exc:  # noqa: BLE001 - un scénario qui plante ne doit pas arrêter le banc
        res["statut"] = "erreur"
        res["erreur"] = f"{type(exc).__name__}: {exc}"
        _journal(journal, evenement="erreur", trace=traceback.format_exc())
        console.print(f"    [red]erreur :[/] {escape(res['erreur'][:300])}")
    finally:
        server.stop_all()
    res["duree"] = round(time.monotonic() - t0, 1)
    if agent is not None:
        cumul = agent.cumul
        res.update(tours=cumul["tours"], tokens_generes=cumul["tokens_generes"], contexte_max=cumul["contexte_max"],
                   tokens_estimes=cumul["estime"],
                   debit=round(cumul["tokens_generes"] / cumul["temps_generation"], 1) if cumul["temps_generation"] else 0.0)
        agent.store.close()
    if surveille is not None:
        res.update(appels_llm=surveille.appels, erreurs_llm=surveille.erreurs)

    if evaluer_criteres and agent is not None:
        console.print("  [dim]évaluation des critères…[/]")
        try:
            evaluer(scenario.criteres, ctx, res["criteres"])
        except KeyboardInterrupt:
            res["statut"] = "interrompu"
        finally:
            server.stop_all()
    res["score"] = score(res["criteres"]) if res["criteres"] else 0.0
    res["reussi"] = bool(res["criteres"]) and all(c["ok"] for c in res["criteres"]) and len(res["criteres"]) == len(scenario.criteres)
    _journal(journal, evenement="fin", statut=res["statut"], score=res["score"])
    return res


def _court(args: dict) -> dict:
    return {k: (v if len(repr(v)) <= 300 else repr(v)[:300] + "…") for k, v in (args or {}).items()}


# --- banc complet --------------------------------------------------------------------------

def preparer_combinaisons(cfg: config_mod.Config, modeles: list[str], contextes: list[int],
                          ollama_actif: bool = True) -> list[Combinaison]:
    """Modèles × contextes. Avec Ollama, la variante au bon contexte est créée au besoin
    (comme « cerveau context ») ; sinon on note le contexte réellement servi."""
    combis = []
    for modele in modeles:
        for contexte in contextes:
            combi = Combinaison(modele, contexte)
            if ollama_actif and ollama.is_ollama(cfg.base_url):
                try:
                    servi, _ = ollama.context_of(cfg.base_url, modele)
                    if servi != contexte:
                        combi.modele_servi = ollama.ensure_context(cfg.base_url, modele, contexte)
                except LLMError as exc:
                    combi.avertissement = f"contexte non vérifié auprès d'Ollama : {exc}"
            combis.append(combi)
    return combis


def synthese(executions: list[dict]) -> list[dict]:
    groupes: dict[tuple[str, str], list[dict]] = {}
    for e in executions:
        groupes.setdefault((e["scenario"], e["configuration"]), []).append(e)
    lignes = []
    for (scenario, configuration), items in groupes.items():
        n = len(items)
        moy = lambda k: round(sum(float(i.get(k) or 0) for i in items) / n, 1)  # noqa: E731
        lignes.append({"scenario": scenario, "configuration": configuration, "modele": items[0]["modele"],
                       "contexte": items[0]["contexte"], "executions": n,
                       "reussites": sum(1 for i in items if i["reussi"]), "score_moyen": moy("score"),
                       "duree_moyenne": moy("duree"), "debit_moyen": moy("debit"), "etapes_moyennes": moy("etapes"),
                       "tokens_moyens": moy("tokens_generes"), "verifications_echec": sum(i["verifications_echec"] for i in items),
                       "erreurs_llm": sum(i["erreurs_llm"] for i in items)})
    return lignes


def lancer_banc(scenarios: list[Scenario], cfg: config_mod.Config, combis: list[Combinaison], dossier_runs: Path,
                repetitions: int = 1, limite_etapes: int | None = None, temps_max: float = TEMPS_MAX_DEFAUT,
                rapport_json: Path | None = None, client_factory: Callable[[], object] | None = None,
                console: Console | None = None) -> dict:
    """Exécute scénarios × combinaisons × répétitions ; le rapport est réécrit après chaque exécution."""
    console = console or Console(quiet=True)
    horodatage = time.strftime("%Y%m%d-%H%M%S")
    base = dossier_runs / horodatage
    suffixe = 1
    while base.exists():
        suffixe += 1
        base = dossier_runs / f"{horodatage}-{suffixe}"
    base.mkdir(parents=True)
    rapport_json = rapport_json or base / "rapport.json"
    plusieurs_contextes = len({c.contexte for c in combis}) > 1
    rapport: dict = {
        "version": 1, "cerveau": __version__, "debut": time.strftime("%Y-%m-%dT%H:%M:%S"), "fin": None,
        "interrompu": False, "dossier": str(base),
        "machine": {"systeme": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
                    "processeur": platform.processor() or platform.machine(), "materiel": cfg.hardware},
        "config": {"base_url": cfg.base_url, "embedding_model": cfg.embedding_model, "temps_max_min": temps_max,
                   "repetitions": repetitions, "limite_etapes": limite_etapes, "temperature": cfg.temperature,
                   "max_tool_rounds": cfg.max_tool_rounds, "web": cfg.web_enabled},
        "scenarios": {s.nom: s.resume() for s in scenarios},
        "executions": [], "synthese": [],
    }
    total = len(scenarios) * len(combis) * repetitions
    numero = 0
    try:
        for scenario in scenarios:
            for combi in combis:
                for n in range(1, repetitions + 1):
                    numero += 1
                    nom = f"{scenario.nom}-{_slug(combi.modele)}" + (f"-{_ctx(combi.contexte)}" if plusieurs_contextes else "") + f"-{n}"
                    console.rule(f"[{numero}/{total}] {escape(scenario.nom)} · {escape(combi.etiquette)} · essai {n}")
                    res = executer(scenario, cfg, combi, base / nom, repetition=n, limite_etapes=limite_etapes,
                                   temps_max=temps_max, client=client_factory() if client_factory else None,
                                   console=console)
                    rapport["executions"].append(res)
                    _afficher_resultat(console, res)
                    if res["statut"] == "interrompu":
                        raise KeyboardInterrupt
                    ecrire_rapports(rapport, rapport_json)
    except KeyboardInterrupt:
        rapport["interrompu"] = True
        console.print("[yellow]banc interrompu : serveurs arrêtés, rapport partiel écrit.[/]")
    finally:
        server.stop_all()
        ecrire_rapports(rapport, rapport_json)
    return rapport


def ecrire_rapports(rapport: dict, chemin_json: Path) -> tuple[Path, Path]:
    rapport["fin"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rapport["synthese"] = synthese(rapport["executions"])
    chemin_json = Path(chemin_json)
    chemin_json.parent.mkdir(parents=True, exist_ok=True)
    chemin_json.write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    chemin_md = chemin_json.with_suffix(".md")
    chemin_md.write_text(rapport_markdown(rapport), encoding="utf-8")
    rapport["fichiers"] = {"json": str(chemin_json), "markdown": str(chemin_md)}
    return chemin_json, chemin_md


# --- rapports ------------------------------------------------------------------------------

def duree(secondes: float) -> str:
    secondes = int(round(secondes or 0))
    if secondes < 60:
        return f"{secondes} s"
    if secondes < 3600:
        return f"{secondes // 60} min {secondes % 60:02d} s"
    return f"{secondes // 3600} h {secondes % 3600 // 60:02d} min"


def _cellule(md: str) -> str:
    return md.replace("|", "\\|").replace("\n", " ")


def rapport_markdown(rapport: dict) -> str:
    lignes = [f"# Banc d'essai Cerveau — {rapport['debut'].replace('T', ' ')}", ""]
    cfg = rapport["config"]
    lignes.append(f"Serveur `{cfg['base_url']}` · {cfg['repetitions']} essai(s) par combinaison · plafond "
                  f"{cfg['temps_max_min']:g} min · machine {rapport['machine']['systeme']}")
    if rapport.get("interrompu"):
        lignes.append("")
        lignes.append("**Banc interrompu : rapport partiel.**")
    synth = rapport.get("synthese") or synthese(rapport["executions"])
    configurations = list(dict.fromkeys(s["configuration"] for s in synth))
    scenarios = list(dict.fromkeys(s["scenario"] for s in synth))
    par_cle = {(s["scenario"], s["configuration"]): s for s in synth}
    lignes += ["", "## Scores", "", "| Scénario | " + " | ".join(_cellule(c) for c in configurations) + " |",
               "|---|" + "---|" * len(configurations)]
    for scenario in scenarios:
        cellules = []
        for configuration in configurations:
            s = par_cle.get((scenario, configuration))
            cellules.append("—" if s is None else
                            f"{s['score_moyen']:g} % ({s['reussites']}/{s['executions']}) · {duree(s['duree_moyenne'])} · "
                            f"{s['debit_moyen']:g} tok/s")
        lignes.append(f"| {scenario} | " + " | ".join(cellules) + " |")
    lignes += ["", "## Exécutions", "",
               "| Scénario | Configuration | Essai | Statut | Score | Durée | Étapes | Tours | Outils | Tokens | Débit | "
               "Contexte max | Vérifs en échec | Erreurs LLM |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in rapport["executions"]:
        tokens = f"{'~' if e['tokens_estimes'] else ''}{e['tokens_generes']}"
        lignes.append(f"| {e['scenario']} | {_cellule(e['configuration'])} | {e['repetition']} | {e['statut']} | "
                      f"{e['score']:g} % | {duree(e['duree'])} | {e['etapes']} | {e['tours']} | {e['appels_outils']} | "
                      f"{tokens} | {e['debit']:g} tok/s | {e['contexte_max']} | "
                      f"{e['verifications_echec']}/{e['verifications']} | {e['erreurs_llm']} |")
    echecs = [(e, c) for e in rapport["executions"] for c in e["criteres"] if not c["ok"]]
    problemes = [e for e in rapport["executions"] if e.get("erreur") or e.get("avertissement")]
    if echecs or problemes:
        lignes += ["", "## Ce qui a échoué", ""]
    for e in problemes:
        texte = e.get("erreur") or e.get("avertissement")
        lignes.append(f"- **{e['scenario']} · {_cellule(e['configuration'])} · essai {e['repetition']}** "
                      f"({e['statut']}) : {_cellule(texte[:400])}")
    for e, c in echecs:
        lignes.append(f"- **{e['scenario']} · {_cellule(e['configuration'])} · essai {e['repetition']}** — "
                      f"{c['libelle']} : {_cellule(c['detail'][:400])}")
    lignes += ["", "## Dossiers", ""]
    for e in rapport["executions"]:
        lignes.append(f"- {e['scenario']} · {_cellule(e['configuration'])} · essai {e['repetition']} : `{e['dossier']}`")
    return "\n".join(lignes) + "\n"


def _afficher_resultat(console: Console, e: dict) -> None:
    couleur = "green" if e["reussi"] else ("red" if e["score"] < 50 else "yellow")
    console.print(f"  [{couleur}]{e['score']:g} %[/] · {e['statut']} · {duree(e['duree'])} · {e['etapes']} étape(s) · "
                  f"{e['appels_outils']} outil(s) · {e['debit']:g} tok/s")
    for c in e["criteres"]:
        if not c["ok"]:
            console.print(f"    [red]✗[/] {escape(c['libelle'])} : [dim]{escape(c['detail'][:160])}[/]")


def afficher_synthese(console: Console, rapport: dict) -> None:
    table = Table(title="Banc d'essai", show_lines=False)
    for col in ("Scénario", "Configuration", "Score", "Réussis", "Durée", "Débit", "Vérifs KO", "Erreurs LLM"):
        table.add_column(col, justify="left" if col in ("Scénario", "Configuration") else "right")
    for s in rapport.get("synthese") or synthese(rapport["executions"]):
        table.add_row(s["scenario"], escape(s["configuration"]), f"{s['score_moyen']:g} %",
                      f"{s['reussites']}/{s['executions']}", duree(s["duree_moyenne"]), f"{s['debit_moyen']:g} tok/s",
                      str(s["verifications_echec"]), str(s["erreurs_llm"]))
    console.print(table)


def comparer(avant: dict, apres: dict) -> list[dict]:
    """Évolution par scénario et configuration entre deux rapports."""
    a = {(s["scenario"], s["configuration"]): s for s in avant.get("synthese") or synthese(avant["executions"])}
    b = {(s["scenario"], s["configuration"]): s for s in apres.get("synthese") or synthese(apres["executions"])}
    lignes = []
    for cle in list(dict.fromkeys([*a, *b])):
        sa, sb = a.get(cle), b.get(cle)
        lignes.append({
            "scenario": cle[0], "configuration": cle[1],
            "score_avant": sa["score_moyen"] if sa else None, "score_apres": sb["score_moyen"] if sb else None,
            "delta": round(sb["score_moyen"] - sa["score_moyen"], 1) if sa and sb else None,
            "duree_avant": sa["duree_moyenne"] if sa else None, "duree_apres": sb["duree_moyenne"] if sb else None,
            "debit_avant": sa["debit_moyen"] if sa else None, "debit_apres": sb["debit_moyen"] if sb else None,
        })
    return lignes


def afficher_comparaison(console: Console, lignes: list[dict]) -> None:
    table = Table(title="Évolution entre les deux rapports")
    for col in ("Scénario", "Configuration", "Score", "Δ", "Durée", "Débit"):
        table.add_column(col, justify="left" if col in ("Scénario", "Configuration") else "right")
    fmt = lambda v, f: "—" if v is None else f(v)  # noqa: E731
    for l in lignes:
        delta = l["delta"]
        couleur = "dim" if delta is None or delta == 0 else ("green" if delta > 0 else "red")
        table.add_row(l["scenario"], escape(l["configuration"]),
                      f"{fmt(l['score_avant'], lambda v: f'{v:g} %')} → {fmt(l['score_apres'], lambda v: f'{v:g} %')}",
                      f"[{couleur}]{fmt(delta, lambda v: f'{v:+g}')}[/]",
                      f"{fmt(l['duree_avant'], duree)} → {fmt(l['duree_apres'], duree)}",
                      f"{fmt(l['debit_avant'], lambda v: f'{v:g}')} → {fmt(l['debit_apres'], lambda v: f'{v:g}')} tok/s")
    console.print(table)


# --- ligne de commande ---------------------------------------------------------------------

def construire_parseur(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    p = parser or argparse.ArgumentParser(prog="cerveau banc", description=__doc__,
                                          formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenarios", nargs="*", help="noms (ou fichiers .json) des scénarios ; par défaut tous")
    p.add_argument("--modele", nargs="+", action="extend", default=[], metavar="M",
                   help="modèle(s) à tester ; par défaut le modèle actif")
    p.add_argument("--contexte", nargs="+", action="extend", type=int, default=[], metavar="N",
                   help="taille(s) de contexte en tokens ; par défaut celle de la config")
    p.add_argument("--repetitions", type=int, default=1, metavar="R", help="essais par combinaison (défaut 1)")
    p.add_argument("--dossier", type=Path, metavar="D", help="dossier des exécutions (défaut ~/.cerveau/banc/runs)")
    p.add_argument("--limite-etapes", type=int, metavar="K", help="remplace la limite d'étapes des scénarios")
    p.add_argument("--temps-max", type=float, default=TEMPS_MAX_DEFAUT, metavar="MIN",
                   help=f"plafond par exécution, en minutes (défaut {TEMPS_MAX_DEFAUT:g})")
    p.add_argument("--rapport", type=Path, metavar="CHEMIN", help="rapport JSON (le .md est écrit à côté)")
    p.add_argument("--comparer", nargs=2, type=Path, metavar=("AVANT", "APRES"), help="compare deux rapports JSON")
    p.add_argument("--liste", action="store_true", help="liste les scénarios disponibles")
    return p


def main_banc(argv: list[str], client_factory: Callable[[], object] | None = None,
              console: Console | None = None) -> int:
    """Point d'entrée de « cerveau banc ». client_factory sert aux tests (faux modèle)."""
    args = construire_parseur().parse_args(argv)
    console = console or Console(highlight=False)

    if args.comparer:
        try:
            avant, apres = (json.loads(p.read_text(encoding="utf-8")) for p in args.comparer)
        except (OSError, ValueError) as exc:
            console.print(f"[red]✗ rapport illisible :[/] {escape(str(exc))}")
            return 1
        afficher_comparaison(console, comparer(avant, apres))
        return 0

    disponibles, erreurs = charger_scenarios()
    for erreur in erreurs:
        console.print(f"[yellow]scénario ignoré :[/] {escape(erreur)}")
    if args.liste:
        for s in disponibles.values():
            mode = "autonome" if s.autonome else "une demande"
            console.print(f"[bold]{s.nom:<18}[/] {escape(s.description)} [dim]({mode}, {len(s.criteres)} critères)[/]")
        console.print(f"[dim]Les vôtres vont dans {escape(str(config_mod.HOME / 'banc'))} (fichiers .json).[/]")
        return 0

    choisis: list[Scenario] = []
    for nom in args.scenarios or list(disponibles):
        if nom in disponibles:
            choisis.append(disponibles[nom])
        elif nom.endswith(".json") and Path(nom).is_file():
            try:
                choisis.append(charger_scenario(Path(nom)))
            except ScenarioInvalide as exc:
                console.print(f"[red]✗ {escape(str(exc))}[/]")
                return 1
        else:
            console.print(f"[red]✗ scénario inconnu : {escape(nom)}[/] ; disponibles : {', '.join(disponibles)}")
            return 1
    if not choisis:
        console.print("[red]✗ aucun scénario[/]")
        return 1
    if args.repetitions < 1 or (args.limite_etapes is not None and args.limite_etapes < 1) or args.temps_max <= 0:
        console.print("[red]✗ répétitions, limite d'étapes et temps maximal doivent être positifs[/]")
        return 1

    cfg = config_mod.load()
    if client_factory is None:
        try:
            Client(cfg.base_url, cfg.api_key).models()
        except LLMError as exc:
            console.print(f"[red]✗ {escape(str(exc))}[/]")
            return 1
    modeles = list(dict.fromkeys(args.modele)) or [cfg.model]
    contextes = list(dict.fromkeys(args.contexte)) or [cfg.context_tokens]
    try:
        combis = preparer_combinaisons(cfg, modeles, contextes, ollama_actif=client_factory is None)
    except LLMError as exc:
        console.print(f"[red]✗ {escape(str(exc))}[/]")
        return 1
    except KeyboardInterrupt:
        console.print("[yellow]banc annulé avant le premier scénario.[/]")
        return 130
    for combi in combis:
        if combi.modele_servi and combi.modele_servi != combi.modele:
            console.print(f"[dim]{escape(combi.etiquette)} : servi par la variante {escape(combi.modele_servi)}[/]")
        if combi.avertissement:
            console.print(f"[yellow]{escape(combi.etiquette)} : {escape(combi.avertissement)}[/]")
    dossier = args.dossier or (config_mod.HOME / "banc" / "runs")
    console.print(f"Banc : {len(choisis)} scénario(s) × {len(combis)} configuration(s) × {args.repetitions} essai(s), "
                  f"plafond {args.temps_max:g} min par essai, serveur {escape(cfg.base_url)}")
    rapport = lancer_banc(choisis, cfg, combis, dossier, repetitions=args.repetitions, limite_etapes=args.limite_etapes,
                          temps_max=args.temps_max, rapport_json=args.rapport, client_factory=client_factory,
                          console=console)
    afficher_synthese(console, rapport)
    fichiers = rapport.get("fichiers", {})
    console.print(f"Rapport : {escape(fichiers.get('json', ''))}\n          {escape(fichiers.get('markdown', ''))}")
    return 130 if rapport["interrompu"] else 0
