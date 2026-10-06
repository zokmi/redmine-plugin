"""互動式安裝精靈的流程編排。

所有 IO（提問、遮蔽輸入、確認、選擇、輸出）都由 Prompts 注入，外部指令與
Redmine 驗證同樣以參數傳入，因此整個流程可在不碰終端機、不打真實 API 的
前提下逐分支測試。
"""
from __future__ import annotations

import asyncio
import tomllib
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redmine_mcp.config import _SITE_NAME_PATTERN, ConfigError
from redmine_mcp.errors import RedmineError
from redmine_mcp.setup import toml_edit
from redmine_mcp.setup.external import (
    CommandRunner,
    is_registered,
    register,
    resolve_executable,
    smoke_test,
    unregister,
)
from redmine_mcp.setup.fsops import tighten_permissions, write_config
from redmine_mcp.setup.site_hints import (
    derive_site_label,
    derive_site_name,
    normalize_url,
    subpath_candidate,
)

#: 驗證函式的介面：收 url 與 api_key，成功回傳登入帳號，失敗拋 RedmineError。
Verifier = Callable[[str, str], Coroutine[Any, Any, str]]

#: 註冊到 Claude Code 時使用的名稱與執行檔名。
_SERVER_NAME = "redmine"
_TOOL = "redmine-mcp"


@dataclass(frozen=True)
class Prompts:
    """精靈與使用者互動的全部管道。

    欄位:
        say: 輸出一行訊息。
        panel: 輸出一組帶標題的訊息（正式實作會畫成 Rich 面板）。
        ask: 提問並取得文字答案；第二參數為預設值，使用者直接按 Enter 時採用。
        ask_secret: 提問並取得遮蔽輸入的答案，供輸入 API key。
        confirm: 是／否確認，回傳 True 代表同意。
        choose: 從選項中擇一，回傳選中的索引。
        choose_many: 顯示帶勾選狀態的清單，回傳使用者最終選中的索引清單。
            正式實作優先畫方向鍵選單（↑↓ 移動、空白鍵勾選，可同時勾多個、Enter 送出）；
            沒有終端可用或主控台畫不出來時，才退回下面這套輸入編號的問法。
            第三個參數是預設勾選的索引（呼叫端算好的預設值，例如「偵測到的
            harness 全部預設勾選」），使用者輸入空白代表接受預設，輸入編號則
            以那組編號**取代**預設，而不是切換勾選狀態。
        open_url: 在使用者的預設瀏覽器開啟一個網址。用來把人直接送到 Redmine 的
            /my/account 拿金鑰——「API key 去哪拿」是非 IT 同事最常卡住的一題。
    """

    say: Callable[[str], None]
    panel: Callable[[str, Sequence[str]], None]
    ask: Callable[..., str]
    ask_secret: Callable[[str], str]
    confirm: Callable[[str], bool]
    choose: Callable[[str, Sequence[str]], int]
    choose_many: Callable[[str, Sequence[str], Sequence[int]], list[int]]
    open_url: Callable[[str], None]


#: 探測終端編不編得出來的字元：Rich 印的裝飾記號，以及一定會出現的中文。
#: 用實際會印的東西探測，而不是猜哪些字碼頁有問題。
_會印出來的字元 = "✓×！請輸入編號"


def _讓輸出容錯() -> None:
    """把 `sys.stdout` 就地改成「編不出來的字元不會拋例外」。

    實測踩過兩次的坑：
      1. 字碼頁 950 的終端（繁中 Windows 的預設之一）上，Rich 印 `[green]✓[/green]`
         會拋 UnicodeEncodeError，整個安裝中斷在那裡——而檔案其實已經寫好了，
         使用者只看到一坨 traceback。
      2. CI 的 Windows runner（cp1252）上，`typer.prompt("請輸入編號")` 同樣炸掉。

    第二點正是「只包 Rich 不夠」的證據：前一版做法是回傳一個包好的資料流交給
    `rich.Console(file=...)`，但 typer／click 走的是**它自己解析出來的輸出流**，
    完全沒被保護到，於是畫不出方向鍵選單時的退路（要人輸入編號）自己會炸。
    因此改成就地 `reconfigure()` `sys.stdout`：Rich 與 click 都拿同一個資料流，
    一次到位。編不出來的字元退化成 `?`，中文與其餘訊息照樣輸出，答案照樣讀得到。

    終端本來就編得出（UTF-8）時什麼都不做——沒必要把 `errors` 降級，那會讓真正
    的編碼問題被靜默吞掉。
    """
    import sys

    stream = sys.stdout
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return
    try:
        _會印出來的字元.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        pass
    else:
        return

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        # 被 pytest 之類的機制換成別種物件時沒有 reconfigure 可用，維持原狀即可——
        # 那種情境下的輸出目標本來就不是真終端。
        return
    try:
        reconfigure(errors="replace")
    except (OSError, ValueError):
        # 資料流已經 detach、或不接受這個參數。印不了漂亮的字不值得讓安裝失敗。
        return


