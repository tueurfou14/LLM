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


def test_streams_when_no_text_reply(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="")])
    assert agent.ask("dis quelque chose") == "flux"
