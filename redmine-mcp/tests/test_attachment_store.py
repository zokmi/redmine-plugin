"""管理下載發布與來源借用的真實檔案系統驗證。"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from redmine_mcp.attachment_storage import file_ops
from redmine_mcp.attachment_storage.paths import site_storage_key

URL = "https://example.test/redmine"
NOW = datetime(2026, 10, 21, tzinfo=UTC)


def make_store(root: Path, **kwargs: object):
    module = importlib.import_module("redmine_mcp.attachment_storage.store")
    return module.AttachmentStore(root, **kwargs)


def records(root: Path) -> list[Path]:
    return list(root.glob("*/records/*.json"))


def test_same_name_concurrent_downloads_have_unique_published_paths(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed", clock=lambda: NOW)

    def download(number: int) -> Path:
        with store.begin_download(URL, "附件.txt") as pending:
            pending.path.write_bytes(str(number).encode())
            return pending.publish()

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(download, [1, 2]))
    assert paths[0] != paths[1]
    assert {path.read_bytes() for path in paths} == {b"1", b"2"}
    assert len(records(store.root)) == 2
    for path in paths:
        assert path.parent.parent.name == "active"
        record = json.loads((store.root / site_storage_key(URL) / "records" / f"{path.parent.name}.json").read_text(encoding="utf-8"))
        assert record == {
            "schema_version": 1, "site_key": site_storage_key(URL),
            "item_id": path.parent.name, "filename": "附件.txt", "state": "active",
            "created_at": NOW.isoformat(), "last_used_at": NOW.isoformat(),
            "size": 1, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "archived_at": None,
        }


def test_pending_has_no_complete_file_or_record_before_publish(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        assert pending.path.name == ".download.part"
        assert not (pending.path.parent / "done.bin").exists()
        assert records(store.root) == []
        pending.path.write_bytes(b"done")
        final = pending.publish()
        assert not pending.path.exists()
        assert final.read_bytes() == b"done"
        assert pending.publish() == final
    with pytest.raises(RuntimeError):
        pending.publish()


def test_failed_download_retains_unregistered_partial_file(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed")
    with pytest.raises(RuntimeError, match="network"):
        with store.begin_download(URL, "done.bin") as pending:
            pending.path.write_bytes(b"partial")
            raise RuntimeError("network")
    assert pending.path.read_bytes() == b"partial"
    assert records(store.root) == []


def test_failed_record_write_retains_published_file_and_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    store = make_store(tmp_path / "managed")

    def fail_record(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(file_ops, "atomic_json", fail_record)
    with caplog.at_level(logging.WARNING), store.begin_download(URL, "done.bin") as pending:
        pending.path.write_bytes(b"complete")
        final = pending.publish()
    assert final.read_bytes() == b"complete"
    assert records(store.root) == []
    assert "未納管" in caplog.text


def test_borrow_external_reads_without_creating_root_or_changing_source(tmp_path: Path) -> None:
    root = tmp_path / "missing-root"
    source = tmp_path / "external.bin"
    source.write_bytes(b"external")
    before = source.stat()
    store = make_store(root)
    with store.borrow_source(source, 8) as data:
        assert data == b"external"
        assert not root.exists()
    after = source.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)


def test_borrow_managed_holds_lock_and_refreshes_usage(tmp_path: Path) -> None:
    now = NOW
    store = make_store(tmp_path / "managed", clock=lambda: now)
    with store.begin_download(URL, "done.bin") as pending:
        pending.path.write_bytes(b"complete")
        final = pending.publish()
    now += timedelta(days=2)
    lock = store.root / site_storage_key(URL) / "locks" / f"{final.parent.name}.lock"
    with store.borrow_source(final, 8) as data:
        assert data == b"complete"
        with file_ops.try_lock(lock) as acquired:
            assert not acquired
        record = json.loads(records(store.root)[0].read_text(encoding="utf-8"))
        assert record["last_used_at"] == now.isoformat()
    with file_ops.try_lock(lock) as acquired:
        assert acquired


def test_borrow_rejects_locked_managed_source_and_oversized_external(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        pending.path.write_bytes(b"complete")
        final = pending.publish()
    lock = store.root / site_storage_key(URL) / "locks" / f"{final.parent.name}.lock"
    with file_ops.try_lock(lock), pytest.raises(OSError):
        with store.borrow_source(final, 8):
            pass
    with pytest.raises(ValueError):
        with store.borrow_source(final, 7):
            pass
    with pytest.raises(OSError):
        with store.borrow_source(tmp_path, 7):
            pass


@pytest.mark.parametrize("filename", ["../escape", "a/b", r"a\b", "", ".", "..", ".download.part"])
def test_invalid_download_filename_creates_nothing(tmp_path: Path, filename: str) -> None:
    root = tmp_path / "managed"
    store = make_store(root)
    with pytest.raises(ValueError):
        with store.begin_download(URL, filename):
            pass
    assert not root.exists()


def test_unknown_file_inside_root_is_never_enrolled(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    source = root / "unknown.tmp"
    root.mkdir()
    source.write_bytes(b"unknown")
    store = make_store(root)
    with store.borrow_source(source, 7) as data:
        assert data == b"unknown"
    assert list(root.iterdir()) == [source]


def test_safe_creation_rejects_linked_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "managed"
    root.symlink_to(outside, target_is_directory=True)
    store = make_store(root)
    with pytest.raises(OSError):
        with store.begin_download(URL, "done.bin"):
            pass
    assert list(outside.iterdir()) == []


def test_safe_publication_never_overwrites_existing_file(tmp_path: Path) -> None:
    pending = tmp_path / ".download.part"
    target = tmp_path / "done.bin"
    pending.write_bytes(b"download")
    target.write_bytes(b"unknown")
    with pytest.raises(OSError):
        file_ops.publish_regular(pending, target)
    assert pending.read_bytes() == b"download"
    assert target.read_bytes() == b"unknown"


def test_safe_directory_creation_and_publication(tmp_path: Path) -> None:
    directory = tmp_path / "new" / "nested"
    file_ops.ensure_directory(directory)
    file_ops.ensure_directory(directory)
    source = directory / ".download.part"
    with file_ops.create_regular(source):
        source.write_bytes(b"download")
    target = directory / "done.bin"
    size, digest = file_ops.publish_regular(source, target)
    assert size == 8
    assert digest == hashlib.sha256(b"download").hexdigest()
    assert target.read_bytes() == b"download"
    assert not source.exists()



def test_anchored_writer_finishes_before_publish(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        with pending.open_writer() as stream:
            stream.write(b"safe writer")
            with pytest.raises(RuntimeError):
                pending.publish()
        assert stream.closed
        final = pending.publish()
    assert final.read_bytes() == b"safe writer"
    with pytest.raises(RuntimeError):
        with pending.open_writer():
            pass


def test_anchored_writer_rejects_replaced_leaf(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed")
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    with store.begin_download(URL, "done.bin") as pending:
        # 模擬原始下載 handle 已關閉後，另一方替換半成品名稱。
        pending._stream.close()
        pending.path.unlink()
        pending.path.symlink_to(outside)
        with pytest.raises(OSError):
            with pending.open_writer() as stream:
                stream.write(b"changed")
        with pytest.raises(OSError):
            pending.publish()
    assert outside.read_bytes() == b"untouched"


def test_record_paths_reject_unvalidated_record_identifiers(tmp_path: Path) -> None:
    from dataclasses import replace

    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        with pending.open_writer() as stream:
            stream.write(b"complete")
        final = pending.publish()
    record = store.load_record(site_storage_key(URL), final.parent.name)
    for malformed in (
        replace(record, site_key="../outside"),
        replace(record, item_id="../outside"),
        replace(record, filename="../outside"),
    ):
        for derive in (store.item_path, store.record_path, store.lock_path):
            with pytest.raises(ValueError):
                derive(malformed)



@pytest.mark.parametrize("change", [
    {"item_id": "../outside"}, {"site_key": "../outside"},
    {"filename": "../outside"}, {"created_at": "2026-10-21T00:00:00"},
    {"last_used_at": "2026-10-21T08:00:00+08:00"}, {"state": "unknown"},
    {"schema_version": 2}, {"size": -1}, {"sha256": "invalid"},
])
def test_malformed_record_is_not_used_or_rewritten(tmp_path: Path, change: dict[str, object]) -> None:
    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        with pending.open_writer() as stream:
            stream.write(b"complete")
        final = pending.publish()
    metadata = records(store.root)[0]
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload.update(change)
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    before = metadata.read_bytes()
    with pytest.raises(ValueError):
        store.load_record(site_storage_key(URL), final.parent.name)
    with store.borrow_source(final, 8) as data:
        assert data == b"complete"
    assert metadata.read_bytes() == before


def test_record_path_field_is_ignored_and_unknown_temp_is_not_enrolled(tmp_path: Path) -> None:
    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        with pending.open_writer() as stream:
            stream.write(b"complete")
        final = pending.publish()
    metadata = records(store.root)[0]
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["path"] = str(tmp_path / "external")
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    record = store.load_record(site_storage_key(URL), final.parent.name)
    assert store.item_path(record) == final
    temporary = metadata.with_name(f".{metadata.name}.unknown.tmp")
    temporary.write_bytes(b"unknown")
    before = metadata.read_bytes()
    with store.borrow_source(temporary, 7) as data:
        assert data == b"unknown"
    assert metadata.read_bytes() == before
    assert temporary.read_bytes() == b"unknown"


def test_failed_usage_refresh_preserves_record_file_and_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    store = make_store(tmp_path / "managed")
    with store.begin_download(URL, "done.bin") as pending:
        with pending.open_writer() as stream:
            stream.write(b"complete")
        final = pending.publish()
    metadata = records(store.root)[0]
    before = metadata.read_bytes()

    def fail_record(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(file_ops, "atomic_json", fail_record)
    with caplog.at_level(logging.WARNING), store.borrow_source(final, 8) as data:
        assert data == b"complete"
    assert metadata.read_bytes() == before
    assert final.read_bytes() == b"complete"
    assert "使用時間更新失敗" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native in-place junction race")
def test_pending_creation_race_never_creates_or_writes_external_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_attachment_file_ops import _set_windows_junction

    store = make_store(tmp_path / "managed")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / ".download.part"
    victim.write_bytes(b"untouched")
    changed: Path | None = None
    original = file_ops._open_fd

    def raced_open(path: Path, parent_fd: int | None, **kwargs: bool) -> int:
        nonlocal changed
        if path.name == ".download.part" and changed is None:
            _set_windows_junction(path.parent, outside)
            changed = path.parent
        return original(path, parent_fd, **kwargs)

    monkeypatch.setattr(file_ops, "_open_fd", raced_open)
    try:
        with pytest.raises(OSError):
            with store.begin_download(URL, "done.bin") as pending:
                with pending.open_writer() as stream:
                    stream.write(b"changed")
        assert changed is not None
        assert victim.read_bytes() == b"untouched"
        assert records(store.root) == []
    finally:
        if changed is not None:
            _set_windows_junction(changed, outside, remove=True)
