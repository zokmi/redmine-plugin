"""建立自帶受保護 DACL 的 Windows 設定暫存檔。"""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    from redmine_mcp.attachment_storage.file_ops_windows import api as kernel_api


    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("descriptor", ctypes.c_void_p),
            ("inheritable", wintypes.BOOL),
        ]


    @contextmanager
    def _private_descriptor() -> Iterator[ctypes.c_void_p]:
        kernel = kernel_api()
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        advapi.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi.GetTokenInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ]

        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ctypes.WinError()
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
            if not size.value:
                raise ctypes.WinError()
            info = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, info, size, ctypes.byref(size)):
                raise ctypes.WinError()
            # TOKEN_USER 開頭是 SID_AND_ATTRIBUTES，其第一個欄位是 SID 指標。
            sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0]
            sid_string = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
                raise ctypes.WinError()
            try:
                descriptor = ctypes.c_void_p()
                sddl = f"D:P(A;;FA;;;{sid_string.value})"
                if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl, 1, ctypes.byref(descriptor), None
                ):
                    raise ctypes.WinError()
            finally:
                kernel.LocalFree(sid_string)
        finally:
            kernel.CloseHandle(token)

        try:
            yield descriptor
        finally:
            kernel.LocalFree(descriptor)


    def open_private_fd(path: Path) -> int:
        """以 CREATE_NEW 建檔，僅允許目前行程帳號存取。"""
        kernel = kernel_api()
        with _private_descriptor() as descriptor:
            attrs = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = kernel.CreateFileW(
                str(path), 0x40000000, 0, ctypes.byref(attrs), 1, 0x80, None,
            )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError()

        try:
            # FAT 等不支援持久 ACL 的檔案系統不能存放金鑰。
            volume_info = kernel.GetVolumeInformationByHandleW
            volume_info.argtypes = [
                wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
            ]
            flags = wintypes.DWORD()
            if not volume_info(handle, None, 0, None, None, ctypes.byref(flags), None, 0):
                raise ctypes.WinError()
            if not flags.value & 0x00000008:  # FILE_PERSISTENT_ACLS
                raise OSError("設定檔所在磁碟不支援 Windows ACL")
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            path.unlink(missing_ok=True)
            raise
