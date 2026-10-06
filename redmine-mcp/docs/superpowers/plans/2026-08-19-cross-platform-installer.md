# 跨平台一鍵安裝器 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者以一行指令從零裝好 `redmine-mcp`（含前置套件），並提供升級、修復、重裝、移除四種後續操作，全程有分步驟的文字進度。

**Architecture:** 薄 shell bootstrap（`install.ps1`／`install.sh`）只負責「還沒有 Python 的世界」——確保 uv 存在、把套件裝上 PATH，然後交棒給新的 `redmine-mcp install` 子命令。後者以一份六步驟的步驟表搭配四種模式策略實作，所有外界相依（指令執行、終端互動、Redmine 驗證）沿用 `setup/` 既有的注入慣例，因此逐步驟逐模式都能單元測試。

**Tech Stack:** Python 3.11+、Typer、Rich、pytest、PowerShell 5.1+、bash、GitHub Actions

**Spec:** `redmine-mcp/docs/superpowers/specs/2026-08-19-cross-platform-installer-design.md`

## Global Constraints

- 工作目錄一律是 `redmine-mcp/`。指令範例中的相對路徑都以它為基準。
- API key 絕不進 argv、不進 log、不進任何例外訊息。`install` **不提供**帶入金鑰的旗標。
- 終端輸出中的金鑰一律只顯示末四碼（沿用 `toml_edit.mask_secret()`）。
- 註冊到 Claude Code 一律 `--scope user`，不提供 `--scope project` 選項。
- 套件來源字串固定為 `git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp`。
- MCP server 註冊名稱固定為 `redmine`，執行檔名固定為 `redmine-mcp`（沿用 `wizard.py` 的 `_SERVER_NAME` 與 `_TOOL`）。
- exit code 語意：`0` 成功、`1` 使用者取消、`2` 前置缺失無法自動補、`3` 步驟失敗。
- 新增或修改 helper method、欄位、公開方法時要補中文說明（專案 CLAUDE.md 規則）。
- 每個 task 結束前 `uv run pytest -q`、`uv run ruff check .`、`uv run mypy` 三項都要過。
- 測試檔沿用既有慣例：測試函式名用中文、假物件類別名用中文（見 `tests/test_setup_external.py` 的 `假執行器`、`tests/test_setup_wizard.py` 的 `腳本化提問`）。
- commit 訊息格式 `type(redmine-mcp): 主旨`，**不加單號**（本專案無 Redmine 單號體系）。

---

### Task 1: `external.py` 擴充 uv 與 Claude Code CLI 的探測與指令

**Files:**
- Modify: `src/redmine_mcp/setup/external.py`
- Test: `tests/test_setup_external.py`

**Interfaces:**
- Consumes: 既有的 `CommandRunner`、`CommandResult`
- Produces:
  - `PACKAGE_SOURCE: str`
  - `uv_version(run: CommandRunner) -> str | None`
  - `installed_version(tool: str, run: CommandRunner) -> str | None`
  - `install_package(tool: str, run: CommandRunner) -> CommandResult`
  - `upgrade_package(tool: str, run: CommandRunner) -> CommandResult`
  - `uninstall_package(tool: str, run: CommandRunner) -> CommandResult`
  - `claude_version(run: CommandRunner) -> str | None`
  - `install_claude_cli(run: CommandRunner, platform: str) -> CommandResult`

- [ ] **Step 1: 寫失敗的測試**

加到 `tests/test_setup_external.py` 檔尾（`假執行器` 已存在於檔案開頭，直接沿用）：

```python
from redmine_mcp.setup.external import (
    PACKAGE_SOURCE,
    claude_version,
    install_claude_cli,
    install_package,
    installed_version,
    uninstall_package,
    upgrade_package,
    uv_version,
)


def test_uv_版本讀得到時回傳版本字串():
    run = 假執行器(CommandResult(0, "uv 0.9.2 (abc1234 2026-08-01)\n", ""))
    assert uv_version(run) == "0.9.2"
    assert run.calls[0][0] == ["uv", "--version"]


def test_uv_不存在時回傳_None():
    run = 假執行器(CommandResult(127, "", "找不到執行檔：uv"))
    assert uv_version(run) is None


def test_已安裝版本從_uv_tool_list_解析():
    output = "redmine-mcp v0.8.0\n- redmine-mcp\nllm-wiki-mcp v0.1.0\n- llm-wiki-mcp\n"
    run = 假執行器(CommandResult(0, output, ""))
    assert installed_version("redmine-mcp", run) == "0.8.0"
    assert run.calls[0][0] == ["uv", "tool", "list"]


def test_未安裝時已安裝版本為_None():
    run = 假執行器(CommandResult(0, "llm-wiki-mcp v0.1.0\n- llm-wiki-mcp\n", ""))
    assert installed_version("redmine-mcp", run) is None


def test_名稱是前綴時不可誤判為已安裝():
    # `redmine-mcp-extra v1.0.0` 不是 `redmine-mcp`；用 startswith 會誤判。
    run = 假執行器(CommandResult(0, "redmine-mcp-extra v1.0.0\n- redmine-mcp-extra\n", ""))
    assert installed_version("redmine-mcp", run) is None


def test_安裝指令帶正確的_git_來源():
    run = 假執行器()
    install_package("redmine-mcp", run)
    assert run.calls[0][0] == [
        "uv", "tool", "install", "--from", PACKAGE_SOURCE, "redmine-mcp",
    ]


def test_來源字串含子目錄片段():
    # 少了 #subdirectory= 會裝到 repo 根目錄而失敗，這是文件警告過的踩雷點。
    assert PACKAGE_SOURCE.endswith("#subdirectory=redmine-mcp")


def test_升級與移除指令():
    run = 假執行器(CommandResult(0, "", ""), CommandResult(0, "", ""))
    upgrade_package("redmine-mcp", run)
    uninstall_package("redmine-mcp", run)
    assert run.calls[0][0] == ["uv", "tool", "upgrade", "redmine-mcp"]
    assert run.calls[1][0] == ["uv", "tool", "uninstall", "redmine-mcp"]


def test_claude_版本讀得到時回傳版本字串():
    run = 假執行器(CommandResult(0, "2.1.4 (Claude Code)\n", ""))
    assert claude_version(run) == "2.1.4"


def test_claude_不存在時回傳_None():
    run = 假執行器(CommandResult(127, "", "找不到執行檔：claude"))
    assert claude_version(run) is None


def test_安裝_claude_在_windows_走_powershell_端點():
    run = 假執行器()
    install_claude_cli(run, "win32")
    argv = run.calls[0][0]
    assert argv[0] == "powershell"
    assert "-ExecutionPolicy" in argv and "Bypass" in argv
    assert "https://claude.ai/install.ps1" in argv[-1]


def test_安裝_claude_在_posix_走_shell_端點():
    run = 假執行器()
    install_claude_cli(run, "linux")
    argv = run.calls[0][0]
    assert argv[0] == "sh"
    assert "https://claude.ai/install.sh" in argv[-1]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_setup_external.py -q`
Expected: FAIL，`ImportError: cannot import name 'PACKAGE_SOURCE'`

- [ ] **Step 3: 實作**

在 `src/redmine_mcp/setup/external.py` 的 import 區加入 `import re`，並在檔尾加入：

```python
#: 以 uv 安裝本套件時的來源字串。
#: `#subdirectory=` 不可省略——套件位在 repo 的子目錄，少了它會裝到根目錄而失敗。
PACKAGE_SOURCE = "git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp"

#: 從版本輸出中抓出第一組形如 1.2.3 的數字。uv 與 claude 的 --version 各自
#: 還會附上 commit hash 或產品名，整行印出來太吵，只取版本號。
_VERSION_PATTERN = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _first_version(result: CommandResult) -> str | None:
    """從指令結果中取出版本號；指令失敗或抓不到數字時回傳 None。

    參數:
        result: 指令執行結果。
    回傳:
        版本字串（例如 `0.9.2`）；不可用時為 None。
    """
    if result.code != 0:
        return None
    match = _VERSION_PATTERN.search(f"{result.stdout}\n{result.stderr}")
    return match.group(0) if match else None


