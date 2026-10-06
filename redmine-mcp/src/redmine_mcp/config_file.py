"""參數檔（config.toml）的讀取與結構化。

只負責「讀檔並整理成 RawConfig」，不做語意驗證——URL 格式、路徑、
大小上限、站台名稱等一律交給 config.py 的 load_settings()，維持驗證邏輯只有一處。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from redmine_mcp.config import LEGACY_SITE_NAME, ConfigError, RawConfig

#: 參數檔在使用者設定目錄下的子目錄名稱。
_APP_DIR_NAME = "redmine-mcp"
_CONFIG_FILENAME = "config.toml"


def user_config_dir() -> Path:
    """回傳本工具在使用者設定目錄下的專屬資料夾。

    Windows 用 %APPDATA%，其餘平台依 XDG 慣例用 $XDG_CONFIG_HOME（未設定則 ~/.config）。
    環境變數缺漏時一律回退到家目錄底下的對應位置，確保任何情境都算得出路徑。
    """
    if sys.platform == "win32":
        raw = os.environ.get("APPDATA")
        base = Path(raw) if raw else Path.home() / "AppData" / "Roaming"
    else:
        raw = os.environ.get("XDG_CONFIG_HOME")
        base = Path(raw) if raw else Path.home() / ".config"
    return base / _APP_DIR_NAME


def default_config_path() -> Path:
    """決定預設的參數檔位置。

    以原始碼執行時（src layout，config_file.py 上溯三層即專案根目錄）沿用專案根目錄的
    config.toml，維持既有開發流程不變；找不到才改用使用者設定目錄。

    順序不可對調：套件一旦被 pip/uv 安裝，模組會位於 site-packages 底下，上溯三層是
    直譯器的 lib 目錄——那裡既不是使用者會想到要放設定檔的地方，也可能沒有寫入權限，
    因此安裝情境必須落到使用者設定目錄。改用函式而非模組層級常數，是為了讓
    HOME／APPDATA 在測試中可被 monkeypatch。
    """
    repo_config = Path(__file__).resolve().parents[2] / _CONFIG_FILENAME
    if repo_config.is_file():
        return repo_config
    return user_config_dir() / _CONFIG_FILENAME

# tomllib 錯誤訊息中的位置片段，例如「(at line 3, column 5)」。
_POSITION_PATTERN = re.compile(r"\(at line \d+, column \d+\)|\(at end of document\)")

logger = logging.getLogger(__name__)


def _position_only(exc: tomllib.TOMLDecodeError) -> str:
    """僅取出 TOML 解析錯誤中的位置資訊。

    tomllib 的訊息可能夾帶出錯處的內容，若出錯的那一行正是 api_key 就會外洩金鑰，
    因此只保留位置片段；無法辨識時回傳通用說明。

    參數:
        exc: tomllib 拋出的解析錯誤。
    回傳:
        僅含位置資訊的字串。
    """
    match = _POSITION_PATTERN.search(str(exc))
    return match.group(0) if match else "無法解析 TOML 內容"


def resolve_config_path(env: Mapping[str, str] | None = None) -> Path:
    """決定要讀取的參數檔路徑。

    參數:
        env: 設定來源；預設讀取 os.environ，測試可注入假字典。
    回傳:
        REDMINE_CONFIG 指定的路徑，未設定時為 default_config_path() 的結果。
    """
    source = os.environ if env is None else env
    raw = (source.get("REDMINE_CONFIG") or "").strip()
    return Path(raw).expanduser() if raw else default_config_path()


#: 全域層級允許的鍵。
GLOBAL_KEYS = frozenset({"download_dir", "upload_dir", "max_attachment_mb"})

#: 站台層級允許的鍵。
SITE_KEYS = frozenset(
    {"url", "api_key", "description", "download_dir", "upload_dir", "max_attachment_mb"}
)

#: 舊格式（扁平）參數檔中代表站台設定的鍵；出現任何一個即視為舊格式。
LEGACY_SITE_KEYS = frozenset({"url", "api_key"})


def _scalar(key: str, value: object, where: str) -> str:
    """把單一設定值轉成字串，遇到不支援的型別即拋出。

    參數:
        key: 鍵名，用於組錯誤訊息。
        value: 參數檔中的原始值。
        where: 出現位置的描述，例如「站台 main」，讓訊息能指出是哪一段出錯。
    回傳:
        轉為字串的值。
    例外:
        ConfigError: 值為表格、陣列或布林值。
    """
    if isinstance(value, dict | list):
        raise ConfigError(f"{where}的 {key} 只接受單一值，不支援表格或陣列")
    if isinstance(value, bool):
        raise ConfigError(f"{where}的 {key} 不接受布林值")
    return str(value)


def _collect(raw: dict, allowed: frozenset[str], where: str) -> dict[str, str]:
    """挑出允許的鍵並轉為字串，未知鍵記警告後忽略。

    警告只提鍵名不提值：拼錯的鍵有可能是 api_kye，其值就是真金鑰。
    """
    result: dict[str, str] = {}
    for key, value in raw.items():
        if key not in allowed:
            logger.warning("%s中有無法識別的設定鍵 %r，已忽略（請確認是否拼錯）", where, key)
            continue
        result[key] = _scalar(key, value, where)
    return result


def load_config_file(path: Path) -> RawConfig:
    """讀取參數檔並整理成結構化設定。

    只做結構整理與鍵名檢查，不做語意驗證——URL 格式、路徑、大小上限、站台名稱
    一律交給 config.py 的 load_settings()，維持驗證邏輯只有一處。

    參數:
        path: 參數檔路徑；檔案不存在時回傳空的 RawConfig，不視為錯誤。
    回傳:
        結構化後的 RawConfig。
    例外:
        ConfigError: TOML 語法錯誤、某鍵的值不是單一純量、新舊格式並存，
            或檔案存在但無法開啟讀取（例如權限不足）。
    """
    if not path.is_file():
        return RawConfig(globals={}, sites={})

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except OSError as exc:
        # 檔案存在但開不了（權限不足、被其他行程鎖住等）；is_file() 在 Windows 上
        # 通常仍回 True，因此這種失敗只會在真正 open() 時才會浮現。訊息只給路徑與
        # 作業系統原因，絕不能帶出檔案內容（該檔可能正好放著 api_key）。
        raise ConfigError(f"無法讀取參數檔（{path}）：{exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        # tomllib 只接受 UTF-8。以 cp950／big5 存檔（Windows 記事本的常見結果）會在這裡
        # 失敗，而 __main__ 只攔 ConfigError，不轉換的話啟動時會是一坨 traceback。
        # 訊息只講編碼與路徑，不帶檔案內容——該檔可能正好放著 api_key。
        raise ConfigError(
            f"參數檔必須以 UTF-8 儲存（{path}）：目前的內容不是合法的 UTF-8，"
            "請用編輯器另存為 UTF-8 後重試"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"參數檔格式錯誤（{path}）：{_position_only(exc)}") from exc

    return structure_config(raw, f"參數檔（{path}）")


def structure_config(raw: Mapping[str, object], where: str) -> RawConfig:
    """把已解析的 TOML 對映整理成 RawConfig。

    與 load_config_file() 拆開，是為了讓「還沒落地的字串」也能走同一套結構檢查——
    `redmine-mcp config` 的各項編輯會在寫檔前先驗證編輯結果，若這段邏輯只綁在
    「讀某個路徑的檔案」上，那道閘就只能複製一份實作，兩份遲早分歧。

    參數:
        raw: tomllib 解析後的對映。
        where: 錯誤訊息中要指出的位置，例如「參數檔（C:/…/config.toml）」或
            「編輯後的參數檔」。
    回傳:
        結構化後的 RawConfig。
    例外:
        ConfigError: 新舊格式並存、sites 不是表格，或某鍵的值不是單一純量。
    """
    sites_table = raw.get("sites")
    has_legacy = bool(LEGACY_SITE_KEYS & raw.keys())

    if sites_table is not None and has_legacy:
        # 兩者並存代表使用者改到一半，靜默採用其中一組會造成
        # 「參數檔寫 A、實際連 B」這種最難排查的狀況。
        raise ConfigError(
            f"{where}同時有最外層的 url／api_key 與 [sites.*] 區塊，"
            "請擇一：改用 [sites.<名稱>] 後移除最外層的 url 與 api_key"
        )

    top_level = {key: value for key, value in raw.items() if key != "sites"}

    if sites_table is None:
        if not has_legacy:
            return RawConfig(globals=_collect(top_level, GLOBAL_KEYS, "參數檔"), sites={})
        site = _collect(top_level, SITE_KEYS, "參數檔")
        globals_ = {key: value for key, value in site.items() if key in GLOBAL_KEYS}
        return RawConfig(globals=globals_, sites={LEGACY_SITE_NAME: site})

    if not isinstance(sites_table, dict):
        raise ConfigError(f"{where}的 sites 必須是 [sites.<名稱>] 形式的表格")

    sites: dict[str, dict[str, str]] = {}
    for name, body in sites_table.items():
        if not isinstance(body, dict):
            raise ConfigError(f"{where}的 sites.{name} 必須是表格")
        sites[name] = _collect(body, SITE_KEYS, f"站台 {name}")

    return RawConfig(globals=_collect(top_level, GLOBAL_KEYS, "參數檔"), sites=sites)
