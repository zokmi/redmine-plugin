from typer.testing import CliRunner

from redmine_mcp.cli import app

runner = CliRunner()


def test_help_列出設定與_config_子命令():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "setup" in result.stdout
    assert "config" in result.stdout


def test_setup_只建立參數檔不註冊_mcp(monkeypatch):
    called = {}

    def fake_setup(prompts, **kwargs):
        called.update(kwargs)
        return 0

    monkeypatch.setattr("redmine_mcp.setup.wizard.run_setup", fake_setup)
    assert runner.invoke(app, ["setup"]).exit_code == 0
    assert called["finish"] is False


def test_舊_install_子命令不存在():
    result = runner.invoke(app, ["install"])
    assert result.exit_code == 2
    assert "No such command" in result.output


