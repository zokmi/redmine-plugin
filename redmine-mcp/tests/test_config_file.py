"""參數檔讀取與結構化的測試。"""
import logging
import sys
from pathlib import Path

import pytest

from redmine_mcp.config import ConfigError
from redmine_mcp.config_file import (
    default_config_path,
    load_config_file,
    resolve_config_path,
    user_config_dir,
)


def _write(path: Path, content: str) -> Path:
    """把 TOML 內容寫進指定檔案並回傳該路徑。"""
    path.write_text(content, encoding="utf-8")
    return path


def test_讀取參數檔並解析為預設站台(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        'url = "https://redmine.example.com/redmine"\napi_key = "secret-key"\n',
    )
    raw = load_config_file(path)
    assert raw.sites == {
        "default": {
            "url": "https://redmine.example.com/redmine",
            "api_key": "secret-key",
        }
    }
    assert raw.globals == {}


def test_檔案不存在時回空的_RawConfig(tmp_path: Path):
    raw = load_config_file(tmp_path / "不存在.toml")
    assert raw.globals == {}
    assert raw.sites == {}


def test_整數值轉為字串(tmp_path: Path):
    path = _write(tmp_path / "config.toml", "max_attachment_mb = 25\n")
    assert load_config_file(path).globals == {"max_attachment_mb": "25"}


def test_語法錯誤時拋出_ConfigError_並帶位置(tmp_path: Path):
    path = _write(tmp_path / "config.toml", 'url "https://redmine.example.com"\n')
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "line 1" in str(exc.value)


def test_語法錯誤訊息不含_API_key(tmp_path: Path):
    # 金鑰所在的那一行語法錯誤時，訊息絕不可回吐該行內容
    path = _write(tmp_path / "config.toml", 'api_key = "super-secret-value\n')
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "super-secret-value" not in str(exc.value)


