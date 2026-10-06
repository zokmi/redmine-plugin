"""把圖片附件轉成內嵌引用，並插進單子內文。

Redmine 只有在內文出現 `![](檔名)` 時，才會把附件畫在內文裡；沒有這行引用，
圖片只會躺在單子底部的附件清單。掛附件與寫引用是兩件事，模型很容易只做前者，
所以在送出前由程式補上——這樣「圖片會顯示」就有測試保證，不必仰賴每次都記得寫。

引用一律用 `uploads` 裡帶的 `filename`：那是 Redmine 實際採用的落地檔名，
而上傳工具會改寫檔名（副檔名對齊實際圖像型別、移除非法字元），與原始檔名未必相同。
"""
from __future__ import annotations

import re

#: 判定為圖片的副檔名。Redmine 依附件的 content_type 決定能不能內嵌顯示，
#: 這裡只在呼叫端沒給 content_type 時當備援；範圍取瀏覽器普遍能直接畫的格式。
#: 刻意不收 SVG——它是文字格式、可內嵌 script，上傳端同樣不收。
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

#: Markdown 的連結目的地不能直接含空白或括號，`![](my shot.png)` 會被解析成
#: 「網址 my」加標題，圖片顯示不出來。命中這些字元時改用角括號形式 `![](<...>)`。
_NEEDS_ANGLE_BRACKETS = re.compile(r"[\s()<>]")

#: 圍欄區塊（``` 或 ~~~ 起訖）。錯誤訊息原文常會提到檔名，那是資料不是引用，
#: 判斷「內文是否已引用」時要先把這些區塊挖掉，否則圖片會被誤判成已經放進去了。
_FENCED_BLOCK = re.compile(r"^(?P<fence>```+|~~~+).*?^(?P=fence)", re.MULTILINE | re.DOTALL)

#: 行內程式碼（`檔名`）。概述裡以行內 code 提到附件檔名是很自然的寫法
#: （「附件清單看得到 `shot.png`」），那同樣是在講檔名這筆資料而不是圖片引用，
#: 與圍欄區塊一樣要先挖掉，否則圖片會被誤判成已經放進去了。
#: 只比對同一行內成對的反引號：跨行的行內 code 極少見，放寬反而會把一整段吃掉。
_INLINE_CODE = re.compile(r"(`+)[^`\n]*\1")

#: 佐證章節的標題。骨架寫的是 `## 佐證（選填）`，實際單子多半只留 `## 佐證`，
#: 因此比對前綴而非全等。找不到就退回文末。
_EVIDENCE_HEADING = re.compile(r"^##[ \t]*佐證.*$", re.MULTILINE)

#: 任一個 `##` 章節標題，用來界定佐證章節到哪裡結束。
_SECTION_HEADING = re.compile(r"^##[ \t]", re.MULTILINE)


def is_image(entry: dict[str, str]) -> bool:
    """判斷這筆 upload 是不是能內嵌顯示的圖片。

    以 content_type 為準；沒給時退回副檔名判斷。兩者皆無從判斷就當作不是圖片——
    對非圖片補上 `![]()` 只會產生一行破圖。

    參數:
        entry: uploads 陣列中的一筆，至少含 token，可能含 filename／content_type。
    """
    content_type = (entry.get("content_type") or "").strip().lower()
    if content_type:
        return content_type.startswith("image/")
    filename = entry.get("filename") or ""
    _, dot, suffix = filename.rpartition(".")
    return bool(dot) and f".{suffix.lower()}" in _IMAGE_EXTENSIONS


def image_reference(filename: str) -> str:
    """產生單一檔名的 Markdown 內嵌引用。

    參數:
        filename: 附件在 Redmine 上的落地檔名。
    """
    if _NEEDS_ANGLE_BRACKETS.search(filename):
        return f"![](<{filename}>)"
    return f"![]({filename})"


def _already_referenced(text: str, filename: str) -> bool:
    """內文是否已經提到這個檔名（圍欄區塊與行內程式碼內的不算）。

    比對檔名本身而不是完整的 `![](...)` 語法：模型可能寫成角括號形式、textile 的
    `!檔名!`，或以連結而非圖片引用。任何一種都代表它已經處理過了，再補一次會重複。
    """
    stripped = _INLINE_CODE.sub("", _FENCED_BLOCK.sub("", text))
    return filename in stripped


def _insert_at(text: str) -> int:
    """算出引用要插進內文的位置（字元索引）。

    優先放在「佐證」章節的末尾——那是骨架裡給截圖的位置，圖片跟著說明走才讀得順。
    沒有佐證章節就退回文末。
    """
    heading = _EVIDENCE_HEADING.search(text)
    if heading is None:
        return len(text)
    following = _SECTION_HEADING.search(text, heading.end())
    if following is None:
        return len(text)
    # 章節內容的結尾（不含下一個標題前的空行），避免在空行中間插入而多出空段落。
    return len(text[: following.start()].rstrip())


def embed_image_references(
    text: str | None, uploads: list[dict[str, str]] | None
) -> str | None:
    """在內文補上圖片附件的內嵌引用；已引用、非圖片、無檔名者一律略過。

    參數:
        text: 單子內文（description 或 notes）。None 或空白時原樣回傳——
            該不該有內文由各工具自己的必填檢核負責，這裡不製造內容。
        uploads: 正規化後的 uploads 陣列。

    回傳:
        補好引用的內文；沒有東西要補時回傳原字串（同一個物件）。
    """
    if not text or not text.strip() or not uploads:
        return text

    pending = [
        entry["filename"]
        for entry in uploads
        if entry.get("filename") and is_image(entry)
        # 這裡用原始 text 判斷即可：本次要補的檔名彼此不同名時互不影響，
        # 同名時本來就只該補一行。
        and not _already_referenced(text, entry["filename"])
    ]
    if not pending:
        return text

    # 去除同名（uploads 可能帶兩筆同名附件），保留首次出現的順序。
    # 不用 `name in seen or seen.add(name)` 那種慣用法：set.add 回傳 None，
    # 在運算式裡使用它的回傳值會被 mypy 的 func-returns-value 擋下。
    seen: set[str] = set()
    unique: list[str] = []
    for name in pending:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    references = [image_reference(name) for name in unique]

    position = _insert_at(text)
    head, tail = text[:position].rstrip(), text[position:]
    block = "\n\n" + "\n".join(references)
    if not tail.strip():
        return f"{head}{block}"
    # 與後續章節之間留一行空白：緊貼著下一個 `##` 雖然仍解析得出標題，
    # 但與骨架其餘部分的排版不一致，讀起來像漏了東西。
    return f"{head}{block}\n\n{tail.lstrip()}"
