"""設定載入與驗證的測試。"""
from pathlib import Path

import pytest

from redmine_mcp.config import ConfigError, RawConfig, Settings, SiteSettings, load_settings

BASE_ENV = {"REDMINE_URL": "https://redmine.example.com", "REDMINE_API_KEY": "secret"}


def _raw(sites: dict[str, dict[str, str]], globals_: dict[str, str] | None = None) -> RawConfig:
    """組出測試用的 RawConfig。"""
    return RawConfig(globals=globals_ or {}, sites=sites)


def test_純環境變數可組出名為_default_的單一站台():
    settings = load_settings(env=BASE_ENV)
    assert isinstance(settings, Settings)
    assert list(settings.sites) == ["default"]
    site = settings.sites["default"]
    assert isinstance(site, SiteSettings)
    assert site.url == "https://redmine.example.com"
    assert site.api_key == "secret"


def test_網址結尾斜線會被去除():
    settings = load_settings(env={**BASE_ENV, "REDMINE_URL": "https://a.example.com/redmine/"})
    assert settings.sites["default"].url == "https://a.example.com/redmine"


def test_執行期不再包含下載目錄設定():
    assert not hasattr(load_settings(env=BASE_ENV).sites["default"], "download_dir")


def test_附件上限預設為十MB():
    assert load_settings(env=BASE_ENV).sites["default"].max_attachment_bytes == 10 * 1024 * 1024


def test_附件上限可由環境變數覆寫():
    settings = load_settings(env={**BASE_ENV, "REDMINE_MAX_ATTACHMENT_MB": "3"})
    assert settings.sites["default"].max_attachment_bytes == 3 * 1024 * 1024


def test_舊下載目錄環境變數不再建立上傳目錄(tmp_path: Path):
    settings = load_settings(env={**BASE_ENV, "REDMINE_DOWNLOAD_DIR": str(tmp_path)})
    site = settings.sites["default"]
    assert not hasattr(site, "upload_dir")


def test_執行期不再包含上傳目錄設定():
    assert not hasattr(load_settings(env=BASE_ENV).sites["default"], "upload_dir")


@pytest.mark.parametrize("bad", ["", "   ", "ftp://a.example.com", "redmine.example.com"])
def test_網址缺漏或格式錯誤時拋出(bad: str):
    with pytest.raises(ConfigError):
        load_settings(env={**BASE_ENV, "REDMINE_URL": bad})


def test_缺少金鑰時拋出():
    with pytest.raises(ConfigError) as exc:
        load_settings(env={"REDMINE_URL": "https://a.example.com"})
    message = str(exc.value)
    assert "REDMINE_API_KEY" in message
    assert "參數檔" in message


def test_缺少網址的訊息同時提到參數檔與環境變數名():
    with pytest.raises(ConfigError) as exc:
        load_settings(env={})
    message = str(exc.value)
    assert "REDMINE_URL" in message
    assert "參數檔" in message


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_附件上限非正整數時拋出(bad: str):
    with pytest.raises(ConfigError):
        load_settings(env={**BASE_ENV, "REDMINE_MAX_ATTACHMENT_MB": bad})


# ── 多站台 ──────────────────────────────────────────────


def test_多站台各自建立設定():
    raw = _raw(
        {
            "main": {"url": "https://a.example.com", "api_key": "ka"},
            "client-b": {"url": "https://b.example.com", "api_key": "kb"},
        }
    )
    settings = load_settings(raw, env={})
    assert list(settings.sites) == ["main", "client-b"]
    assert settings.sites["client-b"].api_key == "kb"


def test_站台層級設定覆寫全域層級(tmp_path: Path):
    site_dir = tmp_path / "b"
    site_dir.mkdir()
    raw = _raw(
        {
            "main": {"url": "https://a.example.com", "api_key": "ka"},
            "client-b": {
                "url": "https://b.example.com",
                "api_key": "kb",
                "download_dir": str(site_dir),
                "max_attachment_mb": "50",
            },
        },
        globals_={"download_dir": str(tmp_path), "max_attachment_mb": "10"},
    )
    settings = load_settings(raw, env={})
    assert not hasattr(settings.sites["main"], "download_dir")
    assert settings.sites["main"].max_attachment_bytes == 10 * 1024 * 1024
    assert not hasattr(settings.sites["client-b"], "download_dir")
    assert settings.sites["client-b"].max_attachment_bytes == 50 * 1024 * 1024


def test_站台缺少必填欄位時訊息指出是哪一站():
    raw = _raw({"client-b": {"url": "https://b.example.com"}})
    with pytest.raises(ConfigError) as exc:
        load_settings(raw, env={})
    assert "client-b" in str(exc.value)


@pytest.mark.parametrize("bad", ["..", "a/b", "a\\b", "有中文", "-lead", "", "a" * 65])
def test_站台名稱不合法時拋出(bad: str):
    raw = _raw({bad: {"url": "https://a.example.com", "api_key": "k"}})
    with pytest.raises(ConfigError) as exc:
        load_settings(raw, env={})
    assert "站台名稱" in str(exc.value)


