"""附件操作驅動的背景清理；沒有獨立計時器或等待中的掃描佇列。"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .store import AttachmentStore

logger = logging.getLogger(__name__)


class CleanupScheduler:
    """啟動強制掃描，其後每小時最多一次，關閉只通知合作式停止。"""

    def __init__(
        self, store: AttachmentStore, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._store = store
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = False
        self._running = False
        self._last_started: float | None = None

    def start(self) -> None:
        """生命周期進入時呼叫一次；不等待掃描完成。"""
        with self._lock:
            if self._stop.is_set() or self._started:
                return
            self._started = True
            self._launch()

    def trigger(self) -> None:
        """工具釋鎖後通知；掃描中或尚未滿一小時則略過。"""
        with self._lock:
            if self._stop.is_set() or not self._started or self._running:
                return
            if self._last_started is not None:
                if self._monotonic() - self._last_started < 3600:
                    return
            self._launch()

    def _launch(self) -> None:
        # 呼叫端持鎖，讓 start／trigger／close 的決定與 thread.start 原子排序。
        self._last_started = self._monotonic()
        self._running = True
        try:
            threading.Thread(
                target=self._scan, name="redmine-attachment-cleanup", daemon=True
            ).start()
        except Exception:
            self._running = False
            logger.warning("附件背景清理無法啟動；保留所有附件")

    def _scan(self) -> None:
        try:
            if not self._stop.is_set():
                self._store.sweep(stop=self._stop)
        except Exception:
            # 固定文字避免例外中的路徑、API key 或檔案內容寫進日誌。
            logger.warning("附件背景清理失敗；保留未處理的附件")
        finally:
            with self._lock:
                self._running = False

    def close(self) -> None:
        """不等待正在壓縮的單一項目，後續項目由 sweep 檢查停止訊號。"""
        with self._lock:
            self._stop.set()
