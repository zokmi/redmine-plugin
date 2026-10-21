"""Recoverable local attachment archiving; only validated registered items are managed."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
import zipfile
import zlib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from . import file_ops
from .store import AttachmentStore, ItemRecord, _utc

IDLE_SECONDS = 604800
ARCHIVE_SECONDS = 2592000
logger = logging.getLogger(__name__)


def _identity(stream: BinaryIO) -> tuple[int, int]:
    info = os.fstat(stream.fileno())
    return info.st_dev, info.st_ino


def _fingerprint(stream: BinaryIO) -> tuple[int, str]:
    stream.seek(0)
    before = os.fstat(stream.fileno())
    digest, size = hashlib.sha256(), 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise ValueError("Source changed during read")
    return size, digest.hexdigest()


def _verify_zip(stream: BinaryIO, record: ItemRecord) -> None:
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != record.filename:
            raise ValueError("Archive member mismatch")
        if entries[0].file_size != record.size:
            raise ValueError("Archive size mismatch")
        digest, size = hashlib.sha256(), 0
        with archive.open(entries[0]) as member:
            while chunk := member.read(1024 * 1024):
                size += len(chunk)
                if size > record.size:
                    raise ValueError("Archive size exceeded")
                digest.update(chunk)
        # Reading the complete member checks ZipFile's CRC as well.
        if size != record.size or digest.hexdigest() != record.sha256:
            raise ValueError("Archive hash mismatch")


def _verify_source(stream: BinaryIO, record: ItemRecord) -> None:
    if _fingerprint(stream) != (record.size, record.sha256):
        raise ValueError("Original changed; original and archive retained")


def _write_archive(source: BinaryIO, temporary: Path, record: ItemRecord) -> None:
    source.seek(0)
    with file_ops.create_regular(temporary) as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            with archive.open(record.filename, "w", force_zip64=True) as member:
                while chunk := source.read(1024 * 1024):
                    member.write(chunk)
        output.flush()
        os.fsync(output.fileno())
        _verify_zip(output, record)


def _archive_path(store: AttachmentStore, record: ItemRecord) -> Path:
    return store.root / record.site_key / "archives" / f"{record.item_id}.zip"


def _archive(store: AttachmentStore, record: ItemRecord) -> ItemRecord:
    archive_path = _archive_path(store, record)
    original = store.item_path(record)
    if record.state == "active":
        with file_ops.open_regular(original, deny_write=True) as source:
            identity = _identity(source)
            size, digest = _fingerprint(source)
            record = replace(record, size=size, sha256=digest)
            file_ops.ensure_directory(archive_path.parent)
            try:
                with file_ops.open_regular(archive_path, deny_write=True) as existing:
                    _verify_zip(existing, record)
            except FileNotFoundError:
                temporary = archive_path.with_name(f".{record.item_id}.{uuid.uuid4().hex}.tmp")
                _write_archive(source, temporary, record)
                _verify_source(source, record)
                file_ops.publish_regular(temporary, archive_path)
            _verify_source(source, record)
        record = replace(record, state="archive_published", archived_at=_utc(store.clock()))
        file_ops.atomic_json(store.record_path(record), record.to_json())
    else:
        try:
            with file_ops.open_regular(original, deny_write=True) as source:
                identity = _identity(source)
                _verify_source(source, record)
        except FileNotFoundError:
            identity = None
    # Keep the validated ZIP handle open through original deletion. Windows denies
    # concurrent writers and replacement; POSIX still revalidates content below.
    with file_ops.open_regular(archive_path, deny_write=True) as archive:
        _verify_zip(archive, record)
        if identity is not None:
            def validate(source: BinaryIO) -> None:
                _verify_source(source, record)
                _verify_zip(archive, record)
            file_ops.remove_regular(original, identity=identity, validate=validate)
    record = replace(record, state="archived")
    file_ops.atomic_json(store.record_path(record), record.to_json())
    return record


def _expire(store: AttachmentStore, record: ItemRecord) -> None:
    archive_path = _archive_path(store, record)
    try:
        with file_ops.open_regular(archive_path, deny_write=True) as archive:
            _verify_zip(archive, record)
            identity = _identity(archive)
    except FileNotFoundError:
        pass  # An earlier expiry may have deleted ZIP before metadata deletion.
    else:
        file_ops.remove_regular(
            archive_path, identity=identity, validate=lambda stream: _verify_zip(stream, record)
        )
    metadata = store.record_path(record)
    with file_ops.open_regular(metadata, deny_write=True) as stream:
        identity = _identity(stream)

    def validate_record(stream: BinaryIO) -> None:
        if ItemRecord.from_json(json.loads(file_ops.read_limited(stream, 64 * 1024))) != record:
            raise ValueError("Record changed during expiry")
    file_ops.remove_regular(metadata, identity=identity, validate=validate_record)


def _sweep_item(store: AttachmentStore, record: ItemRecord, now: datetime) -> None:
    if any(datetime.fromisoformat(value) > now for value in (
        record.created_at, record.last_used_at, record.archived_at or record.created_at
    )):
        return
    if record.state == "active":
        if now - datetime.fromisoformat(record.last_used_at) < timedelta(seconds=IDLE_SECONDS):
            return
        record = _archive(store, record)
    elif record.state == "archive_published":
        record = _archive(store, record)
    if record.state == "archived" and record.archived_at is not None:
        if now - datetime.fromisoformat(record.archived_at) >= timedelta(seconds=ARCHIVE_SECONDS):
            _expire(store, record)


def sweep(store: AttachmentStore, stop: threading.Event | None = None) -> None:
    """Nonblocking sweep of canonical records; retain unknown or uncertain files."""
    try:
        now = datetime.fromisoformat(_utc(store.clock()))
        # An unused store is a read-only no-op, rather than creating its root.
        sites = file_ops.list_names(store.root)
        with file_ops.try_lock(store.root / ".sweep.lock") as acquired:
            if not acquired:
                return
            for site in sites:
                if stop is not None and stop.is_set():
                    return
                if not re.fullmatch(r"[0-9a-f]{64}", site):
                    continue
                try:
                    names = file_ops.list_names(store.root / site / "records")
                except OSError:
                    continue
                for name in names:
                    if stop is not None and stop.is_set():
                        return
                    if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                        continue
                    try:
                        record = store.load_record(site, name[:-5])
                        with file_ops.try_lock(store.lock_path(record)) as acquired_item:
                            if not acquired_item:
                                continue
                            record = store.load_record(site, name[:-5])
                            _sweep_item(store, record, now)
                    except (
                        OSError, ValueError, zipfile.BadZipFile, zlib.error, EOFError, RuntimeError
                    ):
                        logger.warning("Attachment lifecycle retained an uncertain item")
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.warning("Attachment lifecycle scan skipped an unsafe or unavailable root")
