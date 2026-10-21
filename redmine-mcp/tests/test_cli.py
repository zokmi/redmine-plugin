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




def test_retired_options_hidden_and_rejected_without_writes(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    original = 'download_dir = "~/private"\n[sites.main]\nurl = "https://a"\napi_key = "k"\n'
    target.write_text(original, encoding="utf-8")
    monkeypatch.setenv("REDMINE_CONFIG", str(target))
    help_result = runner.invoke(app, ["config", "set-global", "--help"])
    assert help_result.exit_code == 0
    assert "download-dir" not in help_result.output
    assert "upload-dir" not in help_result.output
    for args in (["--download-dir", "~/new-secret"], ["--upload-dir", "~/new-secret"],
                 ["--clear", "download_dir"], ["--clear", "upload_dir"]):
        result = runner.invoke(app, ["config", "set-global", *args, "--yes"])
        assert result.exit_code != 0
        assert "自動管理" in result.output
        assert "new-secret" not in result.output
        assert target.read_text(encoding="utf-8") == original


def test_retired_clear_rejected_before_loading_config(tmp_path, monkeypatch):
    target = tmp_path / "missing.toml"
    monkeypatch.setenv("REDMINE_CONFIG", str(target))
    result = runner.invoke(app, ["config", "set-global", "--clear", "upload_dir", "--yes"])
    assert result.exit_code != 0
    assert "自動管理" in result.output
    assert not target.exists()
