from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_root_readme_only_describes_plugin_installation():
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "plugin marketplace add" in content
    assert "codex plugin marketplace add" in content
    assert "install.cmd" not in content
    assert "redmine-mcp install" not in content


def test_setup_documents_parameter_configuration_without_registration():
    content = (ROOT / "redmine-mcp" / "SETUP.md").read_text(encoding="utf-8")
    assert "只說明站台參數" in content
    assert "claude mcp add" in content
    assert "codex mcp add" in content
    assert "plugin 管理器" in content
