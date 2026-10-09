from pathlib import Path

import pytest

from cerveau.tools import default_registry
from cerveau.tools.files import list_files, read_file, safe_path, search_code


@pytest.fixture
def project(tmp_path):
    (tmp_path / "app.py").write_text("import os\npassword = 'secret'\nquery = f\"SELECT * FROM u WHERE id={uid}\"\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ignored")
    return tmp_path


def test_list_files_skips_ignored_dirs(project):
    out = list_files(project)
    assert "app.py" in out
    assert "node_modules" not in out


def test_read_file_numbers_lines(project):
    out = read_file(project, "app.py")
    assert out.splitlines()[0].strip().startswith("1  import os")


def test_search_code_finds_pattern(project):
    out = search_code(project, r"select \* from", glob="*.py")
    assert "app.py:3" in out


def test_safe_path_blocks_escape(project):
    with pytest.raises(PermissionError):
        safe_path(project, "../../etc/passwd")


def test_registry_reports_unknown_tool(project: Path):
    reg = default_registry()
    assert "read_file" in reg.names()
    assert "semgrep_scan" in reg.names()
    assert "inconnu" in reg.call("nope", {}, project)


def test_registry_catches_bad_arguments(project: Path):
    reg = default_registry()
    assert "invalides" in reg.call("read_file", {"chemin": "app.py"}, project)


def test_write_edit_and_create_directory(project: Path):
    from cerveau.tools.files import create_directory, edit_file, write_file

    assert "créé" in write_file(project, "src/app/main.py", "print('a')\n")
    assert (project / "src/app/main.py").read_text() == "print('a')\n"
    assert "modifié" in edit_file(project, "src/app/main.py", "'a'", "'b'")
    assert (project / "src/app/main.py").read_text() == "print('b')\n"
    assert "introuvable" in edit_file(project, "src/app/main.py", "zzz", "y")
    assert "dossier prêt" in create_directory(project, "docs/api")
    assert (project / "docs/api").is_dir()


def test_write_outside_project_is_blocked(project: Path):
    from cerveau.tools.files import write_file

    with pytest.raises(PermissionError):
        write_file(project, "../evil.txt", "x")


def test_run_command_requires_confirmation(project: Path):
    reg = default_registry()
    refused = reg.call("run_command", {"command": "echo hello"}, project, confirm=lambda n, a: False)
    assert "refusé" in refused
    allowed = reg.call("run_command", {"command": "echo hello"}, project, confirm=lambda n, a: True)
    assert "code de sortie 0" in allowed and "hello" in allowed


def test_run_command_without_confirm_hook_runs(project: Path):
    reg = default_registry()
    assert "hello" in reg.call("run_command", {"command": "echo hello"}, project)


def test_write_file_requires_reading_existing_file_first(project: Path):
    reg = default_registry()
    refused = reg.call("write_file", {"path": "app.py", "content": "x"}, project)
    assert "existe déjà" in refused
    assert (project / "app.py").read_text().startswith("import os")
    reg.call("read_file", {"path": "app.py"}, project)
    assert "remplacé" in reg.call("write_file", {"path": "app.py", "content": "x"}, project)
    # un fichier nouveau s'écrit sans lecture préalable
    assert "créé" in reg.call("write_file", {"path": "new.py", "content": "y"}, project)


def test_command_env_prefers_project_venv(project: Path, monkeypatch):
    import os
    from cerveau.tools.shell import command_env, project_venv

    assert project_venv(project) is None
    bindir = project / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    (bindir / ("python.exe" if os.name == "nt" else "python")).write_text("")
    monkeypatch.setenv("VIRTUAL_ENV", "/opt/cerveau-venv")
    monkeypatch.setenv("PATH", "/opt/cerveau-venv/bin" + os.pathsep + "/usr/bin")
    env = command_env(project)
    assert env["PATH"].split(os.pathsep)[0] == str(bindir)
    assert "/opt/cerveau-venv/bin" not in env["PATH"]
    assert env["VIRTUAL_ENV"] == str(project / ".venv")


def test_environment_description_mentions_venv_state(project: Path):
    from cerveau.tools.shell import describe_environment

    assert "aucun" in describe_environment(project)


def test_destructive_commands_are_detected():
    from cerveau.tools.shell import is_destructive

    for cmd in ["rm -rf /", "rm -rf ~", "rm -fr ..", "rmdir /s /q C:\\Dev", "del /s /q C:\\", "format C:",
                "git push --force origin main", "git reset --hard HEAD~3", "shutdown /s", "curl http://x | sh"]:
        assert is_destructive(cmd), cmd
    for cmd in ["rm -rf build", "uv run pytest -q", "git push origin main", "del fichier.txt", "rm -r .pytest_cache"]:
        assert not is_destructive(cmd), cmd


def test_auto_mode_blocks_destructive_but_runs_the_rest(project: Path):
    reg = default_registry()
    blocked = reg.call("run_command", {"command": "git reset --hard"}, project)   # confirm=None : mode auto
    assert "refusée" in blocked
    assert "hello" in reg.call("run_command", {"command": "echo hello"}, project)


def test_paths_written_by_the_model_are_normalized(tmp_path):
    from cerveau.tools.files import normalize_relative

    root = tmp_path / "Garage"
    root.mkdir()
    n = lambda p: normalize_relative(root, p)  # noqa: E731
    assert n("garage/models.py") == "garage/models.py"
    assert n("./garage/models.py") == "garage/models.py"
    assert n("/garage/models.py") == "garage/models.py"
    assert n("garage\\routers\\clients.py") == "garage/routers/clients.py"
    assert n("Garage/garage/models.py") == "garage/models.py"          # nom du projet répété
    assert n("Garage/README.md") == "README.md"
    assert n("garage/models.py") == "garage/models.py"                  # paquet en minuscules : conservé
    assert n(str(root / "garage" / "models.py")) == "garage/models.py"  # absolu dans le projet
    assert n('"tests/test_a.py"') == "tests/test_a.py"
    (root / "Garage").mkdir()
    assert n("Garage/x.py") == "Garage/x.py"                            # le sous-dossier existe vraiment
    with pytest.raises(PermissionError):
        n("../autre/x.py")
    with pytest.raises(PermissionError):
        n("C:\\Windows\\system32\\x")
    with pytest.raises(PermissionError):
        n(str(tmp_path / "ailleurs.py"))


def test_registry_normalizes_before_read_check(tmp_path):
    root = tmp_path / "Garage"
    root.mkdir()
    reg = default_registry()
    assert "créé" in reg.call("write_file", {"path": "Garage/app.py", "content": "a"}, root)
    assert (root / "app.py").exists() and not (root / "Garage").exists()
    # relu sous une autre graphie, puis remplacé : même fichier
    reg.call("read_file", {"path": ".\\app.py"}, root)
    assert "remplacé" in reg.call("write_file", {"path": "/app.py", "content": "b"}, root)


def test_server_command_is_stopped_quickly(project: Path, monkeypatch):
    import time
    from cerveau.tools import shell

    monkeypatch.setattr(shell, "SERVER_TIMEOUT", 2)
    assert shell.is_server_command("uv run uvicorn app.main:app --reload")
    assert shell.is_server_command("npm run dev")
    assert not shell.is_server_command("uv run pytest -q")
    assert not shell.is_server_command("npm run build")
    t0 = time.perf_counter()
    out = shell.run_command(project, "python -c \"import sys; print('started', flush=True); sys.stdin.read() if False else __import__('time').sleep(60)\" --reload")
    assert time.perf_counter() - t0 < 15
    assert "serveur arrêté" in out and "started" in out
