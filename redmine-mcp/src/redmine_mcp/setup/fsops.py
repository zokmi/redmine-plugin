"""參數檔的落地：原子寫入與權限收緊。

寫入採「同目錄暫存檔 + os.replace」而非直接覆寫：中途失敗或 Ctrl-C 時，
原本的參數檔（裡面是還能用的金鑰）不會被截斷成半個檔。暫存檔必須與目標
同目錄，跨磁碟的 os.replace 在 Windows 上不是原子操作。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def write_config(path: Path, content: str) -> None:
    """以原子替換的方式寫入參數檔。

    參數:
        path: 目標路徑；父目錄不存在時會一併建立。
        content: 完整檔案內容。
    例外:
        OSError: 建立目錄、寫入暫存檔或替換失敗；此時原檔維持不變且不留暫存檔。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        from redmine_mcp.setup.fsops_windows import open_private_fd

        tmp = path.with_name(f".redmine-{uuid4().hex}.tmp")
        fd = open_private_fd(tmp)
    else:
        fd, name = tempfile.mkstemp(dir=path.parent)
        tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        # 用 BaseException 而非 OSError：docstring 明文承諾「中途失敗或 Ctrl-C 時
        # 不留暫存檔」，但 KeyboardInterrupt 繼承 BaseException、不是 OSError，
        # 若只攔 OSError，Ctrl-C 會跳過清理直接往外拋，讓暫存檔留在目錄下。
        # 清理後原樣 raise，不會吞掉任何例外，不影響原有的錯誤傳遞行為。
        tmp.unlink(missing_ok=True)
        raise
