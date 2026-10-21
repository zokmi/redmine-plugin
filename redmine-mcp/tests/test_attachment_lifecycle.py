"""Lifecycle integration tests using real managed files and an injected UTC clock."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from redmine_mcp.attachment_storage import AttachmentStore, file_ops

NOW = datetime(2026, 10, 1, tzinfo=UTC)
DATA = b"attachment contents\x00" * 1000


def setup_item(tmp_path):
    times = [NOW]
    store = AttachmentStore(tmp_path / "managed", clock=lambda: times[0])
    with store.begin_download("https://example.test", "report.bin") as pending:
        with pending.open_writer() as output:
            output.write(DATA)
        path = pending.publish()
    record = store._record_for(path)
    assert record is not None
    archive = store.root / record.site_key / "archives" / f"{record.item_id}.zip"
    return store, times, path, record, archive


@pytest.mark.parametrize("offset,archived", [(-1, False), (0, True), (1, True)])
def test_seven_day_boundary(tmp_path, offset, archived):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7, microseconds=offset)
    store.sweep()
    assert path.exists() is not archived
    assert archive.exists() is archived
    current = store.load_record(record.site_key, record.item_id)
    assert current.state == ("archived" if archived else "active")
    if archived:
        with zipfile.ZipFile(archive) as zipped:
            assert zipped.namelist() == ["report.bin"]
            assert zipped.testzip() is None
            assert zipped.read("report.bin") == DATA
        assert current.archived_at == times[0].isoformat()
        assert current.sha256 == hashlib.sha256(DATA).hexdigest()


@pytest.mark.parametrize("offset,expired", [(-1, False), (0, True), (1, True)])
def test_thirty_day_boundary_survives_restart(tmp_path, offset, expired):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    store.sweep()
    archived_at = times[0]
    times[0] += timedelta(days=30, microseconds=offset)
    restarted = AttachmentStore(store.root, clock=lambda: times[0])
    restarted.sweep()
    assert archive.exists() is not expired
    assert store.record_path(record).exists() is not expired
    assert not path.exists()
    if not expired:
        assert store.load_record(record.site_key, record.item_id).archived_at == archived_at.isoformat()


def test_usage_refresh_and_future_timestamp_skip(tmp_path):
    store, times, path, _record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=6)
    with store.borrow_source(path, len(DATA)) as data:
        assert data == DATA
    times[0] += timedelta(days=1)
    store.sweep()
    assert path.exists() and not archive.exists()
    times[0] = NOW
    store.sweep()
    assert path.exists() and not archive.exists()


@pytest.mark.parametrize("phase", ["compress", "published", "metadata", "deleted"])
def test_interrupted_archive_recovers_without_resetting_time(tmp_path, monkeypatch, phase):
    from redmine_mcp.attachment_storage import lifecycle

    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    original_time = times[0]
    original_json = file_ops.atomic_json
    original_publish = file_ops.publish_regular
    original_remove = file_ops.remove_regular

    def atomic(target, payload):
        original_json(target, payload)
        if phase == "metadata" and payload.get("state") == "archive_published":
            raise OSError("injected metadata interruption")

    def publish(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        if phase == "published":
            raise OSError("injected published interruption")
        return result

    def remove(target, **kwargs):
        result = original_remove(target, **kwargs)
        if phase == "deleted" and target == path:
            raise OSError("injected deletion interruption")
        return result

    def compress(*args, **kwargs):
        raise OSError("injected disk full")

    with monkeypatch.context() as patch:
        patch.setattr(file_ops, "atomic_json", atomic)
        patch.setattr(file_ops, "publish_regular", publish)
        patch.setattr(file_ops, "remove_regular", remove)
        if phase == "compress":
            patch.setattr(lifecycle, "_write_archive", compress)
        store.sweep()
    assert path.exists() or archive.exists()
    times[0] += timedelta(days=1)
    store.sweep()
    assert not path.exists() and archive.exists()
    current = store.load_record(record.site_key, record.item_id)
    assert current.state == "archived"
    if phase in {"metadata", "deleted"}:
        assert current.archived_at == original_time.isoformat()


@pytest.mark.parametrize("change", ["original", "zip_hash", "zip_crc", "size"])
def test_recovery_retains_both_on_integrity_mismatch(tmp_path, monkeypatch, change):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    with monkeypatch.context() as patch:
        patch.setattr(file_ops, "remove_regular", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
        store.sweep()
    assert path.exists() and archive.exists()
    if change == "original":
        path.write_bytes(b"changed original")
    elif change == "zip_hash":
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("report.bin", b"X" * len(DATA))
    elif change == "zip_crc":
        payload = bytearray(archive.read_bytes())
        payload[payload.index(b"PK\x01\x02") + 16] ^= 0xFF
        archive.write_bytes(payload)
    else:
        current = store.load_record(record.site_key, record.item_id)
        file_ops.atomic_json(store.record_path(record), replace(current, size=1).to_json())
    before = path.read_bytes(), archive.read_bytes()
    times[0] += timedelta(days=31)
    store.sweep()
    assert (path.read_bytes(), archive.read_bytes()) == before
    assert store.record_path(record).exists()


def test_unknown_and_malformed_files_untouched(tmp_path):
    store, times, path, record, _archive = setup_item(tmp_path)
    unknown = path.parent / "unknown.bin"
    unknown.write_bytes(b"unknown")
    metadata = store.record_path(record)
    metadata.write_text('{"schema_version":99}', encoding="utf-8")
    temporary = metadata.with_name(".failed.tmp")
    temporary.write_bytes(b"partial json")
    times[0] += timedelta(days=100)
    store.sweep()
    assert path.read_bytes() == DATA
    assert unknown.read_bytes() == b"unknown"
    assert temporary.read_bytes() == b"partial json"
    assert json.loads(metadata.read_text()) == {"schema_version": 99}


def test_item_lock_and_stop_skip(tmp_path):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=8)
    with file_ops.try_lock(store.lock_path(record)) as acquired:
        assert acquired
        store.sweep()
        assert path.exists()
    stop = threading.Event()
    stop.set()
    store.sweep(stop)
    assert path.exists()
    store.sweep()
    assert archive.exists() and not path.exists()


def test_missing_expired_zip_removes_record_only(tmp_path):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    store.sweep()
    archive.unlink()
    path.write_bytes(b"new unregistered original")
    times[0] += timedelta(days=30)
    store.sweep()
    assert path.read_bytes() == b"new unregistered original"
    assert not store.record_path(record).exists()


def test_edited_active_content_is_archived_as_current_snapshot(tmp_path):
    store, times, path, record, archive = setup_item(tmp_path)
    path.write_bytes(b"updated before archiving")
    times[0] += timedelta(days=7)
    store.sweep()
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("report.bin") == b"updated before archiving"
    current = store.load_record(record.site_key, record.item_id)
    assert current.size == 24
    assert current.sha256 == hashlib.sha256(b"updated before archiving").hexdigest()
    assert not path.exists()


def test_replaced_original_between_publication_and_delete_is_retained(tmp_path, monkeypatch):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    remove = file_ops.remove_regular

    def replace_before_delete(target, **kwargs):
        if target == path:
            replacement = path.with_name("replacement")
            replacement.write_bytes(DATA)
            os.replace(replacement, path)
        return remove(target, **kwargs)

    monkeypatch.setattr(file_ops, "remove_regular", replace_before_delete)
    store.sweep()
    assert path.read_bytes() == DATA
    assert archive.exists()
    assert store.load_record(record.site_key, record.item_id).state == "archive_published"


@pytest.mark.parametrize("lock_kind", ["sweep", "borrow"])
def test_subprocess_holds_sweep_or_borrow_lock(tmp_path, lock_kind):
    store, times, path, _record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    code = """
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from redmine_mcp.attachment_storage import AttachmentStore, file_ops
store = AttachmentStore(Path(sys.argv[1]), clock=lambda: datetime(2026,10,1,tzinfo=UTC))
context = (file_ops.try_lock(store.root / '.sweep.lock') if sys.argv[3] == 'sweep'
           else store.borrow_source(Path(sys.argv[2]), 100000))
