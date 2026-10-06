"""參數檔的文字層級編輯。

全為純函式，不碰檔案系統，因此可直接以字串測試各種邊界。

刻意不引入 tomlkit：精靈只做兩種操作——在檔尾附加一個 [sites.X] 區塊，或替換掉
既有的某個區塊——其餘位元組原封不動即可保住使用者自己寫的註解與其他站台設定，
不必為了安裝流程讓執行期多背一個相依。
"""
from __future__ import annotations

import re
import tomllib

from redmine_mcp.config import ConfigError, load_settings
from redmine_mcp.config_file import LEGACY_SITE_KEYS, structure_config


def escape_toml(value: str) -> str:
    """把值逸出成可安全放進 TOML 基本字串（雙引號字串）的形式。

    反斜線必須先處理，否則會把後面新加的逸出斜線再逸出一次。

    參數:
        value: 原始值。
    回傳:
        逸出後的字串，不含外層引號。
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def mask_secret(value: str) -> str:
    """把金鑰遮成只剩末四碼，用於畫面回顯。

    長度不足 5 時整串遮掉：對短字串套「只留末四碼」等於幾乎全部露出，
    比不遮更危險。

    參數:
        value: 原始金鑰。
    回傳:
        形如 `****cdef` 的遮罩字串。
    """
    return "****" + value[-4:] if len(value) > 4 else "****"


def classify(text: str) -> str:
    """判定參數檔目前是哪一種型態。

    三種型態決定精靈接下來要走的分支：empty 直接建立、sites 可安全增改單一
    區塊、legacy 必須先轉格式（新舊混用會讓 server 啟動失敗）。

    參數:
        text: 參數檔全文；檔案不存在時傳空字串。
    回傳:
        "empty"（沒有任何站台）、"sites"（已是 [sites.*] 格式）或
        "legacy"（url／api_key 直接寫在最外層的舊格式）。
    例外:
        ConfigError: TOML 語法錯誤。訊息不帶原文，避免把 api_key 那一行印出來。
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("參數檔格式錯誤，無法解析 TOML；請先修正語法或改名備份後重跑") from exc
    if isinstance(raw.get("sites"), dict):
        return "sites"
    if LEGACY_SITE_KEYS & raw.keys():
        return "legacy"
    return "empty"


#: 站台區塊的標頭樣式。只認行首、未加引號的標準寫法；引號寫法交由 upsert 判錯。
_HEADER = "[sites.{site}]"
#: 任一表格標頭的行首樣式，用來找出目前區塊的結束位置。
_ANY_HEADER = re.compile(r"^\[", re.MULTILINE)


def render_site_block(site: str, url: str, api_key: str, description: str | None) -> str:
    """產生單一站台的 TOML 區塊文字。

    參數:
        site: 站台代號，呼叫端須先以 config.py 的規則驗證過。
        url: 站台根位址。
        api_key: 該站台的 API key。
        description: 站台說明；為 None 時整行省略，不寫成空字串。
    回傳:
        以換行結尾的區塊文字。
    """
    lines = [
        _HEADER.format(site=site),
        f'url         = "{escape_toml(url)}"',
        f'api_key     = "{escape_toml(api_key)}"',
    ]
    if description:
        lines.append(f'description = "{escape_toml(description)}"')
    return "\n".join(lines) + "\n"


def _trim_trailing_annotations(text: str, start: int, end: int) -> int:
    """把區塊尾端「屬於下一個區塊」的空行與註解行退還出去。

    區塊原本一路吃到下一個行首 `[`，於是寫在下一個站台標頭上方的說明註解會被
    算成本區塊的尾巴，替換或刪除本區塊時就把使用者手寫的註解一起帶走。
    因此從尾端往前退掉連續的空行與整行註解，遇到第一個鍵值行就停——
    那才是本區塊真正的結尾。

    參數:
        text: 參數檔全文。
        start: 本區塊起始索引。
        end: 本區塊原本的結束索引（下一個標頭起點或檔尾）。
    回傳:
        調整後的結束索引；不會小於 start。
    """
    cut = end
    while cut > start:
        prev_newline = text.rfind("\n", start, cut - 1)
        line_start = prev_newline + 1 if prev_newline != -1 else start
        if line_start <= start:
            break
        stripped = text[line_start:cut].strip()
        if stripped == "" or stripped.startswith("#"):
            cut = line_start
            continue
        break
    return cut