def test_未知鍵發出警告但不中斷(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = _write(tmp_path / "config.toml", 'api_kye = "typo"\nurl = "https://a.example.com"\n')
    with caplog.at_level(logging.WARNING):
        result = load_config_file(path)
    assert result.sites == {"default": {"url": "https://a.example.com"}}
    assert "api_kye" in caplog.text


def test_未知鍵的警告不含該鍵的值(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # 拼錯的鍵有可能是 api_kye，其值就是真金鑰，警告只能提鍵名
    path = _write(tmp_path / "config.toml", 'api_kye = "super-secret-value"\n')
    with caplog.at_level(logging.WARNING):
        load_config_file(path)
    assert "super-secret-value" not in caplog.text


def test_巢狀表格值拋錯(tmp_path: Path):
    path = _write(tmp_path / "config.toml", '[url]\nhost = "a.example.com"\n')
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "url" in str(exc.value)


def test_陣列值拋錯(tmp_path: Path):
    path = _write(tmp_path / "config.toml", 'url = ["a", "b"]\n')
    with pytest.raises(ConfigError):
        load_config_file(path)


def test_布林值拋錯(tmp_path: Path):
    path = _write(tmp_path / "config.toml", "url = true\n")
    with pytest.raises(ConfigError):
        load_config_file(path)


def test_原始碼情境下預設路徑指向專案根目錄的_config_toml():
    # 開發環境中專案根目錄本來就有 config.toml，此時應優先沿用，維持既有流程不變。
    import redmine_mcp.config_file as config_file_module

    root = Path(config_file_module.__file__).resolve().parents[2]
    if not (root / "config.toml").is_file():
        pytest.skip("專案根目錄沒有 config.toml，此情境不適用")
    assert default_config_path() == root / "config.toml"


def test_安裝情境下預設路徑落在使用者設定目錄(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # 套件被安裝後模組位於 site-packages，上溯三層不會有 config.toml；
    # 此時必須落到使用者設定目錄，而不是去讀直譯器的 lib 目錄。
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = default_config_path()
    assert result.name == "config.toml"
    assert result.parent.name == "redmine-mcp"
    assert tmp_path in result.parents


def test_使用者設定目錄依平台選擇基底(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        assert user_config_dir() == tmp_path / "Roaming" / "redmine-mcp"
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert user_config_dir() == tmp_path / "xdg" / "redmine-mcp"


def test_設定目錄環境變數缺漏時回退到家目錄(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # APPDATA／XDG_CONFIG_HOME 在精簡的服務環境中可能不存在，此時仍要算得出路徑。
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    result = user_config_dir()
    assert result.name == "redmine-mcp"
    assert tmp_path in result.parents


def test_未設_REDMINE_CONFIG_時用預設路徑():
    assert resolve_config_path({}) == default_config_path()


def test_REDMINE_CONFIG_可改變讀取路徑(tmp_path: Path):
    target = tmp_path / "自訂.toml"
    assert resolve_config_path({"REDMINE_CONFIG": str(target)}) == target


def test_多站台區塊解析為站台字典(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        'download_dir = "D:/dl"\n'
        "\n"
        "[sites.main]\n"
        'url = "https://a.example.com/redmine"\n'
        'api_key = "key-a"\n'
        "\n"
        "[sites.client-b]\n"
        'url = "https://b.example.com"\n'
        'api_key = "key-b"\n'
        'download_dir = "D:/dl-b"\n',
    )
    raw = load_config_file(path)
    assert raw.globals == {"download_dir": "D:/dl"}
    assert list(raw.sites) == ["main", "client-b"]
    assert raw.sites["client-b"]["download_dir"] == "D:/dl-b"


def test_舊格式扁平設定視為名為_default_的站台(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        'url = "https://a.example.com/redmine"\napi_key = "key-a"\ndownload_dir = "D:/dl"\n',
    )
    raw = load_config_file(path)
    assert list(raw.sites) == ["default"]
    assert raw.sites["default"]["url"] == "https://a.example.com/redmine"
    assert raw.globals == {"download_dir": "D:/dl"}


def test_新舊格式並存時拋出_ConfigError(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        'url = "https://a.example.com"\napi_key = "k"\n\n[sites.main]\nurl = "https://b.example.com"\napi_key = "k2"\n',
    )
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "sites" in str(exc.value)


def test_檔案存在但讀取失敗時拋出_ConfigError_而非原始例外(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # 檔案存在但無法開啟（例如 ACL 設錯、或以不同帳號執行 MCP 子行程）時，
    # is_file() 在 Windows 上通常仍回 True，真正的失敗只會在 open() 時發生。
    # 用 monkeypatch 模擬 PermissionError，比真的去改 ACL 更可靠，也不依賴檔案系統權限。
    path = _write(tmp_path / "config.toml", 'url = "https://a.example.com"\n')

    def _raise_permission_error(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", _raise_permission_error)

    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    message = str(exc.value)
    assert "無法讀取" in message
    assert str(path) in message
    assert not isinstance(exc.value, PermissionError)


def test_檔案讀取失敗的訊息不含檔案內容(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 讀不到的檔案有可能正好放著金鑰，訊息絕不能把內容一併帶出。
    path = _write(tmp_path / "config.toml", 'api_key = "super-secret-value"\n')

    def _raise_permission_error(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", _raise_permission_error)

    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "super-secret-value" not in str(exc.value)


def test_structure_config_可直接吃已解析的對映():
    from redmine_mcp.config_file import structure_config

    config = structure_config(
        {"download_dir": "D:/dl", "sites": {"main": {"url": "https://a/b", "api_key": "k"}}},
        "編輯後的參數檔",
    )

    assert config.globals == {"download_dir": "D:/dl"}
    assert config.sites == {"main": {"url": "https://a/b", "api_key": "k"}}


def test_structure_config_的錯誤訊息使用傳入的位置描述():
    # 訊息前綴可替換，是為了讓「編輯後的字串」與「某個路徑的檔案」共用同一套驗證。
    from redmine_mcp.config_file import structure_config

    with pytest.raises(ConfigError) as exc:
        structure_config({"url": "https://a/b", "api_key": "k", "sites": {}}, "編輯後的參數檔")

    assert "編輯後的參數檔" in str(exc.value)


def test_參數檔非UTF8時給出可行動的設定錯誤(tmp_path):
    from redmine_mcp.config_file import load_config_file

    path = tmp_path / "config.toml"
    # Windows 記事本以 cp950 存中文說明是常見情況。
    path.write_bytes('[sites.main]\ndescription = "正式站"\n'.encode("cp950"))

    with pytest.raises(ConfigError) as exc:
        load_config_file(path)

    message = str(exc.value)
    assert "UTF-8" in message
    # 訊息絕不可帶出檔案內容（該檔可能放著 api_key）。
    assert "正式站" not in message
