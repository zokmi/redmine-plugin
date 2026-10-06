"""安裝流程的編排與進度輸出。

步驟表與模式策略在 steps.py，本模組只負責「依序跑、印進度、決定 exit code」。
所有輸出經注入的 Prompts，因此 cp950 終端的編碼防線與 Rich markup 逸出都自動
沿用，測試也能用假 Prompts 逐分支驗證。
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from redmine_mcp.setup.external import CommandRunner
from redmine_mcp.setup.steps import (
    TOOL,
    ConfigStep,
    Context,
    InstallSkillsStep,
    Mode,
    PackageStep,
    RegisterStep,
    SmokeStep,
    Step,
    UvStep,
)
from redmine_mcp.setup.wizard import Prompts, Verifier

#: 流程走完。
EXIT_OK = 0
#: 使用者中途取消。
EXIT_CANCELLED = 1
#: 前置缺失且無法自動補（例如沒有 uv）。外層自動化可據此區分「環境問題」與「流程問題」。
EXIT_PREREQUISITE = 2
#: 某個步驟失敗。
EXIT_FAILED = 3


def validate_bundle(bundle: Path) -> str | None:
    """檢查解壓目錄是否完整。

    只驗一個代表性檔案：`redmine-mcp/pyproject.toml`。它不在就代表使用者沒有真的
    解壓縮（Windows 允許直接從 ZIP 預覽視窗雙擊執行，那會只把單一檔案解到
    `Temp\\Temp1_xxx\\`），這是雙擊情境最常見的失敗，而 uv 對此只會吐一行看不懂的
    路徑錯誤。

    參數:
        bundle: 解壓目錄。
    回傳:
        完整時為 None；否則為可直接顯示給使用者的原因。
    """
    if (bundle / "redmine-mcp" / "pyproject.toml").is_file():
        return None
    return (
        "這個資料夾裡找不到安裝檔（缺 redmine-mcp/pyproject.toml）。"
        "請先把下載的 ZIP 按右鍵「解壓縮全部」，再從解壓出來的資料夾雙擊 install.cmd。"
    )

#: 步驟表。順序即依賴順序：沒有 uv 就裝不了套件，沒有套件就沒有東西可註冊。
#:
#: 參數檔刻意排在註冊之前：註冊完才建參數檔的話，使用者中途放棄就會留下一個指向
#: 不存在設定的 MCP server，Claude Code 每次啟動都會紅字。
STEPS: tuple[Step, ...] = (
    UvStep(),
    PackageStep(),
    ConfigStep(),
    RegisterStep(),
    InstallSkillsStep(),
    SmokeStep(),
)

#: 進度行標題的對齊寬度。中文字在終端佔兩格，這裡取的是視覺寬度的近似值——
#: 對不齊不影響正確性，因此不引入 wcwidth 之類的相依只為了補點點。
_TITLE_WIDTH = 24


def _progress(prompts: Prompts, index: int, total: int, title: str) -> None:
    """印出一行進度的開頭（不換行的視覺效果以點點補齊）。

    參數:
        prompts: 互動管道。
        index: 目前是第幾步，從 1 起算。
        total: 總步數。
        title: 步驟名稱。
    """
    dots = "." * max(3, _TITLE_WIDTH - len(title) * 2)
    prompts.say(f"[bold][{index}/{total}][/bold] {title} {dots}")


def run_install(
    prompts: Prompts,
    *,
    config_path: Path,
    run: CommandRunner,
    verify: Verifier,
    mode: Mode = "install",
    platform: str = sys.platform,
    steps: Sequence[Step] | None = None,
    bundle: Path | None = None,
) -> int:
    """依序執行步驟表並回傳行程結束碼。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        run: 外部指令執行器。
        verify: Redmine 金鑰驗證函式。
        mode: 執行模式。
        platform: `sys.platform` 的值，供需要分平台的步驟使用。
        steps: 覆寫步驟表，供測試注入假步驟；正式執行時留空。
        bundle: ZIP 一鍵安裝的解壓目錄；None 代表走既有的 git 來源。
    回傳:
        行程結束碼，語意見本模組的 EXIT_* 常數。
    """
    表 = tuple(steps) if steps is not None else STEPS
    ctx = Context(
        prompts=prompts,
        run=run,
        verify=verify,
        config_path=config_path,
        mode=mode,
        platform=platform,
        bundle=bundle,
    )
    if mode == "uninstall":
        return _run_uninstall(ctx, 表)

    prompts.say("[bold]redmine-mcp 安裝[/bold]")
    total = len(表)
    for index, step in enumerate(表, start=1):
        _progress(prompts, index, total, step.title)
        outcome = step.check(ctx)
        if not outcome.ok:
            outcome = step.fix(ctx)
        if outcome.ok:
            prompts.say(f"      [green]✓[/green] {outcome.detail}".rstrip())
            continue
        prompts.say(f"      [red]×[/red] {outcome.detail}")
        # 停在失敗的那一步，不繼續往下跑到一個更難懂的錯誤。
        return EXIT_PREREQUISITE if step.title == UvStep.title else EXIT_FAILED

    尾段 = [
        "",
        "[bold]接下來這兩步要你自己做，安裝器無法代勞：[/bold]",
        "  1. 重開 Claude Code（MCP server 不會熱重載）",
        "  2. 輸入 /mcp 確認 redmine 顯示為 connected",
    ]
    if bundle is not None:
        # ZIP 裝的機器沒有 GitHub 憑證，`redmine-mcp install` 的 git 升級一定失敗；
        # 不在這裡講清楚，使用者下次就會撞上一個他無法處理的認證錯誤。
        尾段 += ["", "以後要升級：下載新的 ZIP，再雙擊一次 install.cmd。"]
    prompts.panel("完成", [f"參數檔：{config_path}", *尾段])
    return EXIT_OK


def _run_uninstall(ctx: Context, 表: Sequence[Step]) -> int:
    """反向跑一次步驟表，移除每一步留下的東西。

    參數:
        ctx: 執行情境。
        表: 步驟表。
    回傳:
        行程結束碼。
    """
    ctx.prompts.say("[bold]redmine-mcp 移除[/bold]")
    失敗 = False
    for step in reversed(表):
        outcome = step.remove(ctx)
        if outcome is None:
            continue
        mark = "[green]✓[/green]" if outcome.ok else "[red]×[/red]"
        ctx.prompts.say(f"{mark} {step.title}：{outcome.detail}")
        # 比對 title 而非 isinstance：正式流程用的是真的 PackageStep，測試注入的是
        # 同名的假步驟，兩者都要涵蓋，而 title 是它們唯一共有的識別。
        if not outcome.ok and step.title == PackageStep.title:
            # Python 沒辦法乾淨地移除自己所在的套件——Windows 上執行中的 .exe 被
            # 鎖住，uv tool uninstall 會失敗。拆註冊與刪參數檔（真正需要判斷的
            # 部分）此時已經做完了，剩下這一行交給使用者，不假裝做完。
            ctx.prompts.say(
                "[yellow]![/yellow] 套件本身無法由執行中的自己移除。"
                f"請關掉這個視窗後執行：uv tool uninstall {TOOL}"
            )
        失敗 = 失敗 or not outcome.ok
    return EXIT_FAILED if 失敗 else EXIT_OK