def find_site_block(text: str, site: str) -> tuple[int, int] | None:
    """找出指定站台區塊在全文中的位置。

    區塊從自己的標頭開始，到下一個行首 `[` 之前為止（沒有下一個就到檔尾），但尾端若是
    連續的空行或整行註解、且緊接著下一個站台標頭，會被 `_trim_trailing_annotations()`
    退還給下一個區塊——那通常是使用者寫給下一站看的說明，不該被算進本區塊。

    參數:
        text: 參數檔全文。
        site: 站台代號。
    回傳:
        `(起始索引, 結束索引)`；找不到時為 None。
    """
    header = _HEADER.format(site=site)
    for match in _ANY_HEADER.finditer(text):
        if not text.startswith(header, match.start()):
            continue
        after = text[match.start() + len(header) :]
        if after[:1] not in ("", "\n", "\r", " ", "\t"):
            # [sites.mainx] 不該被 [sites.main] 命中。
            continue
        nxt = _ANY_HEADER.search(text, match.start() + 1)
        end = nxt.start() if nxt else len(text)
        return match.start(), _trim_trailing_annotations(text, match.start(), end)
    return None


def global_region(text: str) -> tuple[int, int]:
    """回傳全域鍵所在區間：檔頭到第一個行首 `[` 之前。

    這是 TOML 本身的語意——第一個表格標頭之後的鍵都屬於那個表格——順帶解掉最危險的
    誤動：`[sites.main]` 底下同樣可以有 download_dir，只在這個區間內搜尋就不會改錯層。

    參數:
        text: 參數檔全文。
    回傳:
        `(起始索引, 結束索引)`；沒有任何表格標頭時為整份內容。
    """
    match = _ANY_HEADER.search(text)
    return 0, match.start() if match else len(text)


def _render_value(value: str | int) -> str:
    """把設定值渲染成 TOML 字面值。整數裸寫，字串加引號並逸出。"""
    return str(value) if isinstance(value, int) else f'"{escape_toml(value)}"'


def _key_line(key: str) -> re.Pattern[str]:
    """組出比對「行首某鍵的賦值行」的樣式。

    錨定行首且不允許前導空白或 `#`：註解掉的同名行（`# download_dir = ...`）不可被
    當成該鍵，否則使用者的說明會被替換掉，真正的設定又沒寫進去。

    第一個捕獲群組只框住「鍵名＋鍵名後的空白＋`=`」，不含值：set_key_in() 就地換值
    時要保留這段前綴（原本為了對齊 url／api_key／description 打的空格），只換掉
    `=` 後面的值，否則每次改一個欄位就會把整塊手動對齊的版面弄歪。第二個捕獲群組框住
    `=` 後面的全部內容（值＋可能的行內註解），供 set_key_in() 拆出行內註解後保留。
    """
    return re.compile(rf"^({re.escape(key)}[ \t]*=)([ \t]*.*)$", re.MULTILINE)


def _split_value_comment(rest: str) -> tuple[str, str]:
    """把 `=` 之後的內容拆成 (值, 行內註解)。

    不能直接用正則找第一個 `#`：`api_key = "ab#cd"` 的井號在雙引號內，是值的一部分。
    因此逐字掃描並追蹤是否位於雙引號字串內（含反斜線轉義），
    只把引號外的第一個井號當成註解起點。

    參數:
        rest: 賦值行中 `=` 之後的全部內容（不含換行）。
    回傳:
        `(值部分, 註解部分)`；沒有行內註解時註解為空字串。
        值前面的空白保留在值部分；值與 `#` 之間的空白算進註解部分——`set_key_in()`
        換值時只保留註解部分、丟棄值部分，若把這段空白留在值部分會在換值後被一併
        丟掉，讓版面（例如 `url = "..."   # 說明` 的對齊留白）塌陷成緊貼的
        `"新值"# 說明`。
    """
    in_string = False
    escaped = False
    for index, char in enumerate(rest):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == "#" and not in_string:
            comment_start = index
            while comment_start > 0 and rest[comment_start - 1] in " \t":
                comment_start -= 1
            return rest[:comment_start], rest[comment_start:]
    return rest, ""


