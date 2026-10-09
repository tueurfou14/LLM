"""Skills : fiches de procédure en Markdown, injectées dans la consigne quand
la demande s'y rapporte.

Un petit modèle code bien quand on lui donne la marche à suivre. Chaque
skill est un fichier `.md` :

    # Titre
    mots-clés: créer, projet, nouveau
    description: Une ligne qui dit quand l'utiliser.

    Corps de la procédure...

Les skills livrés sont dans `cerveau/skills/`. L'utilisateur peut ajouter
les siens dans `~/.cerveau/skills/`, ils ont priorité à nom égal.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_DIR = Path(__file__).parent / "skills"


@dataclass
class Skill:
    name: str
    title: str
    description: str
    keywords: list[str]
    body: str
    path: Path
    score: int = field(default=0, compare=False)


def _fold(text: str) -> str:
    """Minuscules sans accents, pour comparer « sécurité » et « securite »."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def parse(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    title, description, keywords, body_lines = path.stem, "", [], []
    for line in text.splitlines():
        stripped = line.strip()
        low = _fold(stripped)
        if not title_set(title, path) and stripped.startswith("# "):
            title = stripped[2:].strip()
        elif low.startswith("mots-cles:") or low.startswith("keywords:"):
            keywords = [_fold(k.strip()) for k in stripped.split(":", 1)[1].split(",") if k.strip()]
        elif low.startswith("description:"):
            description = stripped.split(":", 1)[1].strip()
        else:
            body_lines.append(line)
    return Skill(path.stem, title, description, keywords, "\n".join(body_lines).strip(), path)


def title_set(title: str, path: Path) -> bool:
    return title != path.stem


def load_all(extra_dirs: list[Path] | None = None) -> list[Skill]:
    skills: dict[str, Skill] = {}
    for directory in [BUILTIN_DIR, *(extra_dirs or [])]:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                skills[path.stem] = parse(path)
            except OSError:
                continue
    return list(skills.values())


def select(skills: list[Skill], message: str, limit: int = 2) -> list[Skill]:
    """Les skills dont les mots-clés apparaissent dans la demande, les mieux
    notés d'abord. Un mot-clé compte une fois, même répété."""
    folded = _fold(message)
    words = set(re.findall(r"[a-z0-9]+", folded))
    scored = []
    for skill in skills:
        score = 0
        for kw in skill.keywords:
            if " " in kw:
                score += 2 if kw in folded else 0
            elif kw in words or any(w.startswith(kw) for w in words if len(kw) >= 4):
                score += 1
        if score:
            scored.append(Skill(**{**skill.__dict__, "score": score}))
    scored.sort(key=lambda s: (-s.score, s.name))
    return scored[:limit]


def render(skills: list[Skill]) -> str:
    return "\n\n".join(f"### Skill : {s.title}\n{s.body}" for s in skills)
