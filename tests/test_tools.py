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
