"""Mise en ligne d'un projet de kit sur un hôte Proxmox VE, derrière le reverse proxy Caddy.

    cerveau deployer --configurer
    cerveau deployer <projet> --domaine app.exemple.fr [--ct N] [--ip X] [--simulation] [--mise-a-jour]

Une application (kits gestion, site-admin…) tourne dans son propre conteneur LXC Debian non privilégié,
créé avec pct create, avec un utilisateur système dédié, un service systemd et une sauvegarde nocturne
de sa base SQLite. Le conteneur du proxy Caddy publie le domaine en HTTPS (reverse_proxy vers IP:port).

Un site statique (kit site) est construit sur le poste (dist/) puis servi directement par le Caddy du
proxy (file_server) : pas de processus à faire tourner, donc pas de conteneur, de service ni de mises à
jour système en plus. Chaque version va dans son propre dossier et un lien « courant » bascule
atomiquement ; les trois dernières versions restent pour revenir en arrière.

Toutes les commandes passent par un Executeur injectable : ssh vers l'hôte, puis « pct exec » / « pct push »
pour les conteneurs. Les tests utilisent un faux exécuteur ; --simulation liste les commandes sans rien lancer.
Python standard uniquement ; fonctionne aussi sous Windows (ssh d'OpenSSH).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from . import config
from .tools.projets import _slug, kit_du_projet
from .tools.shell import command_env

GABARITS = Path(__file__).resolve().parent / "deploiement_gabarits"
DOSSIER = "deploiement"                       # dossier versionnable produit dans le projet
ETAT = "deploiement.json"                     # où tourne le projet : écrit après un vrai déploiement
MARQUEUR_MDP = "MOT_DE_PASSE_ADMIN_INITIAL="  # ligne émise une seule fois par installer.sh
TAG = "cerveau"                               # étiquette Proxmox des conteneurs créés ici

EXCLUS_PARTOUT = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  "node_modules", ".DS_Store", ".cerveau", ".idea", ".vscode"}
EXCLUS_RACINE_APPLICATION = {"data", "dist", ".env"}


class ErreurDeploiement(Exception):
    """Échec expliqué en français ; le message dit quoi faire."""


def fichier_config() -> Path:
    return config.HOME / "deploiement.json"


# --- exécuteurs ------------------------------------------------------------------------

@dataclass
class Resultat:
    code: int = 0
    sortie: str = ""
    erreur: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    def texte(self, lignes: int = 25) -> str:
        tout = "\n".join(t for t in (self.sortie.strip(), self.erreur.strip()) if t)
        return "\n".join(tout.splitlines()[-lignes:])


class Executeur:
    """Lance une commande (liste d'arguments) et renvoie son Resultat ; un code non nul n'est pas une exception."""

    def executer(self, argv: list[str], *, entree: bytes | None = None, cwd: Path | None = None,
                 timeout: float = 900) -> Resultat:
        raise NotImplementedError


class ExecuteurLocal(Executeur):
    """Sur le poste, sans shell (pas de différence cmd.exe / sh)."""

    def __init__(self, env: dict[str, str] | None = None):
        self.env = env

    def executer(self, argv, *, entree=None, cwd=None, timeout=900):
        env = self.env or dict(os.environ)
        programme = shutil.which(argv[0], path=env.get("PATH")) or argv[0]
        try:
            # Sans entrée : /dev/null, sinon ssh lirait (et avalerait) le clavier de l'utilisateur.
            entrees = {"input": entree} if entree is not None else {"stdin": subprocess.DEVNULL}
            proc = subprocess.run([programme, *argv[1:]], cwd=cwd, env=env, capture_output=True, timeout=timeout,
                                  **entrees)
        except FileNotFoundError:
            return Resultat(127, "", f"commande introuvable : {argv[0]}")
        except subprocess.TimeoutExpired:
            return Resultat(124, "", f"délai dépassé ({int(timeout)} s) : {argv[0]}")
        return Resultat(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


class ExecuteurSSH(Executeur):
    """Sur l'hôte Proxmox, par ssh (clé uniquement : BatchMode interdit toute question de mot de passe)."""

    def __init__(self, hote: str, utilisateur: str = "root", port: int = 22, cle: str = "",
                 local: Executeur | None = None):
        self.hote, self.utilisateur, self.port, self.cle = hote, utilisateur, port, cle
        self.local = local or ExecuteurLocal()

    def prefixe(self) -> list[str]:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
                "-p", str(self.port)]
        if self.cle:
            argv += ["-i", str(Path(self.cle).expanduser())]
        return argv + [f"{self.utilisateur}@{self.hote}"]

    def executer(self, argv, *, entree=None, cwd=None, timeout=900):
        # L'hôte est un Linux : la commande distante est citée à la mode POSIX, même depuis Windows.
        resultat = self.local.executer([*self.prefixe(), shlex.join(argv)], entree=entree, timeout=timeout)
        if resultat.code == 255:
            resultat.erreur += (f"\nssh n'a pas pu joindre {self.utilisateur}@{self.hote} : vérifie l'adresse, la clé "
                                f"(ssh-copy-id) et connecte-toi une fois à la main pour accepter l'empreinte de l'hôte.")
        return resultat


class ExecuteurPct(Executeur):
    """Dans un conteneur, par « pct exec » sur l'hôte."""

    def __init__(self, hote: Executeur, ct: int):
        self.hote, self.ct = hote, ct

    def executer(self, argv, *, entree=None, cwd=None, timeout=900):
        return self.hote.executer(["pct", "exec", str(self.ct), "--", *argv], entree=entree, timeout=timeout)


# --- configuration -----------------------------------------------------------------------

@dataclass
class ConfigDeploiement:
    hote: str = ""
    utilisateur: str = "root"
    port_ssh: int = 22
    cle_ssh: str = ""
    stockage: str = "local-lvm"
    template: str = "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"
    pont: str = "vmbr0"
    reseau: str = "10.10.20.0/24"
    passerelle: str = "10.10.20.1"
    plage_ip: str = "10.10.20.100-10.10.20.199"
    dns: str = ""
    ct_minimum: int = 200
    coeurs: int = 1
    memoire_mo: int = 1024
    swap_mo: int = 512
    disque_go: int = 8
    ct_proxy: int = 101
    ip_proxy: str = ""
    caddyfile: str = "/etc/caddy/Caddyfile"
    dossier_sites_caddy: str = ""
    journaux_caddy: str = "/var/log/caddy"
    admin_caddy: str = "http://localhost:2019"
    dossier_sites_statiques: str = "/srv/cerveau-sites"
    cible_dns: str = ""
    url_installeur_uv: str = "https://astral.sh/uv/install.sh"
    retention_jours: int = 14
    heure_sauvegarde: str = "02:30"

    def verifier(self) -> list[str]:
        erreurs = []
        if not self.hote.strip():
            erreurs.append("hôte Proxmox non renseigné")
        try:
            reseau = ipaddress.ip_network(self.reseau, strict=False)
            if ipaddress.ip_address(self.passerelle) not in reseau:
                erreurs.append(f"la passerelle {self.passerelle} n'est pas dans {self.reseau}")
            debut, fin = self.bornes_plage()
            if debut not in reseau or fin not in reseau or debut > fin:
                erreurs.append(f"plage d'IP {self.plage_ip} hors de {self.reseau} ou à l'envers")
        except ValueError as exc:
            erreurs.append(f"réseau invalide : {exc}")
        for nom in ("port_ssh", "ct_minimum", "coeurs", "memoire_mo", "disque_go", "ct_proxy", "retention_jours"):
            if int(getattr(self, nom)) <= 0:
                erreurs.append(f"{nom} doit être positif")
        if self.ct_minimum < 100:
            erreurs.append("ct_minimum doit valoir au moins 100 (numérotation Proxmox)")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.heure_sauvegarde):
            erreurs.append(f"heure de sauvegarde invalide « {self.heure_sauvegarde} » (HH:MM)")
        return erreurs

    def bornes_plage(self) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]:
        debut, _, fin = self.plage_ip.partition("-")
        return ipaddress.ip_address(debut.strip()), ipaddress.ip_address((fin or debut).strip())

    def prefixe_reseau(self) -> int:
        return ipaddress.ip_network(self.reseau, strict=False).prefixlen


