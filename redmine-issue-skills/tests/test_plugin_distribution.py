"""Legacy installers were retired in favor of plugin-managed distribution."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "redmine-issue-skills"


@pytest.mark.parametrize("filename", ["install.ps1", "install.sh"])
def test_retired_install_scripts_are_not_distributed(filename):
    assert not (LEGACY / "install" / filename).exists()


@pytest.mark.parametrize("platform", [".claude-plugin", ".codex-plugin"])
def test_plugin_loads_all_maintained_skills(platform):
    manifest = json.loads((ROOT / platform / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "redmine"
    skill_root = (ROOT / manifest["skills"]).resolve()
    assert skill_root == (ROOT / "skills").resolve()
    for skill in (
        "redmine-issue-writing", "issue-code-consistency-check",
        "release-change-items", "redmine-setup",
    ):
        entry = skill_root / skill / "SKILL.md"
        assert entry.is_file(), entry
        assert entry.read_text(encoding="utf-8").startswith("---\n")
    config = json.loads((ROOT / manifest["mcpServers"]).read_text(encoding="utf-8"))
    assert config["mcpServers"]["redmine"]["url"] == "http://127.0.0.1:18765/mcp"
    assert "command" not in config["mcpServers"]["redmine"]


def test_compatibility_readme_routes_installation_to_plugin():
    readme = (LEGACY / "README.md").read_text(encoding="utf-8")
    assert "唯一維護來源" in readme
    assert "skills/redmine-issue-writing/" in readme
    assert "plugin 管理器更新與移除" in readme
    assert "../README.md" in readme
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "plugin 安裝" in root_readme
    assert "redmine@redmine-plugins" in root_readme
