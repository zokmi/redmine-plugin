"""安全檔案操作的實際檔案系統驗證。"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from redmine_mcp.attachment_storage import file_ops


def test_reads_regular_file_and_closes_stream(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"safe")
    with file_ops.open_regular(source) as stream:
        assert stream.read() == b"safe"
    assert stream.closed


@pytest.mark.parametrize("extra", [b"", b"x"])
def test_limited_read_rejects_one_byte_over_limit(extra: bytes) -> None:
    stream = io.BytesIO(b"12345" + extra)
    if extra:
        with pytest.raises(ValueError):
            file_ops.read_limited(stream, 5)
        assert stream.tell() == 6
    else:
        assert file_ops.read_limited(stream, 5) == b"12345"


def test_limited_read_handles_short_chunks_and_negative_limit() -> None:
    class ShortReads(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            return super().read(min(size, 2))

    with pytest.raises(ValueError):
        file_ops.read_limited(ShortReads(b"123456"), 5)
    with pytest.raises(ValueError):
        file_ops.read_limited(io.BytesIO(), -1)


def test_managed_path_checks_boundary_and_missing_leaf(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    file_ops.assert_managed_path(root, root / "future.json")
    for outside in (tmp_path / "managed-other" / "x", root / ".." / "outside"):
        with pytest.raises(OSError):
            file_ops.assert_managed_path(root, outside)
    with pytest.raises(OSError):
        file_ops.assert_managed_path(root, root / "missing-dir" / "future.json")


@pytest.mark.parametrize("operation", ["open_regular", "try_lock", "atomic_json"])
def test_directory_is_rejected(tmp_path: Path, operation: str) -> None:
    with pytest.raises(OSError):
        if operation == "atomic_json":
            file_ops.atomic_json(tmp_path, {"x": 1})
        else:
            with getattr(file_ops, operation)(tmp_path):
                pass


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def test_rejects_linked_ancestor_for_all_operations(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "source.bin"
    source.write_bytes(b"original")
    link = tmp_path / "redirect"
    _junction(link, outside)
    path = link / "source.bin"
    with pytest.raises(OSError):
        file_ops.assert_managed_path(tmp_path, path)
    with pytest.raises(OSError):
        file_ops.assert_managed_path(link, path)
    for operation in (file_ops.open_regular, file_ops.try_lock):
        with pytest.raises(OSError), operation(path):
            pass
    with pytest.raises(OSError):
        file_ops.atomic_json(path, {"changed": True})
    assert source.read_bytes() == b"original"


def test_rejects_leaf_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"original")
    link = tmp_path / "linked"
    # Windows 開發人員模式或提升權限可建立檔案 symlink；不可略過當作通過。
    link.symlink_to(source)
    with pytest.raises(OSError):
        file_ops.assert_managed_path(tmp_path, link)
    for operation in (file_ops.open_regular, file_ops.try_lock):
        with pytest.raises(OSError), operation(link):
            pass
    with pytest.raises(OSError):
        file_ops.atomic_json(link, {"changed": True})
    assert source.read_bytes() == b"original"


def test_os_lock_contends_and_releases_after_process_death(tmp_path: Path) -> None:
    lock = tmp_path / "sweep.lock"
    ready = tmp_path / "ready"
    code = """
import sys, time
from pathlib import Path
from redmine_mcp.attachment_storage.file_ops import try_lock
with try_lock(Path(sys.argv[1])) as acquired:
    Path(sys.argv[2]).write_text(str(acquired))
    time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(lock), str(ready)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.02)
        assert ready.read_text() == "True"
        identity = lock.stat().st_ino
        with file_ops.try_lock(lock) as acquired:
            assert acquired is False
        process.terminate()
        process.wait(timeout=10)
        # Windows 行程 signaled 後的核心 handle 清理可能稍後才完成。
        deadline = time.monotonic() + 3
        while True:
            with file_ops.try_lock(lock) as acquired:
                if acquired:
                    break
            assert time.monotonic() < deadline, "OS lock survived process teardown"
            time.sleep(0.02)
        assert lock.exists()
        assert lock.stat().st_ino == identity
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def test_os_lock_releases_after_context_error(tmp_path: Path) -> None:
    path = tmp_path / "sweep.lock"
    with pytest.raises(RuntimeError), file_ops.try_lock(path) as acquired:
        assert acquired
        raise RuntimeError("caller failure")
    with file_ops.try_lock(path) as acquired:
        assert acquired


def test_atomic_json_replaces_and_removes_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"old": true}', encoding="utf-8")
    file_ops.atomic_json(path, {"name": "附件", "count": 3})
    assert json.loads(path.read_text(encoding="utf-8")) == {"name": "附件", "count": 3}
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_json_failure_before_replace_preserves_old_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "record.json"
    old = b'{"old": true}'
    path.write_bytes(old)

    def fail_fsync(fd: int) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="disk failure"):
        file_ops.atomic_json(path, {"new": True})
    assert path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native handles")
