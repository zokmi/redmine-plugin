# 臭蟲單格式指南分階段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓沒有程式碼層級事實的人（PM／客服／測試）也能開出一張 RD 不必回頭追問的臭蟲單。

**Architecture:** 現行 `get_bug_report_guide` 改為接受必填參數 `stage`，`"report"` 回傳新寫的簡易版、`"diagnose"` 回傳現行完整版。兩份 Markdown 放在 `guides/bug_report/` 底下，以凍結的白名單對照表映射，不把參數拼進路徑。兩者是同一張單的前後段：PM 寫現象與影響，RD 診斷後往下追加根因與建議修正。

**Tech Stack:** Python 3.11+、MCP SDK 2.0（`mcp>=2.0,<3`）、hatchling、pytest（asyncio_mode=auto）、ruff、mypy、uv。

**設計文件：** `redmine-mcp/docs/superpowers/specs/2026-08-12-bug-report-guide-stages-design.md`

## Global Constraints

- 工作目錄：`c:\Users\kenny\Desktop\MCP`（外層 monorepo），套件根為其下的 `redmine-mcp/`。所有 `uv` 指令都在 `redmine-mcp/` 執行。
- 所有改動提交到 `develop` 分支。commit 訊息**不加 Redmine 編號**（分支名非數字）。
- 註解、docstring、錯誤訊息、測試函式名一律繁體中文。
- `mypy` 設定 `disallow_untyped_defs = true` 且 `files = ["src"]`：`src/` 下所有函式都要有型別標註。
- `ruff` `line-length = 100`，`target-version = "py311"`，選用規則 `E,F,I,UP,B,RUF`。
- 每個 `.py` 檔開頭 `from __future__ import annotations`。
- **`diagnose.md` 的內容一字不改**，本次只搬移它的位置。
- `stage` 只有兩個合法值：`"report"`、`"diagnose"`。**絕不可**把 `stage` 直接拼進檔案路徑（路徑穿越漏洞），一律走白名單對照表。
- 兩份指南全文都不得出現 `mcp__redmine__`、`ToolSearch`、`references/example`，也不得出現任何真實內部專案識別字（去識別化前的欄位名、服務類別名、資料表名與單號）。
- 驗證指令（在 `redmine-mcp/` 下）：`uv run pytest -q`、`uv run ruff check .`、`uv run mypy`。
- 目前狀態：`develop` HEAD 為 `332e629`，`pytest` 271 passed，工具數 17。

---

## Task 1: 檔案結構搬移與簡易版文案

把指南改成一個主題一個目錄、一個階段一個檔，並寫出簡易版文案。**本任務不改工具介面**——`get_bug_report_guide` 仍無參數、仍回傳完整版，既有測試不必改動就應全綠。這樣切分是為了讓「檔案搬移沒搬壞」與「參數行為正確」能各自被驗證。

**Files:**
- Create: `redmine-mcp/src/redmine_mcp/guides/bug_report/__init__.py`
- Create: `redmine-mcp/src/redmine_mcp/guides/bug_report/report.md`
- Move: `redmine-mcp/src/redmine_mcp/guides/bug_report.md` → `redmine-mcp/src/redmine_mcp/guides/bug_report/diagnose.md`（內容一字不動）
- Modify: `redmine-mcp/src/redmine_mcp/tools/guides.py`（第 19-21 行的讀檔路徑）
- Test: `redmine-mcp/tests/test_packaging.py`（改寫既有的 `test_指南文件隨套件安裝`）

**Interfaces:**
- Consumes: 無（本任務不依賴其他任務）
- Produces:
  - 套件 `redmine_mcp.guides.bug_report`，內含 `report.md` 與 `diagnose.md`
  - `redmine_mcp.tools.guides.BUG_REPORT_GUIDE: str` 仍存在，值改為讀自 `bug_report/diagnose.md`

- [ ] **Step 1: 搬移檔案並建立子套件**

```powershell
cd c:\Users\kenny\Desktop\MCP
New-Item -ItemType Directory redmine-mcp\src\redmine_mcp\guides\bug_report | Out-Null
git mv redmine-mcp/src/redmine_mcp/guides/bug_report.md redmine-mcp/src/redmine_mcp/guides/bug_report/diagnose.md
git status --short
```

