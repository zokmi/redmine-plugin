"""安裝流程的步驟表。

每個步驟是一個具備 `check()` 與 `fix()` 的物件，外界相依全部經由 Context 注入，
因此逐步驟逐模式都能在不開子行程、不打真實 API 的前提下測試。

四種模式（安裝／修復／重裝／移除）共用同一份步驟表，差別只在各步驟怎麼讀
`ctx.mode`——新增一步只改一個地方，四種模式自動涵蓋。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from redmine_mcp.config import ConfigError, load_settings
from redmine_mcp.config_file import load_config_file
from redmine_mcp.errors import RedmineError
from redmine_mcp.setup.external import (
    CommandResult,
    CommandRunner,
    Harness,
    HarnessDetection,
    install_issue_skills,
    install_package,
    installed_version,
    list_issue_skill_harnesses,
    resolve_executable,
    smoke_test,
    terminate_running_tool,
    tool_env_broken,
    uninstall_package,
    upgrade_package,
    uv_version,
)
from redmine_mcp.setup.registrars import (
    MCP註冊器,
    ClaudeCode註冊器,
    取得註冊器,
    註冊器登錄表,
)
from redmine_mcp.setup.wizard import Prompts, Verifier, run_setup

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
        bundle: ZIP 一鍵安裝時的解壓目錄；None 代表走既有的 git 來源。給了值時
            套件與 skill 都從這個目錄安裝，全程不需要 GitHub 憑證。
    """

    prompts: Prompts
    run: CommandRunner
    verify: Verifier
    config_path: Path
    mode: Mode
    platform: str
    bundle: Path | None = None


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


#: 作業系統回報「檔案被鎖住」的錯誤碼。uv 轉述的訊息本身隨語系而異（zh-TW 是
#: 「存取被拒。」、en-US 是「Access is denied.」），只有錯誤碼是穩定的判斷依據。
#:   - os error 5  = ERROR_ACCESS_DENIED，常見於「移除」時（卸載要刪掉整個
#:     工具目錄，目錄內的執行檔正在跑就會撞到）。
#:   - os error 32 = ERROR_SHARING_VIOLATION，常見於「安裝／升級」複製執行檔
#:     時（真實案例：`failed to copy file ... redmine-mcp.exe`，訊息是「因為
#:     檔案正由另一個程序使用」——這正是一鍵安裝在 bundle 模式下實測撞到的
#:     那種鎖定，且訊息裡沒有 "remove" 這個字）。
_檔案被鎖住的錯誤碼 = ("os error 5", "os error 32")


def _是被鎖住(message: str) -> bool:
    """判斷 uv 的失敗訊息是不是「檔案正被其他行程使用」。

    參數:
        message: uv 的 stderr／stdout 內容。
    回傳:
        是檔案鎖定造成的失敗時為 True。
    """
    # 刻意不再要求同時出現 "remove"：真實案例是安裝／升級複製執行檔時被鎖住
    # （os error 32），訊息裡的動詞是 copy 不是 remove；原本要求 "remove"
    # 反而讓「自動終止並重試」對這種最常見的鎖定情境完全不會觸發。
    return any(碼 in message for 碼 in _檔案被鎖住的錯誤碼)


#: 檔案被鎖住時要給使用者的指引。看到這段代表已經自動終止 redmine-mcp 並重試
#: 過一次仍然失敗，因此只能請使用者自己動手。
#:
#: 指引裡必須先講「重跑安裝腳本」而不是只叫人關掉 Claude Code：`redmine-mcp
#: install` 本身就是以 redmine-mcp.exe 執行，而 uv 升級時要覆蓋的正是這個執行檔。
#: Windows 不允許覆蓋執行中的映像，這個鎖是「自己持有的」——終止其他子行程
#: （taskkill 必須排除自己的 PID，否則安裝會砍掉自己）再重試永遠不會成功，
#: 關掉 Claude Code 也一樣沒用。唯一走得通的路是換一個不是 redmine-mcp.exe 的
#: 行程來裝：install.cmd／install.ps1（Windows）或 install.sh 跑的是
#: powershell.exe／sh，覆蓋得動。
_鎖定指引 = (
    "檔案被鎖住，無法覆蓋 redmine-mcp 的執行檔。"
    "若你是直接下 `redmine-mcp install`，這種鎖定是它自己造成的（要被覆蓋的就是"
    "正在執行的 redmine-mcp.exe），重試永遠不會成功：請改用安裝包升級——"
    "Windows 下載新的 ZIP 後雙擊 install.cmd，其他平台跑 install.sh。"
    "若你本來就是這樣裝的還看到這段，請把 Claude Code 完全關掉"
    "（包含右下角工作列裡的）再重跑一次。"
)

