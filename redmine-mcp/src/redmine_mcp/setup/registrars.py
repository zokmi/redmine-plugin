"""各 AI 工具的 MCP server 註冊器。

安裝器不再假設「MCP 客戶端 ＝ Claude Code」：每個 AI 工具對應一個註冊器，由
`RegisterStep` 依使用者在清單裡勾選的結果決定跑哪幾個。新增一家工具＝新增一個
註冊器並登記進 `註冊器登錄表`，步驟本身不必改。

刻意不做成資料驅動（在表格裡描述每家的設定檔路徑與 JSON 位置）：目前兩家的機制
根本不同（一家走 CLI 指令、一家寫設定檔），抽象會比實作長。第三家真的出現時再抽。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from redmine_mcp.setup.external import (
    claude_version,
    install_claude_cli,
    is_registered,
    register,
    resolve_executable,
    unregister,
)
from redmine_mcp.setup.fsops import write_config
from redmine_mcp.setup.mcp_config import remove_mcp_server, upsert_mcp_server, 讀出設定

if TYPE_CHECKING:  # 只為型別註記；執行期匯入會與 steps.py 形成循環。
    from redmine_mcp.setup.steps import Context, StepOutcome

#: 註冊時使用的 server 名稱。四處必須一致：wizard.py 的 _SERVER_NAME、steps.py 的
#: SERVER_NAME、本模組、以及使用者在 /mcp 看到的名字。
SERVER_NAME = "redmine"

#: 要註冊的工具名稱。
TOOL = "redmine-mcp"


class MCP註冊器(Protocol):
    """把 redmine MCP server 掛到某一個 AI 工具底下的能力。

    欄位:
        id: 對應 harnesses.tsv 第一欄的 harness id。
        會動到既有檔案: 註冊時是否會改寫使用者自己的設定檔。清單的預設勾選規則
            直接看這個旗標：會動到的**一律不預設勾**——改別人的檔案必須是他主動
            勾選才發生，不能預設勾好等他來取消。每個實作都必須明寫，漏了會在
            `RegisterStep` 取值時就地 AttributeError，不會靜默被當成安全的那一類。
    """

    id: str
    會動到既有檔案: bool

    def 說明(self, ctx: Context) -> str:
        """清單裡顯示的一行文字，必須講明「勾了會動到什麼」。"""
        ...

    def 已註冊(self, ctx: Context) -> bool:
        """這個工具底下是否已經有本 server。"""
        ...

    def 註冊(self, ctx: Context) -> StepOutcome:
        """把 server 註冊上去。"""
        ...

    def 移除(self, ctx: Context) -> StepOutcome:
        """把註冊拆掉；本來就沒有時視為成功。"""
        ...


class ClaudeCode註冊器:
    """Claude Code：走官方 CLI 的 `claude mcp add`，不碰任何設定檔。

    scope 固定 user：project scope 會寫進進版控的 `.mcp.json`，SETUP.md 明文禁止，
    因此不開放成選項。
    """

    id = "claude-code"
    #: 走 `claude mcp add`，設定由 Claude Code 自己管，不碰使用者的任何既有檔案。
    會動到既有檔案 = False

    def 說明(self, ctx: Context) -> str:
        """組出清單顯示文字；未安裝時明講會幫忙裝 CLI。

        參數:
            ctx: 執行情境。
        回傳:
            一行說明。
        """
        if claude_version(ctx.run) is None:
            return f"{self.id}（尚未安裝，勾了會幫你裝 CLI 並註冊）"
        return f"{self.id}（用 claude mcp add 註冊，不動你既有的檔案）"

    def 已註冊(self, ctx: Context) -> bool:
        """以 `claude mcp get` 判斷是否已註冊。

        參數:
            ctx: 執行情境。
        回傳:
            已註冊時為 True。
        """
        return is_registered(SERVER_NAME, ctx.run)

    def 註冊(self, ctx: Context) -> StepOutcome:
        """必要時先裝 CLI，再以 user scope 註冊。

        不再問「要裝 Claude Code 嗎」：使用者在清單上勾了這一列，就是同意。

        已註冊時直接回報成功、不跑 `claude mcp add`：那個指令對已存在的名稱會
        失敗（實測訊息 `MCP server redmine already exists in user config`），於是
        每跑一次安裝就紅一次，還會叫使用者去執行他其實早就執行過的那條指令。
        舊流程靠 `check()` 擋住（已註冊就不進 `fix()`），改成多選清單之後每個勾
        中的工具都會無條件跑一次註冊，這層保護必須補在註冊器自己身上——與
        `移除()` 先判斷「本來就沒有」對稱。啟動指令固定是工具名稱 `redmine-mcp`
        （見 `resolve_executable()`），不會因為版本或路徑變動而過期，因此「已註冊
        就不動」不會留下指向錯誤的舊設定。

        參數:
            ctx: 執行情境。
        回傳:
            註冊結果；本來就已註冊時 `skipped=True`；失敗時 detail 帶外部指令的
            原始訊息。
        """
        from redmine_mcp.setup.steps import StepOutcome

        command = resolve_executable(TOOL)
        手動 = f"claude mcp add {SERVER_NAME} --scope user -- {command}"
        已安裝 = claude_version(ctx.run) is not None
        if 已安裝 and self.已註冊(ctx):
            return StepOutcome(True, "已註冊（未變更）", skipped=True)
        if not 已安裝:
            ctx.prompts.say("      找不到 Claude Code CLI，先幫你安裝……")
            install_claude_cli(ctx.run, ctx.platform)
            if claude_version(ctx.run) is None:
                ctx.prompts.say(f"      裝完仍找不到 claude。請開一個新的終端機後執行：{手動}")
                return StepOutcome(False, "Claude Code CLI 裝完仍不在 PATH 上")
        result = register(SERVER_NAME, command, ctx.run)
        if result.code != 0:
            ctx.prompts.say(f"      請自行執行：{手動}")
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        return StepOutcome(True, "scope: user")

    def 移除(self, ctx: Context) -> StepOutcome:
        """拆掉註冊；本來就沒註冊時視為成功，不執行 CLI 指令。

        機器上沒有 `claude` 執行檔時，`claude mcp remove` 會以 127 結束，若照樣
        當成失敗，只用 Gemini CLI 的機器跑 `--uninstall` 會被這一項拖成紅燈，
        儘管它本來就沒有東西可拆。因此先用 `已註冊()` 判斷，沒註冊就直接回報
        成功並跳過，不呼叫任何外部指令。

        參數:
            ctx: 執行情境。
        回傳:
            移除結果；本來就沒註冊時 `skipped=True`。
        """
        from redmine_mcp.setup.steps import StepOutcome

        if not self.已註冊(ctx):
            return StepOutcome(True, "本來就沒有", skipped=True)
        result = unregister(SERVER_NAME, ctx.run)
        if result.code != 0:
            return StepOutcome(False, (result.stderr or result.stdout).strip())
        return StepOutcome(True, "已拆除")


#: Gemini CLI 的使用者層設定檔。MCP server 掛在頂層的 mcpServers 物件下。
_GEMINI_設定檔 = Path.home() / ".gemini" / "settings.json"


class GeminiCLI註冊器:
    """Gemini CLI：直接改 `~/.gemini/settings.json`。

    這是唯一會動到使用者既有檔案的註冊器，因此三條防線缺一不可：
      1. 讀不懂的 JSON（解析失敗、形狀不對、含註解、或非 UTF-8 編碼）一律不碰，
         改印手動步驟。
      2. 已經有同名 server 且內容不同時，當面問過才覆蓋——即使一鍵安裝模式也
         照問：蓋掉使用者自己設好的東西不該被自動代答。
      3. 寫入走 `write_config()` 的原子替換，中途失敗不會留下半份檔案。
    """

    id = "gemini-cli"
    #: 直接改寫 `~/.gemini/settings.json`——使用者自己的檔案，因此清單上不預設勾。
    會動到既有檔案 = True

    def __init__(self, settings_path: Path | None = None) -> None:
        """建立註冊器。

        參數:
            settings_path: 設定檔路徑；預設為 `~/.gemini/settings.json`，
                測試注入 tmp_path。
        """
        self._path = settings_path or _GEMINI_設定檔

    def _讀原文(self) -> str | None:
        """讀出設定檔內容。

        必須區分「檔案本來就不存在」與「檔案存在但讀不懂」兩種情況：前者代表
        「還沒有任何設定」，可以放心當成空字串建立新檔；後者（例如使用者用系統
        內碼存過、內容不是 UTF-8）代表**檔案有內容、只是我們解不出來**，若也
        當成空字串，`upsert_mcp_server()` 會把它當成一份空設定去新增內容，寫回
        後就會把使用者原本的位元組整個蓋掉——這與「讀不懂就完全不碰檔案」的
        既有防線互相矛盾。因此讀不懂編碼時回傳 `None`，讓呼叫端一律走「不寫入、
        印手動步驟」的路徑，而不是讓 `UnicodeDecodeError` 這種例外往外炸穿
        `RegisterStep.check()`，把還沒印出清單就中止安裝的窗口堵起來。

        回傳:
            檔案內容；檔案不存在時為空字串；讀不懂編碼或讀取失敗時為 None。
        """
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeDecodeError):
            return None

    def _手動步驟(self, ctx: Context) -> None:
        """印出使用者可以自己貼進設定檔的那一段。

        參數:
            ctx: 執行情境。
        """
        ctx.prompts.say(
            f'      請自己在 {self._path} 的 mcpServers 底下加入：'
            f'"{SERVER_NAME}": {{"command": "{resolve_executable(TOOL)}"}}'
        )

    def 說明(self, ctx: Context) -> str:
        """組出清單顯示文字，明講會動到哪個檔案。

        參數:
            ctx: 執行情境。
        回傳:
            一行說明。
        """
        return f"{self.id}（會改 {self._path} 的 mcpServers.{SERVER_NAME}）"

    def 已註冊(self, ctx: Context) -> bool:
        """設定檔裡是否已有本 server（entry 存在就算，遠端型也算）。

        原本只認「有 `command` 欄位」，`{"url": ...}` 這種遠端型 entry 會被判斷
        成「沒有」，導致 `註冊()` 誤以為本來就沒有這一筆而直接覆蓋掉。改成只要
        `mcpServers[名稱]` 這個鍵存在（不論值是 dict、字串、或任何 JSON 值）就算
        已註冊；讀不懂編碼或格式時保守回報「未註冊」，不拋例外。

        參數:
            ctx: 執行情境。
        回傳:
            已註冊時為 True。
        """
        原文 = self._讀原文()
        if 原文 is None:
            return False
        return 讀出設定(原文, SERVER_NAME) is not None

    def 註冊(self, ctx: Context) -> StepOutcome:
        """把 server 寫進 settings.json。

        參數:
            ctx: 執行情境。
        回傳:
            註冊結果；使用者選擇保留原設定或設定檔讀不懂時為
            `ok=True, skipped=True`，`ok=False` 才代表真正失敗。
        """
        from redmine_mcp.setup.steps import StepOutcome

        指令 = resolve_executable(TOOL)
        原文 = self._讀原文()
        if 原文 is None:
            ctx.prompts.say(
                f"      [yellow]![/yellow] {self._path} 讀不懂（可能不是 UTF-8 編碼），沒有動它。"
            )
            self._手動步驟(ctx)
            return StepOutcome(False, "設定檔編碼不明，未修改")
        既有 = 讀出設定(原文, SERVER_NAME)
        目標值 = {"command": 指令}
        if 既有 is not None and 既有 != 目標值:
            if not ctx.prompts.confirm(
                f"{self._path} 裡已經有一個叫 {SERVER_NAME} 的 MCP server"
                f"（目前是 {json.dumps(既有, ensure_ascii=False)}），"
                f"要覆蓋成 {json.dumps(目標值, ensure_ascii=False)} 嗎？"
            ):
                return StepOutcome(True, "保留原設定", skipped=True)
        新文 = upsert_mcp_server(原文, SERVER_NAME, 指令)
        if 新文 is None:
            ctx.prompts.say(
                f"      [yellow]![/yellow] {self._path} 不是能安全修改的 JSON，沒有動它。"
            )
            self._手動步驟(ctx)
            return StepOutcome(False, "設定檔格式不明，未修改")
        try:
            write_config(self._path, 新文)
        except OSError as exc:
            ctx.prompts.say(f"      [yellow]![/yellow] 寫入失敗：{exc}")
            self._手動步驟(ctx)
            return StepOutcome(False, str(exc))
        return StepOutcome(True, f"已寫入 {self._path.name}")

    def 移除(self, ctx: Context) -> StepOutcome:
        """從 settings.json 移除本 server；檔案或該鍵本來就不存在時視為成功。

        參數:
            ctx: 執行情境。
        回傳:
            移除結果；設定檔讀不懂編碼時回報失敗，不會嘗試改寫。
        """
        from redmine_mcp.setup.steps import StepOutcome

        if not self._path.is_file():
            return StepOutcome(True, "本來就沒有")
        原文 = self._讀原文()
        if 原文 is None:
            return StepOutcome(False, f"{self._path} 讀不懂（可能不是 UTF-8 編碼），沒有動它")
        新文 = remove_mcp_server(原文, SERVER_NAME)
        if 新文 is None:
            return StepOutcome(False, f"{self._path} 不是能安全修改的 JSON，沒有動它")
        try:
            write_config(self._path, 新文)
        except OSError as exc:
            return StepOutcome(False, str(exc))
        return StepOutcome(True, "已拆除")


class 手動註冊器:
    """偵測得到、但我們沒有自動寫入能力的工具。

    只印出使用者可以自己貼的內容，不碰任何檔案。刻意不去猜各家設定檔的位置與
    schema：猜錯的代價是動到使用者的設定檔，而這裡的收益只是省他一次複製貼上。
    """

    def __init__(self, harness_id: str) -> None:
        """建立註冊器。

        參數:
            harness_id: 對應 harnesses.tsv 第一欄的 harness id。
        """
        self.id = harness_id
        #: 只印手動步驟，什麼都不碰。
        self.會動到既有檔案 = False

    def 說明(self, ctx: Context) -> str:
        """清單顯示文字。

        參數:
            ctx: 執行情境。
        回傳:
            一行說明。
        """
        return f"{self.id}（只會印出手動步驟）"

    def 已註冊(self, ctx: Context) -> bool:
        """一律回 False。

        我們無從得知使用者有沒有自己貼進去，回 False 讓它照樣出現在清單上。

        參數:
            ctx: 執行情境。
        回傳:
            固定 False。
        """
        return False

    def 註冊(self, ctx: Context) -> StepOutcome:
        """只印出手動步驟。

        參數:
            ctx: 執行情境。
        回傳:
            固定 `ok=True, skipped=True`——沒做事，但也不是失敗。
        """
        from redmine_mcp.setup.steps import StepOutcome

        ctx.prompts.say(
            f"      {self.id}：請在它的 MCP 設定裡加入一個名為 {SERVER_NAME} 的 server，"
            f"啟動指令是 {resolve_executable(TOOL)}。"
        )
        return StepOutcome(True, "已印出手動步驟", skipped=True)

    def 移除(self, ctx: Context) -> StepOutcome:
        """只印出手動移除步驟。

        參數:
            ctx: 執行情境。
        回傳:
            固定 `ok=True, skipped=True`。
        """
        from redmine_mcp.setup.steps import StepOutcome

        ctx.prompts.say(
            f"      {self.id}：請自行從它的 MCP 設定裡移除名為 {SERVER_NAME} 的 server。"
        )
        return StepOutcome(True, "已印出手動步驟", skipped=True)


#: harness id 到註冊器的登錄表。新增一家工具＝在這裡加一列，步驟本身不必改。
註冊器登錄表: dict[str, MCP註冊器] = {
    ClaudeCode註冊器.id: ClaudeCode註冊器(),
    GeminiCLI註冊器.id: GeminiCLI註冊器(),
}


def 取得註冊器(harness_id: str) -> MCP註冊器:
    """查出某個 harness 的註冊器；沒有登記的一律走手動。

    參數:
        harness_id: harness id。
    回傳:
        對應的註冊器。
    """
    return 註冊器登錄表.get(harness_id) or 手動註冊器(harness_id)