def _紀錄函式(log_path: Path | None) -> Callable[[str], None]:
    """回傳「把一行訊息追加進安裝紀錄」的函式；沒有紀錄檔時回傳 no-op。

    為什麼需要這一層：一鍵安裝的紀錄檔原本全靠 PowerShell 的 Start-Transcript，
    但它抓 native 子行程（也就是本程式）的輸出是從主控台緩衝區讀回來的，中文這種
    雙寬字元佔兩格，於是每個字都被記成兩次——紀錄檔裡出現「安安裝裝」。那份檔案
    是要傳給 IT 判讀的，糊掉等於沒有。改由這一層在印出的同時自己追加一份，寫進去
    的就是乾淨的 UTF-8。

    刻意只記錄「我們印出去的訊息」與「問題本身」，**不記錄使用者輸入的任何答案**：
    金鑰就是從提問拿到的，而紀錄檔會被整份傳給 IT，這條界線不能靠呼叫端自己小心。

    每一行即時開檔追加，而不是攢在記憶體最後一次寫出：安裝失敗時行程往往就結束在
    那一行，攢著的內容會全部消失，而那正是最需要被記下來的一次。

    參數:
        log_path: 紀錄檔路徑；None 代表不留紀錄。
    回傳:
        接收一行（可能含 Rich 標記）文字並追加成純文字的函式。
    """
    if log_path is None:
        return lambda _文字: None

    from rich.errors import MarkupError
    from rich.text import Text

    寫壞了 = False

    def 記錄(文字: str) -> None:
        nonlocal 寫壞了
        if 寫壞了:
            return
        try:
            純文字 = Text.from_markup(文字).plain
        except MarkupError:
            # 外部內容（例如 uv 的 stderr）可能含有形如 [xxx] 的片段，解析不了就
            # 原樣記下來——紀錄的價值在內容，不在排版。
            純文字 = 文字
        try:
            with log_path.open("a", encoding="utf-8") as 檔:
                print(純文字, file=檔)
        except OSError:
            # 紀錄只是輔助（唯讀目錄、磁碟滿），絕不能讓安裝流程中斷在這裡。
            # 記下已經壞掉，後續每一行不必再重試。
            寫壞了 = True

    return 記錄


def _parse_multi_select(raw: str, default: Sequence[int], option_count: int) -> list[int] | None:
    """把使用者為 `choose_many` 輸入的字串解析成索引清單，是可獨立測試的純函式。

    參數:
        raw: 使用者輸入的原始字串（尚未 strip）。
        default: 空輸入時採用的 0-based 預設索引。
        option_count: 選項總數，用來驗證編號範圍。
    回傳:
        解析出的 0-based 索引清單（已去重排序）：
        - 空白輸入 → 回傳 `default`（原樣接受預設，不是切換）。
        - `"0"` → 回傳空清單，代表使用者一個都不要。
        - 逗號分隔的編號（如 `"1,2"`) → 回傳對應的 0-based 索引，**取代**預設。
        - 非數字或超出 1..option_count 範圍 → 回傳 None，呼叫端應重新詢問。
    """
    text = raw.strip()
    if not text:
        return list(default)
    if text == "0":
        return []
    indices: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit():
            return None
        number = int(part)
        if not (1 <= number <= option_count):
            return None
        indices.add(number - 1)
    return sorted(indices)


def _可用方向鍵選單() -> bool:
    """判斷這個工作階段能不能畫方向鍵選單。

    條件是 stdin 與 stdout 都接在真終端上：被導向檔案或管線（`... | bash`、CI、
    pytest）時畫不出來，也讀不到按鍵，必須退回輸入編號的問法。

    回傳:
        可以用方向鍵選單時為 True。
    """
    import sys

    return bool(
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    )


def _拆出提問與前言(label: str) -> tuple[list[str], str]:
    """把可能跨多行的提問拆成「前言各行」與「最後一行的問句」。

    questionary 的訊息只適合一行；多行提問（例如「偵測到既有設定：…\\n要怎麼處理？」）
    要把前面幾行先印出來，只留最後一行當問句，選單才不會擠成一團。

    參數:
        label: 原始提問（可能含換行與 Rich 標記）。
    回傳:
        `(前言各行, 問句)`。
    """
    行 = label.split("\n")
    return 行[:-1], 行[-1]


def _純文字(text: str) -> str:
    """去掉 Rich 標記，供不認得標記的 questionary 顯示。

    參數:
        text: 可能含 `[green]…[/green]` 這類標記的字串。
    回傳:
        純文字；解析不了時原樣回傳。
    """
    from rich.errors import MarkupError
    from rich.text import Text

    try:
        return Text.from_markup(text).plain
    except MarkupError:
        return text


#: 方向鍵選單底下那一行操作說明。單選與多選共用同一組用語，使用者只要學一次。
_單選說明 = "↑↓ 移動，Enter 選定"
_多選說明 = "↑↓ 移動，空白鍵勾選／取消（可同時勾多個），Enter 送出"


def _方向鍵單選(label: str, options: Sequence[str], 印一行: Callable[[str], None]) -> int | None:
    """畫一個方向鍵單選選單，回傳選中的索引。

    畫不出來時回傳 None，呼叫端要退回輸入編號的問法——不是所有情境都有終端可用
    （管線執行、CI、pytest），也不是所有主控台都吃得下 questionary 的字元
    （字碼頁 950 的視窗畫不出 `❯`，會拋 UnicodeEncodeError）。這裡刻意把例外全部
    接住換成 None：選單只是輸入方式，畫不出來絕不能讓安裝中斷。

    Ctrl-C 不在此列：`unsafe_ask()` 會讓 KeyboardInterrupt 往上拋（KeyboardInterrupt
    不是 Exception 的子類，接不到），由 typer／click 統一處理成中止，語意與其他提問
    一致。

    參數:
        label: 提問，可含換行與 Rich 標記。
        options: 選項文字。
        印一行: 印出一行的函式，用來輸出多行提問的前言。
    回傳:
        選中的 0-based 索引；畫不出來時為 None。
    """
    if not _可用方向鍵選單():
        return None
    try:
        import questionary

        前言, 問句 = _拆出提問與前言(label)
        for 一行 in 前言:
            印一行(一行)
        選中 = questionary.select(
            _純文字(問句),
            choices=[
                questionary.Choice(title=_純文字(選項), value=索引)
                for 索引, 選項 in enumerate(options)
            ],
            instruction=_單選說明,
        ).unsafe_ask()
    except Exception as 例外:
        # 不可以靜默退回：兩條路徑印進紀錄檔的內容一模一樣，事後完全查不出畫面
        # 到底是方向鍵選單還是輸入編號的退路。把原因印出來，退路照走。
        印一行(f"[yellow]![/yellow] 畫不出方向鍵選單（{例外!r}），改用輸入編號的問法。")
        return None
    # questionary 的回傳沒有型別標註（Any）。這裡回的應該是我們自己塞進 Choice 的
    # 索引；型別不對就當成「選單沒給出可用的答案」走退路，不把 Any 往外傳。
    return 選中 if isinstance(選中, int) else None


