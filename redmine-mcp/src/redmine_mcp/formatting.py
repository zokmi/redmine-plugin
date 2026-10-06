"""把 Redmine 的原始 JSON 轉成精簡、可安全交給模型的結構。"""
from __future__ import annotations

from typing import Any

UNTRUSTED_OPEN = '<redmine_content untrusted="true">'
UNTRUSTED_CLOSE = "</redmine_content>"


def neutralize_untrusted_markers(text: str) -> str:
    """把內容裡的角括號轉成標準 HTML 實體，讓內容無法構成任何標記。

    原本以 `re.compile(r"</?redmine_content", re.IGNORECASE)` 比對字面標記，
    但那是黑名單：攻擊者只要在標記名裡插一個不可見字元（零寬空格、NBSP、
    軟連字號……）就能讓 `</redmine_content>` 的視覺呈現與真閉合標籤一字不差，
    卻繞過字面比對——模型看得懂的「標記」跟 regex 看得懂的「標記」不是同一件事。
    因此改為一律轉義角括號，不再嘗試辨認「這是不是標記」，而是讓內容從此無法構成
    任何標記，繞過空間歸零。

    `<` 與 `>` 一起轉義成 `&lt;`／`&gt;`（而不是只轉義 `<`）：只轉義單邊會產生
    `&lt;connectionStrings>` 這種不對應任何既有慣例的半轉義混合體，模型沒有可靠的
    規則能把它還原成原文。一起轉義後就是模型認得的標準 HTML 實體慣例，
    `_BASE_INSTRUCTIONS` 與相關工具說明也已告知模型這個慣例、以及回寫 Redmine 前
    要先還原。代價只是內文中原本合法的角括號會以實體呈現，文字本身不遺失。
    """
    return text.replace("<", "&lt;").replace(">", "&gt;")


def flatten_ref(value: Any) -> str | None:
    """把 {"id": 1, "name": "X"} 攤平成 "X (#1)"，None 原樣回傳。

    name 是使用者可自行修改的參照名稱（如經辦人、指派人），不包不可信圍籬
    （包了會讓輸出難以比對），但仍須中和內容裡的圍籬標記，避免偽造閉合標籤。
    """
    if not isinstance(value, dict):
        return None
    identifier = value.get("id")
    name = value.get("name")
    if name and identifier is not None:
        return f"{neutralize_untrusted_markers(str(name))} (#{identifier})"
    if name:
        return neutralize_untrusted_markers(str(name))
    if identifier is not None:
        return f"#{identifier}"
    return None


def wrap_untrusted(text: str | None) -> str:
    """把使用者可寫入的自由文字包上不可信標記，提醒模型視為資料而非指令。

    包裝前先中和內容裡的圍籬標記，否則內容自己寫一個閉合標籤就能讓後續文字
    落在圍籬之外，變成模型眼中的可信指令。
    """
    return f"{UNTRUSTED_OPEN}{neutralize_untrusted_markers(text or '')}{UNTRUSTED_CLOSE}"


def _wrap_untrusted_value(value: Any) -> Any:
    """自訂欄位值若為字串則包上不可信標記；list 內的字串元素逐一包，其餘型別原樣回傳。"""
    if isinstance(value, str):
        return wrap_untrusted(value)
    if isinstance(value, list):
        return [wrap_untrusted(item) if isinstance(item, str) else item for item in value]
    return value


def format_custom_fields(raw: list[dict] | None) -> dict[str, Any]:
    """把自訂欄位陣列轉成「欄位名稱 → {id, value}」的字典。

    刻意保留 id：update_issue／create_issue 的 custom_fields 參數只接受數字 id，
    而取得 id 的 list_custom_fields 需要管理員權限，一般帳號拿不到。
    只輸出名稱的話，非管理員就只能猜 id，猜錯會靜默改到別的欄位。
    欄位名稱同樣中和不可信標記；字串值（含 list 中的字串元素）標記為不可信。
    """
    if not raw:
        return {}
    return {
        neutralize_untrusted_markers(str(item.get("name") or f"#{item.get('id')}")): {
            "id": item.get("id"),
            "value": _wrap_untrusted_value(item.get("value")),
        }
        for item in raw
    }


