"""Kits de projet, modules déclaratifs et vérification.

Un kit est une base de projet éprouvée et testée (cerveau/kits/<nom>/, ou ~/.cerveau/kits/<nom>/
pour les vôtres) : kit.json décrit comment tester et lancer, files/ est copié dans le projet.
Le petit modèle part de là au lieu d'écrire une application de zéro.

add_module écrit un module déclaratif du kit gestion à partir d'une description, et vérifie
qu'il se charge. verify_project fait ce qu'un humain ferait avant de valider : tests, démarrage,
réponse HTTP. Le cerveau s'en sert aussi lui-même en mode autonome.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from ..config import HOME
from .registry import Tool, truncate
from .shell import command_env, run_command

BUILTIN_KITS = Path(__file__).resolve().parent.parent / "kits"
KIT_DU_PROJET = Path(".cerveau") / "kit.json"


# --- kits -----------------------------------------------------------------------

def kits_disponibles() -> dict[str, dict]:
    """Kits livrés puis kits de l'utilisateur (prioritaires à nom égal)."""
    kits: dict[str, dict] = {}
    for dossier in (BUILTIN_KITS, HOME / "kits"):
        if not dossier.is_dir():
            continue
        for kit in sorted(p for p in dossier.iterdir() if (p / "kit.json").is_file()):
            try:
                meta = json.loads((kit / "kit.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            meta["chemin"] = str(kit)
            kits[kit.name] = meta
    return kits


def kit_du_projet(root: Path) -> dict | None:
    fichier = root / KIT_DU_PROJET
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _slug(titre: str) -> str:
    import unicodedata

    texte = unicodedata.normalize("NFKD", titre.lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", texte).strip("-") or "projet"


def _remplacer(texte: str, titre: str, slug: str, json_: bool) -> str:
    sur = json.dumps(titre, ensure_ascii=False)[1:-1] if json_ else titre.replace('"', "'").replace("\\", "/")
    return texte.replace("__TITRE__", sur).replace("__SLUG__", slug)


def packs_du_kit(kit: str) -> dict[str, dict]:
    meta = kits_disponibles().get(kit)
    packs: dict[str, dict] = {}
    if meta is None:
        return packs
    for dossier in (Path(meta["chemin"]) / "packs", HOME / "kits" / kit / "packs"):
        for fichier in sorted(dossier.glob("*.json")) if dossier.is_dir() else []:
            try:
                packs[fichier.stem] = json.loads(fichier.read_text(encoding="utf-8"))
            except ValueError:
                continue
    return packs


def appliquer_pack(root: Path, kit: str, nom: str) -> str:
    """Pack métier : retire les modules de base inutiles, ajoute rôles et modules du métier."""
    packs = packs_du_kit(kit)
    if nom not in packs:
        return f"échec : pack inconnu « {nom} ». Packs du kit {kit} : {', '.join(packs) or 'aucun'}"
    pack = packs[nom]
    for module in pack.get("retirer", []):
        (root / "app" / "modules" / f"{module}.py").unlink(missing_ok=True)
    if pack.get("roles"):
        config = root / "app" / "config.py"
        texte = config.read_text(encoding="utf-8")
        ajout = "".join(f'    "{cle}": "{libelle}",\n' for cle, libelle in pack["roles"].items() if f'"{cle}"' not in texte)
        config.write_text(re.sub(r"(ROLES = \{\n(?:.*\n)*?)\}", lambda m: m.group(1) + ajout + "}", texte, count=1),
                          encoding="utf-8")
    ajoutes = []
    for spec in pack.get("modules", []):
        resultat = add_module(root, verifier=False, **spec)
        if resultat.startswith("échec"):
            return f"échec du pack {nom} sur le module {spec.get('nom')} : {resultat}"
        ajoutes.append(spec["nom"])
    controle = _charger_application(root)
    if controle:
        return f"échec : l'application ne se charge plus après le pack {nom}.\n{controle}"
    texte = f"pack « {nom} » appliqué : modules {', '.join(ajoutes)}"
    if pack.get("retirer"):
        texte += f" ; retirés : {', '.join(pack['retirer'])}"
    if pack.get("note"):
        texte += f"\nÀ savoir : {pack['note']}"
    return texte


def create_project(root: Path, kit: str, title: str, pack: str = "") -> str:
    """Copie un kit dans le projet, avec le nom affiché du client ou de l'application."""
    kits = kits_disponibles()
    if kit not in kits:
        return f"échec : kit inconnu « {kit} ». Kits disponibles : {', '.join(kits) or 'aucun'}"
    meta = kits[kit]
    source = Path(meta["chemin"]) / "files"
    fichiers = [p for p in sorted(source.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    renommer = meta.get("renommer", {})
    destinations = []
    for fichier in fichiers:
        relatif = fichier.relative_to(source).as_posix()
        relatif = "/".join(renommer.get(part, part) for part in relatif.split("/"))
        destinations.append((fichier, root / relatif, relatif))
    deja = [rel for _, cible, rel in destinations if cible.exists() and rel not in ("README.md", ".gitignore")]
    if deja:
        return (f"échec : le projet contient déjà {', '.join(deja[:5])}. Un kit se pose dans un dossier vide ; "
                "pour un projet existant, lis son code et modifie-le.")
    slug = _slug(title)
    for fichier, cible, _ in destinations:
        cible.parent.mkdir(parents=True, exist_ok=True)
        if cible.exists():
            continue
        try:
            texte = fichier.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copyfile(fichier, cible)
            continue
        cible.write_text(_remplacer(texte, title, slug, fichier.suffix == ".json"), encoding="utf-8")
    (root / KIT_DU_PROJET).parent.mkdir(parents=True, exist_ok=True)
    infos = {k: v for k, v in meta.items() if k != "chemin"}
    infos["nom"] = kit
    (root / KIT_DU_PROJET).write_text(json.dumps(infos, ensure_ascii=False, indent=1), encoding="utf-8")
    start = meta["start"].format(port=meta.get("port", 8000))
    texte_pack = ""
    if pack:
        texte_pack = appliquer_pack(root, kit, pack) + "\n"
        if texte_pack.startswith("échec"):
            return texte_pack
    return (f"projet créé depuis le kit « {kit} » ({len(destinations)} fichiers), tests compris.\n{texte_pack}"
            f"{meta.get('suite', '')}\n"
            f"Étapes suivantes : verify_project (tests + démarrage), puis start_server « {start} » "
            f"(port {meta.get('port', 8000)}) et donne l'adresse à l'utilisateur.")


# --- points de reprise git ------------------------------------------------------------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def _depot_du_projet(root: Path) -> bool:
    """Vrai si le projet a son propre dépôt git (créé au besoin). Un projet rangé dans le dépôt
    d'un autre projet n'est jamais touché."""
    if not shutil.which("git"):
        return False
    haut = _git(root, "rev-parse", "--show-toplevel")
    if haut.returncode == 0:
        return Path(haut.stdout.strip()).resolve() == root.resolve()
    return _git(root, "init", "-q").returncode == 0


def checkpoint(root: Path, message: str) -> bool:
    """Commit de tout l'état vérifié : on peut toujours y revenir."""
    if not _depot_du_projet(root):
        return False
    _git(root, "add", "-A")
    identite = [] if _git(root, "config", "user.email").stdout.strip() else [
        "-c", "user.name=Cerveau", "-c", "user.email=cerveau@localhost"]
    return _git(root, *identite, "commit", "-q", "-m", message).returncode == 0


def set_aside(root: Path, message: str) -> bool:
    """Met les modifications non vérifiées de côté (git stash), sans rien effacer."""
    if not _depot_du_projet(root) or _git(root, "rev-parse", "HEAD").returncode != 0:
        return False
    resultat = _git(root, "stash", "push", "-u", "-m", message)
    return resultat.returncode == 0 and "No local changes" not in resultat.stdout


# --- vérification -------------------------------------------------------------------

def verify_project(root: Path) -> str:
    """Tests du projet, puis démarrage et réponse HTTP. Renvoie OK ou ce qui ne va pas."""
    kit = kit_du_projet(root)
    if kit is None:
        return "pas de kit pour ce projet : lance tes tests avec run_command."
    resultat = run_command(root, kit.get("test", "uv run pytest -q"), timeout=900)
    entete = resultat.splitlines()[0] if resultat else ""
    if not entete.endswith(" 0"):
        return "ÉCHEC des tests. Corrige avant toute autre chose :\n" + resultat
    from .server import _SERVERS, _http_status, start_server, stop_server

    port = int(kit.get("port", 8000)) + 50   # port à part pour ne pas gêner le serveur de l'utilisateur
    demarrage = start_server(root, kit["start"].format(port=port), port=port, name="verification")
    if demarrage.startswith("échec"):
        return "Tests OK, mais l'application ne démarre pas :\n" + demarrage
    try:
        statut = _http_status(f"http://127.0.0.1:{port}{kit.get('sante', '/')}")
    finally:
        if "verification" in _SERVERS:
            stop_server(root, "verification")
    if statut != 200:
        return f"Tests OK, mais {kit.get('sante', '/')} répond {statut} au démarrage. Regarde server_logs nom « verification »."
    resume = [l for l in resultat.splitlines() if re.search(r"\d+ passed", l)]
    return f"OK : tests verts ({resume[-1].strip() if resume else 'réussis'}), l'application démarre et répond."


# --- modules déclaratifs (kit gestion) -----------------------------------------------

TYPES = ("texte", "texte_long", "entier", "decimal", "monnaie", "date", "booleen", "choix", "email", "telephone",
         "ref", "numero", "calcul")


def _modules_existants(root: Path) -> set[str]:
    noms = set()
    for fichier in (root / "app" / "modules").glob("*.py"):
        if not fichier.name.startswith("_"):
            match = re.search(r'nom\s*=\s*"([a-z0-9_]+)"', fichier.read_text(encoding="utf-8", errors="replace"))
            if match:
                noms.add(match.group(1))
    return noms


def _champ_code(champ: dict, modules: set[str], erreurs: list[str]) -> str:
    nom = str(champ.get("nom", ""))
    type_ = champ.get("type", "texte")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", nom):
        erreurs.append(f"nom de champ invalide « {nom} » (minuscules, chiffres, _)")
    if type_ not in TYPES:
        erreurs.append(f"champ {nom} : type « {type_} » inconnu ; types : {', '.join(TYPES)}")
    morceaux = [repr(nom), repr(str(champ.get("libelle") or nom.replace("_", " ").capitalize()))]
    if type_ != "texte":
        morceaux.append(f"type={type_!r}")
    if champ.get("requis"):
        morceaux.append("requis=True")
    if type_ == "choix":
        choix = champ.get("choix") or []
        if not choix:
            erreurs.append(f"champ {nom} : un type choix demande la liste choix")
        morceaux.append(f"choix={[str(c) for c in choix]!r}")
    if type_ == "ref":
        cible = champ.get("ref", "")
        if cible not in modules:
            erreurs.append(f"champ {nom} : ref « {cible} » n'est pas un module existant ({', '.join(sorted(modules))})")
        morceaux.append(f"ref={cible!r}")
    if type_ == "numero":
        morceaux.append(f"format={champ.get('format') or nom[:2].upper() + '-{annee}-{n:04d}'!r}")
    if type_ == "calcul":
        expression = str(champ.get("calcul", "")).strip()
        if not expression:
            erreurs.append(f"champ {nom} : un calcul demande une expression Python sur obj, ex. « obj.prix * obj.quantite »")
        morceaux.append(f"calcul=lambda obj: {expression}")
        if champ.get("affichage"):
            morceaux.append(f"affichage={champ['affichage']!r}")
    defaut = champ.get("defaut")
    if defaut in ("aujourdhui", "aujourd'hui", "today") and type_ == "date":
        morceaux.append("defaut=date.today")
    elif defaut not in (None, ""):
        morceaux.append(f"defaut={defaut!r}")
    if champ.get("liste") is False:
        morceaux.append("liste=False")
    if champ.get("aide"):
        morceaux.append(f"aide={str(champ['aide'])!r}")
    return f"        Champ({', '.join(morceaux)}),"


def add_module(root: Path, nom: str, libelle: str, champs: list, objet: str = "", feminin: bool = False,
               ordre: int = 60, titre: str = "", document: bool = False, imprimable: bool = False,
               suppression: bool = True, planning: str = "", lecture: list | None = None,
               ecriture: list | None = None, journal_consultations: bool = False, verifier: bool = True) -> str:
    """Écrit app/modules/<nom>.py (module déclaratif du kit gestion) et vérifie qu'il se charge."""
    if not (root / "app" / "core" / "framework.py").is_file():
        return "échec : add_module sert aux projets du kit gestion (app/core/framework.py introuvable)."
    if not re.fullmatch(r"[a-z][a-z0-9_]*", nom or ""):
        return f"échec : nom de module invalide « {nom} » : minuscules sans accent, chiffres et _ (ex. vehicules)."
    fichier = root / "app" / "modules" / f"{nom}.py"
    if fichier.exists():
        return f"échec : app/modules/{nom}.py existe déjà. Lis-le et modifie-le avec edit_file."
    modules = _modules_existants(root) | {nom}
    if isinstance(champs, str):
        try:
            champs = json.loads(champs)
        except ValueError:
            return "échec : champs doit être une liste d'objets {nom, libelle, type, ...}"
    erreurs: list[str] = []
    lignes_champs = [_champ_code(c, modules, erreurs) for c in champs if isinstance(c, dict)]
    if not lignes_champs:
        erreurs.append("aucun champ : donne au moins un champ")
    if planning and planning not in [c.get("nom") for c in champs if isinstance(c, dict)]:
        erreurs.append(f"planning « {planning} » n'est pas un champ du module")
    if erreurs:
        return "échec, rien n'a été écrit :\n- " + "\n- ".join(erreurs)

    imports = ["from app.core.framework import Champ, Module"]
    if any("date.today" in l for l in lignes_champs):
        imports.insert(0, "from datetime import date\n")
    if document:
        imports.append("from app.modules._documents import champs_totaux, lignes_standard")
        lignes_champs.append("        *champs_totaux(),")
    options = [f'    nom="{nom}",', f"    libelle={libelle!r},"]
    if objet:
        options.append(f"    objet={objet!r},")
    if feminin:
        options.append("    feminin=True,")
    options.append(f"    ordre={int(ordre)},")
    if titre:
        options.append(f"    titre={titre!r},")
    if document:
        options.append("    lignes=lignes_standard(),")
    if imprimable or document:
        options.append("    imprimable=True,")
    if not suppression:
        options.append("    suppression=False,")
    if planning:
        options.append(f"    planning={planning!r},")
    if lecture:
        options.append(f"    lecture={list(lecture)!r},")
    if ecriture:
        options.append(f"    ecriture={list(ecriture)!r},")
    if journal_consultations or lecture:
        options.append("    journal_consultations=True,")
    code = (f'"""{libelle}."""\n\n' + "\n".join(imports) + "\n\nmodule = Module(\n" + "\n".join(options)
            + "\n    champs=[\n" + "\n".join(lignes_champs) + "\n    ],\n)\n")
    try:
        compile(code, str(fichier), "exec")
    except SyntaxError as exc:
        return f"échec : code invalide ({exc.msg}, ligne {exc.lineno}), vérifie les expressions de calcul.\n{code}"
    fichier.write_text(code, encoding="utf-8")
    controle = _charger_application(root) if verifier else ""
    if controle:
        fichier.unlink()
        return f"échec : le module ne se charge pas, fichier retiré.\n{controle}\n--- code proposé ---\n{code}"
    return (f"module créé : app/modules/{nom}.py. Il a déjà sa table, son menu, sa liste, son formulaire, son export "
            f"et ses tests génériques. Lance « uv run pytest -q ».\n--- app/modules/{nom}.py ---\n{code}")


def _charger_application(root: Path) -> str:
    """Charge l'application dans un processus à part ; renvoie l'erreur, ou "" si tout va bien."""
    if not shutil.which("uv"):
        return ""
    try:
        proc = subprocess.run(["uv", "run", "python", "-c", "import app.main"], cwd=root, env=command_env(root),
                              capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode == 0:
        return ""
    lignes = [l for l in proc.stderr.splitlines() if l.strip()]
    return truncate("\n".join(lignes[-12:]), 3000)


CHAMP_SCHEMA = {
    "type": "object",
    "properties": {
        "nom": {"type": "string", "description": "minuscules et _, ex. date_livraison ; un lien s'appelle client, pas client_id"},
        "libelle": {"type": "string", "description": "affiché à l'utilisateur, avec accents, ex. Kilométrage"},
        "type": {"type": "string", "enum": list(TYPES)},
        "requis": {"type": "boolean"},
        "choix": {"type": "array", "items": {"type": "string"}},
        "ref": {"type": "string", "description": "pour type ref : nom du module lié, ex. clients"},
        "calcul": {"type": "string", "description": "pour type calcul : expression sur obj, ex. obj.quantite * obj.prix"},
        "affichage": {"type": "string", "enum": ["monnaie", "decimal", "entier", "texte"]},
        "format": {"type": "string", "description": "pour type numero, ex. BC-{annee}-{n:04d}"},
        "defaut": {"description": "valeur par défaut ; « aujourdhui » pour une date"},
        "liste": {"type": "boolean", "description": "false pour ne pas l'afficher en colonne de liste"},
        "aide": {"type": "string"},
    },
    "required": ["nom", "libelle", "type"],
}

TOOLS = [
    Tool(
        name="create_project",
        description="Crée un projet à partir d'un kit éprouvé et testé, dans le dossier vide du projet. "
                    "Kits : gestion (ERP, CRM, facturation, stock, GMAO, planning…), site (site vitrine).",
        parameters={"type": "object", "properties": {
            "kit": {"type": "string", "description": "gestion ou site (ou un kit de ~/.cerveau/kits)"},
            "title": {"type": "string", "description": "Nom affiché, ex. Garage Dupont"},
            "pack": {"type": "string", "description": "kit gestion : pack métier qui ajoute les bons modules : "
                     "garage, menuiserie, espaces-verts, scierie, batiment, optique (vide sinon)"}},
            "required": ["kit", "title"]},
        run=create_project,
    ),
    Tool(
        name="add_module",
        description="Projet du kit gestion : ajoute un module métier complet (table, menu, liste, formulaire, export, "
                    "droits, tests automatiques) à partir de sa description. document=true pour un document chiffré "
                    "avec lignes et totaux (commande, bon de livraison…).",
        parameters={"type": "object", "properties": {
            "nom": {"type": "string", "description": "pluriel, minuscules, ex. vehicules"},
            "libelle": {"type": "string", "description": "pour le menu, ex. Véhicules"},
            "objet": {"type": "string", "description": "un élément, ex. véhicule"},
            "feminin": {"type": "boolean", "description": "true si objet est féminin (une commande)"},
            "champs": {"type": "array", "items": CHAMP_SCHEMA},
            "titre": {"type": "string", "description": "champ qui nomme un élément, ex. immatriculation"},
            "ordre": {"type": "integer", "description": "place dans le menu (clients 10, devis 30, factures 40)"},
            "document": {"type": "boolean"},
            "imprimable": {"type": "boolean"},
            "suppression": {"type": "boolean"},
            "planning": {"type": "string", "description": "champ date pour afficher un planning mensuel"},
            "lecture": {"type": "array", "items": {"type": "string"}, "description": "rôles qui peuvent voir"},
            "ecriture": {"type": "array", "items": {"type": "string"}, "description": "rôles qui peuvent modifier"}},
            "required": ["nom", "libelle", "champs"]},
        run=add_module,
    ),
    Tool(
        name="verify_project",
        description="Vérifie le projet comme avant une livraison : tous les tests, puis démarrage et réponse HTTP. "
                    "À lancer après chaque étape ; une étape n'est finie que si la réponse commence par OK.",
        parameters={"type": "object", "properties": {}},
        run=verify_project,
        dangerous=True,
    ),
]
