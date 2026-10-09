"""Client minimal vers un serveur compatible OpenAI, sans dépendance externe.

Fonctionne avec LM Studio et Ollama. Gère le streaming SSE et les appels
d'outils au format OpenAI.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Client:
    def __init__(self, base_url: str, api_key: str = "local", timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # --- HTTP -----------------------------------------------------------------

    def _request(self, path: str, payload: dict, stream: bool = False):
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)  # noqa: S310
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise LLMError(f"HTTP {exc.code} sur {path} : {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                f"Impossible de joindre {self.base_url} ({exc.reason}). "
                "Lancez LM Studio ou Ollama, ou corrigez base_url dans la config."
            ) from exc

    def models(self) -> list[str]:
        req = urllib.request.Request(self.base_url + "/models", headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = json.load(resp)
        except urllib.error.URLError as exc:
            raise LLMError(f"Impossible de joindre {self.base_url} ({exc.reason}).") from exc
        return [m.get("id", "") for m in data.get("data", [])]

    # --- Chat -----------------------------------------------------------------

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.2, max_tokens: int | None = None) -> Reply:
        payload: dict = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens
        with self._request("/chat/completions", payload) as resp:
            data = json.load(resp)
        return _parse_message(data["choices"][0]["message"])

    def chat_stream(self, model: str, messages: list[dict], temperature: float = 0.2,
                    max_tokens: int | None = None) -> Iterator[str]:
        """Génère la réponse token par token. Sans outils : le streaming d'appels
        d'outils est inégal selon les serveurs, on le réserve au texte."""
        payload: dict = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        with self._request("/chat/completions", payload, stream=True) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0]["delta"]
                except (ValueError, KeyError, IndexError):
                    continue
                text = delta.get("content")
                if text:
                    yield text

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        with self._request("/embeddings", {"model": model, "input": texts}) as resp:
            data = json.load(resp)
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in items]


def _parse_message(msg: dict) -> Reply:
    reply = Reply(content=msg.get("content") or "")
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {})
        args = fn.get("arguments") or "{}"
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_raw": args}
        reply.tool_calls.append(ToolCall(id=call.get("id", ""), name=fn.get("name", ""), arguments=args))
    return reply
