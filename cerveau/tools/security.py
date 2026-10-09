"""Outils d'audit de sécurité défensif sur le code du projet.

Chaque outil enveloppe un scanner reconnu et renvoie un résumé lisible par le
modèle. Si le scanner n'est pas installé, l'outil le dit et explique comment
l'installer, pour que le modèle puisse transmettre l'information.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .registry import Tool, to_json, truncate

INSTALL = {
    "semgrep": "pip install semgrep   (Mac, Windows, Linux)",
    "gitleaks": "brew install gitleaks  |  winget install gitleaks  |  https://github.com/gitleaks/gitleaks/releases",
    "trivy": "brew install trivy  |  winget install trivy  |  https://github.com/aquasecurity/trivy/releases",
}


def _missing(name: str) -> str:
    return f"{name} n'est pas installé. Installation : {INSTALL[name]}"


def _run(cmd: list[str], cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def semgrep_scan(root: Path, path: str = ".", config: str = "auto") -> str:
    """Analyse statique : injections, secrets en dur, mauvaises pratiques crypto, etc."""
    if not shutil.which("semgrep"):
        return _missing("semgrep")
    proc = _run(["semgrep", "scan", "--config", config, "--json", "--quiet", "--metrics=off", path], root)
    if proc.returncode not in (0, 1) and not proc.stdout:
        return f"semgrep a échoué (code {proc.returncode}) : {proc.stderr[-1500:]}"
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return f"sortie semgrep illisible : {proc.stdout[:1000]}"
    findings = []
    for r in data.get("results", []):
        extra = r.get("extra", {})
        findings.append({
            "fichier": r.get("path"),
            "ligne": r.get("start", {}).get("line"),
            "severite": extra.get("severity"),
            "regle": r.get("check_id"),
            "message": extra.get("message", "")[:300],
            "extrait": extra.get("lines", "")[:200],
        })
    findings.sort(key=lambda f: {"ERROR": 0, "WARNING": 1}.get(f["severite"], 2))
    summary = {"nombre": len(findings), "resultats": findings[:60]}
    if len(findings) > 60:
        summary["note"] = f"{len(findings) - 60} résultats supplémentaires non affichés"
    return truncate(to_json(summary))


def secrets_scan(root: Path, path: str = ".") -> str:
    """Recherche de secrets : clés d'API, mots de passe, jetons commis dans le code."""
    if not shutil.which("gitleaks"):
        return _missing("gitleaks")
    report = root / ".cerveau-gitleaks.json"
    try:
        proc = _run(["gitleaks", "detect", "--source", path, "--no-git", "--report-format", "json",
                     "--report-path", str(report), "--exit-code", "0"], root)
        if not report.exists():
            return f"gitleaks n'a rien produit : {proc.stderr[-1000:]}"
        data = json.loads(report.read_text(encoding="utf-8") or "[]")
    finally:
        report.unlink(missing_ok=True)
    findings = [{
        "fichier": d.get("File"), "ligne": d.get("StartLine"), "regle": d.get("RuleID"),
        "description": d.get("Description"), "secret_masque": (d.get("Secret") or "")[:4] + "…",
    } for d in data]
    return truncate(to_json({"nombre": len(findings), "resultats": findings[:60]}))


def dependencies_scan(root: Path, path: str = ".") -> str:
    """Vulnérabilités connues (CVE) dans les dépendances : requirements, package.json, go.mod, etc."""
    if not shutil.which("trivy"):
        return _missing("trivy")
    proc = _run(["trivy", "fs", "--scanners", "vuln", "--format", "json", "--quiet", path], root, timeout=900)
    if proc.returncode != 0 and not proc.stdout:
        return f"trivy a échoué : {proc.stderr[-1500:]}"
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return f"sortie trivy illisible : {proc.stdout[:1000]}"
    findings = []
    for result in data.get("Results", []) or []:
        for v in result.get("Vulnerabilities", []) or []:
            findings.append({
                "cible": result.get("Target"), "paquet": v.get("PkgName"), "version": v.get("InstalledVersion"),
                "corrige_en": v.get("FixedVersion"), "cve": v.get("VulnerabilityID"), "severite": v.get("Severity"),
                "titre": (v.get("Title") or "")[:150],
            })
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    findings.sort(key=lambda f: order.get(f["severite"], 4))
    return truncate(to_json({"nombre": len(findings), "resultats": findings[:60]}))


def scanners_status(root: Path) -> str:
    """Indique quels scanners sont installés sur cette machine."""
    return to_json({name: ("installé" if shutil.which(name) else _missing(name)) for name in INSTALL})


_PATH = {"type": "object", "properties": {"path": {"type": "string", "description": "Sous-dossier ou fichier, relatif au projet", "default": "."}}}

TOOLS = [
    Tool("semgrep_scan", "Analyse statique de sécurité avec Semgrep (injections, secrets, crypto faible, etc.).",
         {"type": "object", "properties": {**_PATH["properties"], "config": {"type": "string", "default": "auto",
          "description": "Jeu de règles Semgrep, ex: auto, p/owasp-top-ten, p/python"}}}, semgrep_scan),
    Tool("secrets_scan", "Cherche des secrets commis dans le code avec gitleaks.", _PATH, secrets_scan),
    Tool("dependencies_scan", "Cherche les CVE connues dans les dépendances avec Trivy.", _PATH, dependencies_scan),
    Tool("scanners_status", "Liste les scanners de sécurité installés sur cette machine.",
         {"type": "object", "properties": {}}, scanners_status),
]
