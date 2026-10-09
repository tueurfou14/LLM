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
