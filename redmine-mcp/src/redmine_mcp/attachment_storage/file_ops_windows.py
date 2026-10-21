"""Windows 附件檔案操作：以已驗證父目錄 handle 開啟單一子名稱。"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from functools import cache
from pathlib import Path

if sys.platform == "win32":

    class _FileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root", wintypes.HANDLE),
            ("name", ctypes.POINTER(_UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security", ctypes.c_void_p),
            ("quality", ctypes.c_void_p),
        ]

    class _IoStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    @cache
    def api() -> ctypes.WinDLL:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        dll.CreateFileW.restype = wintypes.HANDLE
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        dll.CloseHandle.restype = wintypes.BOOL
        dll.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
        dll.GetFileInformationByHandle.restype = wintypes.BOOL
        dll.GetFileType.argtypes = [wintypes.HANDLE]
        dll.GetFileType.restype = wintypes.DWORD
        return dll

    @cache
    def _nt_api() -> ctypes.WinDLL:
        dll = ctypes.WinDLL("ntdll", use_last_error=True)
        dll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_ObjectAttributes),
            ctypes.POINTER(_IoStatus),
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
        ]
        dll.NtCreateFile.restype = wintypes.LONG
        dll.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatus),
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.c_int,
        ]
        dll.NtSetInformationFile.restype = wintypes.LONG
        dll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        dll.RtlNtStatusToDosError.restype = wintypes.ULONG
        return dll

    def _relative_handle(name: str, parent: int, access: int, disposition: int) -> int:
        buffer = ctypes.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        unicode_name = _UnicodeString(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
        # OBJ_DONT_REPARSE + OPEN_REPARSE_POINT：不沿用任何重新解析結果。
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            parent,
            ctypes.pointer(unicode_name),
            0x40 | 0x1000,
            None,
            None,
        )
        handle, status = wintypes.HANDLE(), _IoStatus()
        result = _nt_api().NtCreateFile(
            ctypes.byref(handle),
            access | 0x100000,
            ctypes.byref(attributes),
            ctypes.byref(status),
            None,
            0,
            0x1 | 0x2,
            {1: 2, 3: 1, 4: 3}[disposition],
            0x20 | 0x200000 | 0x4000,
            None,
            0,
        )
        if result < 0:
            raise ctypes.WinError(_nt_api().RtlNtStatusToDosError(result))
        assert handle.value is not None
        return handle.value

    def open_handle(
        path: Path,
        access: int,
        disposition: int,
        *,
        directory: bool | None = False,
        parent: int | None = None,
    ) -> int:
        if parent is None:
            # 僅磁碟根目錄使用 Win32 完整路徑；其餘名稱均相對已固定的父 handle。
            handle = api().CreateFileW(
                str(path),
                access,
                0x1 | 0x2,
                None,
                disposition,
                0x00200000 | 0x02000000,
                None,
            )
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            handle = _relative_handle(path.name, parent, access, disposition)
        try:
            info = _FileInfo()
            if not api().GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if api().GetFileType(handle) != 1 or info.attributes & 0x400:
                raise OSError("拒絕 reparse point 或非磁碟檔案")
            if directory is not None and bool(info.attributes & 0x10) != directory:
                raise OSError("檔案類型不符")
        except BaseException:
            api().CloseHandle(handle)
            raise
        return int(handle)

    def replace_open_file(handle: int, name: str, parent: int) -> None:
        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("replace", wintypes.BOOL),
                ("root", wintypes.HANDLE),
                ("length", wintypes.DWORD),
                ("name", wintypes.WCHAR * (len(name.encode("utf-16-le")) // 2 + 1)),
            ]

        info = RenameInfo(1, parent, len(name.encode("utf-16-le")), name)
        status = _IoStatus()
        result = _nt_api().NtSetInformationFile(
            handle,
            ctypes.byref(status),
            ctypes.byref(info),
            ctypes.sizeof(info),
            10,
        )
        if result < 0:
            raise ctypes.WinError(_nt_api().RtlNtStatusToDosError(result))