預期：`git status` 顯示一筆 `R`（rename）。**不要用編輯器重打檔案內容**——用 `git mv` 保留檔案歷史，也確保內容一字不差。

建立 `redmine-mcp/src/redmine_mcp/guides/bug_report/__init__.py`：

```python
"""臭蟲單格式指南的各階段文案。

本套件只放 Markdown 文件，不含程式邏輯。存在的理由是讓 importlib.resources
把這個目錄當套件讀，並讓 hatchling 把 *.md 一併打進 wheel。
"""
```

- [ ] **Step 2: 改寫打包測試（先寫，確認它會失敗）**

把 `redmine-mcp/tests/test_packaging.py` 檔尾既有的 `test_指南文件隨套件安裝` 整個換掉：

```python
@pytest.mark.parametrize(
    ("filename", "marker"),
    [("diagnose.md", "【問題摘要】"), ("report.md", "【影響與急迫度】")],
)
def test_指南文件隨套件安裝(filename: str, marker: str):
    # 兩份指南都是隨套件發佈的 package data。打包漏掉的話，editable 模式測試照樣
    # 全綠，只有裝成 wheel 後才會找不到檔案；新增子目錄又是最容易漏的地方。
    resource = files("redmine_mcp.guides.bug_report") / filename
    text = resource.read_text(encoding="utf-8")
    assert text.strip(), f"{filename} 為空"
    assert marker in text, f"{filename} 缺少章節標記 {marker}"
```

`pytest` 與 `files` 都已在該檔案頂端 import 過，不必再加。

- [ ] **Step 3: 執行測試確認失敗**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest tests/test_packaging.py -q
```

預期：FAIL。`report.md` 尚未建立，該參數化案例會因找不到檔案而失敗；`diagnose.md` 那筆應通過。整包 `uv run pytest -q` 此時也會因 `guides.py` 還在讀舊路徑而在收集階段就爆——這是預期的，Step 5 會修好。

- [ ] **Step 4: 撰寫簡易版文案**

建立 `redmine-mcp/src/redmine_mcp/guides/bug_report/report.md`，內容如下（**逐字照抄**；寫進檔案的是下方 ````markdown` 圍欄**內部**的文字，從 `# Redmine 臭蟲單文案格式（回報階段）` 到最後一行，不要把圍欄本身寫進去；檔案內部原有的三個反引號區塊要完整保留）：

````markdown
# Redmine 臭蟲單文案格式（回報階段）

這份格式給**發現問題、但沒有讀過相關原始碼的人**——PM、客服、測試。你要提供的是「發生了什麼」與「有多嚴重」，不是「為什麼會這樣」。根因與建議修正由接手的 RD 診斷後補上。

一張好的回報單只有一個判準：**RD 不必回頭問你，就能自己重現。**

## 標題

```
<受影響的功能>：<使用者遭遇的後果>
```

**範例：**

- `客戶匯出對帳報表：金額比實際少，財務無法對帳`
- `會員登入頁：輸入正確密碼仍顯示帳號或密碼錯誤`

反例：`報表有問題`（沒有功能、沒有後果，逼人點進來才知道嚴不嚴重）。

RD 診斷出機制後會把標題補成三段（對象、技術根因、使用者可見後果），那是他的事，你不必先猜。

## 內文骨架

章節之間以一行 60 個半形連字號分隔：

```
------------------------------------------------------------
```

四個章節都要寫。缺哪一個，RD 就得回頭問你：

```
【問題描述】

<一兩句：哪個功能、什麼情況下、發生什麼事>

------------------------------------------------------------

【重現步驟】

環境：<正式站／測試站，網址>
對象：<哪個帳號、哪個客戶、哪一筆資料>
時間：<發生時間，愈精確愈好>

1. <操作>
2. <操作>
3. 預期看到：<你認為正確的結果>
   實際看到：<畫面上真正出現的結果>

------------------------------------------------------------

【影響與急迫度】

- 誰受影響、大約多少人
- 多常發生：每次／偶爾／只發生過一次
- 有沒有替代做法可以先擋著
- 有沒有資料被寫錯或遺失

------------------------------------------------------------

【佐證】

<截圖、錯誤訊息原文、相關單號或訂單編號>
```