def charger_config(chemin: Path | None = None) -> ConfigDeploiement:
    chemin = chemin or fichier_config()
    if not chemin.is_file():
        raise ErreurDeploiement(f"configuration absente ({chemin}) : lance d'abord « cerveau deployer --configurer ».")
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ErreurDeploiement(f"{chemin} illisible ({exc}) : corrige-le ou relance « cerveau deployer --configurer ».")
    connus = {f.name: f for f in fields(ConfigDeploiement)}
    valeurs = {}
    for cle, valeur in donnees.items():
        if cle in connus:
            valeurs[cle] = int(valeur) if connus[cle].type == "int" else str(valeur)
    cfg = ConfigDeploiement(**valeurs)
    erreurs = cfg.verifier()
    if erreurs:
        raise ErreurDeploiement(f"configuration incomplète ({chemin}) : " + " ; ".join(erreurs)
                                + ". Relance « cerveau deployer --configurer ».")
    return cfg


QUESTIONS = [
    ("hote", "Adresse de l'hôte Proxmox (accès root par clé SSH depuis ce poste)"),
    ("utilisateur", "Utilisateur SSH"),
    ("port_ssh", "Port SSH"),
    ("cle_ssh", "Clé SSH privée (vide : celle de l'agent ou ~/.ssh par défaut)"),
    ("stockage", "Stockage des disques des conteneurs (pvesm status)"),
    ("template", "Template Debian 12/13 (pveam list local)"),
    ("pont", "Pont réseau des conteneurs"),
    ("reseau", "Réseau interne des conteneurs (CIDR)"),
    ("passerelle", "Passerelle de ce réseau"),
    ("plage_ip", "Plage d'IP réservée aux applications (début-fin)"),
    ("dns", "Serveur DNS des conteneurs (vide : celui de l'hôte)"),
    ("ct_minimum", "Premier numéro de CT envisagé (le premier libre à partir de là est pris, via pct list et qm list)"),
    ("coeurs", "Cœurs par conteneur"),
    ("memoire_mo", "Mémoire par conteneur (Mo)"),
    ("swap_mo", "Swap par conteneur (Mo)"),
    ("disque_go", "Disque par conteneur (Go)"),
    ("ct_proxy", "Numéro du CT du reverse proxy Caddy"),
    ("ip_proxy", "IP du proxy vue par les applications (pour les en-têtes X-Forwarded ; vide si inconnue)"),
    ("caddyfile", "Chemin du Caddyfile dans le CT du proxy"),
    ("dossier_sites_caddy", "Dossier importé par le Caddyfile (ex. /etc/caddy/sites si « import sites/*.caddy » ; "
                            "vide : bloc ajouté au Caddyfile)"),
    ("journaux_caddy", "Dossier des journaux de Caddy"),
    ("admin_caddy", "API d'administration de Caddy (dans le CT du proxy)"),
    ("dossier_sites_statiques", "Dossier des sites statiques dans le CT du proxy"),
    ("cible_dns", "Cible DNS des domaines : IP publique (enregistrement A) ou nom (CNAME), vide si variable"),
    ("url_installeur_uv", "Installeur de uv"),
    ("retention_jours", "Jours de sauvegardes gardés"),
    ("heure_sauvegarde", "Heure de la sauvegarde nocturne (HH:MM)"),
]


def configurer(demander: Callable[[str], str] = input, afficher: Callable[[str], None] = print,
               chemin: Path | None = None) -> int:
    """Questions une par une, réponse vide = valeur proposée. Rien n'est contacté."""
    chemin = chemin or fichier_config()
    try:
        actuelle = charger_config(chemin)
    except ErreurDeploiement:
        actuelle = ConfigDeploiement()
        if chemin.is_file():
            try:
                donnees = json.loads(chemin.read_text(encoding="utf-8"))
                for cle in donnees:
                    if hasattr(actuelle, cle):
                        setattr(actuelle, cle, type(getattr(actuelle, cle))(donnees[cle]))
            except (OSError, ValueError, TypeError):
                pass
    afficher(f"Configuration du déploiement ({chemin}). Entrée garde la valeur entre crochets, « - » vide un "
             "champ facultatif.\n")
    valeurs = asdict(actuelle)
    for cle, question in QUESTIONS:
        while True:
            defaut = valeurs[cle]
            reponse = demander(f"{question} [{defaut}] : ").strip()
            if not reponse:
                break
            if reponse == "-":          # « - » vide un champ facultatif
                reponse = ""
            try:
                valeurs[cle] = int(reponse) if isinstance(defaut, int) else reponse
                break
            except ValueError:
                afficher("  nombre attendu")
    cfg = ConfigDeploiement(**valeurs)
    erreurs = cfg.verifier()
    if erreurs:
        afficher("Configuration non enregistrée : " + " ; ".join(erreurs))
        return 1
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    afficher(f"\nEnregistré dans {chemin}.\nAvant le premier déploiement : ssh {cfg.utilisateur}@{cfg.hote} pveversion "
             f"doit répondre sans mot de passe. Essaie d'abord avec --simulation.")
    return 0


# --- projet -------------------------------------------------------------------------------

@dataclass
class Projet:
    racine: Path
    kit: str
    type: str                 # "application" ou "statique"
    nom: str                  # slug : nom du service, de l'utilisateur, des dossiers
    titre: str
    port: int
    sante: str
    test: str
    commande: str = ""        # application : commande de production, relative au venv
    construire: str = ""      # statique : commande de construction
    dist: str = "dist"


def _nom_systeme(texte: str) -> str:
    nom = _slug(texte)[:32].strip("-")
    if not nom or not nom[0].isalpha():
        nom = ("app-" + nom)[:32].strip("-")
    return nom


def analyser_projet(racine: Path, ip_proxy: str = "") -> Projet:
    kit = kit_du_projet(racine)
    if kit is None:
        raise ErreurDeploiement(f"{racine} n'a pas de .cerveau/kit.json : seuls les projets créés depuis un kit "
                                "savent comment être testés et lancés.")
    nom_kit = str(kit.get("nom", ""))
    reglages = kit.get("deploiement") if isinstance(kit.get("deploiement"), dict) else {}
    type_ = reglages.get("type") or ("statique" if nom_kit == "site" else "application")
    pyproject = racine / "pyproject.toml"
    titre = nom = ""
    if pyproject.is_file():
        texte = pyproject.read_text(encoding="utf-8", errors="replace")
        trouve = re.search(r'^name\s*=\s*"([^"]+)"', texte, re.M)
        nom = trouve.group(1) if trouve else ""
        trouve = re.search(r'^description\s*=\s*"([^"]*)"', texte, re.M)
        titre = trouve.group(1) if trouve else ""
    nom = _nom_systeme(nom or racine.resolve().name)
    titre = re.sub(r"[$`\\\n\r\"]", "", titre or nom).strip() or nom
    port = int(kit.get("port", 8000))
    projet = Projet(racine=racine, kit=nom_kit, type=type_, nom=nom, titre=titre, port=port,
                    sante=str(kit.get("sante", "/")), test=str(kit.get("test", "uv run pytest -q")))
    if type_ == "statique":
        projet.construire = reglages.get("construire") or kit.get("construire") or "uv run python site.py build"
        projet.dist = reglages.get("dist", "dist")
        return projet
    production = reglages.get("production") or kit.get("production")
    if production:
        projet.commande = str(production).format(port=port, hote="0.0.0.0")
    else:
        trouve = re.search(r"\buvicorn\s+([\w.]+:[\w.]+)", str(kit.get("start", "")))
        if not trouve:
            raise ErreurDeploiement(f"kit « {nom_kit} » : impossible de déduire la commande de production. Ajoute "
                                    "\"production\": \"uvicorn app.main:app --host 0.0.0.0 --port {port}\" "
                                    "dans .cerveau/kit.json.")
        projet.commande = f"uvicorn {trouve.group(1)} --host 0.0.0.0 --port {port} --proxy-headers"
        if ip_proxy:
            projet.commande += f" --forwarded-allow-ips {ip_proxy}"
    if re.search(r"[$`\\\n]", projet.commande):
        raise ErreurDeploiement(f"commande de production refusée (caractères spéciaux) : {projet.commande}")
    return projet


