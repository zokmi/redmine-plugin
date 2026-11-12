"""管理附件下載、紀錄與持鎖來源借用；未知檔案不自動納管。"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from . import file_ops
from .paths import site_storage_key, user_attachment_dir

logger = logging.getLogger(__name__)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def utc_now() -> datetime:
    """提供具 UTC 時區的目前時間。"""
    return datetime.now(UTC)


def _filename(value: str) -> str:
    if not value or value in {".", "..", ".download.part"} or any(
        character in value for character in ("/", "\\", "\0", ":")
    ):
        raise ValueError("附件名稱必須是單層檔名")
    return value


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("附件時間必須包含時區")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class ItemRecord:
    """schema 1 的項目紀錄；所有路徑均由已驗證的識別碼推導。"""

    site_key: str
    item_id: str
    filename: str
    created_at: str
    last_used_at: str
    state: str
    size: int
    sha256: str
    archived_at: str | None = None
    schema_version: int = 1

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: object) -> ItemRecord:
        """拒絕未知 schema、任意路徑、非 canonical UUID 及無時區時間。"""
        if not isinstance(value, dict):
            raise ValueError("附件紀錄格式不符")
        strings = (
            "site_key", "item_id", "filename", "created_at", "last_used_at", "state", "sha256"
        )
        if any(not isinstance(value.get(key), str) for key in strings):
            raise ValueError("附件紀錄欄位不符")
        if (
            type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or type(value.get("size")) is not int or value["size"] < 0
            or not _HEX64.fullmatch(value["site_key"])
            or not _HEX64.fullmatch(value["sha256"])
            or uuid.UUID(value["item_id"]).hex != value["item_id"]
            or value["state"] not in {"active", "archive_published", "archived"}
        ):
            raise ValueError("附件紀錄識別資訊不符")
        _filename(value["filename"])
        times = [value["created_at"], value["last_used_at"]]
        archived = value.get("archived_at")
        if archived is not None:
            if not isinstance(archived, str):
                raise ValueError("封存時間格式不符")
            times.append(archived)
        elif value["state"] != "active":
            raise ValueError("封存紀錄缺少時間")
        for timestamp in times:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.utcoffset() != UTC.utcoffset(parsed):
                raise ValueError("附件紀錄時間必須是 UTC")
        return cls(
            site_key=value["site_key"], item_id=value["item_id"], filename=value["filename"],
            created_at=value["created_at"], last_used_at=value["last_used_at"],
            state=value["state"], size=value["size"], sha256=value["sha256"],
            archived_at=archived,
        )


class PendingDownload:
    """下載期間只公開半成品；publish 成功後才提供完成路徑。"""

    def __init__(
        self, store: AttachmentStore, record: ItemRecord, path: Path, stream: BinaryIO
    ) -> None:
        self.path = path
        self._store, self._record, self._stream = store, record, stream
        self._published: Path | None = None
        self._open = True
        self._writing = False
        info = os.fstat(stream.fileno())
        self._identity = (info.st_dev, info.st_ino)

    @contextmanager
    def open_writer(self) -> Iterator[BinaryIO]:
        """安全寫入下載半成品；離開 writer 後才可 publish。"""
        if not self._open or self._published is not None or self._writing:
            raise RuntimeError("下載項目目前不可寫入")
        with file_ops.open_regular_writer(self.path) as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != self._identity:
                raise OSError(errno.ESTALE, "下載半成品識別資訊已變更")
            self._writing = True
            try:
                stream.seek(0)
                stream.truncate()
                yield stream
            finally:
                self._writing = False

    def publish(self) -> Path:
        if not self._open or self._writing:
            raise RuntimeError("下載 context 已結束或 writer 尚未關閉")
        if self._published is not None:
            return self._published
        # Windows 的下載 handle 不分享刪除權；發布前先關閉，祖先仍由 context 固定。
        self._stream.close()
        target = self._store.item_path(self._record)
        size, digest = file_ops.publish_regular(self.path, target, identity=self._identity)
        self._published = target
        completed_at = _utc(self._store.clock())
        record = replace(self._record, size=size, sha256=digest,
                         created_at=completed_at, last_used_at=completed_at)
        try:
            file_ops.atomic_json(self._store.record_path(record), record.to_json())
        except OSError:
            logger.warning("附件下載完成，但紀錄發布失敗；檔案未納管並保留")
        return target


class AttachmentStore:
    """按站台及 UUID 隔離附件；只有有效紀錄所對應的檔案才是管理項目。"""

    def __init__(
        self, root: Path | None = None, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._root = Path(os.path.abspath(root)) if root is not None else None
        self.clock = clock

    @property
    def root(self) -> Path:
        """預設根延後到首次使用才解析；建構 server 不依賴本機目錄可用性。"""
        if self._root is None:
            self._root = Path(os.path.abspath(user_attachment_dir()))
        return self._root

    def item_path(self, record: ItemRecord) -> Path:
        record = ItemRecord.from_json(record.to_json())
        return self.root / record.site_key / "active" / record.item_id / record.filename

    def record_path(self, record: ItemRecord) -> Path:
        record = ItemRecord.from_json(record.to_json())
        return self.root / record.site_key / "records" / f"{record.item_id}.json"

    def lock_path(self, record: ItemRecord) -> Path:
        record = ItemRecord.from_json(record.to_json())
        return self.root / record.site_key / "locks" / f"{record.item_id}.lock"

    def load_record(self, site_key: str, item_id: str) -> ItemRecord:
        """只讀取精確 JSON 檔名；不列舉、不接受 temp 或任意路徑欄位。"""
        if not _HEX64.fullmatch(site_key) or uuid.UUID(item_id).hex != item_id:
            raise ValueError("附件識別碼不符")
        path = self.root / site_key / "records" / f"{item_id}.json"
        with file_ops.open_regular(path) as stream:
            record = ItemRecord.from_json(json.loads(file_ops.read_limited(stream, 64 * 1024)))
        if record.site_key != site_key or record.item_id != item_id:
            raise ValueError("附件紀錄與檔名不符")
        return record

    @contextmanager
    def begin_download(self, url: str, filename: str) -> Iterator[PendingDownload]:
        filename = _filename(filename)
        timestamp = _utc(self.clock())
        record = ItemRecord(
            site_key=site_storage_key(url), item_id=uuid.uuid4().hex, filename=filename,
            created_at=timestamp, last_used_at=timestamp, state="active", size=0,
            sha256=hashlib.sha256(b"").hexdigest(),
        )
        # 先驗證完整目的名稱，Windows 裝置名稱不會留下管理目錄。
        file_ops._absolute(self.item_path(record))
        for directory in (self.item_path(record).parent, self.record_path(record).parent,
                          self.lock_path(record).parent):
            file_ops.ensure_directory(directory)
        with file_ops.try_lock(self.lock_path(record)) as acquired:
            if not acquired:
                raise OSError(errno.EBUSY, "附件項目正在使用中")
            path = self.item_path(record).with_name(".download.part")
            with file_ops.create_regular(path) as stream:
                pending = PendingDownload(self, record, path, stream)
                try:
                    yield pending
                finally:
                    pending._open = False

    def _record_for(self, path: Path) -> ItemRecord | None:
        try:
            relative = path.relative_to(self.root)
            site_key, active, item_id, filename = relative.parts
            if active != "active":
                return None
            record = self.load_record(site_key, item_id)
            if filename == record.filename and record.state in {"active", "archive_published"}:
                return record
        except (OSError, ValueError):
            pass
        return None

    @contextmanager
    def borrow_source(
        self, path: Path, max_bytes: int, *, upload_root: Path | None = None,
    ) -> Iterator[bytes]:
        if max_bytes < 0:
            raise ValueError("讀取上限不得為負數")
        path = Path(os.path.abspath(path))
        record = self._record_for(path)
        if record is None:
            if (
                path.is_relative_to(self.root)
                or upload_root is None
                or path == upload_root
                or not path.is_relative_to(upload_root)
            ):
                raise ValueError("檔案不在允許的上傳路徑，請使用受管理附件或站台的附件上傳目錄")
            with file_ops.open_regular(path) as stream:
                if os.fstat(stream.fileno()).st_nlink != 1:
                    raise ValueError("上傳目錄只接受沒有其他硬連結的一般檔案")
                yield file_ops.read_limited(stream, max_bytes)
            return
        with file_ops.try_lock(self.lock_path(record)) as acquired:
            if not acquired:
                raise OSError(errno.EBUSY, "附件項目正在使用中")
            current = self.load_record(record.site_key, record.item_id)
            if (
                current.filename != path.name
                or current.state not in {"active", "archive_published"}
            ):
                raise OSError(errno.ESTALE, "附件狀態已變更")
            with file_ops.open_regular(path) as stream:
                data = file_ops.read_limited(stream, max_bytes)
                updated = replace(current, last_used_at=_utc(self.clock()))
                try:
                    file_ops.atomic_json(self.record_path(updated), updated.to_json())
                except OSError:
                    logger.warning("附件使用時間更新失敗；保留原有紀錄與檔案")
                yield data

    def sweep(self, stop: threading.Event | None = None) -> None:
        from .lifecycle import sweep

        sweep(self, stop)
