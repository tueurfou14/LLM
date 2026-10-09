"""Configuration du noyau, persistée dans ~/.cerveau/config.json.

Le serveur d'inférence est n'importe quel serveur compatible OpenAI :
LM Studio (port 1234) ou Ollama (port 11434). Le noyau ne fait pas la
différence, il n'utilise que /v1/chat/completions et /v1/embeddings.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import hardware

HOME = Path(os.environ.get("CERVEAU_HOME", Path.home() / ".cerveau"))


@dataclass
class Config:
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "local"
    model: str = "qwen2.5-coder:7b"
    embedding_model: str = "nomic-embed-text"
    context_tokens: int = 16384
    temperature: float = 0.2
    max_memories: int = 6
    max_tool_rounds: int = 60
    timeout_seconds: int = 600     # attente maximale sans aucun octet du serveur
    auto_learn: bool = True        # extraire préférences et décisions après chaque réponse
    permission_mode: str = "confirm"   # "confirm" : les commandes attendent un o ; "auto" : rien n'est demandé
    tier: str = "small"
    hardware: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return HOME / "memoire.sqlite"

    def save(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True)
        (HOME / "config.json").write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


def _from_hardware() -> Config:
    hw = hardware.detect()
    tier = hardware.pick_tier(hw)
    lmstudio = hw.system == "Darwin"
    return Config(
        base_url="http://localhost:1234/v1" if lmstudio else "http://localhost:11434/v1",
        model=tier.main_model_lmstudio if lmstudio else tier.main_model_ollama,
        embedding_model="text-embedding-nomic-embed-text-v1.5" if lmstudio else "nomic-embed-text",
        context_tokens=tier.context_tokens,
        tier=tier.name,
        hardware=asdict(hw),
    )


def load() -> Config:
    path = HOME / "config.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
        cfg = Config(**known)
    else:
        cfg = _from_hardware()
        cfg.save()
    # Les variables d'environnement l'emportent, pratique pour tester.
    cfg.base_url = os.environ.get("CERVEAU_BASE_URL", cfg.base_url)
    cfg.model = os.environ.get("CERVEAU_MODEL", cfg.model)
    cfg.embedding_model = os.environ.get("CERVEAU_EMBEDDING_MODEL", cfg.embedding_model)
    return cfg
