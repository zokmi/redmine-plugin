"""參數檔維護命令的流程編排。

與 wizard.py 同一取向：所有 IO 由 Prompts 注入，外部指令以參數傳入，因此可在不碰
終端機、不打真實 API 的前提下逐分支測試。

四個命令共用同一條管線——讀檔與格式判定在 load_current()，驗證、確認、落地、權限與
提醒在 commit()。命令各自只負責「把舊全文變成新全文」。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from redmine_mcp.config import ConfigError
from redmine_mcp.config_file import GLOBAL_KEYS, structure_config
from redmine_mcp.errors import RedmineError
from redmine_mcp.setup import toml_edit
from redmine_mcp.setup.external import CommandRunner
from redmine_mcp.setup.fsops import tighten_permissions, write_config
from redmine_mcp.setup.wizard import Prompts, Verifier

#: 定位要編輯的區間；每次都吃當下的全文，因為前一次編輯會讓舊索引失效。
Locator = Callable[[str], tuple[int, int]]

#: 一次編輯：鍵名與值，值為 None 代表刪除該鍵。
Change = tuple[str, str | int | None]


@dataclass(frozen=True)
class Loaded:
    """讀進來的參數檔現況。

    欄位:
        text: 參數檔全文。
        sites: 站台代號到該站台鍵值的對映；沒有站台時為空字典。
        kind: toml_edit.classify() 的結果（"empty" 或 "sites"）。
    """

    text: str
    sites: dict[str, dict[str, str]]
    kind: str


def load_current(prompts: Prompts, config_path: Path, *, need_sites: bool) -> Loaded | None:
    """讀出參數檔並判定格式，不符條件時說明原因並回傳 None。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        need_sites: 該命令是否需要至少一個站台（set-global 不需要）。
    回傳:
        現況；不可繼續時為 None（呼叫端一律回傳 exit code 1）。
    """
    if not config_path.is_file():
        prompts.say(f"[red]×[/red] 找不到參數檔：{config_path}")
        prompts.say("請先執行 redmine-mcp setup 建立參數檔。")
        return None

    text = read_or_explain(prompts, config_path)
    if text is None:
        return None
    try:
        kind = toml_edit.classify(text)
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return None

    if kind == "legacy":
        prompts.say("[red]×[/red] 這份參數檔是舊的扁平格式（url／api_key 直接寫在最外層）。")
        prompts.say("請先執行 redmine-mcp setup，它會在徵得同意後轉成 [sites.*] 格式。")
        return None

    # 站台資料一律交給 structure_config() 解析：classify() 只負責分辨 empty／sites／
    # legacy 三種型態，無法涵蓋「sites 這個鍵存在但不是表格」（例如 sites = "oops" 或
    # sites = ["a"]）這種邊界——這種情況 classify() 會誤判成 empty，若在這裡自己重寫一份
    # 「raw.get('sites') or {}」的簡化解析，遇到非空的非表格值會直接 AttributeError。
    # structure_config() 已經對這個邊界拋出乾淨的 ConfigError，沿用它才不會出現第二份
    # 結構驗證規則。
    try:
        sites = structure_config(tomllib.loads(text), f"參數檔（{config_path}）").sites
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return None

    if need_sites and not sites:
        prompts.say(f"[red]×[/red] 參數檔裡沒有任何站台：{config_path}")
        prompts.say("請執行 redmine-mcp setup 新增站台。")
        return None

    return Loaded(text=text, sites=sites, kind=kind)


def commit(
    prompts: Prompts,
    config_path: Path,
    new_text: str,
    preview: Sequence[str],
    *,
    run: CommandRunner,
    assume_yes: bool,
    allow_no_sites: bool = False,
) -> int:
    """驗證、確認、落地、收緊權限並提醒重新連線。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        new_text: 編輯後的全文。
        preview: 要顯示在確認面板裡的摘要行。
        run: 外部指令執行器（收緊權限時會用到）。
        assume_yes: 為真時跳過確認提問，但不跳過驗證與警告。
        allow_no_sites: 允許結果沒有任何站台（刪除最後一個站台時）。
    回傳:
        0 成功；1 驗證失敗、使用者取消或寫檔失敗。
    """
    try:
        toml_edit.validate_config_text(new_text, allow_no_sites=allow_no_sites)
    except ConfigError as exc:
        # 這道閘會連同「原本就不完整的參數檔」一併擋下（load_current() 只用
        # structure_config() 判斷結構、不做語意驗證，例如某站台缺 api_key、或站台
        # 代號不合法，都要等到這裡才會被 load_settings() 發現）。措辭統一放在
        # toml_edit.explain_validation_failure()：wizard.py 的 setup 命令會踩到同一
        # 種情境（新增站台時既有檔案本來就不完整），共用同一份措辭才不會日後改一邊
        # 漏改另一邊而分歧。
        for line in toml_edit.explain_validation_failure(exc):
            prompts.say(line)
        return 1

    prompts.panel("即將寫入", list(preview))
    if not assume_yes and not prompts.confirm("確認寫入嗎？"):
        prompts.say("已取消，參數檔未被修改。")
        return 1

    try:
        write_config(config_path, new_text)
    except OSError as exc:
        prompts.say(f"[red]×[/red] 寫入失敗：{exc}")
        return 1

    # 收緊權限必須緊接在寫檔之後、在任何輸出之前。write_config() 以 os.replace 換掉
    # inode，新檔權限來自建立時的預設與目錄繼承、不會沿用舊檔，所以這一步漏掉就等於把
    # 含金鑰的檔案權限放回預設。之所以不先印「已寫入」再收緊：終端輸出本身會失敗（實測
    # 在字碼頁 950 的終端上，Rich 印 `✓` 會拋 UnicodeEncodeError），而那個例外會讓收緊
    # 整個被跳過，留下一個權限寬鬆的金鑰檔。
    problem = tighten_permissions(config_path, lambda argv: run(argv).code)

    prompts.say(f"[green]✓[/green] 已寫入 {config_path}")
    if problem:
        prompts.say(
            f"[yellow]![/yellow] 權限收緊失敗（{problem}）。"
            "這個檔案含金鑰，請自行確認只有你讀得到。"
        )
    else:
        prompts.say("[green]✓[/green] 已收緊檔案權限")

    prompts.say("改動要重新連線才生效：在 Claude Code 用 /mcp 重連（或重開）。")
    return 0


def read_or_explain(prompts: Prompts, config_path: Path) -> str | None:
    """讀出參數檔全文；讀不到時說明原因並回傳 None。

    不讓 OSError 直接往外拋：讀不到最常見的成因是檔案權限被收成了錯的授權對象
    （本專案 0.4 系列的 icacls 指令用裸帳號名，在某些機器上會授權給非本人的主體），
    這時使用者需要的是一句「怎麼修」，而不是一坨 traceback。

    公開給 wizard.py 共用：精靈讀既有參數檔時同樣可能踩到權限問題或非 UTF-8 編碼，
    兩邊各寫一份錯誤處理遲早會分歧，而分歧的後果是其中一邊變成裸例外。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
    回傳:
        檔案全文；讀取失敗時為 None。
    """
    try:
        return config_path.read_text(encoding="utf-8")
    except OSError as exc:
        prompts.say(f"[red]×[/red] 讀不到參數檔（{exc.strerror or exc}）：{config_path}")
        if sys.platform == "win32":
            user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
            prompts.say(
                f"若是權限問題，可用這行把讀取權還給自己："
                f'icacls "{config_path}" /grant:r "{user}:(R)"'
            )
        else:
            prompts.say(f"若是權限問題，可用這行修復：chmod 600 \"{config_path}\"")
        return None
    except UnicodeDecodeError:
        # Windows 記事本／舊編輯器常把中文說明存成 cp950／big5，read_text(encoding="utf-8")
        # 就會在這裡失敗。訊息只講編碼與路徑，不帶檔案內容——該檔可能正好放著 api_key。
        prompts.say(f"[red]×[/red] 參數檔不是 UTF-8（{config_path}）")
        prompts.say("請用編輯器把它另存為 UTF-8 後重試。")
        return None


def global_locator() -> Locator:
    """回傳定位全域區的 Locator。"""
    return toml_edit.global_region


def site_locator(site: str) -> Locator:
    """回傳定位某站台區塊的 Locator。

    參數:
        site: 站台代號。
    例外:
        ConfigError: 呼叫時找不到該區塊（例如站台以 ["sites"."main"] 這種非標準寫法
            存在，tomllib 看得到但行首樣式定位不到）。
    """

    def locate(text: str) -> tuple[int, int]:
        found = toml_edit.find_site_block(text, site)
        if found is None:
            raise ConfigError(
                f"參數檔中的站台 {site} 不是以 [sites.{site}] 的標準寫法撰寫，無法安全改寫；"
                "請手動調整成標準寫法後重試"
            )
        return found

    return locate


def apply_changes(text: str, locate: Locator, changes: Sequence[Change]) -> str:
    """依序套用多個鍵的設定或刪除。

    每個 change 都重新呼叫 locate()：前一次編輯已經改變了全文長度，舊的區間索引不再
    有效，沿用會寫到錯誤位置。

    參數:
        text: 參數檔全文。
        locate: 區間定位函式。
        changes: 鍵與值的序列；值為 None 代表刪除。
    回傳:
        編輯後的全文。
    """
    for key, value in changes:
        span = locate(text)
        if value is None:
            text = toml_edit.remove_key_in(text, span, key)
        else:
            text = toml_edit.set_key_in(text, span, key, value)
    return text


def show(prompts: Prompts, *, config_path: Path) -> int:
    """印出參數檔現況：路徑、全域設定、各站台的網址與遮罩後金鑰。

    唯讀，因此不走 load_current() 的格式拒絕——舊的扁平格式照實顯示並說明它等同
    站台 default，對排錯比「請先轉格式」有用。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
    回傳:
        0 成功；1 檔案不存在或無法解析。
    """
    if not config_path.is_file():
        prompts.say(f"[red]×[/red] 找不到參數檔：{config_path}")
        prompts.say("請先執行 redmine-mcp setup 建立參數檔。")
        return 1

    text = read_or_explain(prompts, config_path)
    if text is None:
        return 1
    try:
        kind = toml_edit.classify(text)
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return 1

    raw = tomllib.loads(text)
    prompts.say(f"參數檔：{config_path}")

    全域 = [
        f"  {key} = {raw[key]}" if key in raw else f"  {key} =（未設定）"
        for key in sorted(GLOBAL_KEYS)
    ]
    prompts.say("全域設定：")
    for line in 全域:
        prompts.say(line)

    # 站台資料同樣交給 structure_config()：show 是唯讀指令、不能因為解析炸掉而丟出裸
    # Python traceback，但也不像 load_current() 那樣要整段拒絕——沿用同一套結構驗證，
    # 只是把 ConfigError 轉成回傳 1，不重寫第二份「sites 是否為表格」的判斷。
    try:
        structured = structure_config(raw, f"參數檔（{config_path}）")
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return 1

    if kind == "legacy":
        prompts.say("偵測到舊的扁平格式，等同一個名為 default 的站台：")
    站台 = structured.sites

    if not 站台:
        prompts.say("沒有任何站台。執行 redmine-mcp setup 可新增。")
        return 0

    prompts.say("站台：")
    for name in sorted(站台):
        body = 站台[name]
        說明 = f"  {body['description']}" if body.get("description") else ""
        prompts.say(
            f"  {name}  {body.get('url', '（未設定）')}  "
            f"{toml_edit.mask_secret(body.get('api_key', ''))}{說明}"
        )
    return 0


#: 互動模式下代表「清空這個欄位」的輸入。用單一減號而非空字串：空字串是 Enter 的結果，
#: 必須留給「保留原值」，否則使用者無法在不改動的情況下跳過某一題。
_CLEAR_TOKEN = "-"

#: 「不變更」的哨符，與「清空（None）」區分開。
_KEEP: object = object()


def _ask_optional(prompts: Prompts, label: str, current: str) -> str | object | None:
    """互動詢問一個可留白、可清空的欄位。

    參數:
        prompts: 互動管道。
        label: 題目文字。
        current: 目前的值，顯示在題目裡讓使用者知道 Enter 會保留什麼。
    回傳:
        新值；輸入 `-` 時為 None（清空）；直接 Enter 時為 _KEEP（不變）。
    """
    現值 = current or "（未設定）"
    answer = prompts.ask(f"{label}（Enter 保留 {現值}，輸入 - 清空）")
    if not answer:
        return _KEEP
    return None if answer == _CLEAR_TOKEN else answer


def set_site(
    prompts: Prompts,
    *,
    config_path: Path,
    site: str,
    url: str | None,
    description: str | None,
    clear_description: bool,
    ask_key: bool,
    run: CommandRunner,
    verify: Verifier,
    assume_yes: bool,
) -> int:
    """修改既有站台的欄位。

    只改既有站台：新增站台由 redmine-mcp setup 負責，它同時處理全域鍵與註冊，
    沒有理由在這裡做第二套。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        site: 要修改的站台代號。
        url: 新網址；None 代表不改。
        description: 新說明；None 代表不改。
        clear_description: 為真時移除說明那一行。
        ask_key: 為真時以遮蔽的方式詢問新金鑰。
        run: 外部指令執行器。
        verify: 金鑰驗證函式。
        assume_yes: 跳過寫入前的確認提問。
    回傳:
        0 成功；1 使用者取消或可預期的錯誤。
    """
    loaded = load_current(prompts, config_path, need_sites=True)
    if loaded is None:
        return 1

    if site not in loaded.sites:
        prompts.say(f"[red]×[/red] 參數檔裡沒有站台 {site}。")
        prompts.say(f"現有站台：{'、'.join(sorted(loaded.sites))}")
        prompts.say("要新增站台請執行 redmine-mcp setup。")
        return 1

    if clear_description and description is not None:
        prompts.say("[red]×[/red] description 不可同時要求設定與清空。")
        return 1

    current = loaded.sites[site]
    新說明: str | object | None = None if clear_description else (description or _KEEP)

    # 完全沒帶欄位旗標時退回互動，保住精靈的手感。
    if url is None and description is None and not clear_description and not ask_key:
        新網址 = prompts.ask(f"站台網址（Enter 保留 {current.get('url', '')}）") or None
        新說明 = _ask_optional(prompts, "站台說明", current.get("description", ""))
        ask_key = prompts.confirm("要換 API 金鑰嗎？")
    else:
        新網址 = url

    # 是否要把網址寫回檔案的旗標；不能只看 `新網址` 最初的賦值——下面的重試迴圈只更新
    # url_值（拿去打 verify 的工作變數），使用者若是在重試時才輸入／改掉網址（例如
    # 一開始只想換金鑰、沒帶 --url，第一次驗證失敗後才順手把網址也改對），這個旗標
    # 必須跟著翻成 True，否則會出現「畫面顯示驗證通過，但寫進檔案的仍是舊網址」這種
    # 驗證與寫入對不上的情況（Task 5 code review 抓到的 Critical）。
    網址已指定 = bool(新網址)
    # 是否要把金鑰寫回檔案的旗標；不能只看 `ask_key` 最初的賦值——道理與上面的
    # 網址已指定 完全對稱：使用者可能一開始只想換網址（沒帶 --key，ask_key 為 False），
    # 但第一次驗證失敗後，在重試分支才輸入新金鑰並驗證通過。這時畫面已經印出「驗證
    # 通過」，若 changes 仍只看最初的 ask_key，這把剛驗證過的新金鑰就不會落地，寫進
    # 檔案的反而是從未被驗證過的舊金鑰（config-edit 最終審查抓到的 Critical，與
    # Task 5 修過的網址側是同一類問題）。
    金鑰已指定 = ask_key

    url_值 = 新網址 or current.get("url", "")
    key_值 = current.get("api_key", "")
    if ask_key:
        key_值 = prompts.ask_secret("新的 API 存取金鑰")
        if not key_值:
            prompts.say("[red]×[/red] 金鑰不能留白。參數檔未被修改。")
            return 1

    # 只在真的可能影響連線的欄位有變動時才打 Redmine；只改說明不必付一次網路往返。
    if 新網址 or ask_key:
        while True:
            prompts.say("正在驗證金鑰…")
            try:
                login = asyncio.run(verify(url_值, key_值))
            except RedmineError as exc:
                prompts.say(f"[red]×[/red] 驗證失敗：{exc}")
                if not prompts.confirm("要重新輸入嗎？"):
                    prompts.say("已取消，參數檔未被修改。")
                    return 1
                url_值 = prompts.ask("站台網址", url_值)
                網址已指定 = True
                # 留白就當場說清楚並讓使用者重填（比照 wizard.py 的 _ask_credentials()
                # 手感），不能讓空金鑰混進 changes、留到 commit() 的驗證閘才擋下——那條
                # 訊息是「這代表本工具的編輯有問題，請回報」，會把使用者自己的輸入錯誤
                # 誤導成假的內部錯誤。
                while True:
                    key_值 = prompts.ask_secret("API 存取金鑰")
                    if key_值:
                        break
                    prompts.say("[red]×[/red] 金鑰不能留白，請重新輸入。")
                金鑰已指定 = True
                continue
            prompts.say(f"[green]✓[/green] 驗證通過，這把金鑰對應的帳號是 {login}")
            break

    changes: list[Change] = []
    if 網址已指定:
        changes.append(("url", url_值))
    if 金鑰已指定:
        changes.append(("api_key", key_值))
    if 新說明 is None:
        changes.append(("description", None))
    elif 新說明 is not _KEEP:
        changes.append(("description", str(新說明)))

    if not changes:
        prompts.say("沒有任何變更，參數檔未被修改。")
        return 0

    try:
        new_text = apply_changes(loaded.text, site_locator(site), changes)
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return 1

    preview = [f"站台代號：{site}"]
    if 網址已指定:
        preview.append(f"網址：{url_值}")
    if 金鑰已指定:
        preview.append(f"金鑰：{toml_edit.mask_secret(key_值)}")
    if 新說明 is None:
        preview.append("說明：清空")
    elif 新說明 is not _KEEP:
        preview.append(f"說明：{新說明}")

    return commit(prompts, config_path, new_text, preview, run=run, assume_yes=assume_yes)


def set_global(
    prompts: Prompts,
    *,
    config_path: Path,
    download_dir: str | None,
    upload_dir: str | None,
    max_attachment_mb: int | None,
    clear: Sequence[str],
    run: CommandRunner,
    assume_yes: bool,
) -> int:
    """修改全域設定（各站台的預設值）。

    不需要參數檔裡有站台：全域鍵與站台無關。但寫檔前的驗證仍會要求整份檔案是可用的，
    因此一份沒有站台的檔案改完全域鍵仍會被擋下並說明原因。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        download_dir: 附件下載目錄；None 代表不改。
        upload_dir: 允許上傳的來源目錄；None 代表不改。
        max_attachment_mb: 單一附件大小上限（MB）；None 代表不改。
        clear: 要移除的鍵名，必須落在 GLOBAL_KEYS 內。
        run: 外部指令執行器。
        assume_yes: 跳過寫入前的確認提問。
    回傳:
        0 成功；1 使用者取消或可預期的錯誤。
    """
    loaded = load_current(prompts, config_path, need_sites=False)
    if loaded is None:
        return 1

    未知 = [key for key in clear if key not in GLOBAL_KEYS]
    if 未知:
        prompts.say(f"[red]×[/red] 無法清空 {'、'.join(未知)}：不是全域設定鍵。")
        prompts.say(f"可用的鍵：{'、'.join(sorted(GLOBAL_KEYS))}")
        return 1

    值: dict[str, str | int] = {}
    if download_dir is not None:
        值["download_dir"] = download_dir
    if upload_dir is not None:
        值["upload_dir"] = upload_dir
    if max_attachment_mb is not None:
        值["max_attachment_mb"] = max_attachment_mb

    # 完全不帶旗標時退回互動，與 set-site 一致。
    if not 值 and not clear:
        清空清單: list[str] = []
        for key in ("download_dir", "upload_dir", "max_attachment_mb"):
            answer = _ask_optional(prompts, key, str(tomllib.loads(loaded.text).get(key, "")))
            if answer is _KEEP:
                continue
            if answer is None:
                清空清單.append(key)
            else:
                值[key] = str(answer)
        clear = 清空清單

    衝突 = sorted(set(clear) & 值.keys())
    if 衝突:
        prompts.say(f"[red]×[/red] {'、'.join(衝突)} 不可同時要求設定與清空。")
        return 1

    if "max_attachment_mb" in 值:
        # 訊息與 config.py 的 _max_bytes() 一致：同一個規則不該有兩種說法。
        try:
            megabytes = int(str(值["max_attachment_mb"]))
        except ValueError:
            prompts.say("[red]×[/red] max_attachment_mb 必須是整數（單位 MB）。")
            return 1
        if megabytes <= 0:
            prompts.say("[red]×[/red] max_attachment_mb 必須大於 0。")
            return 1
        值["max_attachment_mb"] = megabytes

    for key in ("download_dir", "upload_dir"):
        路徑 = 值.get(key)
        if isinstance(路徑, str) and not Path(路徑).expanduser().is_dir():
            prompts.say(
                f"[yellow]![/yellow] {key} 指向的目錄不存在：{路徑}。"
                "仍會寫入（server 也不檢查存在性），但附件功能要等目錄建立後才能用。"
            )

    changes: list[Change] = [(key, 值[key]) for key in sorted(值)]
    changes += [(key, None) for key in sorted(clear)]
    if not changes:
        prompts.say("沒有任何變更，參數檔未被修改。")
        return 0

    new_text = apply_changes(loaded.text, global_locator(), changes)
    preview = [f"{key} = {值[key]}" for key in sorted(值)]
    preview += [f"{key}：清空" for key in sorted(clear)]
    return commit(prompts, config_path, new_text, preview, run=run, assume_yes=assume_yes)


def remove_site(
    prompts: Prompts, *, config_path: Path, site: str, run: CommandRunner, assume_yes: bool
) -> int:
    """移除一個站台。

    刪掉最後一個站台是允許的（使用者可能想清空重設），但會先明說 server 下次啟動
    會失敗；`--yes` 只跳過確認提問，不會跳過這個警告。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        site: 要移除的站台代號。
        run: 外部指令執行器。
        assume_yes: 跳過確認提問。
    回傳:
        0 成功；1 使用者取消或可預期的錯誤。
    """
    loaded = load_current(prompts, config_path, need_sites=True)
    if loaded is None:
        return 1

    if site not in loaded.sites:
        prompts.say(f"[red]×[/red] 參數檔裡沒有站台 {site}。")
        prompts.say(f"現有站台：{'、'.join(sorted(loaded.sites))}")
        return 1

    if len(loaded.sites) == 1:
        prompts.say(
            "[yellow]![/yellow] 這是唯一的站台。刪掉之後參數檔就沒有任何站台，"
            "server 下次啟動會失敗（要恢復可執行 redmine-mcp setup）。"
        )

    try:
        new_text = toml_edit.remove_site_block(loaded.text, site)
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return 1

    return commit(
        prompts,
        config_path,
        new_text,
        [f"刪除站台：{site}"],
        run=run,
        assume_yes=assume_yes,
        # 刪最後一個站台的結果必然沒有站台；這裡若不放行，spec 明文允許的操作會被
        # 寫檔前的驗證閘擋死。
        allow_no_sites=True,
    )
