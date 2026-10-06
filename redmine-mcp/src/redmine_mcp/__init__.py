"""Redmine MCP server 套件。

版本號的單一來源：一律由已安裝的套件 metadata（pyproject 的 `version`）讀出，
避免 pyproject、MCP server 宣告的版本與 User-Agent 三處各寫一份而改版時漏改。
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

#: 發佈名稱；與 pyproject 的 `[project].name` 一致。
DIST_NAME = "redmine-mcp"

try:
    __version__ = version(DIST_NAME)
except PackageNotFoundError:  # pragma: no cover - 僅在未安裝、直接以路徑匯入時發生
    # 未安裝成套件（例如直接把 src 加進 PYTHONPATH）時仍要能匯入，
    # 但版本號要明確標示為未知，不可假裝成某個實際版本。
    __version__ = "0+unknown"

__all__ = ["DIST_NAME", "__version__"]