#: 偵測到檔案鎖定、終止子行程後，等多久再重試安裝——讓 Windows 有時間釋放
#: 檔案 handle。獨立成常數是為了讓測試可以 monkeypatch 成 0，不必真的等待。
_鎖定重試等待秒數 = 1.0


def _終止鎖定行程並重試(ctx: Context, 動作: Callable[[], CommandResult]) -> CommandResult:
    """遇到檔案鎖定時，終止 redmine-mcp 子行程並重試一次同樣的動作。

    只重試一次：終止行程後檔案 handle 通常會在短暫等待內釋放，若重試仍失敗，
    多半是別的原因（例如防毒軟體另外鎖住了檔案），繼續重試沒有意義，只會拖長
    使用者等待的時間。

    非 Windows 平台呼叫這裡不會有實際效果——`terminate_running_tool()` 在那些
    平台上直接跳過並回傳 None，本函式仍會照常等待並重試一次，但 `os error 5`
    本來就不會出現在那些平台上。

    參數:
        ctx: 執行情境。
        動作: 要重試的動作（uv 的安裝／升級／移除呼叫）。
    回傳:
        重試後的指令執行結果。
    """
    terminate_running_tool(TOOL, ctx.run, ctx.platform)
    time.sleep(_鎖定重試等待秒數)
    ctx.prompts.say(
        "      [yellow]![/yellow] 偵測到 redmine-mcp 正在執行"
        "（Claude Code 開著的 MCP server），已將它結束，正在重試……"
        "若你開著多個 Claude Code 視窗，它們的 redmine 連線都會一起中斷，"
        "裝完各自用 /mcp 重連即可。"
    )
    return 動作()


#: uv 取不到 git 來源時，訊息中會出現的字樣（大小寫不拘）。
#: 刻意不收裸的 "not found"：它在相依解析失敗、DNS 失敗等與認證無關的訊息裡也會出現，
#: 那時附上「你是不是用 ZIP 裝的」只會蓋掉真正的病因。
_認證關鍵字 = (
    "authentication",
    "could not read username",
    "repository not found",
    "403",
    "401",
    "permission denied",
    "access denied",
)


def _像是認證問題(message: str) -> bool:
    """判斷 uv 的失敗訊息是不是取不到 git 來源。

    參數:
        message: uv 的 stderr／stdout 內容。
    回傳:
        看起來是憑證或存取權問題時為 True。
    """
    低 = message.lower()
    return any(關鍵字 in 低 for 關鍵字 in _認證關鍵字)


def package_source(ctx: Context) -> str | None:
    """決定要餵給 uv 的安裝來源。

    參數:
        ctx: 執行情境。
    回傳:
        bundle 模式時為解壓目錄下 `redmine-mcp` 的絕對路徑字串；否則 None
        （代表沿用 external.py 的 git 預設來源）。
    """
    return str(ctx.bundle / "redmine-mcp") if ctx.bundle else None