def _方向鍵多選(
    label: str,
    options: Sequence[str],
    default: Sequence[int],
    印一行: Callable[[str], None],
) -> list[int] | None:
    """畫一個方向鍵多選選單（空白鍵勾選），回傳選中的索引清單。

    畫不出來時回傳 None 讓呼叫端退回輸入編號，理由與 `_方向鍵單選()` 相同。

    空清單是合法答案（使用者一個都不勾），因此不能用「空的就當失敗」來判斷，
    只有 None 才代表選單畫不出來。

    參數:
        label: 提問，可含換行與 Rich 標記。
        options: 選項文字。
        default: 預設勾選的 0-based 索引。
        印一行: 印出一行的函式，用來輸出多行提問的前言。
    回傳:
        選中的 0-based 索引清單（已排序）；畫不出來時為 None。
    """
    if not _可用方向鍵選單():
        return None
    try:
        import questionary

        預設 = set(default)
        前言, 問句 = _拆出提問與前言(label)
        for 一行 in 前言:
            印一行(一行)
        選中 = questionary.checkbox(
            _純文字(問句),
            choices=[
                questionary.Choice(title=_純文字(選項), value=索引, checked=索引 in 預設)
                for 索引, 選項 in enumerate(options)
            ],
            instruction=_多選說明,
        ).unsafe_ask()
    except Exception as 例外:
        # 不可以靜默退回：兩條路徑印進紀錄檔的內容一模一樣，事後完全查不出畫面
        # 到底是方向鍵選單還是輸入編號的退路。把原因印出來，退路照走。
        印一行(f"[yellow]![/yellow] 畫不出方向鍵選單（{例外!r}），改用輸入編號的問法。")
        return None
    return sorted(選中)


def _遮罩輸入(label: str, 印一行: Callable[[str], None]) -> str | None:
    """問一個逐字回顯 `*` 的祕密欄位，回傳輸入的字串。

    金鑰是整個精靈最常被「貼上」的欄位。`getpass` 那種完全不回顯的問法，非 IT
    同事貼完只看到一片空白，無從判斷到底貼進去沒有——實際反應是再貼一次，或按
    Enter 試探。逐字顯示 `*` 既不外洩內容，又看得出「有東西進去了、長度大概對」。

    畫不出來時回傳 None 讓呼叫端退回 `hide_input=True` 的問法，理由與
    `_方向鍵單選()` 相同（沒有終端可用、字碼頁編不出 questionary 的字元）。
    空字串是合法答案（使用者直接按 Enter，由流程層負責要求重填），因此不能用
    「空的就當失敗」判斷，只有 None 才代表遮罩輸入畫不出來。

    參數:
        label: 提問，可含 Rich 標記。
        印一行: 印出一行的函式，用來說明為什麼換了問法。
    回傳:
        使用者輸入的原始字串（未 strip）；畫不出來時為 None。
    """
    if not _可用方向鍵選單():
        return None
    try:
        import questionary

        答案 = questionary.password(_純文字(label)).unsafe_ask()
    except Exception as 例外:
        # 理由同 `_方向鍵單選()`：兩條路徑記進紀錄檔的內容一模一樣，靜默退回就
        # 查不出當時畫面到底有沒有星號。
        印一行(f"[yellow]![/yellow] 畫不出遮罩輸入（{例外!r}），改用不回顯的問法。")
        return None
    # questionary 的回傳沒有型別標註（Any）。型別不對就當成「沒給出可用的答案」
    # 走退路，不把 Any 往外傳。
    return 答案 if isinstance(答案, str) else None


