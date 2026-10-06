"""外部指令：Claude Code CLI 的註冊操作與 stdio 冒煙測試。

所有呼叫都經由注入的 CommandRunner，測試因此不必真的開子行程；正式執行時
由 run_command() 提供實作。金鑰永遠不會出現在這裡的任何 argv 中——設定一律
只存在參數檔，這是本專案的安全底線。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: 冒煙測試判定成功的關鍵字，對應 __main__.py 啟動時的 log 訊息。
_STARTED_MARKER = "啟動"


@dataclass(frozen=True)
class CommandResult:
    """外部指令的執行結果。

    欄位:
        code: exit code。
        stdout: 標準輸出全文。
        stderr: 標準錯誤全文。
    """

    code: int
    stdout: str
    stderr: str


#: 執行外部指令的介面：收 argv 與可選的 stdin 內容與逾時，回傳結果。
CommandRunner = Callable[..., CommandResult]

#: 一般指令的逾時：本機一次 CLI 呼叫，久了就是不對。
DEFAULT_COMMAND_TIMEOUT = 120.0

#: 需要下載的指令（uv tool install/upgrade、Claude CLI 安裝端點）的逾時。
#: 第一次安裝要下載 Python 與整棵相依樹，在公司 proxy 或慢速線路下 120 秒遠遠不夠，
#: 而逾時會讓安裝在 3/6 停住，使用者只看到一句「執行逾時」。
SLOW_COMMAND_TIMEOUT = 600.0


def run_command(
    argv: list[str], stdin: str | None = None, timeout: float | None = None
) -> CommandResult:
    """實際開子行程執行指令。

    參數:
        argv: 指令與參數。
        stdin: 要餵給該行程的標準輸入；None 代表不餵。
        timeout: 逾時秒數；None 代表使用 DEFAULT_COMMAND_TIMEOUT。
    回傳:
        執行結果；找不到執行檔時以 code 127 表示，不拋出。
    """
    # 強制子行程以 UTF-8 輸出。Python 的 stderr 預設走系統字碼頁且以
    # backslashreplace 處理表示不了的字元，因此在 ANSI 字碼頁非中文的 Windows
    # （例如英文版的 cp1252）上，子行程的中文訊息會變成 `\uXXXX` 的字面字串。
    # 冒煙測試靠比對中文 marker 判定 server 是否起得來，那樣會靜默退化成一律
    # 回報失敗。這個環境變數只影響 Python 子行程，對 uv／claude／git 無作用。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        completed = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_COMMAND_TIMEOUT if timeout is None else timeout,
            env=env,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"找不到執行檔：{argv[0]}")
    except subprocess.TimeoutExpired:
        # 逾時最常見的成因是公司 proxy 攔住下載，而不是指令本身壞了。訊息要讓
        # 使用者知道下一步做什麼，也要讓他知道重跑不會從零開始（uv 有快取）。
        return CommandResult(
            124,
            "",
            f"{argv[0]} 執行逾時。若在公司網路內，請確認 proxy 設定是否讓 "
            "https://pypi.org 與 https://github.com 通得過；"
            "可直接重跑安裝，已下載的部分會沿用快取不必重來",
        )
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def terminate_running_tool(tool: str, run: CommandRunner, platform: str) -> CommandResult | None:
    """終止指定名稱正在執行的工具子行程，供解除 Windows 的檔案鎖定使用。

    只在 win32 動作：其他平台沒有「不可覆蓋執行中檔案」這個限制，沒理由、也
    不應該去砍任何行程。

    終止指令必須以 `/FI "PID ne <本行程 PID>"` 排除呼叫者自己——`redmine-mcp
    install` 本身就是以 `redmine-mcp.exe` 執行，若不排除自己，裸的
    `taskkill /F /IM redmine-mcp.exe` 會把安裝流程自己砍掉，安裝停在一半。

    只用精確的映像名（`<tool>.exe`）過濾，且刻意不加 `/T`（連子行程一起砍）、
    不用萬用字元、不對 claude／cmd／powershell 等其他行程名稱下手：這裡只處理
    「鎖住 uv 要覆蓋的檔案」這一個問題，波及範圍越窄越安全。

    參數:
        tool: 要終止的工具名稱（不含副檔名），例如 redmine-mcp。
        run: 指令執行器。
        platform: `sys.platform` 的值；非 win32 時直接跳過並回傳 None，
            也不會下任何指令。
    回傳:
        指令執行結果；非 win32 平台為 None。
    """
    if platform != "win32":
        return None
    return run(
        ["taskkill", "/F", "/IM", f"{tool}.exe", "/FI", f"PID ne {os.getpid()}"]
    )


def resolve_executable(tool: str) -> str:
    """決定要註冊給 Claude Code 的指令。

    一律回傳工具名稱本身：名稱比絕對路徑可攜，日後重裝或換路徑都不必重註冊。
    claude CLI 是否可用由精靈另行以 `claude --version` 探測（見 wizard.py 的
    _do_register），本函式不做 PATH 檢查。

    參數:
        tool: 工具名稱，例如 redmine-mcp。
    回傳:
        要寫進 Claude Code 設定的指令字串。
    """
    return tool


def is_registered(name: str, run: CommandRunner) -> bool:
    """檢查該名稱是否已註冊到 Claude Code。

    參數:
        name: MCP server 在 Claude Code 中的名稱。
        run: 指令執行器。
    回傳:
        已註冊為 True。
    """
    return run(["claude", "mcp", "get", name]).code == 0


def register(name: str, command: str, run: CommandRunner) -> CommandResult:
    """把 MCP server 註冊到 Claude Code。

    scope 固定為 user：project scope 會寫進進版控的 .mcp.json，本專案明文禁止，
    因此不開放成選項。

    參數:
        name: 要註冊的名稱。
        command: 啟動指令。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["claude", "mcp", "add", name, "--scope", "user", "--", command])


