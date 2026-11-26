"""Write real legacy items using the host token and an ordinary user's default owner."""

import ctypes
import json
import sys
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from redmine_mcp.attachment_storage.store import AttachmentStore


@contextmanager
def user_owned_creations():
    # Hosted administrators may have BUILTIN\Administrators as TokenOwner. Use
    # a private impersonation token so only this thread's new files get the user SID.
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    if not adv.ImpersonateSelf(2):  # SecurityImpersonation
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        thread = wintypes.HANDLE(-2)  # GetCurrentThread pseudo-handle
        if not adv.OpenThreadToken(thread, 0x0080 | 0x0008, True, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER and TOKEN_OWNER both start with the SID pointer.
        if not adv.SetTokenInformation(token, 4, user, ctypes.sizeof(ctypes.c_void_p)):
            raise ctypes.WinError(ctypes.get_last_error())
        yield
    finally:
        if token:
            kernel.CloseHandle(token)
        if not adv.RevertToSelf():
            raise ctypes.WinError(ctypes.get_last_error())


def publish(store, filename):
    with store.begin_download("https://example.com", filename) as pending:
        with pending.open_writer() as output:
            output.write(b"from python")
        return str(pending.publish())


store = AttachmentStore(Path(sys.argv[1]))
native = publish(store, "native.txt")
with user_owned_creations():
    with store.borrow_source(Path(sys.argv[2]), 100) as data:
        borrowed = data.decode()
    ordinary = publish(store, "python.txt")
print(json.dumps({"borrowed": borrowed, "ordinary": ordinary, "native": native}))
