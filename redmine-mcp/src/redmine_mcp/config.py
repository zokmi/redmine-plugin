"""參數檔與環境變數設定的載入與驗證。"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_MAX_ATTACHMENT_MB = 10

#: 舊格式（扁平）參數檔轉換後的站台名稱。
LEGACY_SITE_NAME = "default"

#: 站台名稱規則。這是安全邊界而非格式偏好：名稱會直接作為下載子目錄名，
#: 一個名為 ".." 的站台即構成路徑穿越。性質等同 projects.py 的 validate_identifier()。
_SITE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """設定缺漏或格式錯誤時拋出。訊息不得包含金鑰內容。"""


@dataclass(frozen=True)
class RawConfig:
    """參數檔的結構化內容，尚未經過語意驗證。

    定義在此而非 config_file.py：config_file 需要 ConfigError，若兩邊互相匯入
    會形成循環匯入。依賴方向固定為 config_file → config。

    屬性:
        globals: 全域層級的鍵值，值皆已轉為字串。
        sites: 站台名稱 → 該站台鍵值的對照表，維持參數檔中的宣告順序。
    """

    globals: dict[str, str]
    sites: dict[str, dict[str, str]]


@dataclass(frozen=True)
class SiteSettings:
    """單一 Redmine 站台的執行期設定。

    屬性:
        name: 站台代號，同時作為下載子目錄名，已通過名稱規則驗證。
        url: 站台根位址，已去除結尾斜線。
        api_key: 該站台的 API key，僅供組裝認證 header 使用。
        description: 站台說明；供模型把口語對應到站台代號，未設定時為 None。
        download_dir: 附件下載目錄；為 None 時該站台停用下載。
        upload_dir: 允許上傳的來源目錄；為 None 時該站台停用上傳。
        max_attachment_bytes: 單一附件下載／上傳大小上限（位元組）。
    """

    name: str
    url: str
    api_key: str
    description: str | None
    download_dir: Path | None
    upload_dir: Path | None
    max_attachment_bytes: int


@dataclass(frozen=True)
class Settings:
    """整體執行期設定。

    屬性:
        sites: 站台名稱 → 設定的對照表，維持參數檔中的宣告順序，保證非空。
    """

    sites: Mapping[str, SiteSettings]


def _optional_dir(value: str | None) -> Path | None:
    """把目錄設定轉成絕對路徑，空值回傳 None。"""
    cleaned = (value or "").strip()
    return Path(cleaned).expanduser().resolve() if cleaned else None


def _max_bytes(value: str | None, where: str) -> int:
    """把 MB 設定轉成位元組，未設定時回傳預設值。

    參數:
        value: 原始設定值，可能為 None 或空字串。
        where: 出錯時要指出的位置描述。
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return DEFAULT_MAX_ATTACHMENT_MB * 1024 * 1024
    try:
        megabytes = int(cleaned)
    except ValueError as exc:
        raise ConfigError(f"{where}的 max_attachment_mb 必須是整數（單位 MB）") from exc
    if megabytes <= 0:
        raise ConfigError(f"{where}的 max_attachment_mb 必須大於 0")
    return megabytes * 1024 * 1024


def _validate_site_name(name: str) -> str:
    """驗證站台名稱，不合法即拋出。訊息只提名稱不提該站任何其他設定。"""
    if not _SITE_NAME_PATTERN.match(name):
        raise ConfigError(
            f"站台名稱 {name!r} 不合法：只能用英文、數字、減號與底線，"
            "需以英數開頭且長度 1–64。站台名稱會作為下載子目錄名，因此規則從嚴"
        )
    return name