def uv_version(run: CommandRunner) -> str | None:
    """探測 uv 是否可用。

    參數:
        run: 指令執行器。
    回傳:
        uv 的版本號；找不到執行檔或執行失敗時為 None。
    """
    return _first_version(run(["uv", "--version"]))


def installed_version(tool: str, run: CommandRunner) -> str | None:
    """從 `uv tool list` 讀出該工具目前安裝的版本。

    比對整個名稱而非前綴：`uv tool list` 中 `redmine-mcp-extra v1.0.0` 這種項目
    若用 startswith 判斷，會讓「未安裝」被誤判成「已安裝」，後續步驟就不會補裝。

    參數:
        tool: 工具名稱。
        run: 指令執行器。
    回傳:
        版本號；未安裝或指令失敗時為 None。
    """
    result = run(["uv", "tool", "list"])
    if result.code != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        # 工具列的格式是 `<名稱> v<版本>`，其下再以 `- <執行檔>` 縮排列出腳本。
        if len(parts) == 2 and parts[0] == tool and parts[1].startswith("v"):
            return parts[1][1:]
    return None


def install_package(tool: str, run: CommandRunner) -> CommandResult:
    """以 uv 從 git 安裝本套件。

    參數:
        tool: 工具名稱。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["uv", "tool", "install", "--from", PACKAGE_SOURCE, tool])


def upgrade_package(tool: str, run: CommandRunner) -> CommandResult:
    """把已安裝的套件升級到來源的最新版。

    參數:
        tool: 工具名稱。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["uv", "tool", "upgrade", tool])


def uninstall_package(tool: str, run: CommandRunner) -> CommandResult:
    """移除已安裝的套件。

    參數:
        tool: 工具名稱。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["uv", "tool", "uninstall", tool])


def claude_version(run: CommandRunner) -> str | None:
    """探測 Claude Code CLI 是否可用。

    參數:
        run: 指令執行器。
    回傳:
        版本號；找不到執行檔時為 None。
    """
    return _first_version(run(["claude", "--version"]))


def install_claude_cli(run: CommandRunner, platform: str) -> CommandResult:
    """跑 Claude Code 的官方安裝端點。

    兩個端點都是公開的（2026-08-19 實測皆回 200），與本 repo 的 private 狀態無關。

    參數:
        run: 指令執行器。
        platform: `sys.platform` 的值；win32 走 PowerShell 端點，其餘走 sh 端點。
    回傳:
        執行結果。
    """
    if platform == "win32":
        return run([
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "irm https://claude.ai/install.ps1 | iex",
        ])
    return run(["sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"])
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_setup_external.py -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/external.py tests/test_setup_external.py
git commit -m "feat(redmine-mcp): external 層新增 uv 與 Claude Code CLI 的探測與指令"
```

---

### Task 2: 步驟骨架與前兩個步驟（uv、套件）

**Files:**
- Create: `src/redmine_mcp/setup/steps.py`
- Test: `tests/test_setup_steps.py`

**Interfaces:**
- Consumes: Task 1 的 `uv_version`／`installed_version`／`install_package`／`upgrade_package`／`uninstall_package`、既有的 `Prompts`（`wizard.py`）、`Verifier`（`wizard.py`）、`CommandRunner`（`external.py`）
- Produces:
  - `Mode`（`Literal["install", "repair", "reinstall", "uninstall"]`）
  - `StepOutcome(ok: bool, detail: str, skipped: bool = False)`
  - `Context(prompts, run, verify, config_path, mode, platform)`
  - `Step` 基底類別，含 `title: str`、`check(ctx)`、`fix(ctx)`、`remove(ctx) -> StepOutcome | None`
  - `UvStep`、`PackageStep`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_setup_steps.py`：

```python
"""安裝步驟的測試：每個步驟以假 CommandRunner 與假 Prompts 驗證判斷與動作。"""
from pathlib import Path

import pytest

from redmine_mcp.setup.external import CommandResult
from redmine_mcp.setup.steps import Context, PackageStep, UvStep
from redmine_mcp.setup.wizard import Prompts


class 假執行器:
    """依序回傳預先安排的結果並記錄呼叫。"""

    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], stdin: str | None = None) -> CommandResult:
        self.calls.append(argv)
        return self.results.pop(0) if self.results else CommandResult(0, "", "")


def 靜默提問(confirms: list[bool] | None = None) -> tuple[Prompts, list[str]]:
    """建立只收集輸出、確認一律照給定序列回答的 Prompts。"""
    said: list[str] = []
    答案 = list(confirms or [])
    return (
        Prompts(
            say=said.append,
            panel=lambda title, lines: said.extend([title, *lines]),
            ask=lambda label, default=None: "",
            ask_secret=lambda label: "",
            confirm=lambda label: 答案.pop(0) if 答案 else True,
            choose=lambda label, options: 0,
        ),
        said,
    )


def 建立情境(run, *, mode="install", config_path=Path("config.toml")) -> Context:
    prompts, _ = 靜默提問()

    async def 不會被呼叫的驗證(url: str, api_key: str) -> str:
        raise AssertionError("這個測試不該打 Redmine")

    return Context(
        prompts=prompts,
        run=run,
        verify=不會被呼叫的驗證,
        config_path=config_path,
        mode=mode,
        platform="linux",
    )


def test_uv_可用時檢查通過且帶出版本():
    run = 假執行器(CommandResult(0, "uv 0.9.2\n", ""))
    outcome = UvStep().check(建立情境(run))
    assert outcome.ok is True
    assert "0.9.2" in outcome.detail


def test_uv_不可用時檢查失敗且修復只給指引():
    run = 假執行器(CommandResult(127, "", "找不到執行檔：uv"))
    ctx = 建立情境(run)
    assert UvStep().check(ctx).ok is False
    outcome = UvStep().fix(ctx)
    assert outcome.ok is False
    assert "astral.sh" in outcome.detail


def test_套件已安裝時預設模式做升級():
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # check
        CommandResult(0, "", ""),                                     # upgrade
        CommandResult(0, "redmine-mcp v0.9.0\n- redmine-mcp\n", ""),  # 升級後回讀
    )
    ctx = 建立情境(run)
    step = PackageStep()
    assert step.check(ctx).ok is True
    outcome = step.fix(ctx)
    assert outcome.ok is True
    assert run.calls[1] == ["uv", "tool", "upgrade", "redmine-mcp"]
    assert "0.9.0" in outcome.detail


def test_套件未安裝時做全新安裝():
    run = 假執行器(
        CommandResult(0, "", ""),                                     # check：清單是空的
        CommandResult(0, "", ""),                                     # install
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # 安裝後回讀
    )
    ctx = 建立情境(run)
    step = PackageStep()
    assert step.check(ctx).ok is False
    assert step.fix(ctx).ok is True
    assert run.calls[1][:3] == ["uv", "tool", "install"]


def test_reinstall_模式先移除再安裝():
    run = 假執行器(
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # check
        CommandResult(0, "", ""),                                     # uninstall
        CommandResult(0, "", ""),                                     # install
        CommandResult(0, "redmine-mcp v0.8.0\n- redmine-mcp\n", ""),  # 回讀
    )
    ctx = 建立情境(run, mode="reinstall")
    step = PackageStep()
    # reinstall 模式下即使已安裝也要視為「需要處理」，否則 fix 不會被呼叫。
    assert step.check(ctx).ok is False
    assert step.fix(ctx).ok is True
    assert run.calls[1] == ["uv", "tool", "uninstall", "redmine-mcp"]
    assert run.calls[2][:3] == ["uv", "tool", "install"]


def test_安裝失敗時把_stderr_帶進結果供排錯():
    run = 假執行器(
        CommandResult(0, "", ""),
        CommandResult(1, "", "error: Failed to resolve git reference"),
    )
    ctx = 建立情境(run)
    outcome = PackageStep().fix(ctx)
    assert outcome.ok is False
    assert "git reference" in outcome.detail
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_setup_steps.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.steps'`

- [ ] **Step 3: 實作**

建立 `src/redmine_mcp/setup/steps.py`：

```python
"""安裝流程的步驟表。