def format_issue_row(raw: dict) -> dict:
    """列表用的精簡 issue：只保留辨識與排序所需欄位。

    subject 由外部使用者填寫，即使在列表情境也一律包上不可信標記——列表回傳的
    內容同樣會整段進入模型的 context，防線不能只做在 `format_issue_detail` 上。
    """
    return {
        "id": raw.get("id"),
        "subject": wrap_untrusted(raw.get("subject")),
        "status": flatten_ref(raw.get("status")),
        "tracker": flatten_ref(raw.get("tracker")),
        "assigned_to": flatten_ref(raw.get("assigned_to")),
        "updated_on": raw.get("updated_on"),
    }


#: 單張 issue 預設輸出的 journal 筆數上限（取最近的）。
#:
#: Redmine 的 `include=journals` 不分頁，會回傳該單自建立以來的**全部**異動紀錄，
#: 而模型在呼叫前無從得知會拿到多少。一張長期維護的單累積數百筆註解，單次
#: get_issue 就足以塞爆整個 context。超過時會附上 journals_total 與
#: journals_truncated，並可用 journals_limit 參數自行放寬。
JOURNAL_LIMIT = 20

#: journal detail 中單一「原值／新值」的字數上限，超過改輸出 {length, preview}。
#:
#: journals 體積的主要放大器是「內文編輯」這種 detail：它的 old_value 是編輯前的
#: 整份 description、new_value 是編輯後的整份，一次編輯就是兩份全文。完整前後文
#: 對模型判斷「這張單現在怎麼了」幾乎沒有增量價值，成本卻是線性的。
JOURNAL_VALUE_MAX_CHARS = 500

#: 超長值的摘要中保留的前綴字數。
_JOURNAL_VALUE_PREVIEW_CHARS = 200


def _summarize_long_value(value: Any) -> Any:
    """字串超過 JOURNAL_VALUE_MAX_CHARS 時改回傳 {length, preview}，其餘型別原樣處理。

    preview 仍會包上不可信圍籬：截斷不改變它是使用者自由文字的事實。
    """
    if isinstance(value, str) and len(value) > JOURNAL_VALUE_MAX_CHARS:
        return {
            "length": len(value),
            "preview": wrap_untrusted(value[:_JOURNAL_VALUE_PREVIEW_CHARS]),
            "truncated": True,
        }
    return _wrap_untrusted_value(value)


def format_journal(raw: dict) -> dict:
    """格式化單筆註解／異動紀錄，註解內文標記為不可信。"""
    details = [
        {
            "欄位": item.get("name"),
            "原值": _summarize_long_value(item.get("old_value")),
            "新值": _summarize_long_value(item.get("new_value")),
        }
        for item in raw.get("details") or []
    ]
    journal = {
        "id": raw.get("id"),
        "user": flatten_ref(raw.get("user")),
        "created_on": raw.get("created_on"),
    }
    notes = raw.get("notes")
    if notes:
        # 純欄位異動（無註解）的 journal 佔比通常很高；照包會產出一個 52 字元的
        # 空圍籬殼，沒有任何資訊卻要付 token。與 details／attachments 的
        # 「沒有就不輸出該鍵」慣例一致。
        journal["notes"] = wrap_untrusted(notes)
    if details:
        journal["details"] = details
    return journal


def format_attachment(raw: dict) -> dict:
    """格式化附件資訊；刻意不輸出 content_url，避免模型直接引用外部網址。

    filename 是上傳者可控的自由文字，與 subject／description 同等處理，包上不可信圍籬。
    """
    return {
        "id": raw.get("id"),
        "filename": wrap_untrusted(raw.get("filename")),
        "filesize": raw.get("filesize"),
        "author": flatten_ref(raw.get("author")),
        "created_on": raw.get("created_on"),
    }