def set_key_in(text: str, span: tuple[int, int], key: str, value: str | int) -> str:
    """在指定區間內設定一個鍵：已存在就換掉值、不存在就插在區間尾端。

    參數:
        text: 參數檔全文。
        span: 要編輯的區間，來自 global_region() 或 find_site_block()。
        key: 鍵名；合法鍵名由呼叫端限定（GLOBAL_KEYS／SITE_KEYS），本函式不自己判斷，
            避免出現第二份清單。
        value: 設定值；int 裸寫，str 加引號。
    回傳:
        編輯後的全文。
    """
    start, end = span
    region = text[start:end]

    match = _key_line(key).search(region)
    if match:
        # 只換 `=` 後面的值，保留 match.group(1)（鍵名＋原本的對齊空白＋`=`）與
        # 該行的行內註解——那是使用者手寫的說明，換個欄位值不該把它一起刪掉。
        _, comment = _split_value_comment(match.group(2))
        new_line = f"{match.group(1)} {_render_value(value)}{comment}"
        return (
            text[:start] + region[: match.start()] + new_line + region[match.end() :] + text[end:]
        )

    line = f"{key} = {_render_value(value)}"

    # 不存在：插在區間尾端，但要維持原本的尾隨換行——那些換行是與下一個表格標頭之間的
    # 分隔，被吃掉的話 `[sites.main]` 會緊貼在我們新加的那行下面。
    body = region.rstrip("\n")
    trailing = region[len(body) :] or "\n"
    if body:
        # 前一行是註解時多隔一個空行，讓新鍵不會看起來像註解的一部分。
        gap = "\n\n" if body.rsplit("\n", 1)[-1].lstrip().startswith("#") else "\n"
        new_region = body + gap + line + trailing
    else:
        new_region = line + trailing
    return text[:start] + new_region + text[end:]


def remove_key_in(text: str, span: tuple[int, int], key: str) -> str:
    """在指定區間內刪掉一個鍵所在的整行。

    找不到時原樣回傳（冪等）：`--clear` 有可能被重複執行，第二次不該失敗。

    參數:
        text: 參數檔全文。
        span: 要編輯的區間。
        key: 鍵名。
    回傳:
        編輯後的全文。
    """
    start, end = span
    region = text[start:end]
    match = _key_line(key).search(region)
    if match is None:
        return text
    # 連同該行的換行一起移除，否則會留下一個空行。
    after = match.end() + 1 if region[match.end() : match.end() + 1] == "\n" else match.end()
    return text[:start] + region[: match.start()] + region[after:] + text[end:]