每個步驟是一個具備 `check()` 與 `fix()` 的物件，外界相依全部經由 Context 注入，
因此逐步驟逐模式都能在不開子行程、不打真實 API 的前提下測試。

四種模式（安裝／修復／重裝／移除）共用同一份步驟表，差別只在各步驟怎麼讀
`ctx.mode`——新增一步只改一個地方，四種模式自動涵蓋。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from redmine_mcp.setup.external import (
    CommandRunner,
    install_package,
    installed_version,
    uninstall_package,
    upgrade_package,
    uv_version,
)
from redmine_mcp.setup.wizard import Prompts, Verifier

#: 安裝器的四種模式。
Mode = Literal["install", "repair", "reinstall", "uninstall"]

#: 要操作的工具名稱，與 wizard.py 的 _TOOL 一致。
TOOL = "redmine-mcp"


@dataclass(frozen=True)
class StepOutcome:
    """一個步驟執行後的結果。

    欄位:
        ok: 這一步最終是否處於正常狀態。
        detail: 附在進度行尾端的短說明（版本號、scope、或失敗原因）。
        skipped: 檢查就已通過、未動任何東西時為 True，供進度輸出區分「本來就好」
            與「剛剛修好」。
    """

    ok: bool
    detail: str = ""
    skipped: bool = False


@dataclass(frozen=True)
class Context:
    """步驟執行時可用的全部外界相依。

    欄位:
        prompts: 與使用者互動的管道。
        run: 外部指令執行器。
        verify: Redmine 金鑰驗證函式。
        config_path: 參數檔路徑。
        mode: 本次執行的模式。
        platform: `sys.platform` 的值，供需要分平台的步驟使用。
    """

    prompts: Prompts
    run: CommandRunner
    verify: Verifier
    config_path: Path
    mode: Mode
    platform: str


class Step:
    """步驟的基底類別。

    子類別覆寫 `title`、`check()` 與 `fix()`；有東西可移除的步驟另外覆寫
    `remove()`，其餘沿用這裡回傳 None 的預設（代表 `--uninstall` 時跳過）。
    """

    title = ""

    def check(self, ctx: Context) -> StepOutcome:
        """檢查這一步是否已處於正常狀態。

        參數:
            ctx: 執行情境。
        回傳:
            `ok=True` 代表不需要動作。
        """
        raise NotImplementedError

    def fix(self, ctx: Context) -> StepOutcome:
        """把這一步修到正常狀態。

        參數:
            ctx: 執行情境。
        回傳:
            修復結果。
        """
        raise NotImplementedError

    def remove(self, ctx: Context) -> StepOutcome | None:
        """`--uninstall` 時的反向操作。

        參數:
            ctx: 執行情境。
        回傳:
            移除結果；這一步沒有可移除的東西時為 None。
        """
        return None