def unregister(name: str, run: CommandRunner) -> CommandResult:
    """移除既有註冊，供覆蓋流程使用。

    參數:
        name: 要移除的名稱。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["claude", "mcp", "remove", name, "--scope", "user"])


def smoke_test(command: str, run: CommandRunner) -> tuple[bool, str]:
    """以空 stdin 跑一次 server，確認它起得來。

    這是 stdio server：沒有輸入會停在那裡等待，因此必須餵空字串讓它收到 EOF
    後自行結束。判定條件是 exit code 為 0 **且** stderr 出現啟動訊息——只看
    exit code 會把「什麼都沒做就結束」誤判為成功。

    參數:
        command: 啟動指令。
        run: 指令執行器。
    回傳:
        `(是否成功, 可顯示給使用者的訊息)`。失敗時訊息取 stderr，供對照排錯表。
    """
    result = run([command], "")
    output = (result.stderr or result.stdout).strip()
    if result.code == 0 and _STARTED_MARKER in output:
        return True, output
    return False, output or f"指令以 exit code {result.code} 結束，且沒有任何輸出"


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


def tool_env_broken(
    tool: str, run: CommandRunner, exists: Callable[[Path], bool] = Path.exists
) -> bool:
    """判斷某工具是否「已安裝但執行環境殘缺」（venv 缺 pyvenv.cfg）。

    不能靠 `uv tool list`／`installed_version()` 判斷：那條路徑讀的是安裝
    receipt（`uv-receipt.toml`），只要曾經裝過就會照樣列出版本號，即使 venv
    本身已經殘缺不全（例如安裝中途被中斷，`Lib`／`Scripts` 還在但
    `pyvenv.cfg` 不見了）也一樣。實測過的真實案例是：`uv tool list` 顯示
    已安裝，但執行 `<tool>.exe` 只印出一句 `No pyvenv.cfg file` 就結束——
    這正是本函式要抓的情況。

    做法是用 `uv tool dir` 問出工具根目錄，檢查
    `<那個目錄>/<tool>/pyvenv.cfg` 是否存在；不存在就視為殘缺。

    `uv tool dir` 失敗或答不出路徑時一律回傳 False（保守，視為「環境正常」），
    不會去猜測或觸發任何後續動作：修復這種殘缺的手段是整個移除工具目錄再重裝
    （見 PackageStep.fix()），是破壞性動作，只有在真的確認殘缺時才該做；
    連「目錄在哪」都問不出來時去移除重裝，風險遠大於放著不動。

    參數:
        tool: 工具名稱，例如 redmine-mcp。
        run: 指令執行器。
        exists: 檔案是否存在的判斷函式；預設用真正的 `Path.exists`，測試時
            可換成假的以避免真的碰檔案系統。
    回傳:
        判定為殘缺時 True；環境正常或無法判斷時 False。
    """
    result = run(["uv", "tool", "dir"])
    if result.code != 0:
        return False
    root = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not root:
        return False
    return not exists(Path(root) / tool / "pyvenv.cfg")


def install_package(
    tool: str, run: CommandRunner, source: str = PACKAGE_SOURCE
) -> CommandResult:
    """以 uv 安裝本套件。

    `--refresh-package` 不可省略：uv 會把「從這個來源建出來的 wheel」快取起來，
    命中快取時整趟安裝不會重新建置（輸出裡看不到 `Building ...`），裝進去的就還是
    上一次那份程式碼。本專案的一鍵安裝包來源是本機路徑、版本號又常常不變（同一版
    修好幾次），實測過的結果是 uv 回報 `Installed 1 package` 但 venv 裡完全是舊的
    程式碼——比裝不起來更糟，因為它看起來成功了。只 refresh 自己這一個套件，
    相依樹照樣走快取，不會拖慢安裝。

    參數:
        tool: 工具名稱。
        run: 指令執行器。
        source: 安裝來源。預設是 git URL；ZIP 一鍵安裝時傳解壓目錄下的
            `redmine-mcp` 絕對路徑，全程不碰 GitHub，機器上不需要任何憑證。
    回傳:
        執行結果。
    """
    return run(
        ["uv", "tool", "install", "--from", source, "--refresh-package", tool, tool],
        timeout=SLOW_COMMAND_TIMEOUT,
    )


def upgrade_package(
    tool: str, run: CommandRunner, source: str | None = None
) -> CommandResult:
    """把已安裝的套件更新到來源的最新版。

    參數:
        tool: 工具名稱。
        run: 指令執行器。
        source: 本機來源路徑；None 代表沿用當初記錄的來源（git）。
            給了本機路徑時不能走 `uv tool upgrade`——它會回頭解析當初記錄的
            git 來源，在沒有 GitHub 憑證的機器上必然失敗——只能以 `--force`
            從本機重裝一次。
    回傳:
        執行結果。
    """
    if source is None:
        return run(["uv", "tool", "upgrade", tool], timeout=SLOW_COMMAND_TIMEOUT)
    return run(
        # --refresh-package 的理由見 install_package()：少了它，uv 會拿快取裡上一次
        # 建好的 wheel 交差，「升級」完的 venv 內容跟升級前一模一樣。
        ["uv", "tool", "install", "--from", source, "--force", "--refresh-package", tool, tool],
        timeout=SLOW_COMMAND_TIMEOUT,
    )


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
        return run(
            [
                "powershell",
                "-Command",
                "irm https://claude.ai/install.ps1 | iex",
            ],
            timeout=SLOW_COMMAND_TIMEOUT,
        )
    return run(
        ["sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
        timeout=SLOW_COMMAND_TIMEOUT,
    )


#: 工具組 repo。骨架不隨 redmine-mcp 的 wheel 發佈，安裝 skill 必須先取得 repo。
_SKILLS_REPO = "https://github.com/Shinspire/MCP.git"

#: repo 內 skill 子專案的相對路徑。
_SKILLS_SUBDIR = "redmine-issue-skills"


@dataclass(frozen=True)
class Harness:
    """一個偵測到的 agent 平台（由 skill 安裝腳本的 `--list`／`-List` 回報）。

    欄位:
        id: harness 的識別字，對應 harnesses.tsv 的第一欄，也是 `--only`／`-Only`
            要帶的值。
        kind: 類別。`native`＝原生 skill 機制（裝進去不動既有檔案）；
            `fallback`＝有可寫入的全域指示檔（會插入標記區塊）；
            `manual`＝偵測得到但沒有可寫入的指示檔，只會印手動步驟。
        instruction_file: 指示檔路徑，相對於家目錄；`manual` 類為空字串。
    """

    id: str
    kind: str
    instruction_file: str


def _run_skills_script(
    run: CommandRunner,
    repo: Path,
    *,
    platform: str,
    uninstall: bool = False,
    list_only: bool = False,
    only: Sequence[str] | None = None,
    assume_yes: bool = False,
) -> CommandResult:
    """在給定的 repo 目錄上執行 skill 的安裝／移除／列表腳本。

    抽成 helper 是因為有多條路徑會用到它：clone 到暫存目錄與 ZIP 解壓出來的本機
    目錄；安裝、移除與（供呼叫端選單用的）列表。組 argv 的邏輯只在這裡一份，
    install.sh／install.ps1 的旗標名稱不同（後者是 PowerShell 慣例的 `-Xxx`），
    差異全部收斂在這個函式內，呼叫端不需要知道。

    參數:
        run: 外部指令執行器。
        repo: repo 根目錄（其下應有 redmine-issue-skills/install/）。
        platform: `sys.platform` 的值。win32 走 PowerShell，其餘走 bash。
        uninstall: True 時帶上移除旗標（--uninstall／-Uninstall）。
        list_only: True 時只印偵測到的 harness、不動任何檔案（--list／-List）。
        only: 只處理這些 harness id；None 或空序列代表全部
            （--only／-Only，值以逗號分隔）。
        assume_yes: True 時同意寫入後備類的全域指示檔（--yes／-Yes）。
    回傳:
        腳本的執行結果。
    """
    root = repo / _SKILLS_SUBDIR / "install"
    if platform == "win32":
        # 固定用 powershell：PowerShell 5.1 是 Windows 內建，pwsh 不一定有。
        argv = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "install.ps1"),
        ]
        if uninstall:
            argv.append("-Uninstall")
        if list_only:
            argv.append("-List")
        if only:
            argv += ["-Only", ",".join(only)]
        if assume_yes:
            argv.append("-Yes")
    else:
        argv = ["bash", str(root / "install.sh")]
        if uninstall:
            argv.append("--uninstall")
        if list_only:
            argv.append("--list")
        if only:
            argv += ["--only", ",".join(only)]
        if assume_yes:
            argv.append("--yes")
    return run(argv, timeout=SLOW_COMMAND_TIMEOUT)


def _with_skills_repo(
    run: CommandRunner,
    repo_dir: Path | None,
    action: Callable[[Path], CommandResult],
) -> CommandResult:
    """取得 skill 子專案的 repo 目錄後，對它執行給定動作。

    骨架不在 redmine-mcp 的 wheel 內（它是獨立子專案），因此沒有現成本機目錄時
    要先淺 clone 一份。抽成 helper 是因為安裝／移除／列表三種動作都需要同一段
    「先有 repo 才能跑腳本」的前置處理，clone 邏輯只留一份。

    參數:
        run: 外部指令執行器。
        repo_dir: 已在本機的 repo 目錄；None 時淺 clone 一份到暫存目錄。
        action: 拿到 repo 根目錄後要執行的動作。
    回傳:
        action 的執行結果；clone 失敗時直接回 clone 的結果，不再往下跑。
    """
    if repo_dir is not None:
        return action(repo_dir)
    with tempfile.TemporaryDirectory(prefix="redmine-issue-skills-") as tmp:
        workdir = Path(tmp) / "repo"
        cloned = run(
            ["git", "clone", "--depth", "1", _SKILLS_REPO, str(workdir)],
            timeout=SLOW_COMMAND_TIMEOUT,
        )
        if cloned.code != 0:
            # clone 失敗就停在這裡。硬著頭皮去跑不存在的腳本，只會讓使用者拿到
            # 「找不到檔案」這種與真正病因無關的第二個錯誤。
            return cloned
        return action(workdir)


def install_issue_skills(
    run: CommandRunner,
    *,
    platform: str,
    uninstall: bool = False,
    repo_dir: Path | None = None,
    only: Sequence[str] | None = None,
    assume_yes: bool = False,
) -> CommandResult:
    """安裝或移除撰寫格式 skill。

    骨架不在 redmine-mcp 的 wheel 內（它是獨立子專案），因此這裡先淺 clone 一份
    repo，再呼叫子專案自己的安裝腳本——複製邏輯只有那一支實作，一行指令與本函式
    共用它，不會有兩份行為分歧。

    參數:
        run: 外部指令執行器。
        platform: `sys.platform` 的值。win32 走 PowerShell，其餘走 bash。
        uninstall: True 時帶上移除旗標，只刪標記區塊與腳本自己建的目錄。
        repo_dir: 已在本機的 repo 目錄。有值時**跳過 clone**，直接用該目錄下的
            skill 安裝腳本——ZIP 一鍵安裝走這條，機器上不需要 GitHub 憑證。
        only: 只處理這些 harness id（呼叫端先以 `list_issue_skill_harnesses()`
            列出清單、讓使用者勾選後帶回來）；None 代表全部。
        assume_yes: True 時同意寫入後備類的全域指示檔。呼叫端必須先讓使用者從
            `list_issue_skill_harnesses()` 的清單裡勾選過，才可以帶 True——
            這裡不再假設「沒問過就不寫」，決定權交給呼叫端的選單。
    回傳:
        腳本的執行結果；clone 失敗時直接回 clone 的結果，不再往下跑。
    """
    return _with_skills_repo(
        run,
        repo_dir,
        lambda repo: _run_skills_script(
            run,
            repo,
            platform=platform,
            uninstall=uninstall,
            only=only,
            assume_yes=assume_yes,
        ),
    )


@dataclass(frozen=True)
class HarnessDetection:
    """偵測 agent 平台的結果。

    刻意不用「失敗就拋例外」：偵測這一步不該擋住整個安裝流程（見
    `list_issue_skill_harnesses()`），但呼叫端仍需要分辨「執行成功、機器上
    真的一個都沒有」與「指令本身沒跑成功（clone 失敗、腳本失敗）」——這兩種
    情況要對使用者講不同的話，前者是「這裡沒東西」，後者是「這一步本身壞了，
    可能是你的問題（例如 git 憑證）」。

    欄位:
        harnesses: 偵測到的平台；偵測失敗時為空 tuple。
        failed: True 代表偵測指令本身執行失敗（clone 失敗、腳本失敗等）。
            與「執行成功但機器上一個都沒有」是不同的情況，訊息要分開講。
        message: 失敗時的原始輸出（stderr 優先，沒有 stderr 才退回 stdout），
            供使用者判斷病因；成功時為空字串。
    """

    harnesses: tuple[Harness, ...]
    failed: bool = False
    message: str = ""


def list_issue_skill_harnesses(
    run: CommandRunner,
    *,
    platform: str,
    repo_dir: Path | None = None,
) -> HarnessDetection:
    """列出這台機器上偵測到的 agent 平台，供安裝精靈做多選。

    呼叫 skill 安裝腳本的 `--list`／`-List`：只印偵測到的 harness，不動任何檔案。
    這一步失敗（clone 失敗、腳本本身失敗）不該擋住整個安裝流程，因此**不拋出
    例外**，而是把「失敗」與「成功但沒偵測到」分開回報在 `HarnessDetection`
    裡——呼叫端要能對這兩種情況印不同的話，而不是都含糊成「沒有偵測到」。

    參數:
        run: 外部指令執行器。
        platform: `sys.platform` 的值。win32 走 PowerShell，其餘走 bash。
        repo_dir: 已在本機的 repo 目錄；None 時淺 clone 一份到暫存目錄。
    回傳:
        `HarnessDetection`；指令失敗時 `failed=True` 且 `harnesses` 為空。
    """
    result = _with_skills_repo(
        run,
        repo_dir,
        lambda repo: _run_skills_script(run, repo, platform=platform, list_only=True),
    )
    if result.code != 0:
        return HarnessDetection(
            harnesses=(), failed=True, message=(result.stderr or result.stdout).strip()
        )
    return HarnessDetection(harnesses=tuple(_parse_harness_lines(result.stdout)))


def _parse_harness_lines(output: str) -> list[Harness]:
    """解析 `--list`／`-List` 的輸出，每行 `id<TAB>kind<TAB>指示檔`。

    解析要穩健：跳過空行；容忍每行尾隨的 `\\r`（腳本輸出經過子行程仍可能帶
    Windows 風格的行尾）；欄位數不足的行跳過而不是拋例外——這是外部腳本的輸出，
    格式若因故走樣，寧可少列一個 harness 也不能讓整個安裝精靈當掉。

    參數:
        output: 腳本的標準輸出全文。
    回傳:
        解析出的 Harness 清單，依原始行順序排列。
    """
    harnesses: list[Harness] = []
    for raw_line in output.split("\n"):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        harnesses.append(Harness(id=fields[0], kind=fields[1], instruction_file=fields[2]))
    return harnesses