def _build_site(name: str, values: Mapping[str, str], defaults: Mapping[str, str]) -> SiteSettings:
    """組出單一站台的設定，未指定的鍵沿用全域預設值。

    參數:
        name: 站台名稱，呼叫端須先驗證過。
        values: 該站台的鍵值。
        defaults: 全域層級的鍵值。
    """
    where = "參數檔" if name == LEGACY_SITE_NAME else f"站台 {name}"

    raw_url = (values.get("url") or "").strip()
    if not raw_url:
        hint = (
            "（環境變數 REDMINE_URL）"
            if name == LEGACY_SITE_NAME
            else f"，請檢查 [sites.{name}] 區塊"
        )
        raise ConfigError(
            f"缺少必填設定 url{hint}；"
            "請在參數檔中填入 Redmine 站台根位址（可參考 config.example.toml）"
        )

    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"{where}的 url 必須是 http:// 或 https:// 開頭的完整網址")

    api_key = (values.get("api_key") or "").strip()
    if not api_key:
        hint = (
            "（環境變數 REDMINE_API_KEY）"
            if name == LEGACY_SITE_NAME
            else f"，請檢查 [sites.{name}] 區塊"
        )
        raise ConfigError(
            f"缺少必填設定 api_key{hint}；"
            "請在參數檔中填入於 Redmine 個人設定頁取得的金鑰（可參考 config.example.toml）"
        )

    def pick(key: str) -> str | None:
        """站台層級優先，未指定則取全域層級。"""
        value = values.get(key)
        return value if (value or "").strip() else defaults.get(key)

    download_dir = _optional_dir(pick("download_dir"))
    # 上傳來源目錄未設定時沿用下載目錄，讓「下載→修改→上傳」的常見流程免除額外設定；
    # 兩者都沒設定時上傳工具停用，避免模型能把本機任意路徑（例如金鑰檔）送到 Redmine。
    upload_dir = _optional_dir(pick("upload_dir")) or download_dir

    description = (values.get("description") or "").strip() or None

    return SiteSettings(
        name=name,
        url=raw_url.rstrip("/"),
        api_key=api_key,
        description=description,
        download_dir=download_dir,
        upload_dir=upload_dir,
        max_attachment_bytes=_max_bytes(pick("max_attachment_mb"), where),
    )


def load_settings(raw: RawConfig | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """合併參數檔與環境變數並驗證，產出 Settings。

    參數:
        raw: 參數檔的結構化內容；為 None 時視為沒有參數檔，純以環境變數組成。
        env: 環境變數來源；預設讀取 os.environ，測試可注入假字典。
    回傳:
        已驗證的 Settings，sites 保證非空。
    例外:
        ConfigError: 缺漏必填設定、格式錯誤，或站台名稱不合法。
    """
    source = os.environ if env is None else env
    config = raw if raw is not None else RawConfig(globals={}, sites={})

    # 全域層級：環境變數覆寫參數檔，空白值視為未提供。
    defaults = dict(config.globals)
    for key, env_key in (
        ("download_dir", "REDMINE_DOWNLOAD_DIR"),
        ("upload_dir", "REDMINE_UPLOAD_DIR"),
        ("max_attachment_mb", "REDMINE_MAX_ATTACHMENT_MB"),
    ):
        value = (source.get(env_key) or "").strip()
        if value:
            defaults[key] = value

    sites_raw = {name: dict(values) for name, values in config.sites.items()}
    env_url = (source.get("REDMINE_URL") or "").strip()
    env_key_value = (source.get("REDMINE_API_KEY") or "").strip()

    if not sites_raw:
        # 沒有參數檔或參數檔沒有站台區塊時，純以環境變數組出名為 default 的單一站台。
        sites_raw = {LEGACY_SITE_NAME: {}}

    if len(sites_raw) == 1:
        only = next(iter(sites_raw.values()))
        if env_url:
            only["url"] = env_url
        if env_key_value:
            only["api_key"] = env_key_value
    elif env_url or env_key_value:
        # 站台多於一個時這兩個變數無法明確指涉任何一站，靜默覆寫某個任選站台會造成
        # 「參數檔寫 A、實際連 B」這種最難排查的狀況，因此忽略。警告只提變數名不提值。
        named = "、".join(
            name for name, value in (("REDMINE_URL", env_url), ("REDMINE_API_KEY", env_key_value))
            if value
        )
        logger.warning(
            "已設定多個站台，環境變數 %s 無法指涉特定站台，已忽略；"
            "請直接在參數檔的 [sites.<名稱>] 區塊中修改",
            named,
        )

    sites = {
        _validate_site_name(name): _build_site(name, values, defaults)
        for name, values in sites_raw.items()
    }
    return Settings(sites=sites)