def upsert_site_block(text: str, site: str, block: str) -> str:
    """把站台區塊寫進全文：已存在就替換，不存在就附加在檔尾。

    參數:
        text: 參數檔全文；空字串代表新檔。
        site: 站台代號。
        block: `render_site_block()` 產生的區塊文字。
    回傳:
        寫入後的全文。
    例外:
        ConfigError: TOML 解析得到該站台、但行首樣式定位不到（例如寫成
            `["sites"."main"]`）。這時靜默附加會產生重複站台而讓 server 啟動失敗，
            當場說清楚比留一個難查的壞檔好。
    """
    found = find_site_block(text, site)
    if found is not None:
        start, end = found
        old_block = text[start:end]
        # find_site_block() 現在會把「屬於下一個區塊」的尾端空行／整行註解退還出去，
        # 因此 end 不再吃到那些分隔空行——它們仍在原文裡，只是已經算進 text[end:]
        # （亦即下面的「剩餘部分」），不再算進 old_block。
        #
        # 這裡的公式因此仍然成立：trailing_newlines 量的是 old_block 自己還剩多少
        # 尾端換行（沒被退還、真正屬於本區塊的部分，例如區塊本身就以空行結尾，或
        # 區塊與下一個標頭之間根本沒有空行時 trailing_newlines 只有 1），gap 據此補回
        # 「新區塊 → 下一個標頭」之間應有的分隔。至於被退還出去、屬於下一站的那些空行
        # /註解，本來就完整保留在 text[end:] 裡，直接拼接就會原樣接回去，不需要另外
        # 補償。換句話說：退還操作只是把位元組從「算進 old_block」搬到「算進
        # text[end:]」，並未刪除或新增任何位元組，所以 old_block 與 text[end:]
        # 兩段加總後的分隔空行數量與退還前完全相同，這條公式不必因為 end 的定義
        # 改變而跟著改。
        trailing_newlines = len(old_block) - len(old_block.rstrip("\n"))
        gap = "\n" * max(trailing_newlines - 1, 0)
        return text[:start] + block + gap + text[end:]

    if site in (tomllib.loads(text).get("sites") or {}):
        raise ConfigError(
            f"參數檔已有站台 {site}，但它不是以 [sites.{site}] 的標準寫法撰寫，"
            "精靈無法安全改寫。請手動調整成標準寫法後重跑，或改用其他站台代號"
        )

    if not text:
        return block
    prefix = text if text.endswith("\n") else text + "\n"
    return prefix + "\n" + block


def remove_site_block(text: str, site: str) -> str:
    """移除指定站台的整個區塊。

    區塊邊界沿用 find_site_block()：尾端屬於下一個區塊的空行與註解已經被
    `_trim_trailing_annotations()` 退還，因此本函式砍掉的段落不含下一站的說明。
    但這也代表前後兩段直接拼接時，交界處可能同時保留前段自己的空行與後段退還回來
    的空行而疊成兩行空白，因此拼接時要收斂回剛好一個空行；若刪的是最後一個區塊，
    則收斂尾端連續換行成一個，避免檔尾累積空白；整份沒有實質內容時回傳空字串。

    參數:
        text: 參數檔全文。
        site: 站台代號。
    回傳:
        移除後的全文。
    例外:
        ConfigError: 找不到該站台區塊。呼叫端雖然會先確認代號存在，這裡仍要有明確語意，
            否則「靜默不刪卻回報成功」會讓使用者以為刪掉了。
    """
    found = find_site_block(text, site)
    if found is None:
        raise ConfigError(
            f"參數檔中找不到 [sites.{site}] 區塊；"
            "若該站台是以非標準寫法（例如 [\"sites\".\"main\"]）撰寫，請手動移除"
        )
    start, end = found
    before, after = text[:start], text[end:]
    if after.strip():
        # 前後都還有內容：交界處剛好留一個空行分隔，不論前後各自帶了幾個換行。
        remaining = before.rstrip("\n") + ("\n\n" if before.strip() else "") + after.lstrip("\n")
    else:
        remaining = before
    return remaining.rstrip("\n") + "\n" if remaining.strip() else ""


_NEW_FILE_HEADER = """\
# redmine-mcp 參數檔，由 `redmine-mcp setup` 產生。
# 各鍵的完整說明見 SETUP.md；新增站台可再跑一次 setup，或自行複製一段 [sites.*]。
# 本檔含 API key，等同你的 Redmine 帳號權限，切勿提交進版控。
"""


def render_new_config(block: str, download_dir: str | None, upload_dir: str | None) -> str:
    """產生一份全新的參數檔全文。

    參數:
        block: `render_site_block()` 產生的站台區塊。
        download_dir: 附件下載目錄；為 None 時整行省略（附件下載隨之停用）。
        upload_dir: 允許上傳的來源目錄；為 None 時整行省略。
    回傳:
        含說明註解、全域鍵與站台區塊的完整檔案內容。
    """
    parts = [_NEW_FILE_HEADER]
    globals_: list[str] = []
    if download_dir:
        globals_.append(f'download_dir = "{escape_toml(download_dir)}"')
    if upload_dir:
        globals_.append(f'upload_dir   = "{escape_toml(upload_dir)}"')
    if globals_:
        parts.append("\n".join(globals_) + "\n")
    parts.append(block)
    return "\n".join(parts)