def verifier_domaine(domaine: str) -> str:
    domaine = (domaine or "").strip().lower().rstrip(".")
    domaine = re.sub(r"^https?://", "", domaine).split("/")[0]
    if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]", domaine):
        raise ErreurDeploiement(f"domaine invalide « {domaine} » (attendu : app.exemple.fr)")
    return domaine


def resoudre_dns(domaine: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(domaine, 443, proto=socket.IPPROTO_TCP)})
    except (socket.gaierror, UnicodeError, OSError):
        return []


def lire_etat(racine: Path) -> dict:
    try:
        return json.loads((racine / DOSSIER / ETAT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --- fichiers produits dans le projet ------------------------------------------------------------

def _gabarit(nom: str, valeurs: dict[str, object]) -> str:
    texte = (GABARITS / nom).read_text(encoding="utf-8")
    for cle, valeur in valeurs.items():
        texte = texte.replace(f"@@{cle}@@", str(valeur))
    reste = re.findall(r"@@[A-Z_]+@@", texte)
    if reste:
        raise ErreurDeploiement(f"gabarit {nom} : valeurs manquantes {sorted(set(reste))}")
    return texte


def bloc_caddy(cfg: ConfigDeploiement, projet: Projet, domaine: str, ip: str | None) -> str:
    journal = f"""	log {{
		output file {cfg.journaux_caddy}/{domaine}.log {{
			roll_size 10MiB
			roll_keep 5
		}}
	}}"""
    if projet.type == "statique":
        corps = f"""	root * {cfg.dossier_sites_statiques}/{domaine}/courant
	encode zstd gzip
	header /static/* Cache-Control "public, max-age=3600"
	file_server"""
    else:
        corps = f"""	encode zstd gzip
	reverse_proxy {ip}:{projet.port}"""
    return (f"# {projet.titre} (kit {projet.kit}) : généré par cerveau deployer\n"
            f"{domaine} {{\n{corps}\n{journal}\n}}\n")


def generer_fichiers(projet: Projet, cfg: ConfigDeploiement, domaine: str, ct: int | None, ip: str | None,
                     provisoire: bool = False) -> list[Path]:
    """Écrit deploiement/ dans le projet (sans aucun secret) et renvoie les fichiers écrits."""
    dossier = projet.racine / DOSSIER
    dossier.mkdir(exist_ok=True)
    ecrits: list[tuple[str, str]] = [("caddy.conf", bloc_caddy(cfg, projet, domaine, ip))]
    if projet.type == "application":
        valeurs = {"NOM": projet.nom, "UTILISATEUR": projet.nom, "PORT": projet.port, "TITRE": projet.titre,
                   "COMMANDE": projet.commande, "RETENTION": cfg.retention_jours, "HEURE": cfg.heure_sauvegarde,
                   "URL_UV": cfg.url_installeur_uv}
        for nom in ("installer.sh", "sauvegarde.sh", "restaurer.sh"):
            ecrits.append((nom, _gabarit(nom, valeurs)))
    ecrits.append(("DEPLOIEMENT.md", documentation(projet, cfg, domaine, ct, ip, provisoire)))
    chemins = []
    for nom, contenu in ecrits:
        chemin = dossier / nom
        # newline="\n" : les scripts gardent des fins de ligne Unix même écrits depuis Windows.
        with open(chemin, "w", encoding="utf-8", newline="\n") as f:
            f.write(contenu)
        if nom.endswith(".sh") and os.name != "nt":
            chemin.chmod(0o755)
        chemins.append(chemin)
    return chemins


def documentation(projet: Projet, cfg: ConfigDeploiement, domaine: str, ct: int | None, ip: str | None,
                  provisoire: bool = False) -> str:
    ssh = f"ssh {cfg.utilisateur}@{cfg.hote}" + (f" -p {cfg.port_ssh}" if cfg.port_ssh != 22 else "")
    proxy = f"{ssh} pct exec {cfg.ct_proxy} --"
    lignes = [f"# Déploiement : {projet.titre}", ""]
    if provisoire:
        lignes += ["> Généré en simulation : le numéro de CT et l'IP sont provisoires, ils seront confirmés au premier "
                   "vrai déploiement.", ""]
    dns = _conseil_dns(cfg, domaine)
    route = (f"`{cfg.dossier_sites_caddy}/{domaine}.caddy`" if cfg.dossier_sites_caddy
             else f"bloc `# >>> cerveau deployer : {domaine}` du `{cfg.caddyfile}`")
    if projet.type == "statique":
        dossier = f"{cfg.dossier_sites_statiques}/{domaine}"
        lignes += [
            "## Où ça tourne", "",
            f"- Adresse : https://{domaine}",
            f"- Site statique (kit {projet.kit}) servi directement par Caddy dans le CT {cfg.ct_proxy} (proxy) de "
            f"l'hôte {cfg.hote} : `{dossier}/courant` → `{dossier}/versions/<date>` (3 dernières gardées).",
            f"- Route Caddy : {route} (copie dans `deploiement/caddy.conf`).",
            f"- DNS : {dns}", "",
            "Pourquoi pas de conteneur à lui : un site statique n'a aucun processus à isoler. Caddy le sert "
            "directement, avec le HTTPS et la compression : moins de pièces, rien à mettre à jour en plus.", "",
            "## Mettre à jour", "",
            "```", f"cerveau deployer . --mise-a-jour", "```", "",
            "Les tests passent, le site est reconstruit (dist/), envoyé dans une nouvelle version, puis le lien "
            "`courant` bascule d'un coup.", "",
            "## Revenir à la version précédente", "",
            "```", f"{proxy} ls -1 {dossier}/versions",
            f"{proxy} ln -sfn versions/<version> {dossier}/courant", "```", "",
            "## Journaux", "", "```", f"{proxy} tail -f {cfg.journaux_caddy}/{domaine}.log", "```", "",
            "## Sauvegarde", "",
            "Rien à sauvegarder sur le serveur : tout le site se reconstruit depuis ce dépôt.", "",
        ]
        return "\n".join(lignes)
    ct_txt = str(ct) if ct else "?"
    dans_ct = f"{ssh} pct exec {ct_txt} --"
    lignes += [
        "## Où ça tourne", "",
        f"- Adresse : https://{domaine}",
        f"- Conteneur LXC {ct_txt} (« {projet.nom} ») sur l'hôte Proxmox {cfg.hote}, IP {ip or '?'}, "
        f"port {projet.port} ; santé : `http://{ip or '?'}:{projet.port}{projet.sante}`.",
        f"- Route Caddy dans le CT {cfg.ct_proxy} : {route} (copie dans `deploiement/caddy.conf`).",
        f"- DNS : {dns}", "",
        "| Quoi | Où (dans le conteneur) |", "|---|---|",
        f"| Code | `/opt/{projet.nom}/app` (version précédente : `/opt/{projet.nom}/app.precedent`) |",
        f"| Dépendances | `/opt/{projet.nom}/venv` (uv) |",
        f"| Données (base SQLite, fichiers) | `/var/lib/{projet.nom}/data` |",
        f"| Secrets | `/etc/{projet.nom}/app.env` (SECRET_KEY, APP_DATABASE_URL), jamais dans le dépôt |",
        f"| Sauvegardes | `/var/backups/{projet.nom}`, chaque nuit vers {cfg.heure_sauvegarde}, "
        f"{cfg.retention_jours} jours |",
        f"| Service | `{projet.nom}.service`, utilisateur système `{projet.nom}` (pas root), redémarrage automatique |",
        "",
        "Le mot de passe administrateur initial a été affiché une seule fois au premier déploiement. Perdu ? Il est "
        f"aussi dans `/var/lib/{projet.nom}/data/admin-initial.txt` tant que ce fichier n'est pas supprimé : "
        "changez-le dans « Mon compte » puis supprimez ce fichier.", "",
        "## Mettre à jour", "",
        "```", "cerveau deployer . --mise-a-jour", "```", "",
        "Les tests passent d'abord ; le code est envoyé, les dépendances resynchronisées, le service redémarré. "
        "Les données et les secrets ne sont pas touchés. Si la nouvelle version ne répond pas, la précédente est "
        "remise en service automatiquement.", "",
        "## Journaux et service", "",
        "```",
        f"{dans_ct} journalctl -u {projet.nom} -n 100 --no-pager",
        f"{dans_ct} journalctl -u {projet.nom} -f",
        f"{dans_ct} systemctl restart {projet.nom}",
        f"{dans_ct} systemctl status {projet.nom}",
        f"{proxy} tail -f {cfg.journaux_caddy}/{domaine}.log",
        "```", "",
        "## Sauvegardes", "",
        "```",
        f"{dans_ct} ls -lh /var/backups/{projet.nom}",
        f"{dans_ct} systemctl start {projet.nom}-sauvegarde.service     # sauvegarde immédiate",
        f"{dans_ct} systemctl list-timers {projet.nom}-sauvegarde.timer",
        "```", "",
        "Les sauvegardes restent dans le conteneur : elles ne protègent pas d'une perte du conteneur. Incluez le CT "
        "dans les sauvegardes Proxmox (vzdump / PBS) ou copiez `/var/backups` ailleurs.", "",
        "## Restaurer une sauvegarde", "",
        "```",
        f"{dans_ct} bash /opt/{projet.nom}/app/deploiement/restaurer.sh            # liste les sauvegardes",
        f"{dans_ct} bash /opt/{projet.nom}/app/deploiement/restaurer.sh /var/backups/{projet.nom}/app-AAAA-MM-JJ_HHMMSS.db.gz",
        "```", "",
        "La sauvegarde est contrôlée avant l'échange, l'application est arrêtée le temps de remplacer la base, et la "
        "base remplacée est gardée à côté (`app.db.avant-restauration-<date>`).", "",
        "## Revenir à la version précédente du code", "",
        "```", f"{dans_ct} bash /opt/{projet.nom}/app/deploiement/installer.sh --revenir", "```", "",
    ]
    return "\n".join(lignes)


def _conseil_dns(cfg: ConfigDeploiement, domaine: str) -> str:
    cible = cfg.cible_dns.strip()
    if not cible:
        return (f"enregistrement A de `{domaine}` vers l'IP publique qui redirige les ports 80 et 443 vers le proxy "
                "(chez Gandi ou Hostinger).")
    try:
        ipaddress.ip_address(cible)
        return f"enregistrement `{domaine}. A {cible}` (chez Gandi ou Hostinger)."
    except ValueError:
        return f"enregistrement `{domaine}. CNAME {cible.rstrip('.')}.` (chez Gandi ou Hostinger)."


# --- archive du code ----------------------------------------------------------------------------

def archive(source: Path, application: bool) -> tuple[bytes, int]:
    """tar.gz du dossier, propriétaire root, droits normalisés (fiable depuis Windows), sans les dossiers exclus."""
    tampon = io.BytesIO()
    nombre = 0

    def normaliser(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode = 0o755 if info.isdir() else 0o644
        return info

    with tarfile.open(fileobj=tampon, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for chemin in sorted(source.rglob("*")):
            relatif = chemin.relative_to(source)
            if any(part in EXCLUS_PARTOUT for part in relatif.parts) or chemin.suffix in (".pyc", ".pyo"):
                continue
            if application and relatif.parts[0] in EXCLUS_RACINE_APPLICATION:
                continue
            if chemin.is_symlink() or not (chemin.is_file() or chemin.is_dir()):
                continue
            tar.add(chemin, arcname=relatif.as_posix(), recursive=False, filter=normaliser)
            nombre += chemin.is_file()
    return tampon.getvalue(), nombre


# --- Caddyfile ------------------------------------------------------------------------------------

def _marqueurs(domaine: str) -> tuple[str, str]:
    return f"# >>> cerveau deployer : {domaine}", f"# <<< cerveau deployer : {domaine}"


def _motif_bloc(domaine: str) -> re.Pattern:
    debut, fin = _marqueurs(domaine)
    return re.compile(rf"^{re.escape(debut)}[^\n]*\n.*?^{re.escape(fin)}[^\n]*\n?", re.M | re.S)


def inserer_bloc(contenu: str, domaine: str, bloc: str) -> str:
    """Ajoute (ou remplace) le bloc du domaine entre ses marqueurs ; le reste du Caddyfile est intact."""
    debut, fin = _marqueurs(domaine)
    nouveau = f"{debut} (géré par cerveau deployer, ne pas modifier à la main)\n{bloc.rstrip()}\n{fin}\n"
    motif = _motif_bloc(domaine)
    if motif.search(contenu):
        return motif.sub(lambda _: nouveau, contenu, count=1)
    return contenu.rstrip("\n") + ("\n\n" if contenu.strip() else "") + nouveau


def domaine_deja_servi(contenu: str, domaine: str) -> bool:
    """Vrai si le domaine apparaît ailleurs que dans le bloc géré par cerveau (bloc écrit à la main)."""
    sans_bloc = _motif_bloc(domaine).sub("", contenu)
    sans_commentaires = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in sans_bloc.splitlines())
    return re.search(rf"(?<![\w.-]){re.escape(domaine)}(?![\w-]|\.[\w])", sans_commentaires) is not None


# --- déploiement ----------------------------------------------------------------------------------

class Deployeur:
    """Enchaîne les étapes ; chaque commande passe par _lancer, qui l'affiche, l'enregistre, et ne l'exécute
    pas en simulation (elle renvoie alors la réponse la plus probable pour dérouler la suite)."""

    def __init__(self, cfg: ConfigDeploiement, racine: Path, domaine: str | None = None, *, ct: int | None = None,
                 ip: str | None = None, simulation: bool = False, mise_a_jour: bool = False,
                 hote: Executeur | None = None, local: Executeur | None = None,
                 resoudre: Callable[[str], list[str]] | None = None,
                 attendre: Callable[[float], None] = time.sleep, afficher: Callable[[str], None] = print):
        self.cfg = cfg
        self.racine = Path(racine).resolve()
        self.simulation, self.mise_a_jour = simulation, mise_a_jour
        self.afficher, self.attendre = afficher, attendre
        self.resoudre = resoudre
        self.projet = analyser_projet(self.racine, cfg.ip_proxy)
        self.etat = lire_etat(self.racine)
        if domaine:
            self.domaine = verifier_domaine(domaine)
        elif self.etat.get("domaine"):
            self.domaine = verifier_domaine(self.etat["domaine"])
        else:
            raise ErreurDeploiement("indique le domaine : --domaine app.exemple.fr")
        if self.etat.get("domaine") and self.etat["domaine"] != self.domaine:
            self.afficher(f"attention : le domaine change ({self.etat['domaine']} → {self.domaine}) ; l'ancienne route "
                          f"Caddy reste en place, retire-la à la main si elle ne sert plus.")
        self.ct_demande = ct
        self.ip_demandee = str(ipaddress.ip_address(ip)) if ip else None
        self._hote_ssh = hote is None
        self.hote = hote or ExecuteurSSH(cfg.hote, cfg.utilisateur, cfg.port_ssh, cfg.cle_ssh)
        self.local = local or ExecuteurLocal(command_env(self.racine))
        self.commandes: list[str] = []
        self.mot_de_passe_admin: str | None = None
        self.ct: int | None = None
        self.ip: str | None = None
        self._numero = 0

    # -- outillage --

    def _etape(self, titre: str) -> None:
        self._numero += 1
        self.afficher(f"\n{self._numero}. {titre}")

    def _info(self, texte: str) -> None:
        self.afficher(f"   {texte}")

    def _lancer(self, cible: str, executeur: Executeur, argv: list[str], *, entree: bytes | None = None,
                quoi_entree: str = "", simule: Resultat | None = None, timeout: float = 900,
                cwd: Path | None = None) -> Resultat:
        ligne = f"[{cible}] {shlex.join(argv)}" + (f"  < {quoi_entree}" if entree is not None else "")
        self.commandes.append(ligne)
        if self.simulation:
            self.afficher(f"   {ligne}")
            return simule or Resultat(0)
        self.afficher(f"   {ligne if len(ligne) <= 160 else ligne[:157] + '...'}")
        return executeur.executer(argv, entree=entree, cwd=cwd, timeout=timeout)

    def _sur_hote(self, argv: list[str], **kw) -> Resultat:
        return self._lancer("hôte", self.hote, argv, **kw)

    def _dans_ct(self, ct: int, argv: list[str], **kw) -> Resultat:
        return self._lancer(f"CT {ct}", ExecuteurPct(self.hote, ct), argv, **kw)

    def _bash_ct(self, ct: int, script: str, **kw) -> Resultat:
        return self._dans_ct(ct, ["bash", "-c", script], **kw)

    def _exiger(self, resultat: Resultat, message: str) -> Resultat:
        if not resultat.ok:
            detail = resultat.texte()
            raise ErreurDeploiement(message + (f"\n--- sortie ---\n{detail}" if detail else ""))
        return resultat

    def _envoyer(self, ct: int, donnees: bytes, destination: str, quoi: str, droits: str = "0644") -> None:
        """Fichier du poste vers un conteneur : flux ssh vers un fichier temporaire de l'hôte, puis pct push."""
        temporaire = f"/tmp/cerveau-{secrets.token_hex(6)}"
        if self.simulation:
            taille = "taille connue à l'exécution"
        else:
            taille = f"{len(donnees) // 1024} Ko" if len(donnees) >= 1024 else f"{len(donnees)} octets"
        try:
            self._exiger(self._sur_hote(["sh", "-c", 'umask 077 && cat > "$1"', "sh", temporaire], entree=donnees,
                                        quoi_entree=f"{quoi} ({taille})", timeout=1800),
                         f"envoi de {quoi} vers l'hôte impossible")
            self._exiger(self._sur_hote(["pct", "push", str(ct), temporaire, destination, "--perms", droits]),
                         f"pct push de {quoi} dans le CT {ct} impossible")
        finally:
            self._sur_hote(["rm", "-f", temporaire])

    # -- étapes communes --

    def _verifier_hote_et_proxy(self) -> None:
        self._etape("Hôte Proxmox et proxy Caddy")
        if self._hote_ssh and not self.simulation and not shutil.which("ssh"):
            raise ErreurDeploiement("ssh introuvable sur ce poste : installe le client OpenSSH "
                                    "(Windows : Paramètres > Applications > Fonctionnalités facultatives).")
        version = self._exiger(self._sur_hote(["pveversion"], simule=Resultat(0, "pve-manager/8.x (simulation)")),
                               f"l'hôte {self.cfg.hote} ne répond pas à pveversion")
        if "pve-manager" not in version.sortie:
            raise ErreurDeploiement(f"{self.cfg.hote} n'est pas un hôte Proxmox VE (pveversion : {version.sortie.strip()})")
        self._info(version.sortie.strip().splitlines()[0])
        if self.mise_a_jour and self.projet.type == "application":
            return
        p = self.cfg.ct_proxy
        statut = self._sur_hote(["pct", "status", str(p)], simule=Resultat(0, "status: running"))
        if not statut.ok or "running" not in statut.sortie:
            raise ErreurDeploiement(f"le CT {p} du proxy Caddy n'est pas démarré ({statut.texte(3) or 'introuvable'}) : "
                                    "vérifie ct_proxy dans la configuration.")
        self._exiger(self._dans_ct(p, ["caddy", "version"], simule=Resultat(0, "v2 (simulation)")),
                     f"caddy introuvable dans le CT {p} : est-ce bien le proxy ?")

    def _tests(self) -> None:
        self._etape("Tests du projet (refus de déployer s'ils échouent)")
        resultat = self._lancer("local", self.local, shlex.split(self.projet.test, posix=os.name != "nt"),
                                cwd=self.racine, timeout=1800)
        if not resultat.ok:
            raise ErreurDeploiement(f"les tests échouent (« {self.projet.test} ») : rien n'a été déployé. "
                                    f"Corrige-les puis relance.\n--- fin de la sortie ---\n{resultat.texte(30)}")
        resume = [l for l in resultat.sortie.splitlines() if re.search(r"\d+ passed", l)]
        if resume:
            self._info(f"tests verts : {resume[-1].strip(' =')}")

    def _dns(self) -> list[str]:
        self._etape("DNS (géré à part, chez Gandi ou Hostinger)")
        adresses = (self.resoudre or resoudre_dns)(self.domaine)
        if adresses:
            self._info(f"{self.domaine} résout déjà : {', '.join(adresses)}")
            cible = self.cfg.cible_dns.strip()
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", cible) and cible not in adresses:
                self._info(f"attention : la cible configurée est {cible}, vérifie l'enregistrement.")
        else:
            self._info(f"{self.domaine} ne résout pas encore. À créer : {_conseil_dns(self.cfg, self.domaine)}")
            self._info("Caddy obtiendra le certificat tout seul dès que le DNS pointera vers le proxy.")
        return adresses

    def _generer(self, provisoire: bool) -> None:
        self._etape(f"Fichiers de déploiement dans {DOSSIER}/ (versionnables, sans secret)")
        for chemin in generer_fichiers(self.projet, self.cfg, self.domaine, self.ct, self.ip, provisoire):
            self._info(chemin.relative_to(self.racine).as_posix())

    # -- route Caddy, sans demi-route --

    def _charger_caddy(self) -> Resultat:
        p, cfg = self.cfg.ct_proxy, self.cfg
        api = self._bash_ct(p, f"command -v curl >/dev/null && curl -fsS -m 5 -o /dev/null {shlex.quote(cfg.admin_caddy)}/config/")
        if api.ok:
            self._info("chargement par l'API d'administration (pas de reload systemd, coupé à 90 s)")
            return self._bash_ct(p, "set -o pipefail; caddy adapt --config {f} --adapter caddyfile | curl -fsS -m 300 "
                                    '-X POST -H "Content-Type: application/json" --data-binary @- {a}/load'.format(
                                        f=shlex.quote(cfg.caddyfile), a=shlex.quote(cfg.admin_caddy)), timeout=360)
        self._info("API d'administration injoignable : caddy reload")
        return self._dans_ct(p, ["caddy", "reload", "--config", cfg.caddyfile, "--adapter", "caddyfile"], timeout=360)

    def _route_caddy(self) -> None:
        self._etape(f"Route Caddy de {self.domaine} (CT {self.cfg.ct_proxy})")
        p, cfg, dom = self.cfg.ct_proxy, self.cfg, self.domaine
        bloc = bloc_caddy(cfg, self.projet, dom, self.ip)
        caddyfile = self._exiger(self._dans_ct(p, ["cat", cfg.caddyfile]), f"lecture de {cfg.caddyfile} impossible")
        contenu = caddyfile.sortie
        journal = f"{cfg.journaux_caddy}/{dom}.log"
        if cfg.dossier_sites_caddy:
            cible = f"{cfg.dossier_sites_caddy.rstrip('/')}/{dom}.caddy"
            if not self.simulation and "import" not in contenu:
                raise ErreurDeploiement(f"{cfg.caddyfile} n'importe aucun fichier : ajoute « import "
                                        f"{cfg.dossier_sites_caddy}/*.caddy » ou vide dossier_sites_caddy.")
            autres = self._dans_ct(p, ["grep", "-rlF", "--", dom, cfg.dossier_sites_caddy])
            conflits = [l for l in autres.sortie.split() if l and l != cible and not l.endswith(".cerveau-precedent")]
            if domaine_deja_servi(contenu, dom) or conflits:
                raise ErreurDeploiement(f"{dom} est déjà servi par un bloc écrit à la main "
                                        f"({', '.join(conflits) or cfg.caddyfile}) : rien n'a été modifié. Retire ce "
                                        "bloc ou choisis un autre domaine.")
        else:
            cible = cfg.caddyfile
            if domaine_deja_servi(contenu, dom):
                raise ErreurDeploiement(f"{dom} est déjà servi par un bloc écrit à la main dans {cfg.caddyfile} : rien "
                                        "n'a été modifié. Retire ce bloc ou choisis un autre domaine.")
        # Piège 1 : le journal doit exister, au nom de l'utilisateur de Caddy, AVANT le chargement.
        self._exiger(self._bash_ct(p, (
            'set -e; d={d}; f={f}; mkdir -p "$d"; [ -e "$f" ] || touch "$f"; ref=""; '
            'for g in "$d"/*.log; do if [ "$g" != "$f" ] && [ -f "$g" ]; then ref="$g"; break; fi; done; '
            'if [ -n "$ref" ]; then chown --reference="$ref" "$f"; chmod --reference="$ref" "$f"; '
            'elif id -u caddy >/dev/null 2>&1; then chown caddy:caddy "$f"; chmod 640 "$f"; fi'
        ).format(d=shlex.quote(cfg.journaux_caddy), f=shlex.quote(journal))), f"création du journal {journal} impossible")

        horodatage = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        if cfg.dossier_sites_caddy:
            sauve = f"{cible}.cerveau-precedent"
            c, s = shlex.quote(cible), shlex.quote(sauve)
            self._exiger(self._bash_ct(p, f"if [ -e {c} ]; then cp -a {c} {s}; else rm -f {s}; fi"),
                         f"copie de sauvegarde de {cible} impossible")
            restaurer = f"if [ -e {s} ]; then mv -f {s} {c}; else rm -f {c}; fi"
            self._envoyer(p, bloc.encode("utf-8"), cible, f"bloc Caddy de {dom}")
            validation = self._dans_ct(p, ["caddy", "validate", "--config", cfg.caddyfile, "--adapter", "caddyfile"])
            if not validation.ok:
                self._bash_ct(p, restaurer)
                raise ErreurDeploiement(f"caddy validate refuse la configuration : retour arrière fait ({cible} remis "
                                        f"dans son état précédent), Caddy n'a pas été rechargé.\n--- caddy ---\n"
                                        f"{validation.texte(15)}")
        else:
            nouveau = inserer_bloc(contenu, dom, bloc) if not self.simulation else bloc
            candidat = f"{cfg.caddyfile}.cerveau-nouveau"
            sauve = f"{cfg.caddyfile}.cerveau-{horodatage}"
            self._envoyer(p, nouveau.encode("utf-8"), candidat, f"Caddyfile + bloc {dom}")
            validation = self._dans_ct(p, ["caddy", "validate", "--config", candidat, "--adapter", "caddyfile"])
            if not validation.ok:
                self._dans_ct(p, ["rm", "-f", candidat])
                raise ErreurDeploiement(f"caddy validate refuse la nouvelle configuration : retour arrière fait "
                                        f"({cfg.caddyfile} n'a pas été modifié, Caddy n'a pas été rechargé).\n"
                                        f"--- caddy ---\n{validation.texte(15)}")
            # cat > garde le propriétaire et les droits du Caddyfile en place.
            self._exiger(self._bash_ct(p, 'set -e; cp -a {c} {s}; cat {n} > {c}; rm -f {n}'.format(
                c=shlex.quote(cfg.caddyfile), s=shlex.quote(sauve), n=shlex.quote(candidat))),
                f"remplacement de {cfg.caddyfile} impossible")
            self._info(f"copie de l'ancien Caddyfile : {sauve}")
            restaurer = "cat {s} > {c}".format(s=shlex.quote(sauve), c=shlex.quote(cfg.caddyfile))
        chargement = self._charger_caddy()
        if not chargement.ok:
            self._bash_ct(p, restaurer)
            self._charger_caddy()
            raise ErreurDeploiement(f"Caddy n'a pas accepté la nouvelle route : ancienne configuration remise (Caddy "
                                    f"continue de servir l'ancienne).\n--- caddy ---\n{chargement.texte(15)}")
        if cfg.dossier_sites_caddy:
            self._dans_ct(p, ["rm", "-f", f"{cible}.cerveau-precedent"])
        self._info(f"route active : {dom}")

    def _controle_https(self, adresses: list[str]) -> None:
        self._etape("Contrôle HTTPS")
        if not adresses:
            self._info(f"sauté : {self.domaine} ne résout pas encore. Une fois le DNS créé, relance la même commande "
                       "ou ouvre l'adresse.")
            return
        url = f"https://{self.domaine}{self.projet.sante if self.projet.type == 'application' else '/'}"
        # Depuis le proxy lui-même : pas besoin du retour NAT de la box, et le certificat est vérifié.
        commande = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-m", "15",
                    "--resolve", f"{self.domaine}:443:127.0.0.1", url]
        for essai in range(6):
            resultat = self._dans_ct(self.cfg.ct_proxy, commande, simule=Resultat(0, "200"))
            if self.simulation:
                return
            if resultat.ok and resultat.sortie.strip()[:1] in ("2", "3"):
                self._info(f"{url} répond {resultat.sortie.strip()} avec un certificat valide")
                return
            if self.simulation:
                break
            self.attendre(5)
        self._info(f"attention : {url} ne répond pas encore en HTTPS (certificat en cours d'émission ou DNS pas "
                   f"encore propagé ?). Journal : pct exec {self.cfg.ct_proxy} -- journalctl -u caddy -n 50")

    # -- application --

    def _choisir_ct(self) -> int:
        if self.ct_demande:
            return int(self.ct_demande)
        if self.etat.get("ct"):
            return int(self.etat["ct"])
        if self.mise_a_jour:
            raise ErreurDeploiement("mise à jour : aucun déploiement connu (deploiement/deploiement.json absent). "
                                    "Indique --ct N ou fais d'abord un déploiement complet.")
        pris: set[int] = set()
        for commande in (["pct", "list"], ["qm", "list"]):
            resultat = self._exiger(self._sur_hote(commande), f"« {' '.join(commande)} » a échoué sur l'hôte")
            for ligne in resultat.sortie.splitlines()[1:]:
                morceaux = ligne.split()
                if morceaux and morceaux[0].isdigit():
                    pris.add(int(morceaux[0]))
        ct = self.cfg.ct_minimum
        while ct in pris or ct == self.cfg.ct_proxy:
            ct += 1
        if self.simulation:
            self._info(f"CT {ct} : premier numéro libre à partir de {self.cfg.ct_minimum} (provisoire en simulation)")
        else:
            self._info(f"CT {ct} : premier numéro libre à partir de {self.cfg.ct_minimum}")
        return ct

    def _config_ct(self, ct: int) -> str | None:
        """Configuration du CT s'il existe (et s'il a bien été créé par cerveau pour ce projet), sinon None."""
        # En simulation : le CT existe s'il s'agit d'une mise à jour ou d'un projet déjà déployé dans ce CT.
        if self.mise_a_jour or self.etat.get("ct") == ct:
            ip = self.ip_demandee or self.etat.get("ip") or str(self.cfg.bornes_plage()[0])
            simule = Resultat(0, f"hostname: {self.projet.nom}\nnet0: name=eth0,bridge={self.cfg.pont},ip={ip}/"
                                 f"{self.cfg.prefixe_reseau()}\ntags: {TAG}\n")
        else:
            simule = Resultat(2, "", f"Configuration file 'nodes/pve/lxc/{ct}.conf' does not exist")
        resultat = self._sur_hote(["pct", "config", str(ct)], simule=simule)
        if not resultat.ok:
            if "does not exist" in resultat.texte() or "no such" in resultat.texte().lower():
                return None
            raise ErreurDeploiement(f"« pct config {ct} » a échoué :\n{resultat.texte()}")
        texte = resultat.sortie
        tags = re.search(r"^tags:\s*(.+)$", texte, re.M)
        nom = re.search(r"^hostname:\s*(\S+)", texte, re.M)
        if not tags or TAG not in re.split(r"[;,\s]+", tags.group(1)):
            raise ErreurDeploiement(f"le CT {ct} existe et n'a pas été créé par cerveau deployer (pas d'étiquette "
                                    f"« {TAG} ») : rien n'a été touché. Choisis un autre numéro avec --ct.")
        if not nom or nom.group(1) != self.projet.nom:
            raise ErreurDeploiement(f"le CT {ct} appartient à un autre projet ({nom.group(1) if nom else '?'}, pas "
                                    f"{self.projet.nom}) : rien n'a été touché.")
        return texte

    def _choisir_ip(self, config_ct: str | None) -> str:
        reseau = ipaddress.ip_network(self.cfg.reseau, strict=False)
        actuelle = re.search(r"\bip=(\d+\.\d+\.\d+\.\d+)", config_ct or "")
        if config_ct and actuelle:
            if self.ip_demandee and self.ip_demandee != actuelle.group(1):
                raise ErreurDeploiement(f"le CT {self.ct} a l'IP {actuelle.group(1)}, pas {self.ip_demandee} : change-la "
                                        "dans Proxmox ou retire --ip.")
            return actuelle.group(1)
        ip = self.ip_demandee
        if not ip and self.etat.get("ct") == self.ct:
            ip = self.etat.get("ip")
        if ip:
            adresse = ipaddress.ip_address(ip)
            if adresse not in reseau or str(adresse) in (self.cfg.passerelle, self.cfg.ip_proxy):
                raise ErreurDeploiement(f"IP {ip} refusée : hors de {self.cfg.reseau}, ou passerelle / proxy.")
            if config_ct is None:
                pris = self._sur_hote(["ping", "-c", "1", "-W", "1", str(adresse)], simule=Resultat(1))
                if pris.ok:
                    raise ErreurDeploiement(f"l'IP {ip} répond déjà au ping : elle est utilisée. Choisis-en une autre.")
            return str(adresse)
        if self.mise_a_jour:
            raise ErreurDeploiement(f"IP du CT {self.ct} introuvable dans sa configuration.")
        connues = self._sur_hote(["bash", "-c", "cat /etc/pve/nodes/*/lxc/*.conf /etc/pve/nodes/*/qemu-server/*.conf "
                                                "2>/dev/null || true"])
        prises = set(re.findall(r"\bip=(\d+\.\d+\.\d+\.\d+)", connues.sortie))
        debut, fin = self.cfg.bornes_plage()
        candidate = debut
        while candidate <= fin:
            texte = str(candidate)
            if texte not in prises and texte not in (self.cfg.passerelle, self.cfg.ip_proxy):
                if self.simulation:
                    self._info(f"IP {texte} : première libre de la plage {self.cfg.plage_ip} (provisoire en simulation)")
                    return texte
                if not self._sur_hote(["ping", "-c", "1", "-W", "1", texte]).ok:
                    self._info(f"IP {texte} : première libre de la plage {self.cfg.plage_ip}")
                    return texte
            candidate += 1
        raise ErreurDeploiement(f"plus aucune IP libre dans {self.cfg.plage_ip} : élargis la plage ou donne --ip.")

    def _creer_ct(self) -> None:
        cfg, ct = self.cfg, self.ct
        self._etape(f"Création du conteneur {ct} ({self.projet.nom}, {self.ip})")
        self._exiger(self._sur_hote(["bash", "-c", f'test -f "$(pvesm path {shlex.quote(cfg.template)})"']),
                     f"template introuvable : {cfg.template}. Sur l'hôte : « pveam update ; pveam available --section "
                     "system | grep debian ; pveam download local <template> », puis mets-le dans la configuration.")
        argv = ["pct", "create", str(ct), cfg.template, "--hostname", self.projet.nom,
                "--cores", str(cfg.coeurs), "--memory", str(cfg.memoire_mo), "--swap", str(cfg.swap_mo),
                "--rootfs", f"{cfg.stockage}:{cfg.disque_go}",
                "--net0", f"name=eth0,bridge={cfg.pont},ip={self.ip}/{cfg.prefixe_reseau()},gw={cfg.passerelle}",
                "--unprivileged", "1", "--features", "nesting=1", "--onboot", "1", "--tags", TAG,
                "--description", f"{self.projet.titre} : https://{self.domaine} (cerveau deployer, kit {self.projet.kit})"]
        if cfg.dns:
            argv += ["--nameserver", cfg.dns]
        self._exiger(self._sur_hote(argv, timeout=600), f"pct create {ct} a échoué")
        self._exiger(self._sur_hote(["pct", "start", str(ct)]), f"le CT {ct} a été créé mais ne démarre pas")
        self._exiger(self._dans_ct(ct, ["sh", "-c", "i=0; while [ $i -lt 30 ]; do getent hosts deb.debian.org "
                                                    ">/dev/null 2>&1 && exit 0; i=$((i+1)); sleep 1; done; exit 1"],
                                   timeout=120),
                     f"le CT {ct} n'a pas d'accès réseau ou DNS : vérifie le pont ({cfg.pont}), la passerelle "
                     f"({cfg.passerelle}) et le DNS. Il est créé : la prochaine exécution le réutilisera.")

    def _demarrer_ct_si_arrete(self) -> None:
        statut = self._sur_hote(["pct", "status", str(self.ct)], simule=Resultat(0, "status: running"))
        if "running" not in statut.sortie:
            self._exiger(self._sur_hote(["pct", "start", str(self.ct)]), f"le CT {self.ct} ne démarre pas")

    def _sante(self) -> bool:
        url = f"http://{self.ip}:{self.projet.port}{self.projet.sante}"
        commande = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-m", "5", url]
        for _ in range(20):
            resultat = self._sur_hote(commande, simule=Resultat(0, "200"))
            if self.simulation:
                return True
            if resultat.ok and resultat.sortie.strip() == "200":
                self._info(f"{url} répond 200")
                return True
            if self.simulation:
                return True
            self.attendre(1.5)
        return False

    def _installer(self) -> None:
        ct, nom = self.ct, self.projet.nom
        self._etape(f"Envoi du code dans le CT {ct}")
        donnees, nombre = (b"", 0) if self.simulation else archive(self.racine, application=True)
        if not self.simulation:
            self._info(f"{nombre} fichiers (sans .venv, data/, .git, __pycache__, dist/)")
        self._envoyer(ct, donnees, "/root/cerveau-code.tgz", "archive du code", droits="0600")
        nouveau = f"/opt/{nom}/app.nouveau"
        self._exiger(self._bash_ct(ct, f"set -e; rm -rf {nouveau}; mkdir -p {nouveau}; tar --no-same-owner -xzf "
                                       f"/root/cerveau-code.tgz -C {nouveau}; rm -f /root/cerveau-code.tgz"),
                     "extraction du code dans le conteneur impossible")
        self._etape("Installation dans le conteneur (paquets, utilisateur, uv, dépendances, services ; quelques minutes)")
        argv = ["bash", f"{nouveau}/deploiement/installer.sh"] + (["--mise-a-jour"] if self.mise_a_jour else [])
        resultat = self._dans_ct(ct, argv, timeout=1800)
        for ligne in resultat.sortie.splitlines():
            if ligne.startswith(MARQUEUR_MDP):
                self.mot_de_passe_admin = ligne[len(MARQUEUR_MDP):].strip()
            elif ligne.startswith("==>") or "ÉCHEC" in ligne:
                self._info(ligne)
        if not resultat.ok:
            raise ErreurDeploiement(f"l'installation a échoué dans le CT {ct} ; la version en service n'a pas été "
                                    f"remplacée si l'échec précède l'échange du code.\n--- fin de la sortie ---\n"
                                    f"{resultat.texte(30)}")

    def _verifier_sante(self) -> None:
        self._etape(f"Contrôle de santé depuis l'hôte ({self.projet.sante})")
        if self._sante():
            return
        ct, nom = self.ct, self.projet.nom
        journal = self._dans_ct(ct, ["journalctl", "-u", nom, "-n", "40", "--no-pager"])
        message = f"l'application ne répond pas sur http://{self.ip}:{self.projet.port}{self.projet.sante}."
        precedente = self._dans_ct(ct, ["test", "-d", f"/opt/{nom}/app.precedent"])
        if precedente.ok:
            self._info("la nouvelle version ne répond pas : retour à la précédente")
            retour = self._dans_ct(ct, ["bash", f"/opt/{nom}/app/deploiement/installer.sh", "--revenir"], timeout=1800)
            if retour.ok and self._sante():
                message += f" La version précédente a été remise en service (version écartée : /opt/{nom}/app.echec)."
            else:
                message += " Le retour à la version précédente n'a pas suffi : l'application est hors service."
        else:
            message += " Aucune route Caddy n'a été ajoutée."
        raise ErreurDeploiement(f"{message}\n--- journal du service ---\n{journal.texte(40)}")

    def _deployer_application(self) -> None:
        self.ct = self._choisir_ct()
        if self.ct == self.cfg.ct_proxy:
            raise ErreurDeploiement(f"le CT {self.ct} est celui du proxy Caddy : choisis un autre numéro.")
        self._etape(f"Conteneur {self.ct}")
        config_ct = self._config_ct(self.ct)
        if config_ct is None and self.mise_a_jour:
            raise ErreurDeploiement(f"le CT {self.ct} n'existe pas : fais d'abord un déploiement complet (sans "
                                    "--mise-a-jour).")
        self._info("existe déjà (créé par cerveau pour ce projet) : réutilisé" if config_ct
                   else "absent : il sera créé")
        self.ip = self._choisir_ip(config_ct)
        self._generer(provisoire=self.simulation and config_ct is None)
        adresses = self._dns() if not self.mise_a_jour else []
        if config_ct is None:
            self._creer_ct()
        else:
            self._demarrer_ct_si_arrete()
        self._installer()
        self._verifier_sante()
        if self.mise_a_jour:
            return
        if self.mot_de_passe_admin or self.simulation:
            # Le compte admin existe maintenant : le mot de passe initial n'a plus à traîner dans /etc.
            self._dans_ct(self.ct, ["rm", "-f", f"/etc/{self.projet.nom}/admin-initial.env"])
        self._route_caddy()
        self._controle_https(adresses)

    # -- site statique --

    def _deployer_statique(self) -> None:
        if self.ct_demande or self.ip_demandee:
            self._info("--ct et --ip ne servent pas pour un site statique (servi par le proxy) : ignorés")
        self._etape("Construction du site (dist/)")
        self._exiger(self._lancer("local", self.local, shlex.split(self.projet.construire, posix=os.name != "nt"),
                                  cwd=self.racine, timeout=900),
                     f"la construction du site a échoué (« {self.projet.construire} »)")
        dist = self.racine / self.projet.dist
        if not self.simulation and not (dist / "index.html").is_file():
            raise ErreurDeploiement(f"{self.projet.dist}/index.html absent après la construction : rien n'a été envoyé.")
        self._generer(provisoire=False)
        adresses = self._dns() if not self.mise_a_jour else []
        p, cfg = self.cfg.ct_proxy, self.cfg
        self._etape(f"Envoi du site dans le CT {p} (nouvelle version, bascule atomique)")
        donnees = b"" if self.simulation else archive(dist, application=False)[0]
        version = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{cfg.dossier_sites_statiques.rstrip('/')}/{self.domaine}"
        archive_ct = f"/tmp/cerveau-site-{version}.tgz"
        self._envoyer(p, donnees, archive_ct, "archive du site", droits="0600")
        script = (f"set -e; b={shlex.quote(base)}; v=\"$b/versions/{version}\"; mkdir -p \"$v\"; "
                  f"tar --no-same-owner -xzf {archive_ct} -C \"$v\"; rm -f {archive_ct}; "
                  f"chmod -R a+rX \"$v\"; test -f \"$v/index.html\"; "
                  f"ln -sfn \"versions/{version}\" \"$b/courant.tmp\"; mv -T \"$b/courant.tmp\" \"$b/courant\"; "
                  f"ls -1d \"$b\"/versions/* | sort | head -n -3 | xargs -r rm -rf")
        self._exiger(self._bash_ct(p, script), "installation de la nouvelle version du site impossible (l'ancienne "
                                               "reste en ligne)")
        self._info(f"version {version} en ligne dans {base}/courant (3 dernières versions gardées)")
        if self.mise_a_jour:
            return
        self._route_caddy()
        self._controle_https(adresses)

    # -- ensemble --

    def deployer(self) -> int:
        p = self.projet
        sens = "Mise à jour" if self.mise_a_jour else "Déploiement"
        self.afficher(f"{sens} de {p.nom} (kit {p.kit}, {'site statique' if p.type == 'statique' else 'application'})"
                      f" → https://{self.domaine}")
        if self.simulation:
            self.afficher("SIMULATION : rien n'est exécuté ni envoyé ; voici les commandes, dans l'ordre.")
        if isinstance(self.hote, ExecuteurSSH):
            self.afficher(f"[hôte] = {shlex.join(self.hote.prefixe())} … ; [CT n] = pct exec n -- … sur l'hôte")
        self._tests()
        self._verifier_hote_et_proxy()
        if p.type == "statique":
            self._deployer_statique()
        else:
            self._deployer_application()
        if not self.simulation:
            etat = {"kit": p.kit, "type": p.type, "nom": p.nom, "domaine": self.domaine, "hote": self.cfg.hote,
                    "ct_proxy": self.cfg.ct_proxy, "date": _dt.datetime.now().isoformat(timespec="seconds")}
            if p.type == "application":
                etat.update(ct=self.ct, ip=self.ip, port=p.port)
            (self.racine / DOSSIER / ETAT).write_text(json.dumps(etat, indent=1, ensure_ascii=False) + "\n",
                                                      encoding="utf-8")
        self.afficher("")
        if self.simulation:
            self.afficher(f"Simulation terminée : {len(self.commandes)} commandes. Fichiers écrits dans {DOSSIER}/ ; "
                          "relance sans --simulation pour déployer.")
            return 0
        if p.type == "application":
            self.afficher(f"En ligne : https://{self.domaine} (CT {self.ct}, {self.ip}:{p.port}). "
                          f"Mode d'emploi : {DOSSIER}/DEPLOIEMENT.md")
        else:
            self.afficher(f"En ligne : https://{self.domaine}. Mode d'emploi : {DOSSIER}/DEPLOIEMENT.md")
        if self.mot_de_passe_admin:
            self.afficher("\n" + "=" * 64 + "\n Compte administrateur initial, affiché UNE SEULE FOIS :\n"
                          f"   identifiant : admin\n   mot de passe : {self.mot_de_passe_admin}\n"
                          " Note-le maintenant, puis change-le dans « Mon compte ».\n" + "=" * 64)
        return 0


# --- ligne de commande ---------------------------------------------------------------------------------

def ajouter_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("projet", nargs="?", help="dossier du projet (créé depuis un kit)")
    parser.add_argument("--configurer", action="store_true", help=f"questions pour créer {fichier_config()}")
    parser.add_argument("--domaine", help="ex. app.exemple.fr (repris du dernier déploiement si absent)")
    parser.add_argument("--ct", type=int, help="numéro du conteneur (par défaut : premier libre)")
    parser.add_argument("--ip", help="IP du conteneur (par défaut : première libre de la plage)")
    parser.add_argument("--simulation", action="store_true", help="affiche les commandes sans rien exécuter")
    parser.add_argument("--mise-a-jour", dest="mise_a_jour", action="store_true",
                        help="code + dépendances + redémarrage, sans toucher aux données ni aux secrets")
    return parser


def cmd_deployer(args: argparse.Namespace) -> int:
    try:
        if args.configurer:
            return configurer()
        if not args.projet:
            print("échec : indique le dossier du projet, ex. cerveau deployer . --domaine app.exemple.fr")
            return 2
        racine = Path(args.projet).expanduser()
        if not racine.is_dir():
            print(f"échec : dossier introuvable : {racine}")
            return 2
        cfg = charger_config()
        return Deployeur(cfg, racine, args.domaine, ct=args.ct, ip=args.ip, simulation=args.simulation,
                         mise_a_jour=args.mise_a_jour).deployer()
    except ErreurDeploiement as exc:
        print(f"\néchec : {exc}")
        return 1
    except ValueError as exc:
        print(f"\néchec : {exc}")
        return 2
    except KeyboardInterrupt:
        print("\ninterrompu : relance la même commande, chaque étape reprend là où elle en était.")
        return 130


def main_deployer(argv: list[str]) -> int:
    parser = ajouter_arguments(argparse.ArgumentParser(
        prog="cerveau deployer", description="Met en ligne un projet de kit sur Proxmox, derrière Caddy."))
    return cmd_deployer(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main_deployer(sys.argv[1:]))