def console_prompts(log_path: Path | None = None) -> Prompts:
    """建立實際對終端機互動的 Prompts。

    提問用 Typer（它處理好了預設值顯示、確認語句，以及 Ctrl-C 時丟出 Abort），
    輸出用 Rich（樣式與面板）。

    **rich 與 typer 的 import 只出現在這裡**：一是它們不該落在 server 的啟動路徑上，
    二是流程層（run_setup 及其下）一律透過注入的 Prompts 輸出，不知道終端長什麼樣，
    這樣測試才能餵假 IO 逐分支驗證。

    流程層傳進來的字串會混著我們自己寫的裝飾性標記（如 `[green]✓[/green]`）與內插的
    外部內容（例如 Redmine 的錯誤訊息、外部指令的 stderr）。後者一旦含有形如
    `[xxx]` 的片段，會被 Rich 誤判成標記語法而丟出例外，讓精靈在最後一步當掉。這裡
    用 try/except 接住，退回 `rich.markup.escape()` 處理過的純文字重印一次——寧可
    那一行訊息失去顏色，也不能讓整個安裝流程炸掉。這個防線刻意留在這裡，不搬到流程
    層：流程層不該知道 Rich 存在，也没有能力分辨哪段字串是自己寫的標記、哪段是外部
    內容。

    參數:
        log_path: 一鍵安裝的紀錄檔路徑；給了就把印出去的訊息與提問同步追加進去
            （見 `_紀錄函式`）。None 代表不留紀錄。
    回傳:
        以 typer.prompt()／rich.Console 實作的 Prompts。
    """
    import typer
    from rich.console import Console
    from rich.errors import MarkupError
    from rich.markup import escape
    from rich.panel import Panel

    # 先讓 sys.stdout 容錯，Rich 與 typer／click 才會拿到同一個保護過的資料流。
    _讓輸出容錯()
    console = Console()
    記錄 = _紀錄函式(log_path)

    def 印(text: str) -> None:
        # 只印不記錄。方向鍵選單的選項已經另外寫進紀錄檔，這裡再記一次會變兩份。
        try:
            console.print(text)
        except MarkupError:
            console.print(escape(text))

    def say(text: str) -> None:
        記錄(text)
        印(text)

    def panel(title: str, lines: Sequence[str]) -> None:
        content = "\n".join(lines)
        記錄(title)
        for line in lines:
            記錄(line)
        try:
            console.print(Panel(content, title=title, title_align="left"))
        except MarkupError:
            console.print(Panel(escape(content), title=escape(title), title_align="left"))

    def ask(label: str, default: str | None = None) -> str:
        # 只記問題不記答案：金鑰就是從提問拿到的，而紀錄檔會整份傳給 IT。
        記錄(label)
        answer = typer.prompt(label, default=default if default is not None else "")
        return str(answer).strip()

    def choose(label: str, options: Sequence[str]) -> int:
        # 選項先寫進紀錄檔再問：不論走方向鍵選單還是退回輸入編號，IT 拿到的紀錄
        # 都看得到當時給了哪些選項。選完之後再記一次結果——選單的答案是從我們自己
        # 印出的固定清單裡挑的，不是自由輸入、更不是金鑰（那些一律不記，見 ask()），
        # 而它正是 IT 最需要知道的一件事。
        記錄(label)
        for index, option in enumerate(options, start=1):
            記錄(f"  {index}. {option}")
        選中 = _方向鍵單選(label, options, 印)
        if 選中 is None:
            選中 = _輸入編號單選(label, options)
        記錄(f"  → 選了：{options[選中]}")
        return 選中

    def _輸入編號單選(label: str, options: Sequence[str]) -> int:
        """畫不出方向鍵選單時的退路：印出清單並要求輸入編號。

        參數:
            label: 提問。
            options: 選項文字。
        回傳:
            選中的 0-based 索引。
        """
        印(label)
        for index, option in enumerate(options, start=1):
            印(f"  [bold]{index}[/bold]. {option}")
        while True:
            raw = typer.prompt("請輸入編號").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            say(f"[yellow]請輸入 1 到 {len(options)} 之間的編號。[/yellow]")

    def choose_many(label: str, options: Sequence[str], default: Sequence[int]) -> list[int]:
        selected = set(default)
        # 記在前面的是**預設**勾選狀態，使用者改過之後必須再記一次最後結果：
        # 少了那一行，取消掉的工具在紀錄檔裡仍是勾著的，IT 讀到的東西與使用者
        # 畫面完全相反。這是實測踩過的。
        記錄(f"{label}（以下為預設勾選）")
        for index, option in enumerate(options, start=1):
            # `\[` 是 Rich 的跳脫寫法，不可省略：`[x]` 會被當成標記解析掉，畫面與
            # 紀錄檔都只剩空白（`[ ]` 因為含空白不是合法標記才僥倖留得住），使用者
            # 因此看不出哪幾項是勾起來的。這是實測踩過的。
            mark = "x" if (index - 1) in selected else " "
            記錄(f"  \\[{mark}] {index}. {option}")
        選中 = _方向鍵多選(label, options, default, 印)
        if 選中 is None:
            選中 = _輸入編號多選(label, options, default, selected)
        記錄(f"  → 最後勾選：{'、'.join(options[i] for i in 選中) or '（一個都沒勾）'}")
        return 選中

    def _輸入編號多選(
        label: str, options: Sequence[str], default: Sequence[int], selected: set[int]
    ) -> list[int]:
        """畫不出方向鍵選單時的退路：印出帶勾選狀態的清單並要求輸入編號。

        參數:
            label: 提問。
            options: 選項文字。
            default: 預設勾選的索引，空輸入時採用。
            selected: 預設勾選的索引集合，用來畫勾選記號。
        回傳:
            選中的 0-based 索引清單。
        """
        # 退路：沒有終端可用（管線執行、CI）或主控台畫不出選單時，仍然要問得到。
        印(label)
        for index, option in enumerate(options, start=1):
            # `\[` 的理由見上面 記錄() 那一段。
            mark = "x" if (index - 1) in selected else " "
            印(f"  [bold]\\[{mark}][/bold] {index}. {option}")
        印(
            "直接按 Enter 接受目前勾選，或輸入要勾的編號（例如 1,2）；"
            "輸入 0 表示都不要。"
        )
        while True:
            raw = typer.prompt("請輸入編號", default="").strip()
            parsed = _parse_multi_select(raw, default, len(options))
            if parsed is not None:
                return parsed
            say(f"[yellow]請輸入 0，或 1 到 {len(options)} 之間以逗號分隔的編號。[/yellow]")

    def open_url(url: str) -> None:
        import webbrowser

        try:
            webbrowser.open(url)
        except OSError:
            # 開不起來（無桌面環境、WSL 沒設 BROWSER）不算失敗——網址已經印在畫面上，
            # 使用者自己點得到。這裡絕不能讓安裝流程中斷。
            pass

    def ask_secret(label: str) -> str:
        # 只記問題不記答案——這一支拿到的就是金鑰本身，紀錄檔絕不能沾到。
        # .strip()：金鑰是最常被貼上的欄位，貼上常帶尾隨空白／換行；不論走遮罩輸入
        # 還是退路，使用者都無從肉眼察覺多了空白（星號長得都一樣），若不在這裡處理，
        # 錯的金鑰會原樣寫進參數檔。兩條路徑共用這個出口，才不會只有一邊有 strip。
        記錄(label)
        答案 = _遮罩輸入(label, 印)
        if 答案 is None:
            答案 = str(typer.prompt(label, hide_input=True))
        return 答案.strip()

    def confirm(label: str) -> bool:
        記錄(label)
        return bool(typer.confirm(label))

    return Prompts(
        say=say,
        panel=panel,
        ask=ask,
        ask_secret=ask_secret,
        confirm=confirm,
        choose=choose,
        choose_many=choose_many,
        open_url=open_url,
    )