def convert_legacy(text: str) -> str:
    """把舊的扁平格式轉成 [sites.default] 格式。

    這一步會重新產生整個檔案，**原有註解不保留**——呼叫端必須先向使用者說明並取得
    同意。之所以不做文字層級的搬移，是因為舊格式的鍵散在最外層、與全域鍵混在一起，
    逐行搬移的分支比重新產生多且更容易出錯。

    參數:
        text: 舊格式的參數檔全文。
    回傳:
        轉換後的全文。
    例外:
        ConfigError: 傳入的並非舊格式。
    """
    if classify(text) != "legacy":
        raise ConfigError("這份參數檔不是舊的扁平格式，不需要轉換")
    raw = tomllib.loads(text)
    block = render_site_block(
        "default",
        str(raw.get("url", "")),
        str(raw.get("api_key", "")),
        str(raw["description"]) if raw.get("description") else None,
    )
    download = raw.get("download_dir")
    upload = raw.get("upload_dir")
    return render_new_config(
        block,
        str(download) if download else None,
        str(upload) if upload else None,
    )


def validate_config_text(text: str, *, allow_no_sites: bool = False) -> None:
    """把編輯後的全文當成真正的參數檔驗一遍。

    這是寫檔前的最後一道閘。文字層級的編輯萬一產生壞 TOML 或缺鍵，沒有這道閘就會
    直接落地，使用者要到下次重開 Claude Code、`/mcp` 連不上時才發現，而那時已經沒有
    原檔可回頭。env 一律傳空字典：驗證的對象是這份檔案本身，不能讓本機的
    REDMINE_URL／REDMINE_API_KEY 把缺漏補起來而放行一份其實不完整的檔案。

    參數:
        text: 編輯後的參數檔全文。
        allow_no_sites: 允許「一個站台都沒有」。刪除最後一個站台是明文允許的操作
            （呼叫端會另外警告 server 下次啟動會失敗），但 load_settings() 會為空的
            sites 補一個 default 站台再抱怨缺 url，因此該情境必須在這裡放行。
    例外:
        ConfigError: 無法解析、結構不合、或語意驗證不通過。
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # 不帶 tomllib 的原始訊息：它會夾帶出錯處的內容，而那行可能正是 api_key。
        raise ConfigError("編輯後的參數檔無法解析為 TOML") from exc

    config = structure_config(raw, "編輯後的參數檔")
    if allow_no_sites and not config.sites:
        return
    load_settings(config, env={})


def explain_validation_failure(exc: ConfigError) -> list[str]:
    """把 `validate_config_text()` 拋出的例外，轉成給使用者看的說明行。

    這道閘會連同「原本就不完整的參數檔」一併擋下——`load_settings()` 的語意驗證
    (例如某站台缺 api_key、或站台代號不合法) 只有在這裡才會被發現，wizard.py 新增
    站台、config_ops.py 編輯既有站台都可能踩到既有檔案本來就不完整的情形。若無條件說
    「這是本工具的問題，請回報」，會把使用者自己需要修正的參數檔誤導成假的內部錯誤，
    而這正是本功能要服務的對象；因此兩種可能都要講清楚。兩處呼叫端共用這份措辭，
    避免各自寫一份、日後改一邊漏改另一邊而分歧。

    參數:
        exc: `validate_config_text()` 拋出的例外。
    回傳:
        依序輸出即可的說明行清單。
    """
    return [
        f"[red]×[/red] 參數檔無法通過驗證：{exc}",
        "參數檔未被修改。若原本的參數檔就已經不完整（例如某個站台缺 api_key、"
        "或站台代號不合法），請先手動補齊那一處再重試；"
        "若確認原本的參數檔沒問題，才代表本工具的編輯有問題，請回報。",
    ]
