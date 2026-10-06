"""Plugin 是唯一對外安裝入口，設定站台不可建立獨立 MCP 註冊。"""
import json
from pathlib import Path

from typer.testing import CliRunner

from redmine_mcp.cli import app

ROOT = Path(__file__).resolve().parents[2]


def test_plugin_manifests_and_marketplaces():
    manifests = [json.loads((ROOT / p).read_text(encoding="utf-8")) for p in (
        "plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
    )]
    assert {p["name"] for p in manifests} == {"redmine"}
    assert len({p["version"] for p in manifests}) == 1
    prompt = manifests[2]["interface"]["defaultPrompt"]
    for keyword in (
        "Redmine MCP",
        "redmine-issue-writing",
        "issue-code-consistency-check",
        "release-change-items",
        "取得確認",
        "待確認",
    ):
        assert keyword in prompt
    for p in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
        marketplace = json.loads((ROOT / p).read_text(encoding="utf-8"))
        assert marketplace["name"] == "redmine-plugins"
        assert marketplace["plugins"][0]["name"] == "redmine"
    for manifest in manifests[1:]:
        config = json.loads((ROOT / manifest["mcpServers"]).read_text(encoding="utf-8"))
        server = config["mcpServers"]["redmine"]
        assert server["command"] == "uvx"
        assert "--from" in server["args"]
        assert server["args"][-1] == "redmine-mcp"
        assert not any("github.com" in arg for arg in server["args"])


def test_skill_descriptions_route_distinct_workflows():
    expected = {
        "redmine-issue-writing": ("bug", "change", "feature"),
        "issue-code-consistency-check": ("查核", "git", "行號"),
        "release-change-items": ("release", "過版", "TSV"),
    }
    for name, keywords in expected.items():
        content = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        description = content.split("---", 2)[1]
        for keyword in keywords:
            assert keyword in description


def test_setup_only_writes_config(monkeypatch):
    called = {}

    def fake_setup(prompts, **kwargs):
        called.update(kwargs)
        return 0

    monkeypatch.setattr("redmine_mcp.setup.wizard.run_setup", fake_setup)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 0
    assert called["finish"] is False


def test_legacy_install_is_not_a_cli_command():
    result = CliRunner().invoke(app, ["install"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_legacy_entrypoints_are_removed():
    for p in ("install.cmd", "install.command", "install/install.ps1", "install/install.sh",
              "redmine-issue-skills/install/install.ps1", "redmine-issue-skills/install/install.sh",
              "redmine-mcp/scripts/build_setup_zip.py"):
        assert not (ROOT / p).exists(), p