def _bundle_package_version(bundle: Path) -> str | None:
    """讀出 bundle 目錄裡 redmine-mcp 這份原始碼的版本號。

    用來判斷「這台機器已安裝的版本」是不是就是這份一鍵安裝包要裝的版本——
    是的話就不必再跑一次 `uv tool install`。真實案例：install.ps1 的
    `[3/3]` 已經用同一個 bundle 裝過一次，`redmine-mcp install --bundle`
    內部的 PackageStep 若不做這個判斷，會對同一份套件再裝一次，第二次才是
    transcript 裡撞到檔案鎖定的那一次——這個函式要拆掉的正是「自己製造出
    要對付的鎖定情境」這件事。

    參數:
        bundle: ZIP 解壓目錄。
    回傳:
        版本號字串；讀不到或解析失敗時為 None（呼叫端要回退成照裝，不可因為
        讀檔失敗就跳過安裝）。
    """
    pyproject = bundle / "redmine-mcp" / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) else None


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
        # 環境殘缺的偵測對所有模式生效（不限 --repair），且必須排在下面的
        # 「bundle 版本相同就跳過」之前：uv 的 receipt 只記錄「裝過」，venv
        # 本身可能因為安裝中途被中斷而殘缺（缺 pyvenv.cfg），而「版本號相同」
        # 正是這種殘缺情境下必然成立的條件——install.ps1 的 [3/3] 已經用同一個
        # bundle 裝過一次，若那次留下殘缺 venv，receipt 版本照樣跟 bundle 內
        # 原始碼相同。若把 bundle 版本比對排在前面，殘缺會被完全遮蔽：使用者
        # 會看到綠色的「OK」，但 `/mcp` 其實連不上，而且沒有任何一句提示他去
        # 跑 `--repair`。這正是真實案例裡發生過的情況。
        if tool_env_broken(TOOL, ctx.run):
            return StepOutcome(
                False, f"目前 {version}，但執行環境殘缺（缺 pyvenv.cfg），將移除後重裝"
            )
        if ctx.bundle is not None:
            # bundle 模式下，install.ps1 的 [3/3] 已經用同一個 bundle 裝過一次；
            # 這裡若版本跟 bundle 內的原始碼相同，代表那次已經是最新，不必再
            # 裝一次——這正是 transcript 裡「兩次 uv tool install，第二次撞上
            # 檔案鎖定」的成因。讀不到 bundle 版本時保守回退成照裝（走下面
            # install 模式的分支或直接視為已是最新，取決於 ctx.mode）。
            # 逃生口：拿 repo 當 bundle 反覆測同一版本號的開發者，若忘記
            # bump 版本會被這裡跳過而看不到剛改的程式碼，detail 裡點名
            # --reinstall 這個逃生口。
            bundle_version = _bundle_package_version(ctx.bundle)
            if bundle_version is not None and bundle_version == version:
                return StepOutcome(
                    True,
                    f"{version}（已是這個安裝包的版本；要強制重裝請用 --reinstall）",
                    skipped=True,
                )
        if ctx.mode == "install":
            # 預設模式下重跑就是升級，因此已安裝也要進 fix() 做一次 upgrade。
            return StepOutcome(False, f"目前 {version}，檢查是否有更新")
        # 走到這裡是 repair 模式，且環境正常、版本也未命中 bundle 比對——
        # 原本「已安裝就跳過」的行為。
        return StepOutcome(True, version, skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        before = installed_version(TOOL, ctx.run)
        if ctx.mode == "reinstall" and before is not None:
            uninstall_package(TOOL, ctx.run)
            before = None
        elif before is not None and tool_env_broken(TOOL, ctx.run):
            # 已安裝但殘缺（任何模式撞到都一樣處理，不限 --repair）——實測過
            # `uv tool install --force` 修不好（uv 認為環境已存在就不重建），
            # 唯一有效的做法是先整個移除再重裝，不能走下面的 upgrade 路徑。
            ctx.prompts.say(
                "      執行環境殘缺（缺 pyvenv.cfg），--force 無法修好，"
                "將先移除再重新安裝……"
            )
            uninstall_package(TOOL, ctx.run)
            before = None
        # uv 的輸出被 capture_output 收走，下載期間畫面完全沒有動靜。第一次安裝要
        # 抓 Python 與整棵相依樹，不先講一句，使用者會以為當掉而按 Ctrl-C。
        ctx.prompts.say(
            "      這一步可能要幾分鐘（第一次會下載 Python 與相依套件），請不要中斷。"
        )
        來源 = package_source(ctx)
        result = self._安裝或升級一次(ctx, before, 來源)
        if result.code != 0:
            message = (result.stderr or result.stdout).strip()
            # 升級的人幾乎必然正開著 Claude Code 用這個 server，這是這一步最常見
            # 的失敗。與其把 uv 的原始訊息丟回去要人自己猜要關什麼，直接把那個
            # MCP server 子行程終止掉再重試一次——只有重試仍失敗才需要使用者
            # 動手（八成是別的問題，例如防毒軟體另外鎖住了檔案）。
            if _是被鎖住(message):
                result = _終止鎖定行程並重試(ctx, lambda: self._安裝或升級一次(ctx, before, 來源))
                if result.code != 0:
                    return StepOutcome(False, _鎖定指引)
            elif 來源 is None and _像是認證問題(message):
                return StepOutcome(
                    False,
                    f"{message}\n"
                    "      若你當初是用 ZIP（install.cmd）安裝的，升級請下載新的 ZIP "
                    "再雙擊一次，不要用這個指令——這台機器沒有 GitHub 憑證。",
                )
            else:
                return StepOutcome(False, message)
        after = installed_version(TOOL, ctx.run)
        return StepOutcome(True, after or "已安裝")

    @staticmethod
    def _安裝或升級一次(ctx: Context, before: str | None, 來源: str | None) -> CommandResult:
        """依目前是否已安裝與來源，執行一次安裝或升級指令。

        抽成獨立方法是因為檔案鎖定重試需要原封不動地重跑同一個動作一次；寫成
        可重複呼叫的方法，比在 `fix()` 內用迴圈或複製一份判斷式更不容易寫岔。

        參數:
            ctx: 執行情境。
            before: 目前已安裝的版本；None 代表尚未安裝。
            來源: bundle 模式的本機來源路徑；None 代表使用預設 git 來源。
        回傳:
            指令執行結果。
        """
        if before:
            return upgrade_package(TOOL, ctx.run, source=來源)
        if 來源:
            return install_package(TOOL, ctx.run, source=來源)
        return install_package(TOOL, ctx.run)

    def remove(self, ctx: Context) -> StepOutcome | None:
        result = uninstall_package(TOOL, ctx.run)
        if result.code != 0:
            message = (result.stderr or result.stdout).strip()
            if _是被鎖住(message):
                result = _終止鎖定行程並重試(ctx, lambda: uninstall_package(TOOL, ctx.run))
                if result.code != 0:
                    return StepOutcome(False, _鎖定指引)
            else:
                return StepOutcome(False, message)
        return StepOutcome(True, "已移除")


class ConfigStep(Step):
    """確認參數檔可用；不可用時交給既有的安裝精靈。

    這一步不重寫精靈，只是把它當成一個步驟嵌進來——`run_setup()` 已經處理好
    多站台、舊格式轉換、金鑰驗證、原子寫入與權限收緊。

    `check()` 只在 `--repair` 模式下是真正的非互動檢查（驗一次既有金鑰，驗得過就
    跳過）；`install`／`reinstall` 模式一律回報「需要處理」，讓 `fix()` 每次都跑
    一遍精靈——「已經裝過」不等於「什麼都不必問」。精靈本身（`run_setup()`）已經
    處理好既有設定的沿用：網址帶現值當預設，金鑰驗得過會先問要不要換，答否就完全
    不必重貼，這樣才不會逼已經裝過的人每次都重新輸入一遍。
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
            return StepOutcome(False, f"已有站台 {names}，將確認設定")
        # repair 的用途就是回答「為什麼壞了」，而金鑰過期或網址被改正是最常見的
        # 成因之一；不驗等於少檢查一個最可能的原因。訊息一律不含金鑰內容。
        for site in settings.sites.values():
            try:
                asyncio.run(ctx.verify(site.url, site.api_key))
            except RedmineError as exc:
                return StepOutcome(False, f"站台 {site.name} 驗證失敗：{exc}")
        return StepOutcome(True, f"{names}（金鑰皆驗證通過）", skipped=True)

    def fix(self, ctx: Context) -> StepOutcome:
        # finish=False：註冊、冒煙測試與總結由步驟表自己的 RegisterStep／SmokeStep
        # 與 installer 的總結面板負責，精靈只做到參數檔為止。
        code = run_setup(
            ctx.prompts,
            config_path=ctx.config_path,
            run=ctx.run,
            verify=ctx.verify,
            finish=False,
            bundle_mode=ctx.bundle is not None,
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


class RegisterStep(Step):
    """把 MCP server 註冊給使用者勾選的 AI 工具。

    Claude Code 在這裡只是清單裡的一列，不是流程的前提——只用 Gemini CLI 的人
    不該被裝上一個他不用的 CLI，而他真正在用的工具反而拿不到 server。

    `check()` 在 install／reinstall 模式一律回報「需要處理」，讓清單每次都出現：
    使用者事後多裝了一種 AI 工具，只要沒重跑過清單就永遠不會被問到。這與
    `ConfigStep`／`InstallSkillsStep` 的取捨一致。
    """

    title = "註冊 MCP server"

    def _候選(self, ctx: Context) -> list[MCP註冊器]:
        """列出這台機器上可以註冊的對象。

        偵測失敗時保底只回 claude-code：註冊 MCP server 是本體功能，不能因為
        skill 腳本 clone 失敗或子專案缺檔就整個不做——那與 `InstallSkillsStep`
        「偵測失敗就整步略過」的取捨不同，因為那一步的產物是加分項。

        偵測成功但結果裡沒有 claude-code 時，額外把它補在最前面：只裝了其他
        AI 工具（例如只用 Gemini CLI）的機器，偵測腳本本來就不會印出
        claude-code，但 spec 要求這一列永遠要出現在清單上（文字為「尚未安裝，
        勾了會幫你裝 CLI 並註冊」）——使用者要不要順便裝 Claude Code，應該由
        他在清單上勾選決定，而不是因為這台機器沒偵測到就完全看不到這個選項。

        參數:
            ctx: 執行情境。
        回傳:
            註冊器清單，順序與偵測結果一致；補上的 claude-code 放在最前面。
        """
        detection = list_issue_skill_harnesses(
            ctx.run, platform=ctx.platform, repo_dir=ctx.bundle
        )
        if detection.failed or not detection.harnesses:
            return [取得註冊器(ClaudeCode註冊器.id)]
        候選 = [取得註冊器(h.id) for h in detection.harnesses]
        if not any(r.id == ClaudeCode註冊器.id for r in 候選):
            候選 = [取得註冊器(ClaudeCode註冊器.id), *候選]
        return 候選

    def check(self, ctx: Context) -> StepOutcome:
        """install／reinstall 一律要跑清單；repair 時只看有沒有人已經註冊好。

        參數:
            ctx: 執行情境。
        回傳:
            檢查結果。
        """
        已註冊 = [r.id for r in self._候選(ctx) if r.已註冊(ctx)]
        if ctx.mode != "repair":
            現況 = "、".join(已註冊) if 已註冊 else "尚未註冊"
            return StepOutcome(False, f"{現況}，將確認要註冊給哪些工具")
        if 已註冊:
            return StepOutcome(True, "、".join(已註冊), skipped=True)
        return StepOutcome(False, "尚未註冊")

    def fix(self, ctx: Context) -> StepOutcome:
        """列出清單，對勾中的每一個工具跑註冊。

        參數:
            ctx: 執行情境。
        回傳:
            至少一個成功就 `ok=True`；一個都沒勾為 `skipped=True`；全部失敗才紅燈。
        """
        候選 = self._候選(ctx)
        # 預設只勾「有自動寫入能力**且不會動到使用者既有檔案**」的：
        #   - 手動類勾了只是印步驟，預設勾沒有意義，只會讓使用者以為安裝器替他
        #     做了什麼。
        #   - gemini-cli 這種會改 `~/.gemini/settings.json` 的，改的是使用者自己的
        #     檔案，必須由他主動勾才發生；預設勾好等他來取消，等於把「動別人的
        #     檔案」設成不作為時的結果。
        預設 = [
            i
            for i, r in enumerate(候選)
            if r.id in 註冊器登錄表 and not r.會動到既有檔案
        ]
        選中 = ctx.prompts.choose_many(
            "偵測到這些 AI 工具，要把 redmine MCP server 註冊給哪些？",
            [r.說明(ctx) for r in 候選],
            預設,
        )
        if not 選中:
            ctx.prompts.say("      不註冊給任何工具。需要時重跑 redmine-mcp install 即可。")
            return StepOutcome(True, "略過（使用者未勾選）", skipped=True)

        成功: list[str] = []
        略過: list[str] = []
        失敗: list[str] = []
        for i in 選中:
            註冊器 = 候選[i]
            結果 = 註冊器.註冊(ctx)
            if not 結果.ok:
                失敗.append(f"{註冊器.id}：{結果.detail}")
                ctx.prompts.say(f"      [yellow]![/yellow] {註冊器.id} 註冊失敗：{結果.detail}")
            elif 結果.skipped:
                # ok=True 但 skipped=True 代表「沒有真的寫進去」——使用者選擇保留
                # 原設定（Gemini 同名覆蓋問過答否），或這一家本來就只印手動步驟。
                # 混進「成功」清單會讓總結印出的 ✓ 誤導成真的註冊上去了，因此另外
                # 歸類，detail 裡把 結果.detail（「保留原設定」「已印出手動步驟」
                # 之類）原樣帶出來。
                略過.append(f"{註冊器.id}（{結果.detail}）")
            else:
                成功.append(註冊器.id)
        if not 成功 and not 略過:
            return StepOutcome(False, "；".join(失敗) or "沒有任何工具註冊成功")
        detail = "、".join(成功 + 略過)
        if 失敗:
            detail = f"{detail}（{len(失敗)} 個失敗，見上方）"
        return StepOutcome(True, detail)

    def remove(self, ctx: Context) -> StepOutcome | None:
        """`--uninstall` 時對所有有註冊器的工具都跑一次移除。

        不看偵測結果：工具可能已經被使用者移除，但設定檔裡的那一筆還在。

        參數:
            ctx: 執行情境。
        回傳:
            移除結果；任一個失敗就回失敗，detail 帶出是哪一個。
        """
        失敗: list[str] = []
        for 註冊器 in 註冊器登錄表.values():
            結果 = 註冊器.移除(ctx)
            if not 結果.ok:
                失敗.append(f"{註冊器.id}：{結果.detail}")
        if 失敗:
            return StepOutcome(False, "；".join(失敗))
        return StepOutcome(True, "已拆除")


#: skill 安裝／移除失敗時給使用者的下一步。兩者都得先 clone 一個 private repo
#: （骨架不隨 wheel 發佈），沒憑證、沒網路都會失敗，而失敗的補救辦法是同一條。
_SKILLS_MANUAL_INSTALL = (
    "要補裝請跑 redmine-issue-skills 自己的一行指令："
    "https://github.com/Shinspire/MCP/blob/main/redmine-issue-skills/README.md"
)

#: 移除失敗時的手動步驟。刻意寫成人看得懂的三件事，而不是在這裡重寫一份
#: 剔除標記區塊的實作——那份邏輯只有安裝腳本一個實作，複製到 Python 這邊
#: 會變成兩份要同步的高風險程式碼。
_SKILLS_MANUAL_REMOVE = (
    "要手動移除：刪掉 ~/.claude/skills/redmine-issue-writing/ 與 "
    "~/.redmine-issue-guides/，再把全域指示檔（~/.gemini/GEMINI.md 之類）裡 "
    "<!-- redmine-issue-skills:begin --> 到 <!-- redmine-issue-skills:end --> "
    "那一段刪掉。"
)

#: skill 在各原生 harness 底下的預設安裝位置。
#: 由建構子注入而非放進 Context：Context 是 frozen dataclass 且被四種模式與大量
#: 測試共用，為一個步驟加欄位會波及所有既有建構點。
_DEFAULT_SKILL_DIR = Path.home() / ".claude" / "skills" / "redmine-issue-writing"
_CODEX_SKILL_DIR = Path.home() / ".codex" / "skills" / "redmine-issue-writing"

#: 各類別 harness 對使用者代表什麼意義，顯示在多選清單裡讓使用者知道勾了會發生
#: 什麼事。native 完全不碰既有檔案；fallback 會動使用者自己的全域指示檔（雖然是
#: 用可還原的標記區塊）；manual 則什麼都不會被自動改動。
_KIND_HINT = {
    "native": "不動你既有的檔案",
    "fallback": "會插入一段標記區塊",
    "manual": "只會印出手動步驟",
}


def _describe_harness(harness: Harness) -> str:
    """組出多選清單裡一個 harness 的顯示文字。

    參數:
        harness: 偵測到的 harness。
    回傳:
        供 `Prompts.choose_many()` 顯示的一行說明，依類別標出會發生什麼事；
        fallback 類額外帶出實際會被插入標記區塊的檔案路徑。
    """
    hint = _KIND_HINT.get(harness.kind, harness.kind)
    if harness.kind == "fallback" and harness.instruction_file:
        hint = f"會在 ~/{harness.instruction_file} 插入一段標記區塊"
    return f"{harness.id}（{hint}）"


class InstallSkillsStep(Step):
    """確認撰寫格式 skill 已安裝且未被改動。

    這一步是靜默失敗的主要防線。骨架移出套件後，skill 沒裝的後果不是啟動報錯，
    而是模型憑印象亂寫格式——沒有人會發現。因此 check() 不只驗「在不在」，
    還比對 manifest 記下的 sha256。

    「是不是最新版」要連 repo 才知道，離線時不驗——安裝器不該因為沒網路就說東西
    壞了。使用者重跑 install 一律會覆蓋成當前版本，過期最多多跑一次。

    雜湊比對只在 `--repair` 模式下用來判斷「skill 是不是被改壞了」；`install`／
    `reinstall` 模式一律回報「需要處理」，讓多選清單每次都出現——否則使用者事後
    多裝了一種 AI 工具（例如 Gemini CLI），只要雜湊沒變就永遠不會再被問一次要不
    要也讓它讀得到。
    """

    title = "撰寫格式 skill"

    def __init__(self, skill_dir: Path | None = None) -> None:
        """建立步驟。

        參數:
            skill_dir: skill 的安裝目錄；測試注入 tmp_path。未指定時同時檢查
                Claude Code 與 Codex 的原生 skill 位置。
        """
        self._skill_dirs = (skill_dir,) if skill_dir else (_DEFAULT_SKILL_DIR, _CODEX_SKILL_DIR)

    def check(self, ctx: Context) -> StepOutcome:
        """驗證 skill 已安裝、manifest 可讀、且每份檔案的雜湊相符。

        參數:
            ctx: 執行情境。
        回傳:
            `ok=True` 且 `skipped=True` 代表本來就好。
        """
        for skill_dir in self._skill_dirs:
            manifest_path = skill_dir / ".installed.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                files = manifest["files"]
                version = manifest["version"]
            except (OSError, ValueError, KeyError):
                return StepOutcome(ok=False, detail="manifest 讀不到或格式不對")

            if ctx.mode != "repair":
                # 同 ConfigStep：install／reinstall 都要讓多選清單每次出現，雜湊
                # 比對只在 --repair 用來判斷 skill 有沒有被改壞，不再決定「要不要
                # 問人」。
                return StepOutcome(ok=False, detail=f"已裝 {version}，將確認要讀取的 AI 工具")

            changed = [
                rel
                for rel, expected in files.items()
                if self._sha256(skill_dir / rel) != expected
            ]
            if changed:
                return StepOutcome(ok=False, detail=f"檔案被改過：{'、'.join(sorted(changed))}")
            return StepOutcome(ok=True, detail=version, skipped=True)
        return StepOutcome(ok=False, detail="沒裝")

    def fix(self, ctx: Context) -> StepOutcome:
        """先列出偵測到的 agent 平台讓使用者多選，再依選擇安裝；失敗時降級為警告。

        流程：
          1. 以 `--list`／`-List` 問腳本這台機器上偵測到哪些 harness。
          2. 偵測本身執行失敗（clone 失敗、腳本失敗）→ 印出失敗訊息（含原始
             輸出，方便使用者判斷是不是自己的 git 憑證沒設好）後跳過，不擋住
             整個安裝。這與「執行成功但一個都沒偵測到」是不同的話術，見
             `HarnessDetection`。
          3. 執行成功但一個都沒偵測到 → 印一句說明後跳過。
          4. 有偵測到 → 顯示多選清單，預設全部勾選（偵測到的就是這台機器有裝的）。
             **bundle 模式（ZIP 一鍵安裝）也一樣要問**：`fallback` 類會在使用者
             既有的全域指示檔（`~/.gemini/GEMINI.md` 之類）插入標記區塊，那是
             使用者自己的檔案，不該在他沒選過的情況下被動到——即使他是非 IT
             同事、即使這會讓「安裝步驟說明.txt」的步驟數多一步。直接按 Enter 就是
             接受預設（等同全選），對非 IT 使用者的操作成本極低。
          5. 使用者全部取消 → 印一句後跳過。
          6. 有選 → 以 `only=` 選中的 id、`assume_yes=True` 真正安裝。

        skill 是加分項——沒有它 MCP server 一樣能查單、建單、追加註記，只是模型
        少了一份公司格式可讀。`run_install` 的迴圈是「任一步 fix() 失敗就 return
        EXIT_FAILED」，因此本步驟無論哪個分支都回 `ok=True`；真正裝壞的情況由
        下一次 `--repair` 的 `check()` 抓出來。

        參數:
            ctx: 執行情境。
        回傳:
            一律 `ok=True`；未實際安裝（偵測失敗、未偵測到、使用者取消、或安裝
            腳本失敗）時 `skipped=True`，detail 以「略過」開頭。
        """
        detection: HarnessDetection = list_issue_skill_harnesses(
            ctx.run, platform=ctx.platform, repo_dir=ctx.bundle
        )
        if detection.failed:
            訊息 = detection.message[:200] if detection.message else "（沒有輸出）"
            ctx.prompts.say(f"      [yellow]![/yellow] 偵測 AI 工具時失敗：{訊息}")
            ctx.prompts.say(f"      {_SKILLS_MANUAL_INSTALL}")
            return StepOutcome(ok=True, skipped=True, detail="略過（偵測失敗）")
        if not detection.harnesses:
            ctx.prompts.say("      這台機器上沒有偵測到支援的 AI 工具，略過這一步。")
            ctx.prompts.say(f"      {_SKILLS_MANUAL_INSTALL}")
            return StepOutcome(ok=True, skipped=True, detail="略過（未偵測到工具）")

        harnesses = detection.harnesses
        選項 = [_describe_harness(h) for h in harnesses]
        # 預設只勾 native：那一類裝進 harness 自己的 skill 目錄，不動使用者的任何
        # 既有檔案。fallback 會在他的全域指示檔（`~/.gemini/GEMINI.md` 之類）插入
        # 標記區塊，manual 勾了也只是印步驟——前者動的是別人的檔案，必須由他主動
        # 勾才發生，預設勾好等他來取消，等於把「動別人的檔案」設成不作為時的結果；
        # 後者預設勾只會讓他以為安裝器替他做了什麼。與 RegisterStep 同一條規則。
        預設 = [i for i, h in enumerate(harnesses) if h.kind == "native"]
        選中 = ctx.prompts.choose_many(
            "偵測到這些 AI 工具，要讓哪些讀得到單子撰寫格式？", 選項, 預設
        )
        if not 選中:
            ctx.prompts.say("      已取消，不安裝撰寫格式 skill。")
            return StepOutcome(ok=True, skipped=True, detail="略過（使用者取消）")

        only_ids = [harnesses[i].id for i in 選中]
        result = install_issue_skills(
            ctx.run,
            platform=ctx.platform,
            repo_dir=ctx.bundle,
            only=only_ids,
            assume_yes=True,
        )
        if result.code != 0:
            output = (result.stderr or result.stdout).strip()
            ctx.prompts.say(f"      [yellow]![/yellow] skill 沒裝成功：{output[:200]}")
            ctx.prompts.say(f"      {_SKILLS_MANUAL_INSTALL}")
            return StepOutcome(ok=True, skipped=True, detail="略過（MCP server 不受影響）")
        return StepOutcome(ok=True, detail="已安裝")

    def remove(self, ctx: Context) -> StepOutcome | None:
        """`--uninstall` 時移除 skill 與後備指標；取不到腳本時印手動步驟。

        移除本身完全是本機操作（刪兩個目錄、剔掉標記區塊），但它得先 clone repo
        才拿得到那支腳本——離線或憑證失效時就移不掉，而使用者當下要的只是把東西
        清掉。這裡選擇「clone 失敗就印出手動移除步驟並回 ok」，而不是在 Python
        這邊依 manifest 自己刪：剔除標記區塊是這支分支風險最高的一段邏輯，目前
        只有安裝腳本一個實作，複製一份到這裡就變成兩份要同步的實作，一邊改漏就
        會吞掉使用者自己寫在指示檔裡的內容——正是 spec 要求「複製邏輯唯一實作在
        shell 腳本」的理由。

        參數:
            ctx: 執行情境。
        回傳:
            移除結果；腳本取不到或失敗時 `ok=True, skipped=True`，不擋住其餘步驟。
        """
        result = install_issue_skills(
            ctx.run, platform=ctx.platform, uninstall=True, repo_dir=ctx.bundle
        )
        if result.code != 0:
            output = (result.stderr or result.stdout).strip()
            ctx.prompts.say(f"      [yellow]![/yellow] skill 沒移除成功：{output[:200]}")
            ctx.prompts.say(f"      {_SKILLS_MANUAL_REMOVE}")
            return StepOutcome(ok=True, skipped=True, detail="略過（請見上方手動步驟）")
        return StepOutcome(ok=True, detail="已移除")

    @staticmethod
    def _sha256(path: Path) -> str:
        """算出檔案的 sha256；檔案不存在時回空字串（必然與 manifest 不符）。

        參數:
            path: 檔案路徑。
        回傳:
            小寫十六進位摘要，或空字串。
        """
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""


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
