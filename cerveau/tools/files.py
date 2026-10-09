"""Lecture du projet. Tout accès est confiné à la racine du projet."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .registry import Tool, truncate

IGNORED = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".cerveau", ".pytest_cache"}


def normalize_relative(root: Path, raw: str) -> str:
    """Ramène ce qu'écrit le modèle à un chemin relatif à la racine du projet.
    Tolère : antislashs, « ./ », barre initiale, chemin absolu dans le projet,
    et le nom du projet répété en tête (« Garage/garage/models.py »)."""
    text = str(raw).strip().strip("\"'").replace("\\", "/")
    root_resolved = root.resolve()
    root_posix = root_resolved.as_posix().rstrip("/")
    # Chemin absolu qui pointe dans le projet : on retire la racine.
    low = text.lower()
    if low.startswith(root_posix.lower()):
        text = text[len(root_posix):]
    elif ":" in text[:3] or text.startswith("//"):
        raise PermissionError(f"chemin hors du projet : {raw}")
    text = re.sub(r"^(\./)+", "", text).lstrip("/")
    # Nom du projet répété en tête, à la casse exacte, alors que ce dossier n'existe pas.
    # (« garage/models.py » en minuscules reste un paquet Python légitime du projet « Garage ».)
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if len(parts) > 1 and parts[0] == root_resolved.name and not (root_resolved / parts[0]).exists():
        parts = parts[1:]
    if ".." in parts:
        raise PermissionError(f"chemin hors du projet : {raw}")
    return "/".join(parts)


def safe_path(root: Path, relative: str) -> Path:
    clean = normalize_relative(root, relative)
    target = (root / clean).resolve() if clean else root.resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise PermissionError(f"chemin hors du projet : {relative}")
    return target


def list_files(root: Path, pattern: str = "*", max_results: int = 200) -> str:
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if any(part in IGNORED for part in path.parts):
            continue
        if path.is_file() and fnmatch.fnmatch(path.name, pattern):
            out.append(str(path.relative_to(root)))
            if len(out) >= max_results:
                out.append(f"... (limité à {max_results} résultats)")
                break
    return "\n".join(out) or "aucun fichier"


def read_file(root: Path, path: str, start_line: int = 1, max_lines: int = 300) -> str:
    target = safe_path(root, path)
    if not target.is_file():
        return f"fichier introuvable : {path}"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1)
    chunk = lines[start - 1 : start - 1 + max_lines]
    body = "\n".join(f"{start + i:5d}  {line}" for i, line in enumerate(chunk))
    if start - 1 + max_lines < len(lines):
        body += f"\n... ({len(lines)} lignes au total, reprendre à start_line={start + max_lines})"
    return truncate(body)


def search_code(root: Path, pattern: str, glob: str = "*", max_results: int = 100) -> str:
    import re

    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"expression invalide : {exc}"
    hits: list[str] = []
    for path in root.rglob("*"):
        if any(part in IGNORED for part in path.parts) or not path.is_file():
            continue
        if not fnmatch.fnmatch(path.name, glob):
            continue
        try:
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{path.relative_to(root)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + f"\n... (limité à {max_results})"
        except OSError:
            continue
    return "\n".join(hits) or "aucune correspondance"


TOOLS = [
    Tool(
        name="list_files",
        description="Liste les fichiers du projet, avec un motif optionnel sur le nom (ex: *.py).",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Motif glob sur le nom de fichier", "default": "*"},
                "max_results": {"type": "integer", "default": 200},
            },
        },
        run=list_files,
    ),
    Tool(
        name="read_file",
        description="Lit un fichier du projet avec les numéros de ligne.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif à la racine du projet"},
                "start_line": {"type": "integer", "default": 1},
                "max_lines": {"type": "integer", "default": 300},
            },
            "required": ["path"],
        },
        run=read_file,
    ),
    Tool(
        name="search_code",
        description="Cherche une expression régulière dans le code du projet.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Expression régulière, insensible à la casse"},
                "glob": {"type": "string", "description": "Motif sur le nom de fichier", "default": "*"},
                "max_results": {"type": "integer", "default": 100},
            },
            "required": ["pattern"],
        },
        run=search_code,
    ),
]


# --- écriture -----------------------------------------------------------------

def write_file(root: Path, path: str, content: str) -> str:
    """Crée ou remplace un fichier du projet. Les dossiers parents sont créés."""
    target = safe_path(root, path)
    shown = target.relative_to(root.resolve()).as_posix()
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return f"{'remplacé' if existed else 'créé'} : {shown} ({lines} lignes)"


def edit_file(root: Path, path: str, old: str, new: str) -> str:
    """Remplace un passage exact d'un fichier. Le passage doit être unique."""
    target = safe_path(root, path)
    if not target.is_file():
        return f"fichier introuvable : {path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        return f"passage introuvable dans {path} ; relis le fichier et copie le texte exact"
    if count > 1:
        return f"passage présent {count} fois dans {path} ; donne plus de contexte pour le rendre unique"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"modifié : {path}"


def create_directory(root: Path, path: str) -> str:
    """Crée un dossier, parents compris."""
    target = safe_path(root, path)
    target.mkdir(parents=True, exist_ok=True)
    shown = target.relative_to(root.resolve()).as_posix() or "."
    return f"dossier prêt : {shown} (racine du projet : {root.resolve()})"


TOOLS += [
    Tool(
        name="write_file",
        description="Crée ou remplace entièrement un fichier du projet avec le contenu donné.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif à la racine du projet"},
                "content": {"type": "string", "description": "Contenu complet du fichier"},
            },
            "required": ["path", "content"],
        },
        run=write_file,
    ),
    Tool(
        name="edit_file",
        description="Remplace un passage exact d'un fichier existant par un nouveau texte.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string", "description": "Texte exact à remplacer, unique dans le fichier"},
                "new": {"type": "string", "description": "Texte de remplacement"},
            },
            "required": ["path", "old", "new"],
        },
        run=edit_file,
    ),
    Tool(
        name="create_directory",
        description="Crée un dossier dans le projet.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        run=create_directory,
    ),
]
