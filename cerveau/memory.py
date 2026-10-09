"""Mémoire de projet dans SQLite, avec recherche vectorielle et textuelle.

Deux types de souvenirs :
  - "episode"  : ce qui s'est passé (question, décision, faille trouvée, correctif)
  - "fact"     : connaissance stable sur le projet (convention, architecture)

La recherche combine la similarité cosinus sur les embeddings et, à défaut
d'embeddings, la recherche plein texte FTS5. Pas de dépendance externe : pour
quelques milliers de souvenirs, un balayage en Python suffit largement.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Embedder = Callable[[list[str]], list[list[float]]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY,
    project    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  TEXT,
    created_at REAL NOT NULL,
    last_used  REAL,
    uses       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS memories_project ON memories(project);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content, content='memories', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


@dataclass
class Memory:
    id: int
    project: str
    kind: str
    content: str
    created_at: float
    score: float = 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryStore:
    def __init__(self, path: Path | str, embedder: Embedder | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.embedder = embedder

    def close(self) -> None:
        self.conn.close()

    # --- écriture -------------------------------------------------------------

    def remember(self, project: str, content: str, kind: str = "episode") -> int:
        content = content.strip()
        if not content:
            raise ValueError("souvenir vide")
        embedding = None
        if self.embedder:
            try:
                embedding = json.dumps(self.embedder([content])[0])
            except Exception:  # noqa: BLE001 - la mémoire doit marcher même sans embeddings
                embedding = None
        cur = self.conn.execute(
            "INSERT INTO memories(project, kind, content, embedding, created_at) VALUES (?,?,?,?,?)",
            (project, kind, content, embedding, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def forget(self, memory_id: int) -> None:
        self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()

    # --- lecture --------------------------------------------------------------

    def recall(self, project: str, query: str, limit: int = 6) -> list[Memory]:
        results = self._recall_vector(project, query, limit) if self.embedder else []
        if not results:
            results = self._recall_fts(project, query, limit)
        if results:
            ids = [m.id for m in results]
            self.conn.execute(
                f"UPDATE memories SET uses = uses + 1, last_used = ? WHERE id IN ({','.join('?' * len(ids))})",
                (time.time(), *ids),
            )
            self.conn.commit()
        return results

    def _recall_vector(self, project: str, query: str, limit: int) -> list[Memory]:
        try:
            qvec = self.embedder([query])[0]  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return []
        rows = self.conn.execute(
            "SELECT id, project, kind, content, embedding, created_at FROM memories "
            "WHERE project = ? AND embedding IS NOT NULL", (project,)
        ).fetchall()
        scored = []
        for row in rows:
            score = _cosine(qvec, json.loads(row["embedding"]))
            if score > 0.3:
                scored.append(Memory(row["id"], row["project"], row["kind"], row["content"], row["created_at"], score))
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:limit]

    def _recall_fts(self, project: str, query: str, limit: int) -> list[Memory]:
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 2]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms[:12])
        rows = self.conn.execute(
            "SELECT m.id, m.project, m.kind, m.content, m.created_at, bm25(memories_fts) AS rank "
            "FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid "
            "WHERE memories_fts MATCH ? AND m.project = ? ORDER BY rank LIMIT ?",
            (match, project, limit),
        ).fetchall()
        return [Memory(r["id"], r["project"], r["kind"], r["content"], r["created_at"], -r["rank"]) for r in rows]

    def recent(self, project: str, limit: int = 10) -> list[Memory]:
        rows = self.conn.execute(
            "SELECT id, project, kind, content, created_at FROM memories WHERE project = ? "
            "ORDER BY created_at DESC LIMIT ?", (project, limit)
        ).fetchall()
        return [Memory(r["id"], r["project"], r["kind"], r["content"], r["created_at"]) for r in rows]

    def count(self, project: str | None = None) -> int:
        if project is None:
            return self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM memories WHERE project = ?", (project,)).fetchone()[0]
