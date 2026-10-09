from cerveau import skills
from cerveau.llm import extract_tool_calls


def test_builtin_skills_load():
    items = skills.load_all()
    names = {s.name for s in items}
    assert {"nouveau-projet", "api-python", "audit-securite", "tests-et-corrections"} <= names
    for s in items:
        assert s.keywords and s.body and s.description


def test_select_matches_accent_insensitive():
    items = skills.load_all()
    chosen = skills.select(items, "Fais un audit de securite de ce projet")
    assert chosen and chosen[0].name == "audit-securite"


def test_select_new_project_and_api_for_erp():
    items = skills.load_all()
    chosen = skills.select(items, "Crée un dossier ERP et commence une API de gestion des clients")
    assert {s.name for s in chosen} == {"nouveau-projet", "api-python"}


def test_select_nothing_for_small_talk():
    assert skills.select(skills.load_all(), "bonjour, ça va ?") == []


def test_user_skill_overrides_builtin(tmp_path):
    (tmp_path / "audit-securite.md").write_text("# Mon audit\nmots-clés: audit\ndescription: perso\n\ncorps", encoding="utf-8")
    items = skills.load_all([tmp_path])
    mine = next(s for s in items if s.name == "audit-securite")
    assert mine.title == "Mon audit"


def test_extract_tool_calls_from_qwen_tags():
    text = 'Je crée le dossier.\n<tool_call>\n{"name": "create_directory", "arguments": {"path": "src"}}\n</tool_call>'
    cleaned, calls = extract_tool_calls(text)
    assert cleaned == "Je crée le dossier."
    assert calls[0].name == "create_directory" and calls[0].arguments == {"path": "src"}


def test_extract_tool_calls_from_json_fence():
    text = '```json\n{"name": "list_files", "arguments": "{\\"pattern\\": \\"*.py\\"}"}\n```'
    _, calls = extract_tool_calls(text)
    assert calls[0].name == "list_files" and calls[0].arguments == {"pattern": "*.py"}


def test_plain_text_has_no_tool_calls():
    cleaned, calls = extract_tool_calls("Voici du code :\n```json\n{\"a\": 1}\n```")
    assert calls == [] and "code" in cleaned