## 三個規則

### 不知道就寫「未確認」

沒把握的事要標明沒把握，不要猜，也不要留白。留白會讓 RD 以為你確認過了，於是他也不查——這種漏洞最難發現。

### 「預期看到」與「實際看到」一定要分開寫

只寫「結果不對」是最常見的無效回報。兩者並列，RD 才知道你認定的正確行為是什麼；有時真正的分歧就在這裡——功能其實照規格運作，是規格與期待不一致，那不是缺陷而是需求問題。

### 不要診斷

不必猜是哪支程式、哪個欄位壞了。你的猜測會變成 RD 的起點，猜錯就是把人帶往錯的方向繞一圈，比不猜更糟。把現象講清楚已經是最有價值的部分。

## 語氣與用字

- 全篇繁體中文；錯誤訊息、欄位名、單號原樣照抄，不要轉述。
- 描述事實，不評價人。寫「送出後金額少了 500」，不寫「這功能爛透了」。單子會被很多人看到，包含原作者。
- 具體數值優先於形容詞：寫「等了約 30 秒」而不是「非常慢」。

## 送出

以本 server 的 `create_issue` 工具建單。tracker 選「臭蟲」，專案取問題所在系統對應的專案。建完後把單號與網址回報給使用者。

若使用者只是要文案、還沒要開單，就直接把內容輸出在對話裡，不要自作主張送出——開單是對外動作，先確認再送。

## 自我檢查

送出前逐項確認：

- [ ] 標題兩段齊全（受影響的功能、使用者遭遇的後果）
- [ ] 重現步驟帶了環境、對象與時間
- [ ] 「預期看到」與「實際看到」分開寫，兩者都具體
- [ ] 影響評估誠實，沒有誇大也沒有輕描淡寫
- [ ] 佐證已附上（截圖、錯誤訊息原文或相關單號）
- [ ] 沒有猜測性的技術診斷

根因與建議修正由接手的 RD 診斷後補上，這張單不必預留空章節。
````

- [ ] **Step 5: 修正讀檔路徑**

修改 `redmine-mcp/src/redmine_mcp/tools/guides.py` 第 19-21 行，只改套件名與檔名，其餘不動：

```python
BUG_REPORT_GUIDE = (
    (files("redmine_mcp.guides.bug_report") / "diagnose.md").read_text(encoding="utf-8")
)
```

- [ ] **Step 6: 執行測試確認通過**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest -q
uv run ruff check .
uv run mypy
```

預期：272 passed（原 271 筆，打包測試由 1 筆變 2 筆），ruff 與 mypy 全過。既有的 `tests/test_guide_tools.py` 一個字都不必改——工具介面此時尚未變動。

- [ ] **Step 7: 確認新檔案不含禁用字串**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
Select-String -Path src\redmine_mcp\guides\bug_report\report.md -Pattern 'mcp__redmine__|ToolSearch|references/example'
```

預期：無輸出。有輸出就代表文案抄錯，回頭修正。

- [ ] **Step 8: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/src/redmine_mcp/guides redmine-mcp/src/redmine_mcp/tools/guides.py redmine-mcp/tests/test_packaging.py
git commit -m @'
feat(redmine-mcp): 新增臭蟲單回報階段的簡易版文案

現行指南預設寫單者讀過原始碼，PM／客服／測試給不出行號與根因，硬填
只會產生看似完整、實則無依據的內容。新增 report.md 只要求現象、環境、
影響與佐證，並明講「不要診斷」——猜錯的根因會把 RD 帶往錯的方向。