@pytest.mark.parametrize("path", ["NUL", "CON", r"\\.\pipe\attachment-safety-test"])
def test_windows_devices_and_pipe_are_rejected_without_blocking(path: str) -> None:
    with pytest.raises(OSError):
        with file_ops.open_regular(Path(path)):
            pass
    with pytest.raises(OSError):
        with file_ops.try_lock(Path(path)):
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory sharing")
def test_windows_ancestor_cannot_be_replaced_while_file_is_open(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    source = parent / "source"
    source.write_bytes(b"original")
    with file_ops.open_regular(source) as stream:
        with pytest.raises(OSError):
            parent.rename(tmp_path / "moved")
        assert stream.read() == b"original"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO and device")
def test_posix_fifo_and_device_are_rejected_without_blocking(tmp_path: Path) -> None:
    pipe = tmp_path / "fifo"
    os.mkfifo(pipe)
    for path in (pipe, Path("/dev/null")):
        with pytest.raises(OSError), file_ops.open_regular(path):
            pass
        with pytest.raises(OSError), file_ops.try_lock(path):
            pass


def test_managed_path_accepts_real_directory_leaf(tmp_path: Path) -> None:
    directory = tmp_path / "managed child"
    directory.mkdir()
    file_ops.assert_managed_path(tmp_path, directory)


def test_atomic_json_native_replace_failure_preserves_old_record(tmp_path: Path) -> None:
    path = tmp_path / "附件 紀錄.json"
    path.write_bytes(b'{"old": true}')
    if sys.platform != "win32":
        # POSIX rename 可替換開啟的檔案；改用不存在的父目錄驗證失敗。
        with pytest.raises(OSError):
            file_ops.atomic_json(tmp_path / "missing" / "record.json", {"new": True})
    else:
        with file_ops.open_regular(path), pytest.raises(OSError):
            file_ops.atomic_json(path, {"new": True})
    assert path.read_bytes() == b'{"old": true}'
    assert list(tmp_path.iterdir()) == [path]
    file_ops.atomic_json(path, {"new": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def _set_windows_junction(directory: Path, target: Path, *, remove: bool = False) -> None:
    import ctypes
    import struct
    from ctypes import wintypes

    from redmine_mcp.attachment_storage.file_ops_windows import api

    kernel = api()
    kernel.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(directory), 0x40000000, 7, None, 3, 0x02200000, None)
    assert handle != ctypes.c_void_p(-1).value
    substitute = ("\\??\\" + str(target)).encode("utf-16-le")
    printed = str(target).encode("utf-16-le")
    names = substitute + b"\0\0" + printed + b"\0\0"
    data = (
        struct.pack(
            "<IHHHHHH",
            0xA0000003,
            len(names) + 8,
            0,
            0,
            len(substitute),
            len(substitute) + 2,
            len(printed),
        )
        + names
    )
    if remove:
        data = struct.pack("<IHH", 0xA0000003, 0, 0)
    buffer = ctypes.create_string_buffer(data)
    returned = wintypes.DWORD()
    try:
        if not kernel.DeviceIoControl(
            handle,
            0x900AC if remove else 0x900A4,
            buffer,
            len(data),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows in-place junction race")
@pytest.mark.parametrize("operation", ["open_regular", "try_lock", "atomic_json"])
def test_windows_in_place_ancestor_reparse_race_never_accesses_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    parent, outside = tmp_path / "parent", tmp_path / "outside"
    parent.mkdir()
    outside.mkdir()
    victim = outside / "victim"
    victim.write_bytes(b"untouched")
    original_open = file_ops._open_fd
    changed = False

    def raced_open(path: Path, parent_fd: int | None, **kwargs: bool) -> int:
        nonlocal changed
        if not changed:
            _set_windows_junction(parent, outside)
            changed = True
        return original_open(path, parent_fd, **kwargs)

    monkeypatch.setattr(file_ops, "_open_fd", raced_open)
    try:
        # 在祖先已驗證、真正開檔前把空目錄原地改成 junction。
        # handle 相對開啟不得循 junction 接觸 outside/victim。
        with pytest.raises(OSError):
            if operation == "atomic_json":
                file_ops.atomic_json(parent / "victim", {"changed": True})
            else:
                with getattr(file_ops, operation)(parent / "victim"):
                    pass
        assert victim.read_bytes() == b"untouched"
    finally:
        if changed:
            _set_windows_junction(parent, outside, remove=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing contention")
def test_windows_lock_sharing_violation_is_contention(tmp_path: Path) -> None:
    import ctypes

    from redmine_mcp.attachment_storage.file_ops_windows import api

    path = tmp_path / "sweep.lock"
    path.write_bytes(b"\0")
    handle = api().CreateFileW(str(path), 0x80000000 | 0x40000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with file_ops.try_lock(path) as acquired:
            assert acquired is False
    finally:
        api().CloseHandle(handle)
    with file_ops.try_lock(path) as acquired:
        assert acquired is True
