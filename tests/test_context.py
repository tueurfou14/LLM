import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cerveau import ollama
from cerveau.agent import compact_messages


def test_compact_leaves_small_conversations_alone():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert compact_messages(msgs, 1000) == 0
    assert len(msgs) == 2


def test_compact_shortens_old_tool_results_first():
    msgs = [{"role": "system", "content": "consigne"}, {"role": "user", "content": "q"}]
    for i in range(10):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": str(i), "name": "read_file", "content": "x" * 4000})
    before = len(msgs)
    compact_messages(msgs, budget_tokens=8000)
    assert msgs[0]["content"] == "consigne"
    assert len(msgs) == before
    results = [m for m in msgs if m["role"] == "tool"]
    assert any("raccourci" in m["content"] for m in results[:-6])
    assert all("raccourci" not in m["content"] for m in results[-6:])   # les récents restent entiers


def test_compact_drops_oldest_exchanges_but_keeps_system_and_last():
    msgs = [{"role": "system", "content": "consigne"}]
    for i in range(30):
        msgs.append({"role": "user", "content": f"question {i} " + "y" * 2000})
        msgs.append({"role": "assistant", "content": "réponse " + "z" * 2000})
    msgs.append({"role": "user", "content": "dernière question"})
    compact_messages(msgs, budget_tokens=3000)
    assert msgs[0]["content"] == "consigne"
    assert msgs[-1]["content"] == "dernière question"
    assert len(msgs) < 10


def test_variant_name_is_idempotent():
    assert ollama.variant_name("qwen3-coder:30b", 32768) == "qwen3-coder:30b-ctx32k"
    assert ollama.variant_name("qwen3-coder:30b-ctx16k", 32768) == "qwen3-coder:30b-ctx32k"


@pytest.fixture
def fake_ollama():
    created = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/show":
                name = body["model"]
                if name == "base:7b":
                    data = {"parameters": "stop <|im_end|>", "model_info": {"qwen2.context_length": 32768}}
                elif name in created:
                    data = {"parameters": f"num_ctx {created[name]}", "model_info": {"qwen2.context_length": 32768}}
                else:
                    self.send_response(404); self.end_headers(); self.wfile.write(b'{"error":"not found"}'); return
            elif self.path == "/api/create":
                created[body["model"]] = body["parameters"]["num_ctx"]
                data = {"status": "success"}
            raw = json.dumps(data).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1", created
    srv.shutdown()


def test_ensure_context_creates_variant_once(fake_ollama):
    url, created = fake_ollama
    assert ollama.context_of(url, "base:7b") == (None, 32768)
    name = ollama.ensure_context(url, "base:7b", 16384)
    assert name == "base:7b-ctx16k" and created[name] == 16384
    assert ollama.ensure_context(url, "base:7b", 16384) == name
    assert len(created) == 1
    assert ollama.ensure_context(url, name, 16384) == name


def test_ensure_context_refuses_beyond_maximum(fake_ollama):
    url, _ = fake_ollama
    with pytest.raises(Exception, match="au plus"):
        ollama.ensure_context(url, "base:7b", 65536)