指南改為一個主題一個目錄、一個階段一個檔；diagnose.md 由原
bug_report.md 以 git mv 搬入，內容未動。工具介面本次不變。
'@
```

---

## Task 2: stage 參數

**Files:**
- Modify: `redmine-mcp/src/redmine_mcp/tools/guides.py`（整個檔案的常數與工具定義）
- Test: `redmine-mcp/tests/test_guide_tools.py`（既有五條測試全數改為帶參數，並新增兩條）

**Interfaces:**
- Consumes: Task 1 產出的 `redmine_mcp.guides.bug_report` 套件（內含 `report.md`、`diagnose.md`）
- Produces:
  - `redmine_mcp.tools.guides.BUG_REPORT_GUIDES: dict[str, str]` — 取代原本的 `BUG_REPORT_GUIDE`，key 為 `"report"` / `"diagnose"`
  - MCP 工具 `get_bug_report_guide(stage: str)`，回傳 `{"guide": "<Markdown 全文>"}`

- [ ] **Step 1: 改寫測試（先寫，確認會失敗）**

把 `redmine-mcp/tests/test_guide_tools.py` 從第 27 行（第一個測試）到檔尾整段換成：

```python
async def test_回報階段回傳簡易版(settings):
    sites = _single_site(settings)
    mcp = create_server(sites)
    result = await call_tool(mcp, "get_bug_report_guide", {"stage": "report"})
    await sites.aclose()

    guide = result["guide"]
    # 用各自獨有的章節標記區分兩份文案，比斷言字數可靠。
    for marker in ("【問題描述】", "【重現步驟】", "【影響與急迫度】", "【佐證】", "自我檢查"):
        assert marker in guide, f"簡易版缺少章節標記 {marker}"
    # 簡易版不該出現要求根因的章節；注意斷言的是帶方括號的標記，
    # 文末那句「根因與建議修正由接手的 RD 診斷後補上」不會誤觸。
    assert "【建議修正】" not in guide


async def test_診斷階段回傳完整版(settings):
    sites = _single_site(settings)
    mcp = create_server(sites)
    result = await call_tool(mcp, "get_bug_report_guide", {"stage": "diagnose"})
    await sites.aclose()

    guide = result["guide"]
    for marker in ("【問題摘要】", "【問題1】", "【建議修正】", "【待確認", "【參考】", "自我檢查"):
        assert marker in guide, f"完整版缺少章節標記 {marker}"
    assert "【影響與急迫度】" not in guide


async def test_無效的_stage_回報兩個合法值(settings):
    # 錯誤訊息要能讓模型自己修正，因此必須把兩個合法值都講出來，
    # 而不是靜默回傳其中一種。
    sites = _single_site(settings)
    mcp = create_server(sites)
    with pytest.raises(ToolCallError) as exc:
        await call_tool(mcp, "get_bug_report_guide", {"stage": "unknown"})
    await sites.aclose()

    message = str(exc.value)
    assert "report" in message
    assert "diagnose" in message


@pytest.mark.parametrize("stage", ["report", "diagnose"])
async def test_指南不含_client_專屬字眼(settings, stage: str):
    # 這份文字由 server 提供，可能被任何 MCP client 讀到，不可留 Claude Code 專屬措辭。
    # 注意：這裡只放不涉及內部專案的 client 專屬字眼字面常量。內部識別字（去識別化前的
    # 真實欄位名／資料表名／單號）絕對不能以字面常量寫進這支測試，否則測試檔本身
    # （會隨 sdist 發佈到 PyPI、且進公開 GitHub repo）就會變成外洩管道。
    sites = _single_site(settings)
    mcp = create_server(sites)
    guide = (await call_tool(mcp, "get_bug_report_guide", {"stage": stage}))["guide"]
    await sites.aclose()

    for banned in (
        "mcp__redmine__",
        "ToolSearch",
        "references/example",
    ):
        assert banned not in guide, f"{stage} 指南不應出現 {banned}"


async def test_完整版範例已去識別化(settings):
    # 完整版的範例已刻意改寫為去識別化後的虛構名稱（見 diagnose.md）。這裡改為
    # 正向斷言「應該出現去識別化後的名稱」，而非把真實內部識別字以字面常量寫進測試
    # 檔——若日後有人不小心把舊的、含真實內部識別字的文字貼回去，這些去識別化後的
    # 名稱就會消失，本測試就會失敗，藉此守住去識別化成果。
    # 簡易版不適用：它的範例（對帳報表、登入頁）本來就不含任何專案識別字。
    sites = _single_site(settings)
    mcp = create_server(sites)
    guide = (await call_tool(mcp, "get_bug_report_guide", {"stage": "diagnose"}))["guide"]
    await sites.aclose()

    for expected in (
        "cStepDone",
        "RecordService",
        "TblRecordDetail",
        "A 專案 #12341",
    ):
        assert expected in guide, f"完整版應保留去識別化後的名稱 {expected}"


