"""Déploiement Proxmox + Caddy : uniquement des exécuteurs factices, aucune connexion, aucun réseau."""

import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from cerveau import config
from cerveau import deploiement as dep
from cerveau.deploiement import ConfigDeploiement, Deployeur, ErreurDeploiement, Resultat
from cerveau.tools.projets import create_project

BASH = shutil.which("bash") if os.name != "nt" else None
CADDYFILE = "{\n\temail admin@exemple.fr\n}\n\nautre.exemple.fr {\n\treverse_proxy 10.10.20.50:8000\n}\n"


class FauxExecuteur(dep.Executeur):
    """Enregistre les commandes ; la première règle (motif, réponse) qui correspond donne le résultat."""

    def __init__(self, regles=(), interdit=False):
        self.regles = list(regles)
        self.interdit = interdit
        self.appels: list[str] = []
        self.entrees: dict[int, bytes] = {}

    def executer(self, argv, *, entree=None, cwd=None, timeout=900):
        assert not self.interdit, f"aucune commande ne devait être exécutée : {argv}"
        ligne = shlex.join(argv)
        self.appels.append(ligne)
        if entree is not None:
            self.entrees[len(self.appels) - 1] = entree
        for motif, reponse in self.regles:
            if re.search(motif, ligne):
                return reponse(ligne) if callable(reponse) else reponse
        return Resultat(0)

    def index(self, motif: str) -> int:
        for i, ligne in enumerate(self.appels):
            if re.search(motif, ligne):
                return i
        raise AssertionError(f"commande attendue absente : {motif}\n" + "\n".join(self.appels))

    def contient(self, motif: str) -> bool:
        return any(re.search(motif, l) for l in self.appels)


def regles_hote(installation="==> paquets système\nMOT_DE_PASSE_ADMIN_INITIAL=Ab3-secret_initial\n==> démarrage\n",
                validation=Resultat(0, "Valid configuration")):
    return [
        (r"^pveversion$", Resultat(0, "pve-manager/8.2.4/faa83925c9641325 (running kernel: 6.8.8-2-pve)\n")),
        (r"^pct status 101$", Resultat(0, "status: running\n")),
        (r"^pct list$", Resultat(0, "VMID       Status     Lock         Name\n200        running                 jarvis\n"
                                    "201        stopped                 autre\n")),
        (r"^qm list$", Resultat(0, "      VMID NAME                 STATUS     MEM(MB)\n       202 vm-trading  running 4096\n")),
        (r"^pct config 203$", Resultat(2, "", "Configuration file 'nodes/pve/lxc/203.conf' does not exist\n")),
        (r"cat /etc/pve/nodes", Resultat(0, "net0: name=eth0,bridge=vmbr0,ip=10.10.20.100/24,gw=10.10.20.1\n")),
        (r"^ping ", Resultat(1)),
        (r"installer\.sh", Resultat(0, installation)),
        (r"curl .*http://10\.10\.20\.\d+:8000/sante", Resultat(0, "200")),
        (r"pct exec 101 -- cat /etc/caddy/Caddyfile$", Resultat(0, CADDYFILE)),
        (r"caddy validate", validation),
        (r"--resolve", Resultat(0, "200")),
    ]


@pytest.fixture
def cfg():
    return ConfigDeploiement(hote="192.168.1.10", ip_proxy="10.10.30.10", cible_dns="dev.exemple.fr")


@pytest.fixture
def garage(tmp_path):
    racine = tmp_path / "garage"
    racine.mkdir()
    assert create_project(racine, "gestion", "Garage Test").startswith("projet créé")
    return racine


@pytest.fixture
def vitrine(tmp_path):
    racine = tmp_path / "vitrine"
    racine.mkdir()
    assert create_project(racine, "site", "Menuiserie Test").startswith("projet créé")
    return racine


