"""Redmine 呼叫的例外型別。訊息一律不得包含 API key 或 request header。"""
from __future__ import annotations


class RedmineError(RuntimeError):
    """所有 Redmine 相關錯誤的基底型別。"""


class RedmineAuthError(RedmineError):
    """401 / 403：API key 無效或權限不足。"""


class RedmineNotFoundError(RedmineError):
    """404：指定的資源不存在。"""


class RedmineValidationError(RedmineError):
    """422：Redmine 驗證失敗，訊息帶出其 errors 陣列供呼叫端修正。"""


class RedmineServerError(RedmineError):
    """429 / 5xx / 非預期轉址：伺服器端問題，不自動重試。"""


class RedmineConnectionError(RedmineError):
    """連線失敗或逾時。"""
