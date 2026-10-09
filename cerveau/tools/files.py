"""Lecture du projet. Tout accès est confiné à la racine du projet."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from .registry import Tool, truncate

IGNORED = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".cerveau", ".pytest_cache"}


def safe_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
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
