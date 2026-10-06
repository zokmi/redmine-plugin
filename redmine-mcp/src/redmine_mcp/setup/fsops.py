"""參數檔的落地：原子寫入與權限收緊。

寫入採「同目錄暫存檔 + os.replace」而非直接覆寫：中途失敗或 Ctrl-C 時，
原本的參數檔（裡面是還能用的金鑰）不會被截斷成半個檔。暫存檔必須與目標
同目錄，跨磁碟的 os.replace 在 Windows 上不是原子操作。
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

#: 執行外部指令的介面：收指令與參數，回傳 exit code。測試以假函式注入。
Runner = Callable[[list[str]], int]


def write_config(path: Path, content: str) -> None:
    """以原子替換的方式寫入參數檔。

    參數:
        path: 目標路徑；父目錄不存在時會一併建立。
        content: 完整檔案內容。
    例外:
        OSError: 建立目錄、寫入暫存檔或替換失敗；此時原檔維持不變且不留暫存檔。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        # 用 BaseException 而非 OSError：docstring 明文承諾「中途失敗或 Ctrl-C 時
        # 不留暫存檔」，但 KeyboardInterrupt 繼承 BaseException、不是 OSError，
        # 若只攔 OSError，Ctrl-C 會跳過清理直接往外拋，讓暫存檔留在目錄下。
        # 清理後原樣 raise，不會吞掉任何例外，不影響原有的錯誤傳遞行為。
        tmp.unlink(missing_ok=True)
        raise


def tighten_permissions(path: Path, run: Runner) -> str | None:
    """把參數檔權限收到只有本人可讀。

    失敗不拋出：檔案此時已經寫好且可用，為了權限沒設成而讓整個安裝失敗並不划算。
    回傳原因讓精靈提醒使用者自行處理即可。

    參數:
        path: 參數檔路徑。
        run: 執行外部指令的函式（Windows 才會用到）。
    回傳:
        成功時為 None；失敗時為可直接顯示給使用者的原因字串。
    """
    if sys.platform != "win32":
        try:
            path.chmod(0o600)
        except OSError as exc:
            return f"chmod 失敗：{exc.strerror or exc}"
        return None

    user = os.environ.get("USERNAME") or ""
    if not user:
        return "找不到 USERNAME 環境變數，無法組出 icacls 指令"

    # 授權對象要帶上網域（電腦名稱），不能只給裸帳號名。實測踩過的坑：在一台電腦名稱
    # 與帳號名稱相同的機器上（whoami 為 kenny\kenny），`/grant:r kenny:(R)` 會回傳 0，
    # 但產生的 DACL 是 `KENNY\:(R)`——裸名被解析成了別的主體，結果沒有任何一條 ACE
    # 符合執行中的使用者，連本人都讀不到這個檔，server 下次啟動就讀不到參數檔。
    domain = os.environ.get("USERDOMAIN") or ""
    principal = f"{domain}\\{user}" if domain else user

    code = run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(R)"])
    if code != 0:
        return f"icacls 以 exit code {code} 結束"

    # icacls 回傳 0 不代表結果正確（上面那個坑就是「成功」地把自己鎖在門外），因此回讀
    # 驗一次。讀不到比權限沒收緊嚴重得多——後者是風險，前者是這台機器的 server 直接壞掉。
    try:
        path.read_bytes()
    except OSError as exc:
        return (
            f"收緊後反而讀不到這個檔（{exc.strerror or exc}）；"
            f"請執行 icacls \"{path}\" /grant:r \"{principal}:(R)\" 自行確認授權對象是否正確"
        )
    return None
