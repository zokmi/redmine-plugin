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

#: 站台名稱規則；維持既有識別碼限制，附件儲存另依網址隔離。
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
        name: 站台代號，已通過名稱規則驗證。
        url: 站台根位址，已去除結尾斜線。
        api_key: 該站台的 API key，僅供組裝認證 header 使用。
        description: 站台說明；供模型把口語對應到站台代號，未設定時為 None。
        max_attachment_bytes: 單一附件下載／上傳大小上限（位元組）。
        allow_insecure_http: 是否明確允許這個站台使用明文 HTTP。
        attachment_upload_root: 此站允許上傳外部檔案的專用目錄。
    """

    name: str
    url: str
    api_key: str
    description: str | None
    max_attachment_bytes: int
    allow_insecure_http: bool = False
    attachment_upload_root: Path | None = None


@dataclass(frozen=True)
class Settings:
    """整體執行期設定。

    屬性:
        sites: 站台名稱 → 設定的對照表，維持參數檔中的宣告順序，保證非空。
    """

    sites: Mapping[str, SiteSettings]


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
            "需以英數開頭且長度 1–64。"
        )
    return name


def validate_site_url(url: str, *, allow_insecure_http: bool = False, where: str = "站台") -> None:
    try:
        parsed = urlparse(url)
        if not parsed.hostname or parsed.scheme not in {"http", "https"}:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
    except ValueError:
        raise ConfigError(f"{where}的 url 必須是完整網址，且不能包含帳密") from None
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ConfigError(f"{where}預設要求 HTTPS；舊站請在該站設定 allow_insecure_http = true")


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

    insecure_http = values.get("allow_insecure_http", "false")
    if insecure_http not in {"true", "false"}:
        raise ConfigError(f"{where}的 allow_insecure_http 必須是布林值")
    validate_site_url(raw_url, allow_insecure_http=insecure_http == "true", where=where)

    upload_root_value = (values.get("attachment_upload_root") or "").strip()
    try:
        upload_root = Path(upload_root_value).expanduser() if upload_root_value else None
    except RuntimeError:
        raise ConfigError(f"{where}的 attachment_upload_root 無法展開使用者目錄") from None
    if upload_root is not None:
        if not upload_root.is_absolute():
            raise ConfigError(f"{where}的 attachment_upload_root 必須是絕對路徑")
        upload_root = Path(os.path.abspath(upload_root))
        if upload_root == Path(upload_root.anchor):
            raise ConfigError(f"{where}的 attachment_upload_root 不能是磁碟根目錄")

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

    description = (values.get("description") or "").strip() or None

    return SiteSettings(
        name=name,
        url=raw_url.rstrip("/"),
        api_key=api_key,
        description=description,
        max_attachment_bytes=_max_bytes(pick("max_attachment_mb"), where),
        allow_insecure_http=insecure_http == "true",
        attachment_upload_root=upload_root,
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

    # 僅辨識舊鍵名稱；絕不展開、存取或遷移其路徑。
    legacy_keys = {"download_dir", "upload_dir"}
    obsolete = set(config.globals) & legacy_keys
    for values in config.sites.values():
        obsolete.update(set(values) & legacy_keys)
    obsolete.update(
        name for name in ("REDMINE_DOWNLOAD_DIR", "REDMINE_UPLOAD_DIR") if name in source
    )
    if obsolete:
        logger.warning("附件已改為自動管理，已忽略舊設定：%s；舊目錄與檔案不會遷移或清理",
                       "、".join(sorted(obsolete)))

    # 全域層級：環境變數覆寫參數檔，空白值視為未提供。
    defaults = dict(config.globals)
    for key, env_key in (
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
            if env_url.rstrip("/") != only.get("url", "").strip().rstrip("/"):
                only.pop("allow_insecure_http", None)
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
