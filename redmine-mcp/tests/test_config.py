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


def test_未設定下載目錄時為_None():
    assert load_settings(env=BASE_ENV).sites["default"].download_dir is None


def test_附件上限預設為十MB():
    assert load_settings(env=BASE_ENV).sites["default"].max_attachment_bytes == 10 * 1024 * 1024


def test_附件上限可由環境變數覆寫():
    settings = load_settings(env={**BASE_ENV, "REDMINE_MAX_ATTACHMENT_MB": "3"})
    assert settings.sites["default"].max_attachment_bytes == 3 * 1024 * 1024


def test_上傳目錄未設定時沿用下載目錄(tmp_path: Path):
    settings = load_settings(env={**BASE_ENV, "REDMINE_DOWNLOAD_DIR": str(tmp_path)})
    site = settings.sites["default"]
    assert site.upload_dir == tmp_path.resolve()


def test_兩個目錄都未設定時上傳目錄為_None():
    assert load_settings(env=BASE_ENV).sites["default"].upload_dir is None


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
    assert settings.sites["main"].download_dir == tmp_path.resolve()
    assert settings.sites["main"].max_attachment_bytes == 10 * 1024 * 1024
    assert settings.sites["client-b"].download_dir == site_dir.resolve()
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


def test_全域層級空白環境變數不覆寫參數檔既有的下載目錄(tmp_path: Path):
    raw = _raw(
        {"main": {"url": "https://a.example.com", "api_key": "ka"}},
        globals_={"download_dir": str(tmp_path)},
    )
    settings = load_settings(raw, env={"REDMINE_DOWNLOAD_DIR": "   "})
    assert settings.sites["main"].download_dir == tmp_path.resolve()


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


def test_全域目錄環境變數在多站台時仍然生效(tmp_path: Path):
    raw = _raw(
        {
            "main": {"url": "https://a.example.com", "api_key": "ka"},
            "client-b": {"url": "https://b.example.com", "api_key": "kb"},
        }
    )
    settings = load_settings(raw, env={"REDMINE_DOWNLOAD_DIR": str(tmp_path)})
    assert settings.sites["main"].download_dir == tmp_path.resolve()
    assert settings.sites["client-b"].download_dir == tmp_path.resolve()


def test_站台說明會被保留():
    raw = _raw({"main": {"url": "https://a.example.com", "api_key": "ka", "description": "正式站"}})
    assert load_settings(raw, env={}).sites["main"].description == "正式站"