def validate_site_name(name: str) -> str | None:
    """檢查站台代號是否合法。

    規則沿用 config.py 的 _SITE_NAME_PATTERN，不另寫一套——兩份規則遲早會分歧，
    而分歧的結果是精靈寫出 server 讀不了的參數檔。

    參數:
        name: 使用者輸入的代號。
    回傳:
        合法時為 None；否則為可直接顯示的錯誤說明。
    """
    if _SITE_NAME_PATTERN.match(name):
        return None
    return "代號只能用英文、數字、減號與底線，需以英數開頭且長度 1–64"


def _site_credentials(sites: dict[str, Any], name: str) -> tuple[str, str] | None:
    """從已解析的 `[sites]` 表格讀出某站台目前的網址與金鑰。

    參數:
        sites: `tomllib.loads(existing).get("sites")` 的結果。
        name: 站台代號。
    回傳:
        `(url, api_key)`；該站台不存在、不是表格、或缺任一必填欄位時回傳 None——
        呼叫端會安全地退回「當作沒有可沿用的設定」，而不是揣測不完整的資料。
    """
    data = sites.get(name)
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    api_key = data.get("api_key")
    if isinstance(url, str) and url and isinstance(api_key, str) and api_key:
        return url, api_key
    return None


def _ask_site_name(prompts: Prompts, taken: Sequence[str], default: str = "main") -> str | None:
    """反覆詢問直到取得合法且未被占用的站台代號。

    參數:
        prompts: 互動管道。
        taken: 已存在且本次不打算取代的代號。
        default: 使用者直接按 Enter 時採用的預設代號。
    回傳:
        代號；使用者連續留白視為放棄時回傳 None。
    """
    for _ in range(5):
        name = prompts.ask("站台代號", default)
        problem = validate_site_name(name)
        if problem:
            prompts.say(f"[red]×[/red] {problem}")
            continue
        if name in taken:
            prompts.say(f"[red]×[/red] 代號 {name} 已存在，請換一個（要覆蓋請重跑並選「取代」）")
            continue
        return name
    return None


def _reuse_existing_key(
    prompts: Prompts, verify: Verifier, url: str, existing_key: str
) -> str | None:
    """檢查既有金鑰在這次要用的網址上是否仍然有效，並讓使用者決定要不要沿用。

    「裝過一次」不該等於「什麼都不問」，但也不該逼使用者每次都重貼一次金鑰——
    折衷是先用現有金鑰驗一次，驗得過才問「要不要換一把新的」（預設否，直接
    Enter 就沿用），完全不觸碰 `ask_secret`；驗不過代表這把金鑰已經不能用，
    直接請使用者輸入新的，不必多問一句「要換嗎」（答案顯然是要）。

    參數:
        prompts: 互動管道。
        verify: 驗證函式。
        url: 這次要寫入的網址（可能是使用者直接 Enter 沿用的原網址，也可能改過）。
        existing_key: 參數檔裡目前記錄的金鑰。
    回傳:
        使用者選擇沿用時為 `existing_key`；金鑰已失效、或使用者選擇要換一把新的，
        則回傳 None，由呼叫端接著走輸入新金鑰的流程。
    """
    try:
        login = asyncio.run(verify(url, existing_key))
    except RedmineError as exc:
        prompts.say(f"[yellow]![/yellow] 目前的金鑰驗證失敗（{exc}），請輸入新的金鑰。")
        return None
    prompts.say(f"目前金鑰：{toml_edit.mask_secret(existing_key)}（對應帳號 {login}）")
    if prompts.confirm("要換一把新的金鑰嗎？"):
        return None
    prompts.say("[green]✓[/green] 沿用原金鑰，未變更。")
    return existing_key