async def test_指南工具不發出_HTTP_請求(settings):
    # 工具不依賴 SiteRegistry，也不該碰任何站台；_never_called 會在有請求時拋出。
    sites = _single_site(settings)
    mcp = create_server(sites)
    await call_tool(mcp, "get_bug_report_guide", {"stage": "report"})
    await sites.aclose()


async def test_server_指示詞提示先取得臭蟲單格式(settings):
    # 指路句是本設計的核心機制，不能只存在於某次 commit 的措辭裡。
    sites = _single_site(settings)
    instructions = build_instructions(sites)
    await sites.aclose()

    assert "get_bug_report_guide" in instructions
```

同時把該檔案頂端的 import 區改成（新增 `pytest` 與 `ToolCallError`，維持 ruff 的 isort 排序）：

```python
"""get_bug_report_guide 工具的測試。"""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from redmine_mcp.config import Settings
from redmine_mcp.server import build_instructions, create_server
from redmine_mcp.sites import SiteRegistry
from tests.conftest import ToolCallError, call_tool
```

`ToolCallError` 定義在 `tests/conftest.py`：工具內拋出的例外會被 MCP SDK 包成 `is_error=True` 的結果，`call_tool` 把它還原成這個例外，測試才能用 `pytest.raises` 斷言訊息。

- [ ] **Step 2: 執行測試確認失敗**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest tests/test_guide_tools.py -q
```

預期：FAIL。工具目前不接受任何參數，帶 `stage` 呼叫會被 schema 擋下。

- [ ] **Step 3: 實作 stage 參數**

把 `redmine-mcp/src/redmine_mcp/tools/guides.py` 整個換成：

```python
"""文件工具：把隨套件發佈的格式指南交給模型。

不連任何 Redmine 站台，因此 register() 不收 SiteRegistry。
"""
from __future__ import annotations

from importlib.resources import files
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

READ_ONLY = ToolAnnotations(read_only_hint=True)

#: 階段代號對應的檔名。stage 由模型提供，絕不可直接拼進路徑（路徑穿越），
#: 一律經由這張白名單查表。
_GUIDE_FILES = {"report": "report.md", "diagnose": "diagnose.md"}

#: 各階段的指南全文。在模組載入時各讀一次：檔案隨套件發佈，
#: 在 server 生命週期內不會變，沒有理由每次呼叫都重讀。
#: 用 importlib.resources 而非 __file__ 相對路徑，wheel、zipapp、
#: editable 安裝三種形態下都成立。讀不到就讓模組載入失敗——
#: 那代表打包漏了 package data，啟動時炸掉比執行時回傳空指南好診斷。
BUG_REPORT_GUIDES = {
    stage: (files("redmine_mcp.guides.bug_report") / name).read_text(encoding="utf-8")
    for stage, name in _GUIDE_FILES.items()
}

_STAGE_DESCRIPTION = (
    "撰寫階段。report：你只觀察到現象、沒有讀過相關原始碼（PM／客服／測試回報）。"
    "diagnose：你已實際讀過原始碼、要寫出根因與建議修正。"
    "判斷依據是你手上有沒有程式碼層級的事實，不是你的角色。"
)


def register(mcp: Any) -> None:
    """在 MCP server 上註冊文件工具。

    參數:
        mcp: MCP server 實例。本模組的工具不需要站台，故不收 SiteRegistry。
    """

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "取得撰寫 Redmine 臭蟲單（bug ticket）的公司標準格式指南，"
            "包含標題句型、章節骨架、重現步驟與影響評估的寫法，以及送出前的自我檢查清單。"
            "要建立或撰寫臭蟲單時必須先呼叫本工具並依其格式產出內容。"
            "stage=report 取得回報階段的簡易版（只需現象、環境、影響與佐證）；"
            "stage=diagnose 取得診斷階段的完整版（含根因與建議修正）。"
            "兩者是同一張單的前後段：回報者先寫現象，接手的 RD 診斷後往下追加。"
            "本工具只讀取本機內建文件，不會連線任何站台。"
        ),
    )
    async def get_bug_report_guide(
        stage: Annotated[str, Field(description=_STAGE_DESCRIPTION)],
    ) -> dict[str, str]:
        """取得指定階段的臭蟲單格式指南全文。

        參數:
            stage: 撰寫階段，report（只有現象）或 diagnose（已讀過原始碼）。
        """
        try:
            return {"guide": BUG_REPORT_GUIDES[stage]}
        except KeyError:
            valid = "、".join(sorted(BUG_REPORT_GUIDES))
            raise ValueError(
                f"stage 只接受 {valid}；收到的是 {stage!r}。"
                "只觀察到現象、沒讀過原始碼用 report；已讀過原始碼要寫根因用 diagnose。"
            ) from None
```

