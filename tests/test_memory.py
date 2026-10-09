import math

import pytest

from cerveau.memory import MemoryStore


def fake_embedder(texts):
    """Embedding jouet : un vecteur par mot-clé connu, pour tester le rappel vectoriel."""
    keys = ["sql", "injection", "mot", "passe", "python", "docker"]
    out = []
    for t in texts:
        low = t.lower()
        vec = [1.0 if k in low else 0.0 for k in keys]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm + 1e-3 for v in vec])
    return out


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.sqlite", embedder=fake_embedder)
    yield s
    s.close()


def test_remember_and_count(store):
    store.remember("proj", "Injection SQL trouvée dans login.py")
    store.remember("proj", "Le projet utilise Docker pour les tests", kind="fact")
    store.remember("autre", "souvenir d'un autre projet")
    assert store.count("proj") == 2
    assert store.count() == 3


def test_vector_recall_ranks_relevant_first(store):
    store.remember("proj", "Injection SQL trouvée dans login.py")
    store.remember("proj", "Le projet utilise Docker pour les tests", kind="fact")
    store.remember("proj", "Mot de passe en dur dans config.py")
    hits = store.recall("proj", "y a-t-il une injection sql ?", limit=2)
    assert hits
    assert "SQL" in hits[0].content


def test_recall_is_scoped_to_project(store):
    store.remember("a", "Injection SQL dans a")
    store.remember("b", "Injection SQL dans b")
    hits = store.recall("a", "injection sql")
    assert all(h.project == "a" for h in hits)


def test_fts_fallback_without_embedder(tmp_path):
    s = MemoryStore(tmp_path / "m.sqlite")
    s.remember("proj", "Mot de passe en dur dans config.py")
    s.remember("proj", "Le projet utilise Docker")
    hits = s.recall("proj", "mot de passe")
    assert len(hits) == 1
    assert "config.py" in hits[0].content
    s.close()


def test_recall_updates_usage(store):
    mid = store.remember("proj", "Injection SQL trouvée dans login.py")
    store.recall("proj", "injection sql")
    uses = store.conn.execute("SELECT uses FROM memories WHERE id = ?", (mid,)).fetchone()[0]
    assert uses == 1


def test_forget(store):
    mid = store.remember("proj", "souvenir temporaire python")
    store.forget(mid)
    assert store.count("proj") == 0
    assert store.recall("proj", "python") == []


def test_empty_memory_rejected(store):
    with pytest.raises(ValueError):
        store.remember("proj", "   ")


def test_facts_are_deduplicated_but_episodes_are_not(store):
    a = store.remember("proj", "Le projet utilise Docker pour les tests", kind="fact")
    b = store.remember("proj", "le projet utilise docker pour les tests", kind="fact")
    assert a == b and store.count("proj") == 1
    store.remember("proj", "même épisode")
    store.remember("proj", "même épisode")
    assert store.count("proj") == 3


def test_global_memories_are_recalled_with_project_ones(store):
    from cerveau.memory import GLOBAL
    store.remember(GLOBAL, "L'utilisateur préfère Python avec des tests pytest", kind="preference")
    store.remember("proj", "Injection SQL trouvée dans login.py")
    hits = store.recall("proj", "python injection sql")
    assert {h.project for h in hits} == {"proj", GLOBAL}
