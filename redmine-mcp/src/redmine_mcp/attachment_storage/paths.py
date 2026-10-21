"""計算自動附件儲存路徑，不建立目錄。"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


def user_attachment_dir() -> Path:
    """取得目前使用者在作業系統上的附件儲存根目錄。"""
    home = Path.home()
    if sys.platform == "win32":
        fallback = home / "AppData" / "Local"
        configured = os.environ.get("LOCALAPPDATA", "")
    elif sys.platform == "darwin":
        return home / "Library" / "Application Support" / "redmine-mcp" / "attachments"
    else:
        fallback = home / ".local" / "share"
        configured = os.environ.get("XDG_DATA_HOME", "")
    base = Path(configured) if configured else fallback
    if not base.is_absolute():
        base = fallback
    return base / "redmine-mcp" / "attachments"


def site_storage_key(url: str) -> str:
    """將正規化的站台網址轉為完整 SHA-256，不洩露站台資訊。"""
    invalid = "Invalid site URL"
    try:
        parsed = urlsplit(url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in url
            or "#" in url
        ):
            raise ValueError(invalid)
        port = parsed.port
    except ValueError:
        raise ValueError(invalid) from None

    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if parsed.scheme == "http" else 443
    authority = host if port is None or port == default_port else f"{host}:{port}"
    normalized = f"{parsed.scheme}://{authority}{parsed.path.rstrip('/')}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()