`raise ... from None` 是刻意的：`KeyError` 對呼叫端沒有資訊價值，鏈上去只會讓錯誤訊息更難讀。

- [ ] **Step 4: 執行測試確認通過**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest -q
uv run ruff check .
uv run mypy
```

預期：275 passed（Task 1 後為 272；本任務把 5 條測試變成 7 條、其中 1 條參數化為 2 筆，淨增 3 筆），ruff 與 mypy 全過。

若 mypy 對 `BUG_REPORT_GUIDES` 的推導型別有意見，加上明確標註 `BUG_REPORT_GUIDES: dict[str, str] = {...}`，**不要**用 `# type: ignore` 掩蓋。

- [ ] **Step 5: 確認工具數未變**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
(Select-String -Path src\redmine_mcp\tools\*.py -Pattern '@mcp\.tool\(' -AllMatches | Measure-Object).Count
```

預期：`17`。本任務只加參數、不新增工具；若變成 18 代表誤加了工具，回頭檢查。

- [ ] **Step 6: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/src/redmine_mcp/tools/guides.py redmine-mcp/tests/test_guide_tools.py
git commit -m @'
feat(redmine-mcp): 指南工具改以必填 stage 區分回報與診斷階段

report 給只觀察到現象的人、diagnose 給已讀過原始碼的人。判準以「手上
有沒有程式碼層級事實」而非角色界定，因為模型知道自己讀過什麼、不知道
自己代表誰。

刻意不給預設值：預設 report 會讓工程端沒帶參數時拿到簡易版、單子品質
變差；預設 diagnose 則讓回報情境仍拿到要求行號的文件，等於改動沒發生。
兩種預設都在某一半情境靜默降級。

stage 走凍結的白名單對照表，不拼進路徑；無效值回報兩個合法值與各自
適用時機，讓模型能自己修正。
'@
```

---

## Task 3: 指路句加上階段提示

`server.py` 的指路句與 `create_issue` 的說明目前都只寫「先呼叫 get_bug_report_guide」，沒提參數。工具現在必填 `stage`，兩處要同步，否則模型會在最靠近決策點的地方看到一句不完整的指示。

**Files:**
- Modify: `redmine-mcp/src/redmine_mcp/server.py`（`_BASE_INSTRUCTIONS`，第 14-21 行附近）
- Modify: `redmine-mcp/src/redmine_mcp/tools/issues.py`（`create_issue` 的 `description`，第 237-243 行附近）

**Interfaces:**
- Consumes: Task 2 產出的 `get_bug_report_guide(stage)`
- Produces: 無新介面

- [ ] **Step 1: 修改 server 指示詞**

`redmine-mcp/src/redmine_mcp/server.py` 的 `_BASE_INSTRUCTIONS`，把既有那句指路句（`"要建立或撰寫臭蟲單（bug）時，必須先呼叫 get_bug_report_guide "` 與 `"取得公司標準格式，再依格式撰寫內容。\n"` 兩行）換成：

```python
    "要建立或撰寫臭蟲單（bug）時，必須先呼叫 get_bug_report_guide "
    "取得公司標準格式，再依格式撰寫內容。"
    "只觀察到現象、沒讀過原始碼用 stage=report；"
    "已讀過原始碼要寫根因用 stage=diagnose。\n"
```

- [ ] **Step 2: 修改 create_issue 說明**

`redmine-mcp/src/redmine_mcp/tools/issues.py` 的 `create_issue` 裝飾器，把既有那句指路句（`"要建立或撰寫臭蟲單（bug）時，必須先呼叫 get_bug_report_guide "` 與 `"取得公司標準格式，再依格式撰寫內容。"` 兩行）換成與上一步**一字不差**的措辭（僅結尾不帶 `\n`，因為這裡是單段文字而非多行指示詞）：