def format_issue_detail(
    raw: dict, journals_limit: int = JOURNAL_LIMIT, *,
    summary: bool = False, journals_offset: int = 0,
) -> dict:
    """單張 issue 的完整內容，自由文字皆標記為不可信。

    參數:
        raw: Redmine 回應中的 issue 物件。
        journals_limit: 最多輸出幾筆 journal（取最近的）；超過時另外輸出
            journals_total 與 journals_truncated，讓模型知道被截斷、可再細查。
        summary: 省略次要欄位，內文與留言保留最多 600 字預覽。
        journals_offset: 從最新紀錄跳過的筆數，用於往前讀取較早的批次。
    """
    detail: dict[str, Any] = {
        "id": raw.get("id"),
        "subject": wrap_untrusted(raw.get("subject")),
        "project": flatten_ref(raw.get("project")),
        "status": flatten_ref(raw.get("status")),
        "tracker": flatten_ref(raw.get("tracker")),
        "priority": flatten_ref(raw.get("priority")),
        "author": flatten_ref(raw.get("author")),
        "assigned_to": flatten_ref(raw.get("assigned_to")),
        "parent_id": (raw.get("parent") or {}).get("id"),
        "done_ratio": raw.get("done_ratio"),
        "start_date": raw.get("start_date"),
        "due_date": raw.get("due_date"),
        "created_on": raw.get("created_on"),
        "updated_on": raw.get("updated_on"),
        "description": wrap_untrusted(raw.get("description")),
        "custom_fields": format_custom_fields(raw.get("custom_fields")),
    }
    kept = []
    if raw.get("journals"):
        journals = raw["journals"]
        # Redmine 依時間由舊到新回傳，因此取尾端才是「最近 N 筆」。
        end = max(0, len(journals) - journals_offset)
        start = max(0, end - journals_limit) if journals_limit >= 0 else 0
        kept = journals[start:end]
        detail["journals"] = [format_journal(item) for item in kept]
        if start > 0:
            detail["journals_next_offset"] = journals_offset + len(kept)
        if len(kept) < len(journals):
            detail["journals_total"] = len(journals)
            detail["journals_truncated"] = True
    if raw.get("attachments"):
        detail["attachments"] = [format_attachment(item) for item in raw["attachments"]]
    if raw.get("relations"):
        detail["relations"] = [format_relation(item) for item in raw["relations"]]
    if raw.get("children"):
        # 子單主旨同樣是外部使用者填寫的自由文字，與父單 subject 一致處理。
        detail["children"] = [
            {"id": child.get("id"), "subject": wrap_untrusted(child.get("subject"))}
            for child in raw["children"]
        ]
    if raw.get("watchers"):
        # watchers 在 ALLOWED_INCLUDES 裡，請求會真的送出 include=watchers；
        # 這裡不輸出的話模型會誤以為這張單沒有關注者。
        detail["watchers"] = [flatten_ref(item) for item in raw["watchers"]]
    if summary:
        # 摘要是獨立的讀取模式；回寫前須重新取得完整內容。
        keys = {"id", "subject", "project", "status", "tracker", "updated_on",
                "description", "journals", "journals_total", "journals_truncated",
                "journals_next_offset", "attachments", "relations", "children", "watchers"}
        detail = {key: value for key, value in detail.items() if key in keys}
        detail["summary"] = True
        detail["description"] = _preview_text(raw.get("description"))
        for journal, original in zip(detail.get("journals", []), kept, strict=True):
            if original.get("notes"):
                journal["notes"] = _preview_text(original["notes"])
    return detail


def _preview_text(value: Any) -> Any:
    """摘要保留 600 字原文，轉義在截斷之後執行以維持圍籬完整。"""
    if isinstance(value, str) and len(value) > 600:
        return {"length": len(value), "preview": wrap_untrusted(value[:600]), "truncated": True}
    return wrap_untrusted(value)


#: relations 允許輸出的欄位；其餘欄位一律捨棄，不把 Redmine 回應原樣透傳給模型。
_RELATION_KEYS = ("id", "issue_id", "issue_to_id", "relation_type", "delay")


def format_relation(raw: dict) -> dict:
    """格式化 issue 關聯，只保留白名單欄位。

    這些欄位都是 id、關聯型態與延遲天數，不含使用者可填寫的自由文字，
    因此不需要包不可信標記；但仍以白名單過濾，避免站台外掛塞進未預期欄位。
    """
    return {key: raw.get(key) for key in _RELATION_KEYS if key in raw}


def format_named_list(raw: dict, key: str) -> list[dict]:
    """把 metadata 清單縮成只有 id 與 name 的陣列。

    name（專案／追蹤類型／狀態／優先權等名稱）屬使用者或管理員可自訂的參照名稱，
    與 `flatten_ref` 的 name 同等處理：只中和圍籬標記、不包圍籬。
    """
    return [
        {"id": item.get("id"), "name": neutralize_untrusted_markers(str(item.get("name") or ""))}
        for item in raw.get(key) or []
    ]


def paginated(raw: dict, key: str, rows: list) -> dict:
    """組出統一的分頁信封。"""
    return {
        "total_count": raw.get("total_count", 0),
        "offset": raw.get("offset", 0),
        "limit": raw.get("limit", 0),
        key: rows,
    }