def deployeur(cfg, racine, *, hote=None, local=None, sortie=None, resoudre=lambda d: [], **kw):
    sortie = sortie if sortie is not None else []
    return Deployeur(cfg, racine, kw.pop("domaine", "garage.exemple.fr"), hote=hote or FauxExecuteur(regles_hote()),
                     local=local or FauxExecuteur([(r"pytest", Resultat(0, "12 passed in 3.1s"))]),
                     resoudre=resoudre, attendre=lambda s: None, afficher=sortie.append, **kw)


# --- configuration ------------------------------------------------------------------------

def test_configuration_absente_dit_de_lancer_configurer(tmp_path, monkeypatch, capsys, garage):
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    assert dep.main_deployer([str(garage), "--domaine", "garage.exemple.fr", "--simulation"]) == 1
    sortie = capsys.readouterr().out
    assert "configuration absente" in sortie and "cerveau deployer --configurer" in sortie


def test_configurer_ecrit_des_valeurs_sures(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    reponses = iter(["192.168.1.10"] + [""] * 40)
    messages = []
    assert dep.configurer(demander=lambda q: next(reponses), afficher=messages.append) == 0
    cfg = dep.charger_config()
    assert cfg.hote == "192.168.1.10" and cfg.utilisateur == "root" and cfg.ct_proxy == 101
    assert cfg.reseau == "10.10.20.0/24" and cfg.ct_minimum >= 100 and cfg.retention_jours == 14


def test_configuration_incomplete_refusee(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    (tmp_path / "deploiement.json").write_text(json.dumps({"hote": "", "passerelle": "192.168.9.1"}), encoding="utf-8")
    with pytest.raises(ErreurDeploiement, match="--configurer"):
        dep.charger_config()


# --- simulation ------------------------------------------------------------------------------

def test_simulation_gestion_liste_tout_sans_rien_executer(cfg, garage):
    hote, local = FauxExecuteur(interdit=True), FauxExecuteur(interdit=True)
    sortie = []
    assert deployeur(cfg, garage, hote=hote, local=local, sortie=sortie, simulation=True).deployer() == 0
    texte = "\n".join(sortie)
    assert "SIMULATION" in texte and not hote.appels and not local.appels
    ordre = ["[local] uv run pytest -q", "[hôte] pveversion", "[hôte] pct create 200 ", "[hôte] pct push 200 ",
             "[CT 200] bash /opt/garage-test/app.nouveau/deploiement/installer.sh",
             "http://10.10.20.100:8000/sante", "[CT 101] caddy validate", "/load"]
    positions = [texte.index(m) for m in ordre]
    assert positions == sorted(positions), ordre
    assert "--unprivileged 1" in texte and "--onboot 1" in texte and "--tags cerveau" in texte
    assert "CNAME dev.exemple.fr." in texte

    dossier = garage / "deploiement"
    assert {p.name for p in dossier.iterdir()} == {"installer.sh", "sauvegarde.sh", "restaurer.sh", "caddy.conf",
                                                    "DEPLOIEMENT.md"}
    assert "reverse_proxy 10.10.20.100:8000" in (dossier / "caddy.conf").read_text(encoding="utf-8")
    installeur = (dossier / "installer.sh").read_text(encoding="utf-8")
    assert "@@" not in installeur and "uvicorn app.main:app --host 0.0.0.0 --port 8000" in installeur
    assert "--reload" not in installeur and "--forwarded-allow-ips 10.10.30.10" in installeur
    assert "Restart=always" in installeur and "User=$UTILISATEUR" in installeur
    assert "OnCalendar=*-*-* 02:30:00" in installeur
    assert "-mtime" in (dossier / "sauvegarde.sh").read_text(encoding="utf-8")
    doc = (dossier / "DEPLOIEMENT.md").read_text(encoding="utf-8")
    assert "provisoires" in doc and "restaurer.sh" in doc and "journalctl -u garage-test" in doc
    assert not (dossier / "deploiement.json").exists()          # pas d'état écrit en simulation
    if BASH:
        for script in dossier.glob("*.sh"):
            assert subprocess.run([BASH, "-n", str(script)]).returncode == 0, script.name


def test_simulation_site_statique_servi_par_le_proxy(cfg, vitrine):
    hote, local = FauxExecuteur(interdit=True), FauxExecuteur(interdit=True)
    sortie = []
    assert deployeur(cfg, vitrine, hote=hote, local=local, sortie=sortie, domaine="www.menuiserie.fr",
                     simulation=True).deployer() == 0
    texte = "\n".join(sortie)
    assert "[local] uv run python site.py build" in texte
    assert "pct create" not in texte and "installer.sh" not in texte
    assert "[hôte] pct push 101 " in texte and "mv -T" in texte
    bloc = (vitrine / "deploiement" / "caddy.conf").read_text(encoding="utf-8")
    assert "file_server" in bloc and "/srv/cerveau-sites/www.menuiserie.fr/courant" in bloc
    assert {p.name for p in (vitrine / "deploiement").iterdir()} == {"caddy.conf", "DEPLOIEMENT.md"}


# --- déploiement « réel » avec faux exécuteurs ------------------------------------------------

def test_deploiement_complet_application(cfg, garage):
    hote, sortie = FauxExecuteur(regles_hote()), []
    assert deployeur(cfg, garage, hote=hote, sortie=sortie).deployer() == 0
    # CT : 200-202 pris (pct list + qm list) → 203 ; IP : .100 déjà dans une conf → .101
    assert hote.index(r"^pct create 203 .*ip=10\.10\.20\.101/24,gw=10\.10\.20\.1")
    assert hote.index(r"^pct create") < hote.index(r"^pct push 203 ") < hote.index(r"installer\.sh") \
        < hote.index(r"curl .*:8000/sante") < hote.index(r"caddy validate") < hote.index(r"/load")
    # le code envoyé est une vraie archive, sans .venv ni data/
    archive = next(v for i, v in hote.entrees.items() if len(v) > 1000 and v[:2] == b"\x1f\x8b")
    noms = tarfile.open(fileobj=io.BytesIO(archive)).getnames()
    assert "deploiement/installer.sh" in noms and "app/main.py" in noms
    assert not {n.split("/")[0] for n in noms} & {".venv", "data", ".git", ".cerveau", "dist"}
    assert ".gitignore" in noms
    # le Caddyfile envoyé garde l'existant et ajoute le bloc entre marqueurs
    caddyfile = next(v.decode() for v in hote.entrees.values() if b"cerveau deployer : garage.exemple.fr" in v)
    assert caddyfile.startswith(CADDYFILE.rstrip()) and "reverse_proxy 10.10.20.101:8000" in caddyfile
    assert hote.contient(r"f=/var/log/caddy/garage\.exemple\.fr\.log;.*touch \"\$f\".*chown")
    assert hote.index(r"garage\.exemple\.fr\.log") < hote.index(r"caddy validate")
    # mot de passe : affiché une fois, retiré de /etc, jamais écrit dans le projet
    texte = "\n".join(sortie)
    assert texte.count("Ab3-secret_initial") == 1
    assert hote.contient(r"pct exec 203 -- rm -f /etc/garage-test/admin-initial\.env")
    assert not any("Ab3-secret_initial" in p.read_text(encoding="utf-8", errors="ignore")
                   for p in garage.rglob("*") if p.is_file())
    etat = json.loads((garage / "deploiement" / "deploiement.json").read_text(encoding="utf-8"))
    assert (etat["ct"], etat["ip"], etat["domaine"]) == (203, "10.10.20.101", "garage.exemple.fr")


def test_refus_de_deployer_si_les_tests_echouent(cfg, garage):
    hote = FauxExecuteur(interdit=True)
    local = FauxExecuteur([(r"pytest", Resultat(1, "FAILED tests/test_socle.py::test_x\n1 failed, 11 passed"))])
    with pytest.raises(ErreurDeploiement, match="les tests échouent") as exc:
        deployeur(cfg, garage, hote=hote, local=local).deployer()
    assert "1 failed" in str(exc.value) and not hote.appels
    assert not (garage / "deploiement").exists()


def test_validation_caddy_en_echec_retour_arriere(cfg, garage):
    hote = FauxExecuteur(regles_hote(validation=Resultat(1, "", "Error: adapting config: unknown directive 'revrse'")))
    with pytest.raises(ErreurDeploiement, match="retour arrière") as exc:
        deployeur(cfg, garage, hote=hote).deployer()
    assert "unknown directive" in str(exc.value)
    assert hote.index(r"caddy validate") < hote.index(r"rm -f /etc/caddy/Caddyfile\.cerveau-nouveau$")
    assert not hote.contient(r"> /etc/caddy/Caddyfile")          # le Caddyfile en service n'est pas remplacé
    assert not hote.contient(r"/load|caddy reload")              # rien n'est rechargé
    assert not (garage / "deploiement" / "deploiement.json").exists()


def test_chargement_caddy_en_echec_remet_l_ancien_caddyfile(cfg, garage):
    regles = [(r"/load", Resultat(7, "", "curl: (22) The requested URL returned error: 400"))] + regles_hote()
    hote = FauxExecuteur(regles)
    with pytest.raises(ErreurDeploiement, match="ancienne configuration remise"):
        deployeur(cfg, garage, hote=hote).deployer()
    remplace = hote.index(r"cat /etc/caddy/Caddyfile\.cerveau-nouveau > /etc/caddy/Caddyfile")
    restaure = hote.index(r"cat /etc/caddy/Caddyfile\.cerveau-\d{8}-\d{6} > /etc/caddy/Caddyfile")
    assert remplace < restaure
    assert sum("/load" in l for l in hote.appels) == 2            # nouvelle config refusée, ancienne rechargée


def test_mode_dossier_sites_retour_arriere(cfg, garage):
    cfg.dossier_sites_caddy = "/etc/caddy/sites"
    regles = [(r"cat /etc/caddy/Caddyfile$", Resultat(0, "import sites/*.caddy\n")),
              (r"grep -rlF", Resultat(1))] + regles_hote(validation=Resultat(1, "", "Error: bad"))
    hote = FauxExecuteur(regles)
    with pytest.raises(ErreurDeploiement, match="retour arrière"):
        deployeur(cfg, garage, hote=hote).deployer()
    assert hote.index(r"pct push 101 \S+ /etc/caddy/sites/garage\.exemple\.fr\.caddy") \
        < hote.index(r"mv -f /etc/caddy/sites/garage\.exemple\.fr\.caddy\.cerveau-precedent")
    assert not hote.contient(r"/load|caddy reload")


def test_domaine_deja_servi_a_la_main_refuse(cfg, garage):
    regles = [(r"cat /etc/caddy/Caddyfile$", Resultat(0, CADDYFILE + "\ngarage.exemple.fr {\n\trespond hello\n}\n"))]
    hote = FauxExecuteur(regles + regles_hote())
    with pytest.raises(ErreurDeploiement, match="déjà servi"):
        deployeur(cfg, garage, hote=hote).deployer()
    assert not hote.contient(r"caddy validate|Caddyfile\.cerveau")


def test_mise_a_jour_sans_toucher_aux_secrets_ni_a_caddy(cfg, garage):
    (garage / "deploiement").mkdir()
    (garage / "deploiement" / "deploiement.json").write_text(json.dumps(
        {"ct": 203, "ip": "10.10.20.101", "domaine": "garage.exemple.fr"}), encoding="utf-8")
    regles = [(r"^pct config 203$", Resultat(0, "hostname: garage-test\nnet0: name=eth0,bridge=vmbr0,"
                                                "ip=10.10.20.101/24,gw=10.10.20.1\ntags: cerveau\n")),
              (r"^pct status 203$", Resultat(0, "status: running"))]
    hote, sortie = FauxExecuteur(regles + regles_hote(installation="==> secrets\ndéjà en place\n")), []
    assert deployeur(cfg, garage, hote=hote, sortie=sortie, domaine=None, mise_a_jour=True).deployer() == 0
    assert hote.contient(r"^pct exec 203 -- bash /opt/garage-test/app\.nouveau/deploiement/installer\.sh --mise-a-jour$")
    assert not hote.contient(r"pct create|pct exec 101|caddy|admin-initial|app\.env")
    assert "Compte administrateur" not in "\n".join(sortie)
    installeur = (garage / "deploiement" / "installer.sh").read_text(encoding="utf-8")
    assert "une mise à jour ne crée pas de secrets" in installeur


def test_ct_existant_non_cree_par_cerveau_refuse(cfg, garage):
    regles = [(r"^pct config 196$", Resultat(0, "hostname: jarvis-core\nnet0: name=eth0,ip=10.10.20.9/24\n"))]
    hote = FauxExecuteur(regles + regles_hote())
    with pytest.raises(ErreurDeploiement, match="pas été créé par cerveau"):
        deployeur(cfg, garage, hote=hote, ct=196).deployer()
    assert not hote.contient(r"pct (create|push|exec 196)")


def test_sante_ko_remet_la_version_precedente(cfg, garage):
    etat = {"revenu": False}

    def sante(_ligne):
        return Resultat(0, "200" if etat["revenu"] else "502")

    def revenir(_ligne):
        etat["revenu"] = True
        return Resultat(0, "version précédente remise en service")

    regles = [(r"installer\.sh --revenir", revenir), (r"curl .*:8000/sante", sante),
              (r"test -d /opt/garage-test/app\.precedent", Resultat(0)),
              (r"journalctl", Resultat(0, "Traceback: ImportError"))]
    hote = FauxExecuteur(regles + regles_hote())
    with pytest.raises(ErreurDeploiement, match="version précédente a été remise en service") as exc:
        deployeur(cfg, garage, hote=hote).deployer()
    assert "ImportError" in str(exc.value)
    assert not hote.contient(r"caddy validate")                    # pas de route vers une version cassée


def test_site_statique_reel(cfg, vitrine):
    (vitrine / "dist" / "static").mkdir(parents=True)
    (vitrine / "dist" / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    hote = FauxExecuteur(regles_hote())
    local = FauxExecuteur([(r"pytest", Resultat(0, "9 passed")), (r"site\.py build", Resultat(0))])
    assert deployeur(cfg, vitrine, hote=hote, local=local, domaine="www.menuiserie.fr",
                     resoudre=lambda d: ["203.0.113.7"]).deployer() == 0
    assert local.index(r"pytest") < local.index(r"site\.py build")
    assert hote.index(r"versions/\d{8}-\d{6}") < hote.index(r"caddy validate") < hote.index(r"--resolve")
    archive = next(v for v in hote.entrees.values() if v[:2] == b"\x1f\x8b")
    assert tarfile.open(fileobj=io.BytesIO(archive)).getnames() == ["index.html", "static"]


# --- briques ---------------------------------------------------------------------------------

def test_executeurs_ssh_et_pct_composent_la_commande():
    local = FauxExecuteur()
    ssh = dep.ExecuteurSSH("pve.lan", "root", 2222, "~/.ssh/cle", local=local)
    dep.ExecuteurPct(ssh, 101).executer(["caddy", "validate", "--config", "/etc/caddy/mon Caddyfile"])
    argv = shlex.split(local.appels[0])
    assert argv[:3] == ["ssh", "-o", "BatchMode=yes"] and "2222" in argv and "root@pve.lan" in argv
    assert argv[-1] == "pct exec 101 -- caddy validate --config '/etc/caddy/mon Caddyfile'"


def test_archive_exclut_ce_qui_ne_part_pas(tmp_path):
    for chemin in (".venv/bin/python", "data/app.db", ".git/config", "app/__pycache__/x.pyc", "dist/index.html",
                   ".env", "app/main.py", "pyproject.toml", ".cerveau/kit.json"):
        (tmp_path / chemin).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / chemin).write_text("x", encoding="utf-8")
    donnees, nombre = dep.archive(tmp_path, application=True)
    infos = tarfile.open(fileobj=io.BytesIO(donnees)).getmembers()
    assert sorted(i.name for i in infos if i.isfile()) == ["app/main.py", "pyproject.toml"] and nombre == 2
    assert all(i.uid == 0 and i.uname == "root" for i in infos)


def test_bloc_caddy_insere_puis_remplace():
    premier = dep.inserer_bloc(CADDYFILE, "a.fr", "a.fr {\n\treverse_proxy 10.0.0.1:8000\n}\n")
    second = dep.inserer_bloc(premier, "a.fr", "a.fr {\n\treverse_proxy 10.0.0.2:8000\n}\n")
    assert second.count("a.fr {") == 1 and "10.0.0.2" in second and "10.0.0.1" not in second
    assert second.startswith(CADDYFILE.rstrip())
    assert not dep.domaine_deja_servi(second, "a.fr")
    assert dep.domaine_deja_servi(second + "\nwww.b.fr, a.fr {\n}\n", "a.fr")
    assert not dep.domaine_deja_servi("x.a.fr {\n\tlog /var/log/a.fr.log\n}\n", "a.fr")


def test_domaine_invalide():
    assert dep.verifier_domaine("https://App.Exemple.fr/") == "app.exemple.fr"
    with pytest.raises(ErreurDeploiement):
        dep.verifier_domaine("pas un domaine")


# --- scripts générés, exécutés pour de vrai dans un faux système ------------------------------------

def _systeme_factice(tmp_path):
    """Dossier racine + commandes système factices (apt, systemctl, chown, uv…) qui journalisent leurs appels."""
    shims, journal = tmp_path / "shims", tmp_path / "appels.log"
    shims.mkdir()
    for nom in ("apt-get", "dpkg", "useradd", "id", "chown", "chgrp", "systemctl", "curl"):
        (shims / nom).write_text(f'#!/bin/sh\necho "{nom} $*" >> "{journal}"\nexit 0\n', encoding="utf-8")
    (shims / "uv").write_text(f'#!/bin/sh\necho "uv $* ($PWD)" >> "{journal}"\nmkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n',
                              encoding="utf-8")
    (shims / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    for shim in shims.iterdir():
        shim.chmod(0o755)
    env = dict(os.environ, PATH=f"{shims}{os.pathsep}{os.environ['PATH']}", CERVEAU_RACINE_SYSTEME=str(tmp_path / "sys"))
    return env, journal


def _pousser(projet: Path, systeme: Path, nom: str) -> Path:
    """Comme le déployeur : archive du projet extraite dans /opt/<nom>/app.nouveau."""
    nouveau = systeme / "opt" / nom / "app.nouveau"
    shutil.rmtree(nouveau, ignore_errors=True)
    nouveau.mkdir(parents=True)
    options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    tarfile.open(fileobj=io.BytesIO(dep.archive(projet, application=True)[0])).extractall(nouveau, **options)
    return nouveau


@pytest.mark.skipif(not BASH, reason="bash requis")
def test_installer_idempotent_ne_recree_pas_les_secrets(cfg, garage, tmp_path):
    projet = dep.analyser_projet(garage, cfg.ip_proxy)
    dep.generer_fichiers(projet, cfg, "garage.exemple.fr", 203, "10.10.20.101")
    env, journal = _systeme_factice(tmp_path)
    systeme = tmp_path / "sys"
    opt, conf = systeme / "opt" / "garage-test", systeme / "etc" / "garage-test"

    def lancer(script: Path, *args):
        return subprocess.run([BASH, str(script), *args], env=env, capture_output=True, text=True, encoding="utf-8")

    premier = lancer(_pousser(garage, systeme, "garage-test") / "deploiement" / "installer.sh")
    assert premier.returncode == 0, premier.stderr
    assert premier.stdout.count(dep.MARQUEUR_MDP) == 1
    secrets = (conf / "app.env").read_text(encoding="utf-8")
    assert re.search(r"^SECRET_KEY=[\w-]{40,}$", secrets, re.M)
    assert "APP_DATABASE_URL=sqlite:////var/lib/garage-test/data/app.db" in secrets
    assert (conf / "admin-initial.env").read_text(encoding="utf-8").startswith("ADMIN_PASSWORD=")
    assert (opt / "app" / "app" / "main.py").is_file() and not (opt / "app.nouveau").exists()
    assert (opt / "app" / "data").is_symlink()
    service = (systeme / "etc" / "systemd" / "system" / "garage-test.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/garage-test/venv/bin/uvicorn app.main:app" in service
    assert "User=garage-test" in service and "EnvironmentFile=/etc/garage-test/app.env" in service

    # relancé sur place : rien n'est recréé
    second = lancer(opt / "app" / "deploiement" / "installer.sh")
    assert second.returncode == 0, second.stderr
    assert dep.MARQUEUR_MDP not in second.stdout and "conservés" in second.stdout
    assert (conf / "app.env").read_text(encoding="utf-8") == secrets

    # mise à jour : nouvelle version, ancienne gardée, secrets intacts
    (garage / "app" / "nouveau.py").write_text("X = 1\n", encoding="utf-8")
    maj = lancer(_pousser(garage, systeme, "garage-test") / "deploiement" / "installer.sh", "--mise-a-jour")
    assert maj.returncode == 0, maj.stderr
    assert (opt / "app" / "app" / "nouveau.py").is_file() and (opt / "app.precedent" / "app" / "main.py").is_file()
    assert (conf / "app.env").read_text(encoding="utf-8") == secrets
    assert "systemctl restart garage-test.service" in journal.read_text(encoding="utf-8")

    # une mise à jour refuse de fabriquer des secrets qui auraient disparu
    (conf / "app.env").unlink()
    refus = lancer(opt / "app" / "deploiement" / "installer.sh", "--mise-a-jour")
    assert refus.returncode != 0 and "ne crée pas de secrets" in refus.stderr
    assert not (conf / "app.env").exists()


@pytest.mark.skipif(not BASH or not shutil.which("sqlite3"), reason="bash et sqlite3 requis")
def test_sauvegarde_retention_et_restauration(cfg, garage, tmp_path):
    projet = dep.analyser_projet(garage)
    dep.generer_fichiers(projet, cfg, "garage.exemple.fr", 203, "10.10.20.101")
    env, journal = _systeme_factice(tmp_path)
    donnees, sauvegardes = tmp_path / "sys/var/lib/garage-test/data", tmp_path / "sys/var/backups/garage-test"
    donnees.mkdir(parents=True)
    sauvegardes.mkdir(parents=True)
    with sqlite3.connect(donnees / "app.db") as base:
        base.execute("PRAGMA journal_mode=WAL")
        base.execute("CREATE TABLE clients (nom TEXT)")
        base.execute("INSERT INTO clients VALUES ('Dupont')")
    vieille, recente = sauvegardes / "app-2026-01-01_023000.db.gz", sauvegardes / "app-2026-10-05_023000.db.gz"
    for fichier, jours in ((vieille, 20), (recente, 5)):
        fichier.write_bytes(b"x")
        os.utime(fichier, (time.time() - jours * 86400,) * 2)

    script = garage / "deploiement"
    resultat = subprocess.run([BASH, str(script / "sauvegarde.sh")], env=env, capture_output=True, text=True)
    assert resultat.returncode == 0, resultat.stderr
    nouvelles = [p for p in sauvegardes.glob("app-*.db.gz") if p not in (vieille, recente)]
    assert len(nouvelles) == 1 and not vieille.exists() and recente.exists()

    with sqlite3.connect(donnees / "app.db") as base:
        base.execute("DELETE FROM clients")
    resultat = subprocess.run([BASH, str(script / "restaurer.sh"), str(nouvelles[0])], env=env, capture_output=True,
                              text=True)
    assert resultat.returncode == 0, resultat.stderr
    with sqlite3.connect(donnees / "app.db") as base:
        assert base.execute("SELECT nom FROM clients").fetchall() == [("Dupont",)]
    assert list(donnees.glob("app.db.avant-restauration-*"))
    appels = journal.read_text(encoding="utf-8")
    assert appels.index("systemctl stop garage-test.service") < appels.index("systemctl start garage-test.service")