def _ask_credentials(
    prompts: Prompts,
    verify: Verifier,
    *,
    bundle_mode: bool = False,
    existing: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    """詢問網址與金鑰，並在通過驗證前不放行。

    做了三件對非 IT 使用者關鍵的事：網址自動補 scheme 與去尾斜線；問金鑰之前直接
    把瀏覽器開到該站的 /my/account；驗證失敗且網址沒有子路徑時自動再試一次
    `<網址>/redmine`（README FAQ 第一名的成因）。

    參數:
        prompts: 互動管道。
        verify: 驗證函式。
        bundle_mode: True 時不問「要重新輸入嗎？」，自動重試至上限。
        existing: 這次要覆蓋的既有站台目前的 `(url, api_key)`；為 None 代表這是
            全新設定，網址沒有預設值可帶、也不會嘗試沿用金鑰。
    回傳:
        `(url, api_key)`；使用者放棄或重試用盡時回傳 None。
    """
    剩餘次數 = 3
    while True:
        剩餘次數 -= 1
        url = normalize_url(
            prompts.ask(
                "Redmine 網址（從瀏覽器位址欄複製即可，例如 https://redmine.example.com/redmine）",
                default=existing[0] if existing else None,
            )
        )
        if not url:
            prompts.say("[red]×[/red] 網址不能留白。")
            if not _retry(prompts, bundle_mode, 剩餘次數):
                return None
            continue

        if existing is not None:
            重複使用的金鑰 = _reuse_existing_key(prompts, verify, url, existing[1])
            if 重複使用的金鑰 is not None:
                return url, 重複使用的金鑰

        prompts.say(
            f"已幫你開啟 {url}/my/account —— "
            "右側「API 存取金鑰」按「顯示」後複製整串貼回這裡。"
        )
        prompts.open_url(f"{url}/my/account")
        api_key = prompts.ask_secret("API 存取金鑰")
        if not api_key:
            prompts.say("[red]×[/red] 金鑰不能留白。")
            if not _retry(prompts, bundle_mode, 剩餘次數):
                return None
            continue

        prompts.say("正在驗證金鑰…")
        候選 = [url]
        補上的 = subpath_candidate(url)
        if 補上的:
            候選.append(補上的)
        最後錯誤 = ""
        for 第幾個, 試的網址 in enumerate(候選):
            try:
                login = asyncio.run(verify(試的網址, api_key))
            except RedmineError as exc:
                最後錯誤 = str(exc)
                if 第幾個 == 0 and len(候選) > 1:
                    prompts.say(
                        f"[yellow]![/yellow] {試的網址} 連不上，"
                        f"改試 {候選[1]}（Redmine 常掛在子路徑底下）"
                    )
                continue
            if 試的網址 != url:
                prompts.say(f"[green]✓[/green] 實際可用的網址是 {試的網址}")
            prompts.say(f"[green]✓[/green] 驗證通過，這把金鑰對應的帳號是 {login}")
            return 試的網址, api_key

        prompts.say(f"[red]×[/red] 驗證失敗：{最後錯誤}")
        if not _retry(prompts, bundle_mode, 剩餘次數):
            return None


def _retry(prompts: Prompts, bundle_mode: bool, 剩餘次數: int) -> bool:
    """決定要不要再讓使用者輸入一次。

    參數:
        prompts: 互動管道。
        bundle_mode: True 時不詢問，只依剩餘次數決定。
        剩餘次數: 還可以重試幾次。
    回傳:
        要繼續時為 True。
    """
    if not bundle_mode:
        return prompts.confirm("要重新輸入嗎？")
    if 剩餘次數 <= 0:
        prompts.say("[red]×[/red] 試了三次都連不上，請把畫面截圖給 IT。")
        return False
    prompts.say(f"再試一次（還有 {剩餘次數} 次）。")
    return True


def run_setup(
    prompts: Prompts,
    *,
    config_path: Path,
    run: CommandRunner,
    verify: Verifier,
    finish: bool = True,
    bundle_mode: bool = False,
) -> int:
    """執行整個安裝流程。

    參數:
        prompts: 互動管道。
        config_path: 參數檔目標路徑，由呼叫端以 resolve_config_path() 決定。
        run: 外部指令執行器。
        verify: 金鑰驗證函式。
        finish: 是否在寫檔後接著註冊、跑冒煙測試並印出總結面板。
            `setup` 子命令用 True（它本身就是完整流程）；由 installer 的步驟表呼叫時
            要給 False——註冊、冒煙與總結是步驟表自己的職責，兩邊都做會讓使用者看到
            兩次「完成」面板、server 被啟動兩次，分不清到底裝完了沒。
        bundle_mode: True 時進入一鍵安裝的精簡問法：不問位置、不問代號、不問目錄、
            不問寫入確認，只問網址與金鑰。
    回傳:
        行程結束碼；0 代表流程走完（註冊可被使用者跳過，仍算成功）。
    """
    prompts.say("[bold]redmine-mcp 安裝精靈[/bold]")
    prompts.say(f"參數檔將寫入：{config_path}（要換位置請設 REDMINE_CONFIG 環境變數後重跑）")

    # 延遲匯入：config_ops 在模組層級 import 了本模組的 Prompts／Verifier，若這裡改成
    # 模組層級 import config_ops 會造成循環匯入；讀取邏輯只在函式內部才用得到，
    # 延遲到呼叫時才匯入即可避開這個循環。
    from redmine_mcp.setup import config_ops

    if config_path.is_file():
        existing_or_none = config_ops.read_or_explain(prompts, config_path)
        if existing_or_none is None:
            return 1
        existing = existing_or_none
    else:
        existing = ""
    try:
        kind = toml_edit.classify(existing)
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return 1

    if kind == "legacy":
        prompts.say("偵測到舊的扁平格式（url／api_key 直接寫在最外層）。")
        prompts.say("新舊格式混用會讓 server 啟動失敗，必須先轉成 [sites.default]。")
        prompts.say("注意：轉換會重新產生整個檔案，原有的註解不會保留。")
        if not prompts.confirm("要現在轉換嗎？"):
            prompts.say("已取消，參數檔未被修改。")
            return 1
        existing = toml_edit.convert_legacy(existing)
        kind = "sites"

    taken: list[str] = []
    action = 0  # 預設視為「新增一個站台」；bundle 模式或非 sites 分支都不會另外詢問。
    目標站台: str | None = None  # 非 bundle 模式選了「取代」時，已知要覆蓋哪一個既有站台。
    既有沿用資料: tuple[str, str] | None = None  # (網址, 金鑰)；有值代表可以沿用現值。
    既有單站代號: str | None = None  # bundle 模式下，唯一既存站台的代號；用來判斷這次
    # 推導出的代號是不是換了網址（見下方站台代號決定那一段）。
    if kind == "sites":
        sites_dict = tomllib.loads(existing).get("sites") or {}
        taken = sorted(sites_dict.keys())
        prompts.say(f"現有站台：{'、'.join(taken)}")
        if bundle_mode:
            # 已經設定過的機器（重跑一鍵安裝多半只是為了升級）不該被逼著重填一次
            # 網址與金鑰。真實案例：參數檔裡已有 main、new 兩站，精靈卻直接跳到
            # 「請貼上 Redmine 網址」，使用者只想升級而按了三次 Enter，被判定連不上
            # 而整個安裝中止在參數檔這一步，後面的註冊與冒煙測試全沒跑到。
            # 預設（直接 Enter＝否）就是沿用現有設定、參數檔原樣不動。
            if not prompts.confirm(
                "已經有設定好的站台。這次要修改嗎？（直接按 Enter 就沿用現有設定）"
            ):
                prompts.say("沿用現有設定，參數檔未變更。")
                return 0
            if len(taken) == 1:
                # 只有一站時幾乎必然就是這次要改的那一站，不必再問一次。
                既有單站代號 = taken[0]
            else:
                # 不只一站就無從猜要改哪一個。這是 bundle 模式唯一需要選擇的地方，
                # 選項直接列站台代號，使用者不必先理解「站台」是什麼概念也點得下去。
                index = prompts.choose("要修改哪一個站台？", [*taken, "新增一個站台"])
                if index < len(taken):
                    既有單站代號 = taken[index]
            if 既有單站代號 is not None:
                # 選定了要改哪一站，網址就能帶現值當預設、金鑰驗得過也能沿用，
                # 不會退化成從零重填。
                既有沿用資料 = _site_credentials(sites_dict, 既有單站代號)
        else:
            action = prompts.choose(
                "要做什麼？", ["新增一個站台", "取代其中一個站台", "取消"]
            )
            if action == 2:
                prompts.say("已取消，參數檔未被修改。")
                return 1
            if action == 1:
                # 提前到問憑證之前選好要取代哪一個，才有現值可以當網址的預設、
                # 也才知道要拿哪一站的金鑰去驗證是否還能沿用。
                index = prompts.choose("要取代哪一個？", taken)
                目標站台 = taken[index]
                既有沿用資料 = _site_credentials(sites_dict, 目標站台)

    credentials = _ask_credentials(
        prompts, verify, bundle_mode=bundle_mode, existing=既有沿用資料
    )
    if credentials is None:
        prompts.say("已取消，參數檔未被修改。")
        return 1
    url, api_key = credentials

    if bundle_mode:
        # derive_site_label()（不做 -2 遞增）：代號若剛好與既有站台相同，就是要
        # 覆蓋那一站，而不是疊出一個重複站台。
        site = derive_site_label(url)
        if 既有單站代號 is not None and site != 既有單站代號:
            # 唯一既存站台，但這次的網址推導出「不同」的代號——多半是公司換了
            # Redmine 網址。兩種靜默處理都不對：靜默新增會留下使用者不知道的
            # 舊站台（含舊金鑰，形同殭屍設定）；靜默取代則可能刪掉他刻意保留
            # 的東西。因此明講並讓使用者選，不用猜的。
            舊網址 = 既有沿用資料[0] if 既有沿用資料 else "（讀不到網址）"
            選擇 = prompts.choose(
                f"偵測到既有設定：站台 {既有單站代號}（{舊網址}）\n"
                f"這次的網址對應到新的代號 {site}。\n要怎麼處理？",
                [
                    f"取代原本的 {既有單站代號}（舊網址與金鑰會被移除）",
                    "另外新增一個站台，兩個都保留",
                ],
            )
            if 選擇 == 0:
                site = 既有單站代號
                prompts.say(f"站台代號：{site}（沿用原代號，取代為這次輸入的設定）")
            else:
                prompts.say(f"站台代號：{site}（另外新增一個站台，{既有單站代號} 保留）")
        elif site in taken:
            prompts.say(f"站台代號：{site}（已存在，將覆蓋原本的設定）")
        else:
            prompts.say(f"站台代號：{site}（由網址自動決定，要改用 redmine-mcp config）")
    elif kind == "sites" and action == 1:
        if 目標站台 is None:
            # 只有 action == 1（選了「取代其中一個站台」）才會走進這個分支，而那
            # 條路徑上面已經無條件設過 目標站台；會是 None 代表前面的邏輯被改動
            # 過而壞掉了，寧可在這裡就地炸掉，也不要讓 site 悄悄變成 None 再往
            # 下寫出一個代號是 "None" 的站台。刻意不用 assert：`python -O` 會把
            # assert 整段跳過，讓這個不變量在正式環境裡失去保護。
            raise AssertionError("action == 1 時目標站台不應為 None，這是內部邏輯錯誤")
        site = 目標站台
    else:
        picked = _ask_site_name(prompts, taken, default=derive_site_name(url, taken))
        if picked is None:
            prompts.say("已取消。")
            return 1
        site = picked

    description = None
    if not bundle_mode:
        description = prompts.ask("站台說明（選填，直接 Enter 略過）") or None
    elif taken:
        # 第二站以上才需要說明——它的用途是幫模型把口語對應到代號，只有一站時沒有歧義。
        description = prompts.ask("這個站台是做什麼的？（選填，直接 Enter 略過）") or None

    # 下載／上傳目錄是參數檔的全域鍵，只在全新建立參數檔時才問：既有檔案（sites 格式、
    # legacy 轉換後、甚至只有註解的檔案）走 upsert_site_block()，它只替換／附加單一
    # [sites.*] 區塊，收不到這兩個值──問了卻不會被用上，比不問更糟（使用者會誤以為
    # 已經改好）。要調整既有檔案的全域鍵，請直接編輯該檔。
    download_dir: str | None = None
    upload_dir: str | None = None
    if not existing:
        if bundle_mode:
            # 不問：非 IT 使用者對「上傳來源目錄」沒有判斷依據，給錯會讓附件功能直接
            # 失效。預設走使用者目錄下的 Downloads，要改用 config set-global。
            download_dir = str(Path.home() / "Downloads" / "redmine-mcp")
            prompts.say(f"附件下載目錄：{download_dir}（要改用 redmine-mcp config set-global）")
        else:
            download_dir = prompts.ask("附件下載目錄（選填，留白則停用附件下載）") or None
            upload_dir = prompts.ask("允許上傳的來源目錄（選填，留白則沿用下載目錄）") or None
    else:
        prompts.say("既有參數檔的全域設定（下載／上傳目錄）維持不變，要調整請直接編輯該檔。")

    block = toml_edit.render_site_block(site, url, api_key, description)
    if existing:
        try:
            content = toml_edit.upsert_site_block(existing, site, block)
        except ConfigError as exc:
            # 站台以非標準寫法存在（例如 ["sites"."main"]）時，upsert_site_block()
            # 會拋出這個例外並在訊息裡給出處理建議；沒接住就會變成使用者看不懂的
            # traceback，等於 toml_edit 那層的防護白做。
            prompts.say(f"[red]×[/red] {exc}")
            return 1
    else:
        content = toml_edit.render_new_config(block, download_dir, upload_dir)

    try:
        # 與 config_ops.commit() 同一道閘門：寫檔前先確認組出來的全文是合法 TOML，
        # 避免精靈這條路徑把壞檔寫進使用者的參數檔。既有參數檔本身就可能不完整
        # （例如另一個站台缺 api_key），這種情況不是本工具的 bug，因此措辭統一用
        # toml_edit.explain_validation_failure()，與 config_ops.commit() 共用同一份，
        # 不再各自寫一份會分歧的訊息。
        toml_edit.validate_config_text(content)
    except ConfigError as exc:
        for line in toml_edit.explain_validation_failure(exc):
            prompts.say(line)
        return 1

    preview = [
        f"站台代號：{site}",
        f"網址：{url}",
        f"金鑰：{toml_edit.mask_secret(api_key)}",
    ]
    if description:
        preview.append(f"說明：{description}")
    prompts.panel("即將寫入", preview)
    if not bundle_mode and not prompts.confirm("確認寫入嗎？"):
        prompts.say("已取消，參數檔未被修改。")
        return 1

    try:
        write_config(config_path, content)
    except OSError as exc:
        # 唯讀目錄、權限不足等落地失敗；write_config() 承諾失敗時原檔不動、不留暫存檔，
        # 這裡只需要把原因說清楚，不能讓 OSError 原樣往外拋成 traceback。
        prompts.say(f"[red]×[/red] 寫入失敗：{exc}")
        return 1

    # 收緊權限緊接在寫檔之後、在任何輸出之前：終端輸出本身會失敗（字碼頁 950 的終端上
    # Rich 印 `✓` 會拋 UnicodeEncodeError），先印再收緊會讓那個例外把收緊整個跳過，
    # 留下一個權限寬鬆的金鑰檔。
    problem = tighten_permissions(config_path, lambda argv: run(argv).code)

    prompts.say(f"[green]✓[/green] 已寫入 {config_path}")
    if problem:
        prompts.say(
            f"[yellow]![/yellow] 權限收緊失敗（{problem}）。"
            "這個檔案含金鑰，請自行確認只有你讀得到。"
        )
    else:
        prompts.say("[green]✓[/green] 已收緊檔案權限")

    if not finish:
        return 0

    registration = _do_register(prompts, run)
    _finish(prompts, config_path, site, run, registration)
    return 0


def _do_register(prompts: Prompts, run: CommandRunner) -> str:
    """把 server 註冊到 Claude Code；找不到 CLI 或使用者拒絕時改為印出指令。

    參數:
        prompts: 互動管道。
        run: 外部指令執行器。
    回傳:
        供總結面板直接顯示的註冊結果描述。這個回傳值刻意存在：總結曾經無條件印
        「註冊 scope：user」，於是跳過註冊、註冊失敗、或找不到 claude CLI 的人也會
        看到「已註冊」的假象，等到重開 Claude Code 才發現 `/mcp` 裡沒有這個 server。
    """
    command = resolve_executable(_TOOL)
    manual = f"claude mcp add {_SERVER_NAME} --scope user -- {command}"
    未完成 = f"註冊：**未完成**，請自行執行 {manual}"

    if run(["claude", "--version"]).code != 0:
        prompts.say("找不到 claude CLI（裝完 Claude Code 後要開新的終端機才會進 PATH）。")
        prompts.say(f"請自行執行：{manual}")
        return 未完成

    if is_registered(_SERVER_NAME, run):
        prompts.say(f"Claude Code 已經註冊過 {_SERVER_NAME}。")
        if not prompts.confirm("要用這次的設定覆蓋嗎？"):
            prompts.say("保留原有註冊。")
            return "註冊：沿用原有設定（本次未變更）"
        unregister(_SERVER_NAME, run)

    if not prompts.confirm(f"要執行「{manual}」嗎？"):
        prompts.say(f"跳過註冊。需要時請自行執行：{manual}")
        return 未完成

    result = register(_SERVER_NAME, command, run)
    if result.code == 0:
        prompts.say(f"[green]✓[/green] 已註冊 {_SERVER_NAME}（scope: user）")
        return "註冊 scope：user（所有專案都吃得到）"
    prompts.say(f"[red]×[/red] 註冊失敗：{(result.stderr or result.stdout).strip()}")
    prompts.say(f"請自行執行：{manual}")
    return 未完成


def _finish(
    prompts: Prompts,
    config_path: Path,
    site: str,
    run: CommandRunner,
    registration: str,
) -> None:
    """跑冒煙測試並印出總結與使用者必須自己做的事。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        site: 本次設定的站台代號。
        run: 外部指令執行器。
        registration: _do_register() 回傳的註冊結果描述，原樣印在總結裡。
    """
    ok, message = smoke_test(resolve_executable(_TOOL), run)
    if ok:
        prompts.say("[green]✓[/green] server 起得來")
    else:
        prompts.say(f"[red]×[/red] server 啟動測試沒過：{message}")
        prompts.say("請對照 SETUP.md 的排錯表處理後再重跑一次。")

    prompts.panel(
        "完成",
        [
            f"參數檔：{config_path}",
            f"站台代號：{site}",
            registration,
            "",
            "[bold]接下來這兩步要你自己做，精靈無法代勞：[/bold]",
            "  1. 重開 Claude Code（MCP server 不會熱重載）",
            "  2. 輸入 /mcp 確認 redmine 顯示為 connected",
        ],
    )
