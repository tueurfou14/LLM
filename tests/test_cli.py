from cerveau import config
from cerveau.__main__ import main


def test_model_command_updates_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setenv("CERVEAU_BASE_URL", "http://127.0.0.1:9/v1")  # injoignable, c'est voulu
    assert main(["model", "qwen3-coder:30b", "--context", "8192"]) == 0
    cfg = config.load()
    assert cfg.model == "qwen3-coder:30b"
    assert cfg.context_tokens == 8192
    assert "injoignable" in capsys.readouterr().out


def test_use_command_switches_server(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.delenv("CERVEAU_BASE_URL", raising=False)
    assert main(["use", "lmstudio"]) == 0
    assert config.load().base_url == "http://localhost:1234/v1"
    assert main(["use", "ollama"]) == 0
    assert config.load().base_url == "http://localhost:11434/v1"
