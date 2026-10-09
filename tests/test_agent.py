"""L'agent est testé avec un faux client : aucun serveur d'inférence requis."""

from cerveau.agent import Agent
from cerveau.config import Config
from cerveau.llm import Reply, ToolCall
from cerveau.memory import MemoryStore


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, model, messages, tools=None, temperature=0.2, max_tokens=None):
        self.calls.append(messages)
        self.last_tools = tools
        return self.replies.pop(0)

    def chat_stream(self, *a, **k):
        yield "flux"

    def embed(self, model, texts):
        return [[1.0, 0.0] for _ in texts]


def make_agent(tmp_path, replies):
    (tmp_path / "main.py").write_text("x = 1\n")
    cfg = Config(model="fake")
    store = MemoryStore(tmp_path / "mem.sqlite")
    return Agent(cfg, tmp_path, client=FakeClient(replies), store=store)


def test_tool_call_then_answer(tmp_path):
    agent = make_agent(tmp_path, [
        Reply(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "main.py"})]),
        Reply(content="Le fichier contient x = 1."),
    ])
    answer = agent.ask("que contient main.py ?")
    assert answer == "Le fichier contient x = 1."
    tool_msgs = [m for m in agent.client.calls[1] if m.get("role") == "tool"]
    assert tool_msgs and "x = 1" in tool_msgs[0]["content"]


def test_answer_is_remembered(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="Réponse.")])
    agent.ask("question ?")
    assert agent.store.count(agent.project) == 1
    assert "question" in agent.store.recent(agent.project)[0].content


def test_memories_injected_in_system_prompt(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="ok")])
    agent.note("Le projet suit PEP8 strictement.")
    agent.ask("rappelle-moi la convention de style PEP8")
    system = agent.client.calls[0][0]["content"]
    assert "PEP8" in system


def test_stats_are_estimated_without_usage(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="Une réponse d'une vingtaine de caractères.")])
    agent.ask("dis quelque chose")
    st = agent.last_stats
    assert st.estimated and st.completion_tokens > 0 and st.prompt_tokens > 0 and st.rounds == 1


def test_stats_use_server_usage(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="ok", usage={"prompt_tokens": 120, "completion_tokens": 7})])
    agent.ask("x")
    assert (agent.last_stats.prompt_tokens, agent.last_stats.completion_tokens, agent.last_stats.estimated) == (120, 7, False)


def test_tool_round_limit_asks_for_summary(tmp_path):
    agent = make_agent(tmp_path, [])
    agent.config.max_tool_rounds = 3
    agent.client.replies = [Reply(tool_calls=[ToolCall(id=str(i), name="list_files", arguments={})]) for i in range(3)]
    agent.client.replies.append(Reply(content="Bilan : j'ai listé les fichiers, écrivez « continue »."))
    answer = agent.ask("fais plein de choses")
    assert answer.startswith("Bilan")
    # le dernier appel se fait sans outils
    assert agent.client.last_tools == []
    assert "Actions effectuées" in agent.history[-1]["content"]


class EventsClient(FakeClient):
    """Client qui parle le streaming : vérifie que l'agent consomme chat_events."""

    def chat_events(self, model, messages, tools=None, temperature=0.2, max_tokens=None):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        for word in (reply.content.split(" ") if reply.content else []):
            yield ("token", word + " ")
        for call in reply.tool_calls:
            yield ("tool", call)
        yield ("done", reply)


def test_agent_streams_tokens_through_chat_events(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    received = []
    agent = Agent(Config(model="fake"), tmp_path, client=EventsClient([
        Reply(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "main.py"})]),
        Reply(content="Le fichier contient x."),
    ]), store=MemoryStore(tmp_path / "m.sqlite"), on_token=received.append)
    assert agent.ask("lis main.py").strip() == "Le fichier contient x."
    assert len(received) == 4


def test_text_tool_call_is_executed(tmp_path):
    """Un modèle qui écrit <tool_call> en texte brut est quand même servi."""
    agent = make_agent(tmp_path, [
        Reply(content='<tool_call>{"name": "create_directory", "arguments": {"path": "src/erp"}}</tool_call>'),
        Reply(content="Dossier créé."),
    ])
    # Reply ne passe pas par _parse_message ; on simule ce que le client ferait.
    from cerveau.llm import extract_tool_calls
    first = agent.client.replies[0]
    first.content, first.tool_calls = extract_tool_calls(first.content)
    assert agent.ask("crée le dossier src/erp") == "Dossier créé."
    assert (tmp_path / "src/erp").is_dir()


def test_skill_injected_for_matching_request(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="ok")])
    agent.ask("fais un audit de sécurité")
    system = agent.client.calls[0][0]["content"]
    assert "Skill : Audit de sécurité" in system
    assert "- write_file :" in system


def test_timeout_becomes_llm_error():
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from cerveau.llm import Client, LLMError

    class Silent(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            import time
            time.sleep(2)   # plus long que le timeout du client

    srv = HTTPServer(("127.0.0.1", 0), Silent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = Client(f"http://127.0.0.1:{srv.server_port}/v1", timeout=0.5)
    import pytest
    with pytest.raises(LLMError, match="n'a rien envoyé"):
        list(client.chat_events("m", [{"role": "user", "content": "x"}]))
    srv.shutdown()