def test_站台名稱不合法的訊息不含金鑰():
    raw = _raw({"..": {"url": "https://a.example.com", "api_key": "super-secret-value"}})
    with pytest.raises(ConfigError) as exc:
        load_settings(raw, env={})
    assert "super-secret-value" not in str(exc.value)


def test_單一站台時環境變數可覆寫網址與金鑰():
    raw = _raw({"main": {"url": "https://a.example.com", "api_key": "ka"}})
    settings = load_settings(raw, env={"REDMINE_URL": "https://override.example.com"})
    assert settings.sites["main"].url == "https://override.example.com"
    assert settings.sites["main"].api_key == "ka"


def test_站台層級空白環境變數不覆寫參數檔既有的網址():
    raw = _raw({"main": {"url": "https://a.example.com", "api_key": "ka"}})
    settings = load_settings(raw, env={"REDMINE_URL": "   "})
    assert settings.sites["main"].url == "https://a.example.com"


def test_舊參數檔與空白環境變數下載目錄均忽略(tmp_path: Path):
    raw = _raw(
        {"main": {"url": "https://a.example.com", "api_key": "ka"}},
        globals_={"download_dir": str(tmp_path)},
    )
    settings = load_settings(raw, env={"REDMINE_DOWNLOAD_DIR": "   "})
    assert not hasattr(settings.sites["main"], "download_dir")


def test_多站台時環境變數不覆寫站台設定並記警告(caplog: pytest.LogCaptureFixture):
    import logging

    raw = _raw(
        {
            "main": {"url": "https://a.example.com", "api_key": "ka"},
            "client-b": {"url": "https://b.example.com", "api_key": "kb"},
        }
    )
    with caplog.at_level(logging.WARNING):
        settings = load_settings(raw, env={"REDMINE_URL": "https://override.example.com"})
    assert settings.sites["main"].url == "https://a.example.com"
    assert settings.sites["client-b"].url == "https://b.example.com"
    assert "REDMINE_URL" in caplog.text


def test_多站台時環境變數的警告不含金鑰值(caplog: pytest.LogCaptureFixture):
    import logging

    raw = _raw(
        {
            "main": {"url": "https://a.example.com", "api_key": "ka"},
            "client-b": {"url": "https://b.example.com", "api_key": "kb"},
        }
    )
    with caplog.at_level(logging.WARNING):
        load_settings(raw, env={"REDMINE_API_KEY": "super-secret-value"})
    assert "super-secret-value" not in caplog.text


def test_舊全域目錄環境變數在多站台時忽略(tmp_path: Path):
    raw = _raw(
        {
            "main": {"url": "https://a.example.com", "api_key": "ka"},
            "client-b": {"url": "https://b.example.com", "api_key": "kb"},
        }
    )
    settings = load_settings(raw, env={"REDMINE_DOWNLOAD_DIR": str(tmp_path)})
    assert not hasattr(settings.sites["main"], "download_dir")
    assert not hasattr(settings.sites["client-b"], "download_dir")


def test_站台說明會被保留():
    raw = _raw({"main": {"url": "https://a.example.com", "api_key": "ka", "description": "正式站"}})
    assert load_settings(raw, env={}).sites["main"].description == "正式站"


def test_legacy_paths_ignored_and_warning_aggregated_without_access(monkeypatch, caplog):
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy paths must never be expanded or accessed")
    monkeypatch.setattr(Path, "expanduser", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    raw = _raw({"main": {"url": "https://a", "api_key": "k", "upload_dir": "~/site-secret"}},
               {"download_dir": "~/global-secret"})
    settings = load_settings(raw, env={"REDMINE_DOWNLOAD_DIR": "~/env-secret",
                                       "REDMINE_UPLOAD_DIR": ""})
    assert not hasattr(settings.sites["main"], "download_dir")
    assert not hasattr(settings.sites["main"], "upload_dir")
    assert len(caplog.records) == 1
    for name in ("download_dir", "upload_dir", "REDMINE_DOWNLOAD_DIR", "REDMINE_UPLOAD_DIR"):
        assert name in caplog.text
    assert "secret" not in caplog.text
    assert "自動管理" in caplog.text


def test_http_requires_explicit_site_opt_in():
    raw = RawConfig(globals={}, sites={"legacy": {"url": "http://legacy.test", "api_key": "key"}})
    with pytest.raises(ConfigError, match="HTTPS"):
        load_settings(raw, env={})
    raw.sites["legacy"]["allow_insecure_http"] = "true"
    assert load_settings(raw, env={}).sites["legacy"].allow_insecure_http
    raw.sites["other"] = {"url": "http://other.test", "api_key": "key"}
    with pytest.raises(ConfigError, match="other"):
        load_settings(raw, env={})



def test_toml_requires_a_boolean_http_opt_in():
    from redmine_mcp.config_file import structure_config

    site = {"url": "http://legacy.test", "api_key": "key", "allow_insecure_http": True}
    raw = structure_config({"sites": {"legacy": site}}, "test")
    assert load_settings(raw, env={}).sites["legacy"].allow_insecure_http
    with pytest.raises(ConfigError, match="HTTPS"):
        load_settings(raw, env={"REDMINE_URL": "http://other.test"})
    with pytest.raises(ConfigError, match="布林"):
        structure_config({"sites": {"legacy": {**site, "allow_insecure_http": "true"}}}, "test")
