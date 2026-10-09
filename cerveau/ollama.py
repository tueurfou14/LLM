"""Réglages propres à Ollama, par son API native.

L'API compatible OpenAI ne permet pas de fixer la taille de contexte : Ollama
applique son défaut, souvent 4096 tokens, et tronque en silence au-delà. On
crée donc une variante du modèle avec le contexte voulu, via /api/create.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .llm import LLMError


def is_ollama(base_url: str) -> bool:
    return ":11434" in base_url


def native_url(base_url: str) -> str:
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


def _post(url: str, payload: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise LLMError(f"Ollama a répondu {exc.code} : {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Ollama injoignable sur {url} ({exc.reason}).") from exc
    # /api/create répond en NDJSON ; on garde la dernière ligne.
    last = [line for line in body.splitlines() if line.strip()][-1:]
    return json.loads(last[0]) if last else {}


def show(base_url: str, model: str) -> dict:
    return _post(native_url(base_url) + "/api/show", {"model": model})


def context_of(base_url: str, model: str) -> tuple[int | None, int | None]:
    """(contexte configuré sur le modèle, contexte maximal supporté).
    Le premier vaut None si Ollama appliquera son défaut."""
    info = show(base_url, model)
    configured = None
    match = re.search(r"num_ctx\s+(\d+)", info.get("parameters") or "")
    if match:
        configured = int(match.group(1))
    maximum = None
    for key, value in (info.get("model_info") or {}).items():
        if key.endswith(".context_length"):
            maximum = int(value)
    return configured, maximum


def variant_name(model: str, context_tokens: int) -> str:
    base = re.sub(r"-ctx\d+k$", "", model)
    return f"{base}-ctx{context_tokens // 1024}k"


def ensure_context(base_url: str, model: str, context_tokens: int) -> str:
    """Renvoie le nom d'un modèle servi avec `context_tokens` de contexte,
    en le créant si besoin. Le modèle d'origine n'est pas modifié."""
    configured, maximum = context_of(base_url, model)
    if maximum and context_tokens > maximum:
        raise LLMError(f"{model} supporte au plus {maximum} tokens de contexte.")
    if configured == context_tokens:
        return model
    name = variant_name(model, context_tokens)
    try:
        if context_of(base_url, name)[0] == context_tokens:
            return name
    except LLMError:
        pass
    result = _post(native_url(base_url) + "/api/create",
                   {"model": name, "from": model, "parameters": {"num_ctx": context_tokens}, "stream": False},
                   timeout=600)
    if result.get("status") not in ("success", None) and "error" in result:
        raise LLMError(f"création de {name} refusée : {result['error']}")
    return name