class UvStep(Step):
    """確認 uv 可用。

    正常情況下 shell bootstrap 已經處理過，走到這裡代表使用者是直接呼叫
    `redmine-mcp install` 而環境又缺 uv——這種情況沒辦法自動補（沒有 uv 就沒有
    安裝套件的手段），只能給出官方安裝端點讓使用者自己處理。
    """

    title = "uv 可用"

    def check(self, ctx: Context) -> StepOutcome:
        version = uv_version(ctx.run)
        if version is None:
            return StepOutcome(False, "找不到 uv")
        return StepOutcome(True, version, skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        return StepOutcome(
            False,
            "請先安裝 uv：Windows 用 winget install astral-sh.uv，"
            "其他平台用 curl -LsSf https://astral.sh/uv/install.sh | sh",
        )


class PackageStep(Step):
    """確認套件已安裝且為最新。

    `check()` 在 reinstall 模式下一律回報「需要處理」，否則已安裝的機器不會走進
    `fix()`，`--reinstall` 就等於什麼都沒做。
    """

    title = f"安裝 {TOOL}"

    def check(self, ctx: Context) -> StepOutcome:
        version = installed_version(TOOL, ctx.run)
        if version is None:
            return StepOutcome(False, "尚未安裝")
        if ctx.mode == "reinstall":
            return StepOutcome(False, f"目前 {version}，將重新安裝")
        if ctx.mode == "install":
            # 預設模式下重跑就是升級，因此已安裝也要進 fix() 做一次 upgrade。
            return StepOutcome(False, f"目前 {version}，檢查是否有更新")
        return StepOutcome(True, version, skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        before = installed_version(TOOL, ctx.run)
        if ctx.mode == "reinstall" and before is not None:
            uninstall_package(TOOL, ctx.run)
            before = None
        result = upgrade_package(TOOL, ctx.run) if before else install_package(TOOL, ctx.run)
        if result.code != 0:
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        after = installed_version(TOOL, ctx.run)
        return StepOutcome(True, after or "已安裝")

    def remove(self, ctx: Context) -> StepOutcome | None:
        result = uninstall_package(TOOL, ctx.run)
        if result.code != 0:
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        return StepOutcome(True, "已移除")
```

注意 `PackageStep.fix()` 的 `before` 是重新讀一次而非沿用 `check()` 的結果：`fix()` 必須能被單獨呼叫（測試就是這樣用的），不能假設 `check()` 剛剛跑過。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_setup_steps.py -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/steps.py tests/test_setup_steps.py
git commit -m "feat(redmine-mcp): 新增安裝步驟骨架與 uv、套件兩個步驟"
```

---

### Task 3: Claude Code CLI 與參數檔兩個步驟

**Files:**
- Modify: `src/redmine_mcp/setup/steps.py`
- Test: `tests/test_setup_steps.py`

**Interfaces:**
- Consumes: Task 2 的 `Step`／`Context`／`StepOutcome`、Task 1 的 `claude_version`／`install_claude_cli`、既有的 `run_setup`（`wizard.py`）、`load_config_file`（`config_file.py`）、`load_settings`（`config.py`）
- Produces: `ClaudeCliStep`、`ConfigStep`

- [ ] **Step 1: 寫失敗的測試**

加到 `tests/test_setup_steps.py` 檔尾：

```python
from redmine_mcp.setup.steps import ClaudeCliStep, ConfigStep

_可用參數檔 = """
[sites.main]
url = "https://redmine.example.com/redmine"
api_key = "secret-key-1234"
"""


def test_claude_已安裝時檢查通過():
    run = 假執行器(CommandResult(0, "2.1.4 (Claude Code)\n", ""))
    outcome = ClaudeCliStep().check(建立情境(run))
    assert outcome.ok is True
    assert "2.1.4" in outcome.detail


def test_claude_未安裝且使用者同意時跑官方端點():
    run = 假執行器(
        CommandResult(0, "", ""),                       # 安裝端點
        CommandResult(0, "2.1.4 (Claude Code)\n", ""),  # 安裝後回讀
    )
    prompts, said = 靜默提問([True])
    ctx = 建立情境(run)
    outcome = ClaudeCliStep().fix(
        Context(
            prompts=prompts,
            run=run,
            verify=ctx.verify,
            config_path=ctx.config_path,
            mode="install",
            platform="linux",
        )
    )
    assert outcome.ok is True
    assert run.calls[0][0] == "sh"


def test_claude_未安裝且使用者拒絕時不動系統():
    run = 假執行器()
    prompts, said = 靜默提問([False])
    ctx = 建立情境(run)
    outcome = ClaudeCliStep().fix(
        Context(
            prompts=prompts,
            run=run,
            verify=ctx.verify,
            config_path=ctx.config_path,
            mode="install",
            platform="linux",
        )
    )
    assert outcome.ok is False
    assert run.calls == []
    assert any("claude mcp add" in line for line in said)


def test_參數檔可用時檢查通過(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=path))
    assert outcome.ok is True
    assert "main" in outcome.detail


def test_參數檔不存在時檢查失敗(tmp_path):
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=tmp_path / "無.toml"))
    assert outcome.ok is False


def test_參數檔缺必填欄位時檢查失敗(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[sites.main]\nurl = "https://x/redmine"\n', encoding="utf-8")
    outcome = ConfigStep().check(建立情境(假執行器(), config_path=path))
    assert outcome.ok is False
    assert "api_key" in outcome.detail


def test_repair_模式會實際驗一次金鑰(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    驗過的: list[tuple[str, str]] = []

    async def 假驗證(url: str, api_key: str) -> str:
        驗過的.append((url, api_key))
        return "kenny"

    prompts, _ = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=假驗證,
        config_path=path,
        mode="repair",
        platform="linux",
    )
    outcome = ConfigStep().check(ctx)
    assert outcome.ok is True
    assert 驗過的 == [("https://redmine.example.com/redmine", "secret-key-1234")]


def test_repair_模式金鑰驗不過時檢查失敗且訊息不含金鑰(tmp_path):
    from redmine_mcp.errors import RedmineError

    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")

    async def 假驗證(url: str, api_key: str) -> str:
        raise RedmineError("401 認證失敗")

    prompts, _ = 靜默提問()
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=假驗證,
        config_path=path,
        mode="repair",
        platform="linux",
    )
    outcome = ConfigStep().check(ctx)
    assert outcome.ok is False
    assert "401" in outcome.detail
    assert "secret-key-1234" not in outcome.detail


def test_參數檔步驟的移除會刪檔但先問過(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    prompts, _ = 靜默提問([True])
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=建立情境(假執行器()).verify,
        config_path=path,
        mode="uninstall",
        platform="linux",
    )
    outcome = ConfigStep().remove(ctx)
    assert outcome is not None and outcome.ok is True
    assert not path.exists()


def test_參數檔步驟的移除在使用者拒絕時保留檔案(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_可用參數檔, encoding="utf-8")
    prompts, _ = 靜默提問([False])
    ctx = Context(
        prompts=prompts,
        run=假執行器(),
        verify=建立情境(假執行器()).verify,
        config_path=path,
        mode="uninstall",
        platform="linux",
    )
    outcome = ConfigStep().remove(ctx)
    assert outcome is not None and outcome.ok is True
    assert path.exists()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_setup_steps.py -q`
Expected: FAIL，`ImportError: cannot import name 'ClaudeCliStep'`

- [ ] **Step 3: 實作**

在 `src/redmine_mcp/setup/steps.py` 的 import 區加入：

```python
import asyncio

from redmine_mcp.config import ConfigError, load_settings
from redmine_mcp.config_file import load_config_file
from redmine_mcp.errors import RedmineError
from redmine_mcp.setup.external import claude_version, install_claude_cli
from redmine_mcp.setup.wizard import run_setup
```

並在檔尾加入：

```python
class ClaudeCliStep(Step):
    """確認 Claude Code CLI 可用。

    刻意不放在 shell bootstrap：它不是安裝套件的前提，放在這裡才問得出「要不要
    幫你裝」並印得出與其他步驟一致的進度。
    """

    title = "Claude Code CLI"

    def check(self, ctx: Context) -> StepOutcome:
        version = claude_version(ctx.run)
        if version is None:
            return StepOutcome(False, "找不到 claude")
        return StepOutcome(True, version, skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        ctx.prompts.say("找不到 Claude Code CLI。它是把本 server 註冊上去的必要工具。")
        if not ctx.prompts.confirm("要現在安裝 Claude Code 嗎？"):
            ctx.prompts.say(
                "跳過。裝好之後請自行執行："
                "claude mcp add redmine --scope user -- redmine-mcp"
            )
            return StepOutcome(False, "使用者選擇跳過")
        result = install_claude_cli(ctx.run, ctx.platform)
        if result.code != 0:
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        version = claude_version(ctx.run)
        if version is None:
            return StepOutcome(
                False, "安裝跑完了但仍找不到 claude；請開一個新的終端機讓 PATH 更新後重跑"
            )
        return StepOutcome(True, version)


class ConfigStep(Step):
    """確認參數檔可用；不可用時交給既有的安裝精靈。

    這一步不重寫精靈，只是把它當成一個步驟嵌進來——`run_setup()` 已經處理好
    多站台、舊格式轉換、金鑰驗證、原子寫入與權限收緊。
    """

    title = "參數檔"

    def check(self, ctx: Context) -> StepOutcome:
        if not ctx.config_path.is_file():
            return StepOutcome(False, "尚未建立")
        try:
            settings = load_settings(load_config_file(ctx.config_path))
        except ConfigError as exc:
            return StepOutcome(False, str(exc))
        names = "、".join(settings.sites)
        if ctx.mode != "repair":
            return StepOutcome(True, names, skipped=True)
        # repair 的用途就是回答「為什麼壞了」，而金鑰過期或網址被改正是最常見的
        # 成因之一；不驗等於少檢查一個最可能的原因。訊息一律不含金鑰內容。
        for site in settings.sites.values():
            try:
                asyncio.run(ctx.verify(site.url, site.api_key))
            except RedmineError as exc:
                return StepOutcome(False, f"站台 {site.name} 驗證失敗：{exc}")
        return StepOutcome(True, f"{names}（金鑰皆驗證通過）", skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        code = run_setup(
            ctx.prompts, config_path=ctx.config_path, run=ctx.run, verify=ctx.verify
        )
        if code != 0:
            return StepOutcome(False, "精靈未完成")
        return StepOutcome(True, "已設定")

    def remove(self, ctx: Context) -> StepOutcome | None:
        if not ctx.config_path.is_file():
            return StepOutcome(True, "本來就不存在")
        ctx.prompts.say(f"參數檔含 API 金鑰：{ctx.config_path}")
        if not ctx.prompts.confirm("要一併刪除嗎？"):
            return StepOutcome(True, "保留")
        ctx.config_path.unlink()
        return StepOutcome(True, "已刪除")
```

`run_setup()` 內部會自己呼叫 `_do_register()` 與冒煙測試，因此步驟 5、6 在剛跑完精靈的情況下會直接檢查通過而跳過——這是預期行為，不是重複工作。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_setup_steps.py -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/steps.py tests/test_setup_steps.py
git commit -m "feat(redmine-mcp): 新增 Claude Code CLI 與參數檔兩個安裝步驟"
```

---

### Task 4: 註冊與啟動測試兩個步驟，並補上啟動訊息的回歸測試

**Files:**
- Modify: `src/redmine_mcp/setup/steps.py`
- Modify: `src/redmine_mcp/__main__.py`
- Modify: `src/redmine_mcp/setup/external.py`
- Test: `tests/test_setup_steps.py`
- Test: `tests/test_setup_external.py`

**Interfaces:**
- Consumes: Task 2 的 `Step`／`Context`／`StepOutcome`、既有的 `is_registered`／`register`／`unregister`／`smoke_test`／`resolve_executable`
- Produces: `RegisterStep`、`SmokeStep`；`__main__.STARTED_MESSAGE`

- [ ] **Step 1: 寫失敗的測試**

加到 `tests/test_setup_steps.py` 檔尾：

```python
from redmine_mcp.setup.steps import RegisterStep, SmokeStep


def test_已註冊時檢查通過():
    run = 假執行器(CommandResult(0, "redmine:\n  Type: stdio\n", ""))
    outcome = RegisterStep().check(建立情境(run))
    assert outcome.ok is True


def test_未註冊時修復會用_user_scope_註冊():
    run = 假執行器(CommandResult(0, "", ""))
    prompts, _ = 靜默提問([True])
    ctx = 建立情境(run)
    outcome = RegisterStep().fix(
        Context(
            prompts=prompts,
            run=run,
            verify=ctx.verify,
            config_path=ctx.config_path,
            mode="install",
            platform="linux",
        )
    )
    assert outcome.ok is True
    assert run.calls[0] == [
        "claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp",
    ]


def test_註冊步驟的移除會拆掉註冊():
    run = 假執行器(CommandResult(0, "", ""))
    outcome = RegisterStep().remove(建立情境(run, mode="uninstall"))
    assert outcome is not None and outcome.ok is True
    assert run.calls[0] == ["claude", "mcp", "remove", "redmine", "--scope", "user"]


def test_啟動測試通過時檢查成功():
    run = 假執行器(CommandResult(0, "", "INFO Redmine MCP server 啟動，站台：main\n"))
    outcome = SmokeStep().check(建立情境(run))
    assert outcome.ok is True


def test_啟動測試失敗時給出排錯指引():
    run = 假執行器(CommandResult(1, "", "設定錯誤：缺少必填設定 api_key\n"))
    ctx = 建立情境(run)
    assert SmokeStep().check(ctx).ok is False
    outcome = SmokeStep().fix(ctx)
    assert outcome.ok is False
    assert "SETUP.md" in outcome.detail
```

加到 `tests/test_setup_external.py` 檔尾：

```python
def test_冒煙測試比對的字串確實出現在啟動訊息裡():
    # smoke_test() 靠比對啟動 log 中的關鍵字判定成功。改掉 __main__ 那行訊息會讓
    # 它靜默退化成一律回報失敗，而其餘測試全部餵假字串，不會示警——這條斷言就是
    # 把那條隱性耦合釘住。
    from redmine_mcp.__main__ import STARTED_MESSAGE
    from redmine_mcp.setup.external import _STARTED_MARKER

    assert _STARTED_MARKER in STARTED_MESSAGE
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_setup_steps.py tests/test_setup_external.py -q`
Expected: FAIL，`ImportError: cannot import name 'RegisterStep'` 與 `cannot import name 'STARTED_MESSAGE'`

- [ ] **Step 3: 實作**

在 `src/redmine_mcp/__main__.py` 的 import 之後、`main()` 之前加入常數，並改用它：

```python
#: server 啟動成功時的 log 訊息樣板。抽成模組層級常數是為了讓
#: setup/external.py 的 _STARTED_MARKER 能被測試釘住——冒煙測試靠比對這行文字
#: 判定 server 是否起得來，改掉措辭而沒同步 marker 會讓判定靜默退化成一律失敗。
STARTED_MESSAGE = "Redmine MCP server 啟動，站台：%s"
```

把 `main()` 裡原本的 `logging.info("Redmine MCP server 啟動，站台：%s", ...)` 連同它上方那段「注意：這行文字是 setup/external.py 的 _STARTED_MARKER…」的註解一起換成：

```python
    logging.info(STARTED_MESSAGE, "、".join(sites.names))
```

原註解的警告內容已移到 `STARTED_MESSAGE` 的說明上，不要兩邊各留一份。

在 `src/redmine_mcp/setup/steps.py` 的 import 區加入：

```python
from redmine_mcp.setup.external import (
    is_registered,
    register,
    resolve_executable,
    smoke_test,
    unregister,
)
```

並在檔尾加入：

```python
#: 註冊到 Claude Code 時使用的 server 名稱，與 wizard.py 的 _SERVER_NAME 一致。
SERVER_NAME = "redmine"


class RegisterStep(Step):
    """確認 server 已註冊到 Claude Code。

    scope 固定 user：project scope 會寫進進版控的 .mcp.json，SETUP.md 明文禁止，
    因此不開放成選項。
    """

    title = "註冊到 Claude Code"

    def check(self, ctx: Context) -> StepOutcome:
        if not is_registered(SERVER_NAME, ctx.run):
            return StepOutcome(False, "尚未註冊")
        return StepOutcome(True, "scope: user", skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        command = resolve_executable(TOOL)
        manual = f"claude mcp add {SERVER_NAME} --scope user -- {command}"
        if not ctx.prompts.confirm(f"要執行「{manual}」嗎？"):
            ctx.prompts.say(f"跳過註冊。需要時請自行執行：{manual}")
            return StepOutcome(False, "使用者選擇跳過")
        result = register(SERVER_NAME, command, ctx.run)
        if result.code != 0:
            ctx.prompts.say(f"請自行執行：{manual}")
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        return StepOutcome(True, "scope: user")

    def remove(self, ctx: Context) -> StepOutcome | None:
        result = unregister(SERVER_NAME, ctx.run)
        if result.code != 0:
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        return StepOutcome(True, "已拆除")


class SmokeStep(Step):
    """跑一次 stdio 啟動測試，確認整套裝好之後真的起得來。

    這一步沒有自動修復手段：起不來的原因五花八門（參數檔、網路、相依），能做的
    是把 stderr 原樣帶出來並指向排錯表。
    """

    title = "啟動測試"

    def check(self, ctx: Context) -> StepOutcome:
        ok, message = smoke_test(resolve_executable(TOOL), ctx.run)
        return StepOutcome(ok, message if not ok else "", skipped=ok)

    def fix(self, ctx: Context) -> StepOutcome:
        _, message = smoke_test(resolve_executable(TOOL), ctx.run)
        return StepOutcome(False, f"{message}；請對照 SETUP.md 的排錯表處理後重跑")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS（整套跑，因為改動了 `__main__.py`）

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/steps.py src/redmine_mcp/__main__.py tests/test_setup_steps.py tests/test_setup_external.py
git commit -m "feat(redmine-mcp): 新增註冊與啟動測試步驟，並釘住啟動訊息與冒煙判定的耦合"
```

---

### Task 5: `installer.py` 流程編排、進度輸出與 exit code

**Files:**
- Create: `src/redmine_mcp/setup/installer.py`
- Test: `tests/test_setup_installer.py`

**Interfaces:**
- Consumes: Task 2–4 的六個步驟類別、`Context`、`StepOutcome`、`Mode`
- Produces:
  - `STEPS: tuple[Step, ...]`
  - `EXIT_OK = 0`、`EXIT_CANCELLED = 1`、`EXIT_PREREQUISITE = 2`、`EXIT_FAILED = 3`
  - `run_install(prompts: Prompts, *, config_path: Path, run: CommandRunner, verify: Verifier, mode: Mode = "install", platform: str = sys.platform, steps: Sequence[Step] | None = None) -> int`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_setup_installer.py`：

```python
"""安裝流程編排的測試：以假步驟驗證進度輸出、中止行為與 exit code。"""
from pathlib import Path

from redmine_mcp.setup.installer import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PREREQUISITE,
    run_install,
)
from redmine_mcp.setup.steps import Context, Step, StepOutcome, UvStep
from redmine_mcp.setup.wizard import Prompts


class 假步驟(Step):
    """依預設答案回應 check／fix，並記錄被呼叫的順序。"""

    def __init__(self, title: str, *, 檢查通過: bool, 修復通過: bool = True) -> None:
        self.title = title
        self._檢查通過 = 檢查通過
        self._修復通過 = 修復通過
        self.紀錄: list[str] = []

    def check(self, ctx: Context) -> StepOutcome:
        self.紀錄.append("check")
        return StepOutcome(self._檢查通過, "ok" if self._檢查通過 else "壞了")

    def fix(self, ctx: Context) -> StepOutcome:
        self.紀錄.append("fix")
        return StepOutcome(self._修復通過, "修好了" if self._修復通過 else "修不好")


def 建立提問() -> tuple[Prompts, list[str]]:
    said: list[str] = []
    return (
        Prompts(
            say=said.append,
            panel=lambda title, lines: said.extend([title, *lines]),
            ask=lambda label, default=None: "",
            ask_secret=lambda label: "",
            confirm=lambda label: True,
            choose=lambda label, options: 0,
        ),
        said,
    )


def 執行(steps, mode="install"):
    prompts, said = 建立提問()

    async def 假驗證(url: str, api_key: str) -> str:
        return "kenny"

    code = run_install(
        prompts,
        config_path=Path("config.toml"),
        run=lambda argv, stdin=None: None,  # type: ignore[arg-type,return-value]
        verify=假驗證,
        mode=mode,
        platform="linux",
        steps=steps,
    )
    return code, said


def test_全部檢查通過時不呼叫任何修復且回傳零():
    steps = [假步驟("甲", 檢查通過=True), 假步驟("乙", 檢查通過=True)]
    code, said = 執行(steps)
    assert code == EXIT_OK
    assert all(s.紀錄 == ["check"] for s in steps)


def test_進度行帶有編號與總數():
    steps = [假步驟("甲", 檢查通過=True), 假步驟("乙", 檢查通過=True)]
    _, said = 執行(steps)
    合併 = "\n".join(said)
    assert "[1/2]" in 合併 and "[2/2]" in 合併
    assert "甲" in 合併 and "乙" in 合併


def test_檢查未過時呼叫修復並繼續往下():
    steps = [假步驟("甲", 檢查通過=False), 假步驟("乙", 檢查通過=True)]
    code, _ = 執行(steps)
    assert code == EXIT_OK
    assert steps[0].紀錄 == ["check", "fix"]
    assert steps[1].紀錄 == ["check"]


def test_修復失敗時停在該步不再往下():
    steps = [
        假步驟("甲", 檢查通過=False, 修復通過=False),
        假步驟("乙", 檢查通過=True),
    ]
    code, said = 執行(steps)
    assert code == EXIT_FAILED
    assert steps[1].紀錄 == []
    assert any("修不好" in line for line in said)


def test_前置步驟失敗時回傳前置專用的_exit_code():
    # 第一步是 uv，它失敗代表環境缺前置而非流程出錯，要能被外層自動化區分。
    steps = [假步驟(UvStep.title, 檢查通過=False, 修復通過=False), 假步驟("乙", 檢查通過=True)]
    code, _ = 執行(steps)
    assert code == EXIT_PREREQUISITE


def test_成功時印出使用者必須自己做的兩件事():
    steps = [假步驟("甲", 檢查通過=True)]
    _, said = 執行(steps)
    合併 = "\n".join(said)
    assert "重開 Claude Code" in 合併
    assert "/mcp" in 合併
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_setup_installer.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.installer'`

- [ ] **Step 3: 實作**

建立 `src/redmine_mcp/setup/installer.py`：

```python
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
    ClaudeCliStep,
    ConfigStep,
    Context,
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

#: 步驟表。順序即依賴順序：沒有 uv 就裝不了套件，沒有套件就沒有東西可註冊。
STEPS: tuple[Step, ...] = (
    UvStep(),
    PackageStep(),
    ClaudeCliStep(),
    ConfigStep(),
    RegisterStep(),
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

    prompts.panel(
        "完成",
        [
            f"參數檔：{config_path}",
            "",
            "[bold]接下來這兩步要你自己做，安裝器無法代勞：[/bold]",
            "  1. 重開 Claude Code（MCP server 不會熱重載）",
            "  2. 輸入 /mcp 確認 redmine 顯示為 connected",
        ],
    )
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
        失敗 = 失敗 or not outcome.ok
    return EXIT_FAILED if 失敗 else EXIT_OK
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_setup_installer.py -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/installer.py tests/test_setup_installer.py
git commit -m "feat(redmine-mcp): 新增安裝流程編排、進度輸出與 exit code 語意"
```

---

### Task 6: `--uninstall` 最後一步的自我移除與退回路徑

**Files:**
- Modify: `src/redmine_mcp/setup/installer.py`
- Test: `tests/test_setup_installer.py`

**Interfaces:**
- Consumes: Task 5 的 `_run_uninstall`、Task 2 的 `PackageStep.remove`
- Produces: `_run_uninstall` 的行為擴充（無新的對外名稱）

- [ ] **Step 1: 寫失敗的測試**

加到 `tests/test_setup_installer.py` 檔尾：

```python
from redmine_mcp.setup.steps import PackageStep


class 假移除步驟(Step):
    """remove() 依預設答案回應，用來驗證反向流程。"""

    def __init__(self, title: str, *, 成功: bool, detail: str = "") -> None:
        self.title = title
        self._成功 = 成功
        self._detail = detail
        self.移除過 = False

    def check(self, ctx: Context) -> StepOutcome:
        raise AssertionError("uninstall 不該呼叫 check")

    def fix(self, ctx: Context) -> StepOutcome:
        raise AssertionError("uninstall 不該呼叫 fix")

    def remove(self, ctx: Context) -> StepOutcome | None:
        self.移除過 = True
        return StepOutcome(self._成功, self._detail)


def test_移除以反向順序執行():
    甲 = 假移除步驟("甲", 成功=True)
    乙 = 假移除步驟("乙", 成功=True)
    順序: list[str] = []
    甲.remove = lambda ctx: (順序.append("甲"), StepOutcome(True, ""))[1]  # type: ignore[method-assign]
    乙.remove = lambda ctx: (順序.append("乙"), StepOutcome(True, ""))[1]  # type: ignore[method-assign]
    code, _ = 執行([甲, 乙], mode="uninstall")
    assert code == EXIT_OK
    assert 順序 == ["乙", "甲"]


def test_套件移除失敗時印出可貼的指令而不是假裝完成():
    步驟 = 假移除步驟(
        PackageStep.title, 成功=False, detail="error: failed to remove redmine-mcp.exe"
    )
    code, said = 執行([步驟], mode="uninstall")
    assert code == EXIT_FAILED
    合併 = "\n".join(said)
    assert "uv tool uninstall redmine-mcp" in 合併
    assert "執行中" in 合併
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_setup_installer.py -q`
Expected: FAIL，`assert "uv tool uninstall redmine-mcp" in 合併`

- [ ] **Step 3: 實作**

在 `src/redmine_mcp/setup/installer.py` 的 import 區，把 `redmine_mcp.setup.steps` 的匯入清單加上 `TOOL`（`PackageStep` 已在 Task 5 匯入過）。

把 `_run_uninstall()` 中印出結果的那段改成：

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_setup_installer.py -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/installer.py tests/test_setup_installer.py
git commit -m "feat(redmine-mcp): 移除流程在套件自我移除失敗時退回可貼的指令"
```

---

### Task 7: `install` 子命令

**Files:**
- Modify: `src/redmine_mcp/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 5 的 `run_install`、既有的 `resolve_config_path`／`run_command`／`verify_credentials`／`console_prompts`
- Produces: `redmine-mcp install [--repair|--reinstall|--uninstall]`

- [ ] **Step 1: 寫失敗的測試**

先看一眼 `tests/test_cli.py` 現有的寫法再加。加到檔尾：

```python
def test_install_子命令存在且說明可讀():
    from typer.testing import CliRunner

    from redmine_mcp.cli import app

    result = CliRunner().invoke(app, ["install", "--help"])
    assert result.exit_code == 0
    assert "--repair" in result.output
    assert "--reinstall" in result.output
    assert "--uninstall" in result.output


def test_install_不提供帶入金鑰的旗標():
    # 安全底線：金鑰絕不進 argv。多一個 --api-key 之類的旗標就破功了。
    from typer.testing import CliRunner

    from redmine_mcp.cli import app

    result = CliRunner().invoke(app, ["install", "--help"])
    assert "key" not in result.output.lower()


def test_模式旗標互斥():
    from typer.testing import CliRunner

    from redmine_mcp.cli import app

    result = CliRunner().invoke(app, ["install", "--repair", "--uninstall"])
    assert result.exit_code != 0
    assert "只能擇一" in result.output
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL，`No such command 'install'`

- [ ] **Step 3: 實作**

在 `src/redmine_mcp/cli.py` 的 `setup()` 命令之後加入：

```python
@app.command()
def install(
    repair: bool = typer.Option(False, "--repair", help="逐項檢查並只修不對勁的部分"),
    reinstall: bool = typer.Option(False, "--reinstall", help="套件砍掉重裝，設定保留"),
    uninstall: bool = typer.Option(False, "--uninstall", help="拆註冊、移除套件，並問是否刪參數檔"),
) -> None:
    """安裝、升級、修復或移除 redmine-mcp（不帶旗標時＝安裝並升級到最新）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import installer, wizard
    from redmine_mcp.setup.external import run_command
    from redmine_mcp.setup.verify import verify_credentials

    選定 = [名稱 for 名稱, 開 in
            (("repair", repair), ("reinstall", reinstall), ("uninstall", uninstall)) if 開]
    if len(選定) > 1:
        # 三個旗標語意互斥，同時給只會讓人以為它們會依序執行。
        typer.echo("--repair、--reinstall、--uninstall 只能擇一。", err=True)
        raise typer.Exit(2)

    mode = 選定[0] if 選定 else "install"
    raise typer.Exit(
        installer.run_install(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            run=run_command,
            verify=verify_credentials,
            mode=mode,  # type: ignore[arg-type]
        )
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/cli.py tests/test_cli.py
git commit -m "feat(redmine-mcp): 新增 install 子命令與四種模式旗標"
```

---

### Task 8: Windows bootstrap 腳本

**Files:**
- Create: `install.ps1`

**Interfaces:**
- Consumes: Task 7 的 `redmine-mcp install`
- Produces: `install.ps1`，接受與 `redmine-mcp install` 相同的旗標並原樣轉交

- [ ] **Step 1: 建立腳本**

建立 `redmine-mcp/install.ps1`：

```powershell
# redmine-mcp 的 Windows bootstrap。
#
# 職責只到「redmine-mcp 這個指令在 PATH 上可以執行」為止，之後一律交給
# `redmine-mcp install`。這裡刻意不做任何判斷分支以外的事，也不印安裝總結——
# 那些邏輯放在 Python 那一層才寫得起單元測試。
#
# 用法（由 README 的一行指令呼叫）：
#   powershell -ExecutionPolicy Bypass -File install.ps1 [--repair|--reinstall|--uninstall]

$ErrorActionPreference = 'Stop'

$總步數 = 3

function 步驟 {
    param([int]$編號, [string]$標題)
    Write-Host ("[{0}/{1}] {2} ..." -f $編號, $總步數, $標題)
}

function 成功 { param([string]$訊息) Write-Host ("      OK  " + $訊息) -ForegroundColor Green }
function 失敗 {
    param([string]$訊息)
    Write-Host ("      NG  " + $訊息) -ForegroundColor Red
    exit 2
}

步驟 1 '檢查 git'
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    失敗 'git 不在 PATH 上。你是怎麼拿到這個腳本的？請確認 git 已安裝並開一個新的終端機。'
}
成功 (git --version)

步驟 2 '確保 uv'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host '      找不到 uv，開始安裝（可能會跳出 UAC 提權視窗）'
    winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        # winget 在部分企業環境被停用或找不到來源；官方安裝腳本裝到使用者目錄，
        # 不需要提權，是可靠的退路。
        Write-Host '      winget 失敗，改用官方安裝腳本'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    # 官方腳本裝到 %USERPROFILE%\.local\bin，該目錄要進本次工作階段的 PATH，
    # 否則下一行馬上就找不到 uv。
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        失敗 'uv 裝完仍不在 PATH 上。請開一個新的終端機後重跑這個腳本。'
    }
}
成功 (uv --version)

步驟 3 '安裝 redmine-mcp'
$來源 = 'git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp'
uv tool install --from $來源 redmine-mcp
if ($LASTEXITCODE -ne 0) {
    失敗 '套件安裝失敗。若訊息提到認證，請確認你的 GitHub 帳號有 Shinspire/MCP 的存取權。'
}
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
成功 'redmine-mcp'

Write-Host ''
redmine-mcp install @args
exit $LASTEXITCODE
```

- [ ] **Step 2: 語法檢查**

Run: `powershell -NoProfile -Command "[void][System.Management.Automation.Language.Parser]::ParseFile('install.ps1', [ref]$null, [ref]$null); '語法 OK'"`
Expected: 印出「語法 OK」

- [ ] **Step 3: 實跑一次（本機已裝好的環境）**

Run: `powershell -ExecutionPolicy Bypass -File install.ps1 --repair`
Expected: 三個步驟都印 OK，然後接上 `redmine-mcp install --repair` 的六步驟進度

- [ ] **Step 4: Commit**

```bash
git add install.ps1
git commit -m "feat(redmine-mcp): 新增 Windows bootstrap 安裝腳本"
```

---

### Task 9: Linux／WSL bootstrap 腳本

**Files:**
- Create: `install.sh`

**Interfaces:**
- Consumes: Task 7 的 `redmine-mcp install`
- Produces: `install.sh`，接受與 `redmine-mcp install` 相同的旗標並原樣轉交

- [ ] **Step 1: 建立腳本**

建立 `redmine-mcp/install.sh`：

```bash
#!/usr/bin/env bash
# redmine-mcp 的 Linux／WSL bootstrap。
#
# 職責只到「redmine-mcp 這個指令在 PATH 上可以執行」為止，之後一律交給
# `redmine-mcp install`。這裡刻意不做任何判斷分支以外的事——那些邏輯放在
# Python 那一層才寫得起單元測試。
#
# 用法（由 README 的一行指令呼叫）：
#   bash install.sh [--repair|--reinstall|--uninstall]
set -euo pipefail

總步數=3

步驟() { printf '[%s/%s] %s ...\n' "$1" "$總步數" "$2"; }
成功() { printf '      OK  %s\n' "$1"; }
失敗() { printf '      NG  %s\n' "$1" >&2; exit 2; }

步驟 1 '檢查 git'
command -v git >/dev/null 2>&1 || 失敗 'git 不在 PATH 上。你是怎麼拿到這個腳本的？請先安裝 git。'
成功 "$(git --version)"

步驟 2 '確保 uv'
if ! command -v uv >/dev/null 2>&1; then
  echo '      找不到 uv，以官方安裝腳本安裝到 ~/.local/bin（不需要 sudo）'
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # 官方腳本裝到 ~/.local/bin，該目錄要進本次工作階段的 PATH，否則下一行馬上找不到。
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || 失敗 'uv 裝完仍不在 PATH 上。請開一個新的終端機後重跑。'
fi
成功 "$(uv --version)"

步驟 3 '安裝 redmine-mcp'
來源='git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp'
if ! uv tool install --from "$來源" redmine-mcp; then
  失敗 '套件安裝失敗。若訊息提到認證，請確認你的 GitHub 帳號有 Shinspire/MCP 的存取權。'
fi
export PATH="$HOME/.local/bin:$PATH"
成功 'redmine-mcp'

echo
exec redmine-mcp install "$@"
```

- [ ] **Step 2: 語法檢查**

Run: `bash -n install.sh && echo "語法 OK"`
Expected: 印出「語法 OK」

- [ ] **Step 3: 設定執行權限並實跑一次**

Run: `chmod +x install.sh && bash install.sh --repair`
Expected: 三個步驟都印 OK，然後接上 `redmine-mcp install --repair` 的六步驟進度

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat(redmine-mcp): 新增 Linux／WSL bootstrap 安裝腳本"
```

---

### Task 10: CI 端對端驗證安裝腳本

**Files:**
- Modify: `.github/workflows/redmine-mcp.yml`

**Interfaces:**
- Consumes: Task 8、Task 9 的兩份腳本
- Produces: `installer` job，在 `ubuntu-latest` 與 `windows-latest` 上實跑

- [ ] **Step 1: 加入 job**

在 `.github/workflows/redmine-mcp.yml` 的 `build` job 之後加入（注意這個檔案的 `defaults.run.working-directory` 已是 `redmine-mcp`）：

```yaml
  installer:
    # shell bootstrap 刻意壓到三步無邏輯，因此不寫 shell 單元測試，改在兩個平台
    # 上真的跑一次。這比任何 shell 單元測試有價值：它驗的是真的裝得起來。
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - name: 跑 install.sh（Linux）
        if: runner.os == 'Linux'
        run: bash install.sh --repair || true

      - name: 跑 install.ps1（Windows）
        if: runner.os == 'Windows'
        shell: pwsh
        run: powershell -ExecutionPolicy Bypass -File install.ps1 --repair
        continue-on-error: true

      - name: 驗證套件確實裝到 PATH 上且起得來
        # 上一步以 --repair 執行，會在「參數檔」那一步停下（CI 沒有 Redmine 金鑰，
        # 且精靈需要互動），因此它的 exit code 不是判準；判準是套件真的裝好了。
        shell: bash
        run: |
          export PATH="$HOME/.local/bin:$PATH"
          uv tool list | grep -q '^redmine-mcp v'
          "" | redmine-mcp 2>&1 | grep -q '設定錯誤' || \
            "" | redmine-mcp 2>&1 | grep -q '啟動'
```

`|| true` 與 `continue-on-error` 是必要的：`--repair` 在沒有參數檔的 CI 環境會以 exit code 3 結束，那是正確行為而非失敗。真正的判準在下一步。

- [ ] **Step 2: 本機驗證 YAML 可解析**

Run: `uv run python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('../.github/workflows/redmine-mcp.yml').read_text(encoding='utf-8')); print('YAML OK')"`
Expected: 印出 `YAML OK`（`pyyaml` 不在本專案相依中時改用 `uvx --with pyyaml python -c ...`）

- [ ] **Step 3: Commit**

```bash
git add ../.github/workflows/redmine-mcp.yml
git commit -m "ci(redmine-mcp): 在兩個平台上端對端驗證 bootstrap 安裝腳本"
```

- [ ] **Step 4: 推上去看 CI 結果**

Run: `git push`
Expected: `installer` job 在兩個平台上都綠。若 Windows 那步因 `$env:PATH` 未更新而找不到 `redmine-mcp`，在驗證步驟前補一行 `echo "$HOME/.local/bin" >> $GITHUB_PATH`。

---

### Task 11: 文件改寫

**Files:**
- Modify: `README.md`（repo 根目錄）
- Modify: `redmine-mcp/SETUP.md`

**Interfaces:**
- Consumes: Task 7–9 的最終指令形狀
- Produces: 使用者看得到的唯一安裝路徑

- [ ] **Step 1: 改寫根目錄 README 的「安裝操作指引」**

把該節現有的兩行指令換成下面的內容（保留該節之後關於「為什麼是先本機安裝」的警告框與多站台說明，那些仍然成立）：

````markdown
## 安裝操作指引

以 `redmine-mcp` 為例，一行指令。

**Windows：**

```powershell
git clone --depth 1 https://github.com/Shinspire/MCP.git "$env:TEMP\shinspire-mcp"; powershell -ExecutionPolicy Bypass -File "$env:TEMP\shinspire-mcp\redmine-mcp\install.ps1"
```

**WSL／Linux：**

```bash
git clone --depth 1 https://github.com/Shinspire/MCP.git /tmp/shinspire-mcp && bash /tmp/shinspire-mcp/redmine-mcp/install.sh
```

`-ExecutionPolicy Bypass` 不可省：Windows 用戶端的預設執行政策會擋下 `.ps1`，
而它的錯誤訊息看不出是這個原因。

腳本會依序：確認 git → 裝好 uv（沒有的話）→ 安裝套件 → 接上 `redmine-mcp install`，
由它處理 Claude Code CLI、參數檔精靈、註冊與啟動測試，全程印出分步驟進度。

唯一無法自動安裝的前置是 **git**——這個 repo 是 private，要先有 git 且帳號有存取權
才拿得到安裝腳本本身。

金鑰只存在參數檔，**不會進入指令參數、不會進入 log，也不會經過任何 AI 對話**。

裝完之後：

| 想做的事 | 指令 |
|---|---|
| 升級到最新版 | `redmine-mcp install` |
| 檢查哪裡壞了並修好 | `redmine-mcp install --repair` |
| 套件砍掉重裝（設定保留） | `redmine-mcp install --reinstall` |
| 完整移除 | `redmine-mcp install --uninstall` |

這四個都不需要再 clone 一次——套件已經在 PATH 上了。

安裝完有兩件事要你自己做：**重開 Claude Code**（MCP server 不會熱重載），然後輸入
`/mcp` 確認 `redmine` 顯示為 connected。
````

同時刪掉 README 中「更新到新版本」一節裡的 `uv tool upgrade redmine-mcp` 作為主要路徑的敘述，改為指向 `redmine-mcp install`；「移除」一節的兩行手動指令改為 `redmine-mcp install --uninstall`，並保留手動指令作為備援說明。

- [ ] **Step 2: 改寫 `redmine-mcp/SETUP.md` 的第 1 節**

在「## 1. 前置需求」表格上方加入一段，說明現在有一鍵腳本、本文件是參考而非安裝步驟：

```markdown
> 一般安裝請用 [README 的一鍵指令](../README.md#安裝操作指引)，它會處理本節列出的
> 所有前置需求。本節保留下來是給「想知道每一步在做什麼」與排錯時查閱用的。
```

並把表格中的 `uv` 那一列補上「安裝腳本會自動處理」。

- [ ] **Step 3: 確認文件內的指令與實作一致**

Run: `grep -n "install.ps1\|install.sh\|redmine-mcp install" ../README.md SETUP.md`
Expected: 列出的每一處指令拼字都與 Task 7–9 完成的實際形狀一致（尤其 `--repair`／`--reinstall`／`--uninstall` 三個旗標名）

- [ ] **Step 4: Commit**

```bash
git add ../README.md SETUP.md
git commit -m "docs(redmine-mcp): 安裝章節改寫為一鍵腳本與四種維護模式"
```

---

## Self-Review

**Spec coverage：**

| Spec 章節 | 對應 Task |
| --- | --- |
| 入口與交棒點（一行指令、ExecutionPolicy、暫存 clone） | 8、9、11 |
| shell 腳本三步驟職責邊界 | 8、9 |
| 步驟表（六步） | 2、3、4 |
| 四種模式策略 | 2（reinstall）、3（repair 驗金鑰）、5（install）、6（uninstall） |
| 進度呈現 | 5 |
| `--uninstall` 最後一步的誠實限制 | 6 |
| 錯誤處理（停在失敗那步、exit code 語意） | 5 |
| 提權只問一次 | 8（winget 前的提示）、3（Claude CLI 安裝前的確認） |
| 安全紀律（金鑰不進 argv） | 7（測試明文斷言沒有 key 旗標） |
| 檔案配置 | 全部 |
| 測試策略（Python 逐步驟、CI 端對端、`_STARTED_MARKER` 回歸） | 2–7、10、4 |

**已知的收尾風險：**

- Task 10 的 Windows PATH 行為在 CI 上可能與本機不同，該 Task 的 Step 4 已寫明退路。
- `_TITLE_WIDTH` 的對齊是視覺近似值，中英文混排時點點數量會有落差；這不影響正確性，刻意不引入 `wcwidth` 相依。