with context as result:
    assert result
    print('ready', flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(store.root), str(path), lock_kind],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        store.sweep()
        assert path.exists() and not archive.exists()
    finally:
        process.communicate("release\n", timeout=10)
    assert process.returncode == 0
    store.sweep()
    assert not path.exists() and archive.exists()


def test_future_archive_time_does_not_expire(tmp_path):
    store, times, _path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    store.sweep()
    times[0] = NOW
    store.sweep()
    assert archive.exists() and store.record_path(record).exists()


@pytest.mark.parametrize("target", ["root", "site", "active", "archives", "records"])
def test_junction_paths_never_modify_external_files(tmp_path, target):
    from tests.test_attachment_file_ops import _junction

    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    if target == "archives":
        store.sweep()
        times[0] += timedelta(days=30)
    selected = {
        "root": store.root,
        "site": store.root / record.site_key,
        "active": path.parent,
        "archives": archive.parent,
        "records": store.record_path(record).parent,
    }[target]
    outside = tmp_path / "outside"
    selected.rename(outside)
    _junction(selected, outside)
    before = {str(p.relative_to(outside)): p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    store.sweep()
    after = {str(p.relative_to(outside)): p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows in-place reparse race")
def test_delete_handle_never_follows_in_place_junction(tmp_path, monkeypatch):
    from tests.test_attachment_file_ops import _set_windows_junction

    parent, outside = tmp_path / "parent", tmp_path / "outside"
    parent.mkdir()
    outside.mkdir()
    victim = outside / "victim"
    victim.write_bytes(b"external data")
    info = victim.stat()
    opening = file_ops._open_fd
    changed = False

    def race(path, parent_fd, **kwargs):
        nonlocal changed
        _set_windows_junction(parent, outside)
        changed = True
        return opening(path, parent_fd, **kwargs)

    monkeypatch.setattr(file_ops, "_open_fd", race)
    try:
        with pytest.raises(OSError):
            file_ops.remove_regular(
                parent / "victim", identity=(info.st_dev, info.st_ino), validate=lambda stream: None
            )
        assert victim.read_bytes() == b"external data"
    finally:
        if changed:
            _set_windows_junction(parent, outside, remove=True)


def test_failed_record_delete_is_recoverable(tmp_path, monkeypatch):
    store, times, _path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    store.sweep()
    times[0] += timedelta(days=30)
    remove = file_ops.remove_regular

    def fail_record(target, **kwargs):
        if target == store.record_path(record):
            raise PermissionError("metadata removal denied")
        return remove(target, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(file_ops, "remove_regular", fail_record)
        store.sweep()
    assert not archive.exists() and store.record_path(record).exists()
    store.sweep()
    assert not store.record_path(record).exists()


def test_metadata_failure_before_write_keeps_original_and_published_zip(tmp_path, monkeypatch):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("disk full")
        patch.setattr(file_ops, "atomic_json", fail)
        store.sweep()
    assert path.exists() and archive.exists()
    assert store.load_record(record.site_key, record.item_id).state == "active"
    store.sweep()
    assert not path.exists() and archive.exists()


def test_missing_root_does_not_create_storage(tmp_path):
    root = tmp_path / "absent"
    AttachmentStore(root).sweep()
    assert not root.exists()


def test_broken_deflate_stream_retained_without_aborting_sweep(tmp_path, monkeypatch):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise PermissionError("leave original")
        patch.setattr(file_ops, "remove_regular", fail)
        store.sweep()
    payload = bytearray(archive.read_bytes())
    name_length = int.from_bytes(payload[26:28], "little")
    extra_length = int.from_bytes(payload[28:30], "little")
    offset = 30 + name_length + extra_length
    payload[offset] = 0xFF  # Invalid DEFLATE block type 3.
    archive.write_bytes(payload)
    store.sweep()
    assert path.read_bytes() == DATA
    assert archive.read_bytes() == payload
    assert store.load_record(record.site_key, record.item_id).state == "archive_published"


def test_partial_zip_is_retained_and_never_enrolled(tmp_path, monkeypatch):
    store, times, path, _record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    write = zipfile._ZipWriteFile.write

    def fail_after_write(output, data):
        write(output, data)
        raise OSError("injected compression failure")

    with monkeypatch.context() as patch:
        patch.setattr(zipfile._ZipWriteFile, "write", fail_after_write)
        store.sweep()
    assert path.read_bytes() == DATA
    assert not archive.exists()
    temporary = next(archive.parent.glob("*.tmp"))
    before = temporary.read_bytes()
    store.sweep()
    assert temporary.read_bytes() == before
    assert archive.exists() and not path.exists()


def test_corrupt_archived_zip_is_never_expired(tmp_path):
    store, times, _path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    store.sweep()
    archive.write_bytes(b"corrupt archive")
    times[0] += timedelta(days=30)
    store.sweep()
    assert archive.read_bytes() == b"corrupt archive"
    assert store.record_path(record).exists()


def test_two_actual_sweeps_are_mutually_exclusive(tmp_path):
    store, times, path, _record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    code = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from redmine_mcp.attachment_storage import AttachmentStore, lifecycle
write_archive = lifecycle._write_archive
def pause(*args):
    print('ready', flush=True)
    sys.stdin.readline()
    return write_archive(*args)
lifecycle._write_archive = pause
AttachmentStore(Path(sys.argv[1]), clock=lambda: datetime(2026,10,8,tzinfo=UTC)).sweep()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(store.root)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        store.sweep()
        assert path.exists() and not archive.exists()
    finally:
        process.communicate("release\n", timeout=10)
    assert process.returncode == 0
    assert archive.exists() and not path.exists()


def test_item_record_is_reloaded_after_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager

    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    lock = file_ops.try_lock

    @contextmanager
    def refreshed_lock(target):
        with lock(target) as acquired:
            if target == store.lock_path(record):
                updated = replace(record, last_used_at=times[0].isoformat())
                file_ops.atomic_json(store.record_path(record), updated.to_json())
            yield acquired

    monkeypatch.setattr(file_ops, "try_lock", refreshed_lock)
    store.sweep()
    assert path.exists() and not archive.exists()


def test_same_filename_archives_are_isolated_and_external_source_is_untouched(tmp_path):
    store, times, path, record, archive = setup_item(tmp_path)
    external = tmp_path / "external.bin"
    external.write_bytes(b"external original")
    with store.borrow_source(external, 100) as source:
        assert source == b"external original"
    with store.begin_download("https://example.test", "report.bin") as pending:
        with pending.open_writer() as output:
            output.write(b"second attachment")
        second = pending.publish()
    times[0] += timedelta(days=7)
    store.sweep()
    second_archive = archive.with_name(f"{second.parent.name}.zip")
    assert second_archive != archive
    with zipfile.ZipFile(second_archive) as zipped:
        assert zipped.read("report.bin") == b"second attachment"
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("report.bin") == DATA
    assert external.read_bytes() == b"external original"
    assert not path.exists() and not second.exists()
    assert store.load_record(record.site_key, record.item_id).state == "archived"


def test_archive_retention_starts_after_compression_completes(tmp_path, monkeypatch):
    from redmine_mcp.attachment_storage import lifecycle

    store, times, _path, record, _archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    write_archive = lifecycle._write_archive

    def slow_compression(*args):
        write_archive(*args)
        times[0] += timedelta(hours=2)

    monkeypatch.setattr(lifecycle, "_write_archive", slow_compression)
    store.sweep()
    current = store.load_record(record.site_key, record.item_id)
    assert current.archived_at == times[0].isoformat()


def test_retention_starts_after_download_completion(tmp_path):
    times = [NOW]
    store = AttachmentStore(tmp_path / "managed", clock=lambda: times[0])
    with store.begin_download("https://example.test", "report.bin") as pending:
        with pending.open_writer() as output:
            output.write(DATA)
        times[0] += timedelta(days=2)
        path = pending.publish()
    record = store._record_for(path)
    assert record.created_at == times[0].isoformat()
    assert record.last_used_at == times[0].isoformat()
    times[0] += timedelta(days=7, microseconds=-1)
    store.sweep()
    assert path.read_bytes() == DATA
    times[0] += timedelta(microseconds=1)
    store.sweep()
    assert not path.exists()
    assert store.load_record(record.site_key, record.item_id).state == "archived"


@pytest.mark.parametrize("state", ["active", "archive_published", "archived"])
@pytest.mark.parametrize("field", ["created_at", "last_used_at", "archived_at"])
def test_future_timestamps_warn_and_preserve_files(tmp_path, monkeypatch, caplog, state, field):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    if state != "active":
        with monkeypatch.context() as patch:
            patch.setattr(file_ops, "remove_regular",
                          lambda *a, **k: (_ for _ in ()).throw(OSError("retain original")))
            store.sweep()
        record = store.load_record(record.site_key, record.item_id)
    times[0] += timedelta(days=40)
    record = replace(record, state=state, **{field: (times[0] + timedelta(days=1)).isoformat()})
    file_ops.atomic_json(store.record_path(record), record.to_json())
    before_archive = archive.read_bytes() if archive.exists() else None
    caplog.clear()
    store.sweep()
    assert path.read_bytes() == DATA
    assert (archive.read_bytes() if archive.exists() else None) == before_archive
    assert store.load_record(record.site_key, record.item_id) == record
    assert "future timestamp" in caplog.text
    assert "attachment contents" not in caplog.text


@pytest.mark.parametrize("barrier", ["archive", "record", "recovery"])
def test_publication_barrier_failure_retains_original_and_retry_recovers(tmp_path, monkeypatch, barrier):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    reached = []
    original_sync = getattr(file_ops, "_sync_directory", None)

    def fail(fd):
        current = store.load_record(record.site_key, record.item_id)
        if ((barrier == "archive" and archive.exists() and current.state == "active")
                or (barrier in {"record", "recovery"} and current.state == "archive_published")):
            reached.append(current.state)
            raise OSError("injected directory fsync failure")
        if original_sync is not None:
            original_sync(fd)

    if barrier == "recovery":
        with monkeypatch.context() as patch:
            patch.setattr(file_ops, "remove_regular",
                          lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted")))
            store.sweep()
    with monkeypatch.context() as patch:
        patch.setattr(file_ops, "_sync_directory", fail, raising=False)
        store.sweep()
    assert reached, "publication/recovery must pass a directory durability barrier"
    assert path.read_bytes() == DATA
    assert archive.exists()
    store.sweep()
    assert not path.exists() and archive.exists()
    assert store.load_record(record.site_key, record.item_id).state == "archived"


def test_archive_and_record_barriers_precede_original_deletion(tmp_path, monkeypatch):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    phases = []
    remove = file_ops.remove_regular

    def sync(fd):
        if archive.exists():
            phases.append(store.load_record(record.site_key, record.item_id).state)

    def deleting(target, **kwargs):
        if target == path:
            assert "active" in phases
            assert "archive_published" in phases
            phases.append("delete")
        return remove(target, **kwargs)

    monkeypatch.setattr(file_ops, "_sync_directory", sync, raising=False)
    monkeypatch.setattr(file_ops, "remove_regular", deleting)
    store.sweep()
    assert "delete" in phases
    assert not path.exists()


def test_recovery_syncs_missing_original_before_advancing_record(tmp_path, monkeypatch):
    store, times, path, record, archive = setup_item(tmp_path)
    times[0] += timedelta(days=7)
    remove = file_ops.remove_regular

    def interrupted(target, **kwargs):
        remove(target, **kwargs)
        if target == path:
            raise OSError("crash after unlink")

    with monkeypatch.context() as patch:
        patch.setattr(file_ops, "remove_regular", interrupted)
        store.sweep()
    assert not path.exists() and archive.exists()
    assert store.load_record(record.site_key, record.item_id).state == "archive_published"
    sync_parent = file_ops.sync_parent
    observed = []

    def fail(target):
        if target == path:
            observed.append(target)
            raise OSError("unlink barrier failed")
        sync_parent(target)

    with monkeypatch.context() as patch:
        patch.setattr(file_ops, "sync_parent", fail)
        store.sweep()
    assert observed == [path]
    assert archive.exists()
    assert store.load_record(record.site_key, record.item_id).state == "archive_published"
    store.sweep()
    assert store.load_record(record.site_key, record.item_id).state == "archived"
