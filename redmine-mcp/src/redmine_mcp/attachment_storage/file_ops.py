"""附件儲存專用的安全檔案操作與非阻塞行程鎖。"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    from . import file_ops_windows as _windows


def _absolute(path: Path) -> Path:
    if ".." in path.parts:
        raise OSError(errno.EINVAL, "路徑不得包含上層目錄", str(path))
    absolute = Path(os.path.abspath(path))
    if sys.platform == "win32":
        # 本機附件儲存不接受裝置 namespace、UNC 或 NTFS alternate streams。
        if len(absolute.drive) != 2 or absolute.drive[1] != ":":
            raise OSError(errno.EINVAL, "僅接受本機磁碟路徑", str(path))
        devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        devices.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))
        for component in absolute.parts[1:]:
            stem = component.split(".", 1)[0].rstrip(" ").upper()
            if ":" in component or stem in devices or component.endswith((".", " ")):
                raise OSError(errno.EINVAL, "拒絕裝置或不明確檔名", str(path))
    return absolute


@contextmanager
def _parent(path: Path) -> Iterator[tuple[Path, int | None]]:
    """固定全部祖先；POSIX 後續操作使用父目錄 fd，不重新走字串路徑。"""
    path = _absolute(path)
    with ExitStack() as stack:
        parent_fd: int | None = None
        if sys.platform == "win32":
            for ancestor in reversed(path.parents):
                parent_fd = _windows.open_handle(
                    ancestor,
                    0x80 | 0x20,
                    3,
                    directory=True,
                    parent=parent_fd,
                )
                stack.callback(_windows.api().CloseHandle, parent_fd)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
            parent_fd = os.open(path.anchor, flags)
            stack.callback(os.close, parent_fd)
            for component in path.parts[1:-1]:
                parent_fd = os.open(component, flags, dir_fd=parent_fd)
                stack.callback(os.close, parent_fd)
        yield path, parent_fd


def _open_fd(
    path: Path,
    parent_fd: int | None,
    *,
    write: bool = False,
    create: bool = False,
    exclusive: bool = False,
    rename: bool = False,
) -> int:
    flags = os.O_RDWR if write else os.O_RDONLY
    if sys.platform == "win32":
        import msvcrt

        access = 0x80000000 | (0x40000000 if write else 0) | (0x10000 if rename else 0)
        handle = _windows.open_handle(
            path,
            access,
            1 if exclusive else (4 if create else 3),
            parent=parent_fd,
        )
        try:
            fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        except BaseException:
            _windows.api().CloseHandle(handle)
            raise
    else:
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT
        if exclusive:
            flags |= os.O_EXCL
        fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "僅接受一般檔案", str(path))
    except BaseException:
        os.close(fd)
        raise
    return fd


def _check_leaf(path: Path, parent_fd: int | None, *, allow_directory: bool = False) -> None:
    """驗證可不存在的目標，拒絕連結與特殊檔案。"""
    try:
        if sys.platform == "win32":
            handle = _windows.open_handle(
                path,
                0x80,
                3,
                directory=None if allow_directory else False,
                parent=parent_fd,
            )
            _windows.api().CloseHandle(handle)
        else:
            mode = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False).st_mode
            if not (stat.S_ISREG(mode) or (allow_directory and stat.S_ISDIR(mode))):
                raise OSError(errno.EINVAL, "檔案類型不符", str(path))
    except FileNotFoundError:
        return


def assert_managed_path(root: Path, path: Path) -> None:
    """驗證界線及現有祖先，不建立檔案；最後一節可尚不存在。"""
    root, path = _absolute(root), _absolute(path)
    if not path.is_relative_to(root):
        raise OSError(errno.EINVAL, "路徑超出管理範圍", str(path))
    with _parent(root) as (_, parent_fd):
        if sys.platform == "win32":
            handle = _windows.open_handle(root, 0x80, 3, directory=True, parent=parent_fd)
            _windows.api().CloseHandle(handle)
        else:
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(errno.EINVAL, "管理根目錄類型不符", str(root))
    if path == root:
        return
    with _parent(path) as (path, parent_fd):
        _check_leaf(path, parent_fd, allow_directory=True)


@contextmanager
def open_regular(path: Path) -> Iterator[BinaryIO]:
    """不跟隨連結開啟一般檔案，直到使用者離開 context 才釋放祖先 handle。"""
    with _parent(path) as (path, parent_fd):
        fd = _open_fd(path, parent_fd)
        with os.fdopen(fd, "rb") as stream:
            yield stream


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """最多讀取上限加一個位元組，超限則拒絕；支援短讀取的串流。"""
    if max_bytes < 0:
        raise ValueError("讀取上限不得為負數")
    chunks = bytearray()
    while len(chunks) <= max_bytes:
        chunk = stream.read(min(1024 * 1024, max_bytes + 1 - len(chunks)))
        if not chunk:
            return bytes(chunks)
        chunks.extend(chunk)
    raise ValueError("附件超過讀取上限")


@contextmanager
def try_lock(path: Path) -> Iterator[bool]:
    """取得非阻塞 OS 鎖；鎖檔保留原 inode，行程死亡由 OS 釋放鎖。"""
    with _parent(path) as (path, parent_fd):
        try:
            fd = _open_fd(path, parent_fd, write=True, create=True)
        except OSError as exc:
            if sys.platform == "win32" and exc.winerror in (32, 33):
                yield False
                return
            raise
        acquired = False
        try:
            if sys.platform == "win32":
                import msvcrt

                if not os.fstat(fd).st_size:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
            else:
                import fcntl

                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
            yield acquired
        finally:
            try:
                if acquired:
                    if sys.platform == "win32":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _replace_open_file(fd: int, temporary: Path, path: Path, parent_fd: int | None) -> None:
    if sys.platform == "win32":
        import msvcrt

        assert parent_fd is not None
        _windows.replace_open_file(msvcrt.get_osfhandle(fd), path.name, parent_fd)
    else:
        os.replace(temporary.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    """同目錄唯一暫存檔寫入並 fsync，成功後才原子替換舊紀錄。"""
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    with _parent(path) as (path, parent_fd):
        _check_leaf(path, parent_fd)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = _open_fd(temporary, parent_fd, write=True, create=True, exclusive=True, rename=True)
        replaced = False
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
                _replace_open_file(fd, temporary, path, parent_fd)
                replaced = True
        finally:
            os.close(fd)
            # Windows retains failed temporaries: a pathname cleanup after close
            # could follow an ancestor replaced with a junction into external data.
            if not replaced and sys.platform != "win32":
                try:
                    os.unlink(temporary.name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass


def ensure_directory(path: Path) -> None:
    """相對固定祖先逐層建立管理目錄，拒絕既有連結及非目錄。"""
    path = _absolute(path)
    with ExitStack() as stack:
        if sys.platform == "win32":
            parent = _windows.open_handle(Path(path.anchor), 0x80 | 0x20, 3, directory=True)
            stack.callback(_windows.api().CloseHandle, parent)
            for index in range(1, len(path.parts)):
                parent = _windows.open_handle(
                    Path(*path.parts[: index + 1]),
                    0x80 | 0x20,
                    4,
                    directory=True,
                    parent=parent,
                )
                stack.callback(_windows.api().CloseHandle, parent)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
            parent = os.open(path.anchor, flags)
            stack.callback(os.close, parent)
            for component in path.parts[1:]:
                try:
                    os.mkdir(component, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                parent = os.open(component, flags, dir_fd=parent)
                stack.callback(os.close, parent)


@contextmanager
def create_regular(path: Path) -> Iterator[BinaryIO]:
    """建立唯一下載半成品並在 context 期間固定其檔案與祖先。"""
    with _parent(path) as (path, parent_fd):
        fd = _open_fd(path, parent_fd, write=True, create=True, exclusive=True)
        with os.fdopen(fd, "r+b") as stream:
            yield stream


def publish_regular(
    temporary: Path, target: Path, *, identity: tuple[int, int] | None = None
) -> tuple[int, str]:
    """只發布同目錄的套件下載半成品；不覆寫既有項目、不清除失敗檔案。"""
    temporary, target = _absolute(temporary), _absolute(target)
    if temporary.parent != target.parent or temporary == target:
        raise OSError(errno.EINVAL, "發布必須使用同目錄的不同檔名")
    with _parent(temporary) as (temporary, parent_fd):
        fd = _open_fd(temporary, parent_fd, write=True, rename=True)
        try:
            info = os.fstat(fd)
            if identity is not None and (info.st_dev, info.st_ino) != identity:
                raise OSError(errno.ESTALE, "下載半成品識別資訊已變更")
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "rb", closefd=False) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            os.fsync(fd)
            if sys.platform == "win32":
                import msvcrt

                assert parent_fd is not None
                _windows.replace_open_file(
                    msvcrt.get_osfhandle(fd), target.name, parent_fd, replace=False
                )
            else:
                source_info = os.fstat(fd)

                def matches(name: str) -> bool:
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    return stat.S_ISREG(info.st_mode) and (
                        info.st_dev, info.st_ino
                    ) == (source_info.st_dev, source_info.st_ino)

                if not matches(temporary.name):
                    raise OSError(errno.ESTALE, "下載半成品已變更")
                os.link(
                    temporary.name,
                    target.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if not matches(target.name) or not matches(temporary.name):
                    raise OSError(errno.ESTALE, "發布項目已變更，保留現有檔案")
                os.unlink(temporary.name, dir_fd=parent_fd)
            return size, digest.hexdigest()
        finally:
            os.close(fd)


@contextmanager
def open_regular_writer(path: Path) -> Iterator[BinaryIO]:
    """相對固定祖先開啟既有一般檔案，呼叫者驗證識別資訊後才可寫入。"""
    with _parent(path) as (path, parent_fd):
        fd = _open_fd(path, parent_fd, write=True)
        with os.fdopen(fd, "r+b") as stream:
            yield stream
