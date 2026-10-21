"""背景清理的節流、互斥、停止與錯誤邊界。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from redmine_mcp.attachment_storage import AttachmentStore
from redmine_mcp.attachment_storage.scheduler import CleanupScheduler


class ScanStore(AttachmentStore):
    def __init__(self, root, *, blocked=False, fail=False):
        super().__init__(root)
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.stop = None
        self.daemon = False
        self.fail = fail
        if not blocked:
            self.release.set()

    def sweep(self, stop=None):
        self.calls += 1
        self.stop = stop
        self.daemon = threading.current_thread().daemon
        self.entered.set()
        try:
            assert self.release.wait(3), "測試未釋放掃描"
            if self.fail:
                raise OSError("secret-file-content")
        finally:
            self.finished.set()


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "背景工作未完成"
        threading.Event().wait(0.001)


def wait_idle():
    wait_for(lambda: not any(t.name == "redmine-attachment-cleanup" for t in threading.enumerate()))


def test_啟動強制掃描與整點節流(tmp_path):
    clock = [0.0]
    store = ScanStore(tmp_path)
    scheduler = CleanupScheduler(store, monotonic=lambda: clock[0])
    scheduler.trigger()
    assert store.calls == 0
    scheduler.start()
    wait_idle()
    assert store.calls == 1
    scheduler.start()
    clock[0] = 3599
    scheduler.trigger()
    wait_idle()
    assert store.calls == 1
    clock[0] = 3600
    scheduler.trigger()
    wait_idle()
    assert store.calls == 2
    scheduler.close()


def test_慢掃描與併發觸發不疊加且關閉立即停止(tmp_path):
    clock = [0.0]
    store = ScanStore(tmp_path, blocked=True)
    scheduler = CleanupScheduler(store, monotonic=lambda: clock[0])
    scheduler.start()
    assert store.entered.wait(2)
    clock[0] = 3600
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: scheduler.trigger(), range(40)))
    assert store.calls == 1
    assert store.daemon
    before = time.monotonic()
    scheduler.close()
    assert time.monotonic() - before < 0.25
    assert store.stop.is_set()
    store.release.set()
    wait_idle()
    clock[0] = 7200
    scheduler.trigger()
    scheduler.start()
    wait_idle()
    assert store.calls == 1


def test_掃描失敗不外洩並可再次掃描(tmp_path, caplog, capsys):
    clock = [0.0]
    store = ScanStore(tmp_path, fail=True)
    scheduler = CleanupScheduler(store, monotonic=lambda: clock[0])
    scheduler.start()
    wait_idle()
    assert store.calls == 1
    clock[0] = 3600
    scheduler.trigger()
    wait_idle()
    assert store.calls == 2
    assert len(caplog.records) == 2
    assert all(record.levelname == "WARNING" for record in caplog.records)
    assert "secret-file-content" not in caplog.text
    assert capsys.readouterr().out == ""
    scheduler.close()