```python
            "要建立或撰寫臭蟲單（bug）時，必須先呼叫 get_bug_report_guide "
            "取得公司標準格式，再依格式撰寫內容。"
            "只觀察到現象、沒讀過原始碼用 stage=report；"
            "已讀過原始碼要寫根因用 stage=diagnose。"
```

- [ ] **Step 3: 執行檢查**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest -q
uv run ruff check .
uv run mypy
```

預期：275 passed、ruff 與 mypy 全過。既有的 `test_server_指示詞提示先取得臭蟲單格式` 只斷言含 `get_bug_report_guide`，不會因措辭變動而失敗。

本步驟**不新增測試**：斷言文案字面值會讓每次潤稿都要改測試，指路機制已由該條測試守住一處。

- [ ] **Step 4: 確認兩處措辭一致**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
Select-String -Path src\redmine_mcp\server.py,src\redmine_mcp\tools\issues.py -Pattern 'stage=report|stage=diagnose'
```

預期：兩個檔案各出現一次 `stage=report` 與一次 `stage=diagnose`，共四行。

- [ ] **Step 5: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/src/redmine_mcp/server.py redmine-mcp/src/redmine_mcp/tools/issues.py
git commit -m @'
feat(redmine-mcp): 指路句補上 stage 的判準

工具改為必填 stage 後，兩處指路句若只說「先呼叫 get_bug_report_guide」，
模型會在最靠近建單決策點的地方看到一句不完整的指示。兩處同步補上
「沒讀過原始碼用 report、讀過用 diagnose」，措辭維持一致。
'@
```

---

## Task 4: 更新文件

**Files:**
- Modify: `redmine-mcp/README.md`（「指南」那段，第 74-76 行附近）
- Modify: `redmine-mcp/SETUP.md`（工具分類清單的「指南」那條）

**Interfaces:**
- Consumes: Task 2 產出的 `get_bug_report_guide(stage)`
- Produces: 無

- [ ] **Step 1: 更新 README**

把 `redmine-mcp/README.md` 中「指南」那一段（`**指南**：` 開頭那行，以及其下描述臭蟲單格式的整段）換成：

```markdown
**指南**：`get_bug_report_guide(stage)`

臭蟲單的公司標準格式內建於 server，不需另外安裝 skill。分兩個階段：

- `stage="report"` — 回報階段的簡易版。給只觀察到現象、沒讀過原始碼的人（PM、客服、測試），只需要問題描述、重現步驟、影響與佐證。
- `stage="diagnose"` — 診斷階段的完整版。給已讀過原始碼的人，含根因、建議修正與待確認事項。

兩者是同一張單的前後段：回報者先寫現象與影響，接手的 RD 診斷後往下追加根因與建議修正。`stage` 必填，判準是**手上有沒有程式碼層級的事實**，不是職稱。

這是提示而非強制，`create_issue` 的行為與驗證邏輯完全不變。
```

- [ ] **Step 2: 更新 SETUP.md**

把 `redmine-mcp/SETUP.md` 工具分類清單中的「指南」那一條換成：

```markdown
- **指南**：`get_bug_report_guide(stage)`（臭蟲單格式，內建於 server，不需另裝 skill；`report` 為回報階段簡易版、`diagnose` 為診斷階段完整版）
```

工具總數維持 17，分類仍為五類，那兩處數字不必改。

- [ ] **Step 3: 確認數字仍然正確**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
(Select-String -Path src\redmine_mcp\tools\*.py -Pattern '@mcp\.tool\(' -AllMatches | Measure-Object).Count
Select-String -Path SETUP.md -Pattern '個工具，分'
```

預期：`17`，且 SETUP.md 該行仍寫「17 個工具，分五類」。

- [ ] **Step 4: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/README.md redmine-mcp/SETUP.md
git commit -m @'
docs(redmine-mcp): 說明指南工具的兩個階段

補上 report 與 diagnose 的適用時機，並講明判準是手上有沒有程式碼層級
的事實而非職稱。工具總數不變。
'@
```
