# create_issue 預計完成日必填 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 Redmine issue 一律要有預計完成日，且日期格式在送出前就確認正確。

**Architecture:** `create_issue` 的 `due_date` 從選填改為 schema 必填（型別 `str`、無預設值）；日期格式驗證抽成模組層級 helper，掛在 `create_issue` 與 `update_issue` 共用的 `_build_issue_payload` 上，一份程式碼兩處生效。必填只加在 `create_issue`——`update_issue` 若強制必填，每次改別的欄位都得重填期限。

**Tech Stack:** Python 3.11+、MCP SDK 2.0（`mcp>=2.0,<3`）、pytest（asyncio_mode=auto）、ruff、mypy、uv。

**設計文件：** `redmine-mcp/docs/superpowers/specs/2026-08-12-create-issue-due-date-required-design.md`

## Global Constraints

- 工作目錄：`c:\Users\kenny\Desktop\MCP`（外層 monorepo），套件根為其下的 `redmine-mcp/`。所有 `uv` 指令都在 `redmine-mcp/` 執行。
- 所有改動提交到 `develop` 分支。commit 訊息**不加 Redmine 編號**（分支名非數字）。
- 註解、docstring、錯誤訊息、測試函式名一律繁體中文。
- `mypy` 設定 `disallow_untyped_defs = true` 且 `files = ["src"]`：`src/` 下所有函式都要有型別標註。
- `ruff` `line-length = 100`，`target-version = "py311"`，選用規則 `E,F,I,UP,B,RUF`（`I` 為 isort，新增 import 要放對位置）。
- 每個 `.py` 檔開頭 `from __future__ import annotations`。
- 日期格式一律 `YYYY-MM-DD`（Redmine 接受的形式）。
- **不改** `create_project`、不擋過去的日期、不在 server 端推算或預設日期。
- 測試中新增的日期一律用 `"2026-09-30"`（合法值），錯誤案例用計畫指定的字串。
- 驗證指令（在 `redmine-mcp/` 下）：`uv run pytest -q`、`uv run ruff check .`、`uv run mypy`。
- 目前狀態：`develop` HEAD 為 `720da5d`，`pytest` 275 passed，工具數 17。
- 工作樹另有**不屬於本計畫**的改動，任何 `git add` 都不得納入：repo 根目錄的 `README.md`（若有修改）、`.github/release.yml`、`.github/workflows/release.yml`。禁用 `git add -A` 與 `git add .`。

---

## Task 1: 日期格式驗證（共用路徑）

先做非破壞性的那一半：加上格式驗證並讓 `create_issue` 與 `update_issue` 都生效。此時 `due_date` **仍是選填**，既有 11 處 `create_issue` 呼叫一個字都不用改，全部測試應維持綠燈。這樣切分讓「驗證邏輯對不對」與「必填會不會打壞既有呼叫」能各自被審。

**Files:**
- Modify: `redmine-mcp/src/redmine_mcp/tools/issues.py`（檔頭 import、新增 `_DATE_PATTERN` 與 `_validate_date`、`_build_issue_payload` 內呼叫）
- Test: `redmine-mcp/tests/test_issue_write_tools.py`（檔尾新增兩條測試）

**Interfaces:**
- Consumes: 既有的 `_build_issue_payload(project_id=None, subject=None, description=None, tracker_id=None, status_id=None, priority_id=None, assigned_to_id=None, parent_issue_id=None, done_ratio=None, start_date=None, due_date=None, custom_fields=None) -> dict[str, Any]`；`tests.conftest.call_tool`（該測試檔以 `from tests.conftest import call_tool as _call` 匯入）
- Produces:
  - `redmine_mcp.tools.issues._DATE_PATTERN`（`re.Pattern[str]`，`^\d{4}-\d{2}-\d{2}$`）
  - `redmine_mcp.tools.issues._validate_date(value: str | None, field: str) -> None`

- [ ] **Step 1: 寫出失敗的測試**

在 `redmine-mcp/tests/test_issue_write_tools.py` **檔尾**追加：

```python
async def test_update_issue_預計完成日同樣驗證格式(registry):
    # 驗證掛在 create_issue 與 update_issue 共用的 payload 組裝函式上，
    # 因此更新期限時也該被同一套規則擋下。
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("格式驗證失敗時不該送出請求")

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(mcp, "update_issue", {"issue_id": 7, "due_date": "2026/09/30"})
    await sites.aclose()

    assert "YYYY-MM-DD" in str(exc.value)


async def test_update_issue_可不帶預計完成日(registry):
    # 必填只加在 create_issue：update_issue 若強制必填，每次改別的欄位都要重填期限。
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(mcp, "update_issue", {"issue_id": 7, "subject": "改主旨"})
    await sites.aclose()

    assert seen["body"]["issue"]["subject"] == "改主旨"
    assert "due_date" not in seen["body"]["issue"]
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest tests/test_issue_write_tools.py -q
```

預期：`test_update_issue_預計完成日同樣驗證格式` FAIL——目前沒有任何格式驗證，`"2026/09/30"` 會被原樣送給假 handler，而該 handler 一收到請求就拋 `AssertionError`。另一條 `test_update_issue_可不帶預計完成日` 應該直接 PASS（它驗證的是現有行為，作為必填不擴散的迴歸防護）。

- [ ] **Step 3: 加入驗證 helper**

修改 `redmine-mcp/src/redmine_mcp/tools/issues.py`。

檔頭 import 區（目前是 `from __future__ import annotations` 後接空行、`from typing import Annotated, Any`）改為：

```python
from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Any
```

在 `_build_issue_payload` **之前**（`_build_custom_fields` 之後）新增：

```python
#: Redmine 只接受 YYYY-MM-DD 形式的日期。
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(value: str | None, field: str) -> None:
    """檢查日期字串是否為 Redmine 接受的 YYYY-MM-DD；None 視為未提供，不檢查。

    兩道檢查各補對方的漏：Python 3.11 起 date.fromisoformat 放寬到接受多種 ISO
    形式（例如無連字號的 20260930），那些 Redmine 並不接受，所以要先用正規式
    卡住形狀；而正規式看不出 2026-02-31 這種形狀正確但不存在的日期，所以再用
    date.fromisoformat 確認它真的是一天。

    參數:
        value: 待檢查的日期字串；None 代表呼叫端未提供該欄位。
        field: 欄位名稱，用於錯誤訊息。
    """
    if value is None:
        return
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{field} 必須是 YYYY-MM-DD 格式（收到：{value!r}）")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} 不是有效日期（收到：{value!r}）") from None
```

- [ ] **Step 4: 在共用路徑呼叫驗證**

在 `_build_issue_payload` 的函式主體最前面（docstring 之後、`fields: dict[str, Any] = {` 之前）加入兩行：

```python
    _validate_date(start_date, "start_date")
    _validate_date(due_date, "due_date")
```

放在組 payload 之前，是為了讓驗證失敗時不會有任何請求送出——既有測試中多條都以 `calls == []` 斷言「參數驗證失敗時不送請求」，這個順序是那個性質的一部分。

- [ ] **Step 5: 執行測試確認通過**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest -q
uv run ruff check .
uv run mypy
```

預期：277 passed（原 275 加本任務 2 條），ruff 與 mypy 全過。**既有 11 處 `create_issue` 呼叫不必修改**——此時 `due_date` 仍是選填，未提供即為 `None`，`_validate_date` 直接返回。

- [ ] **Step 6: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/src/redmine_mcp/tools/issues.py redmine-mcp/tests/test_issue_write_tools.py
git commit -m @'
feat(redmine-mcp): 日期欄位在送出前先驗格式

start_date 與 due_date 原本原樣送給 Redmine，格式錯了要等對方回錯才知道，
而 Redmine 的訊息不一定講得清楚。改為本地先驗，掛在 create_issue 與
update_issue 共用的 payload 組裝函式上。

用正規式加 date.fromisoformat 兩道：只靠後者會放行 20260930 這種 Redmine
不接受的 ISO 形式，只靠前者則放行 2026-02-31。驗證放在組 payload 之前，
維持「參數驗證失敗不送出任何請求」這個既有性質。
'@
```

---

## Task 2: create_issue 的 due_date 改為必填

這是破壞性的那一半：改契約，並修好所有因此失效的既有呼叫。

**Files:**
- Modify: `redmine-mcp/src/redmine_mcp/tools/issues.py`（`create_issue` 的簽章與 docstring）
- Modify: `redmine-mcp/tests/test_issue_write_tools.py`（8 處既有呼叫補參數、檔尾新增 4 條測試）
- Modify: `redmine-mcp/tests/test_upload_tools.py`（3 處既有呼叫補參數）

**Interfaces:**
- Consumes: Task 1 產出的 `_validate_date(value: str | None, field: str) -> None`（已由 `_build_issue_payload` 呼叫，本任務不需再呼叫）
- Produces: `create_issue(project_id: str, subject: str, due_date: str, site: str | None = None, ...)` — `due_date` 為第三個位置參數、無預設值，MCP schema 將其列入 `required`

- [ ] **Step 1: 寫出失敗的測試**

在 `redmine-mcp/tests/test_issue_write_tools.py` **檔尾**追加四條：

```python
async def test_create_issue_未給預計完成日被拒絕(registry):
    # due_date 是 schema 必填，工具根本不會被執行，因此不該有任何請求送出。
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("缺少必填欄位時不該送出請求")

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(mcp, "create_issue", {"project_id": "crm", "subject": "沒填期限"})
    await sites.aclose()

    # 斷言訊息指名 due_date，才能與「handler 收到請求時拋的 AssertionError」區分開。
    # 少了這一句，本條在改為必填之前也會通過——通過的原因是假 handler 拋錯，
    # 而不是 schema 攔截。
    assert "due_date" in str(exc.value)


@pytest.mark.parametrize(
    "bad_date",
    [
        "2026/09/30",  # 斜線
        "20260930",  # 無連字號；date.fromisoformat 會放行，但 Redmine 不接受
        "2026-9-30",  # 單位數月日
        "下週五",  # 自然語言
    ],
)
async def test_create_issue_預計完成日格式錯誤被拒絕(registry, bad_date: str):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("格式驗證失敗時不該送出請求")

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "create_issue",
            {"project_id": "crm", "subject": "s", "due_date": bad_date},
        )
    await sites.aclose()

    assert "YYYY-MM-DD" in str(exc.value)


async def test_create_issue_不存在的日期被拒絕(registry):
    # 形狀正確但不是真的一天；錯誤訊息要與格式錯誤區分，因為呼叫端要採取的
    # 行動不同：格式錯誤是改寫法，日期不存在是改日期。
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("日期無效時不該送出請求")

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "create_issue",
            {"project_id": "crm", "subject": "s", "due_date": "2026-02-31"},
        )
    await sites.aclose()

    message = str(exc.value)
    assert "不是有效日期" in message
    assert "YYYY-MM-DD" not in message


async def test_create_issue_開始日期同樣驗證格式(registry):
    # start_date 也是日期字串，走同一個 helper；不驗會讓同一個工具有兩套標準。
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("格式驗證失敗時不該送出請求")

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "create_issue",
            {
                "project_id": "crm",
                "subject": "s",
                "due_date": "2026-09-30",
                "start_date": "2026/09/01",
            },
        )
    await sites.aclose()

    assert "start_date" in str(exc.value)
```

- [ ] **Step 2: 執行測試確認失敗**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest tests/test_issue_write_tools.py -q
```

```powershell
uv run pytest tests/test_issue_write_tools.py -v -k "預計完成日 or 不存在的日期 or 開始日期"
```

預期：

- `test_create_issue_未給預計完成日被拒絕` **FAIL**。目前 `due_date` 選填，不帶也會成功建單，假 handler 因收到請求而拋 `AssertionError`；該例外雖然也是 `Exception`，但訊息裡沒有 `due_date`，所以最後那句斷言會失敗。這正是它必須斷言訊息內容的原因——否則這條測試在改動前後都會綠，什麼也沒守住。
- `test_create_issue_預計完成日格式錯誤被拒絕` 四個案例、`test_create_issue_不存在的日期被拒絕`、`test_create_issue_開始日期同樣驗證格式` 此時**應該已經通過**：格式驗證是 Task 1 加的，本任務只改必填。它們留在這裡是為了讓「格式規則對 `create_issue` 也生效」有明確的測試，而非只透過 `update_issue` 間接驗到。

- [ ] **Step 3: 改為必填**

修改 `redmine-mcp/src/redmine_mcp/tools/issues.py` 的 `create_issue`。

把原本位於選填區的 `due_date`（`due_date: Annotated[str | None, Field(description="完成期限，格式 YYYY-MM-DD。")] = None,`）整段**刪除**，並在 `subject` 之後、`site` 之前插入：

```python
        due_date: Annotated[
            str, Field(description="預計完成日，格式 YYYY-MM-DD（必填）。")
        ],
```

Python 要求無預設值的參數排在有預設值者之前，因此它必須落在 `project_id`、`subject` 之後、`site` 之前。

同時更新該函式 docstring 的參數列：把原本的 `due_date: 完成期限，格式 YYYY-MM-DD。` 改為 `due_date: 預計完成日，格式 YYYY-MM-DD（必填）。`，並把它移到 `subject` 那一行之後，讓順序與簽章一致。

- [ ] **Step 4: 修好既有的 11 處呼叫**

每一處都在傳給 `create_issue` 的參數字典中加入 `"due_date": "2026-09-30"`。

`redmine-mcp/tests/test_issue_write_tools.py` 八處：

| 位置 | 原本的參數字典 |
| --- | --- |
| `test_create_issue_送出正確的_payload` | 多行字典，含 `"custom_fields": {"5": "A001"}` |
| `test_create_issue_未給的欄位不出現在_payload` | `{"project_id": "crm", "subject": "只有必填"}` |
| `test_create_issue_空白subject被拒絕` | `{"project_id": "crm", "subject": "   "}` |
| `test_422_錯誤訊息帶到工具層` | `{"project_id": "crm", "subject": "x"}` |
| `test_custom_fields_的key不是數字時給出可行動的錯誤` | `{"project_id": "crm", "subject": "s", "custom_fields": {"客戶代號": "A001"}}` |
| `test_多站台時建立單未指定站台會被拒絕` | `{"project_id": "p", "subject": "s"}` |
| `test_寫入只送到指定的站台` | `{"site": "client-b", "project_id": "p", "subject": "s"}` |
| `test_單站台時可省略站台` | `{"project_id": "p", "subject": "s"}` |

`redmine-mcp/tests/test_upload_tools.py` 三處：

| 位置 | 原本的參數字典 |
| --- | --- |
| 帶 `uploads` 建單的測試（含 `"project_id": "mcp-test"`） | 多行字典，含 `"uploads": [...]` |
| `test_uploads缺token被拒絕` | `{"project_id": "p", "subject": "s", "uploads": [{"filename": "a.png"}]}` |
| `test_uploads含不支援欄位被拒絕` | `{"project_id": "p", "subject": "s", "uploads": [{"token": "t", "path": "/etc/passwd"}]}` |

`test_create_issue_空白subject被拒絕` 這一處**特別重要**：不補 `due_date` 的話它仍會「通過」，但通過的原因變成缺少必填欄位而不是空白主旨——測試名稱與實際驗證的行為就對不上了。補上合法日期後，它才真的在驗空白主旨。

`test_create_issue_未給的欄位不出現在_payload` 的斷言（`assigned_to_id`、`description`、`custom_fields` 不在 payload 中）不受影響，`due_date` 不在那份清單裡。

- [ ] **Step 5: 執行測試確認通過**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run pytest -q
uv run ruff check .
uv run mypy
```

預期：284 passed（Task 1 後為 277，本任務新增 4 條測試函式、其中 1 條參數化為 4 筆，淨增 7 筆），ruff 與 mypy 全過。

- [ ] **Step 6: 確認 schema 真的把它列為必填**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
uv run python -c "
import asyncio, json
from dataclasses import replace
import httpx
from redmine_mcp.config import Settings, SiteSettings
from redmine_mcp.server import create_server
from redmine_mcp.sites import SiteRegistry
from pathlib import Path

s = SiteSettings(name='main', url='https://x.example.com', api_key='k', description=None,
                 download_dir=Path('.'), upload_dir=Path('.'), max_attachment_bytes=1024)
sites = SiteRegistry(Settings(sites={'main': s}),
                     transports={'main': httpx.MockTransport(lambda r: httpx.Response(200))})
mcp = create_server(sites)
tools = asyncio.run(mcp.list_tools())
schema = next(t for t in tools if t.name == 'create_issue').input_schema
print('required:', sorted(schema.get('required', [])))
asyncio.run(sites.aclose())
"
```

預期輸出的 `required` 清單同時含 `due_date`、`project_id`、`subject`。**只有這一步能證明必填是靠 schema 而非執行期檢查**——測試那條用 `pytest.raises(Exception)` 無法區分兩者。若 `due_date` 不在清單裡，回頭檢查它是否真的沒有預設值。

- [ ] **Step 7: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/src/redmine_mcp/tools/issues.py redmine-mcp/tests/test_issue_write_tools.py redmine-mcp/tests/test_upload_tools.py
git commit -m @'
feat(redmine-mcp): create_issue 的預計完成日改為必填

單子沒有期限就排不進工作，也無法判斷是否延誤。改為 schema 必填（型別
str、無預設值），模型看 schema 就知道要填，不必撞一次錯誤才學到。

這是工具契約的 breaking change：漏帶 due_date 的呼叫會被 schema 擋下，
工具不會被執行。既有測試 11 處呼叫一併補上合法日期——其中空白主旨那條
若不補，通過的原因會變成缺必填欄位而不是空白主旨，測試名稱與實際驗證的
行為就對不上。

update_issue 維持選填：它只送有提供的欄位，強制必填會導致每次改別的欄位
都要重填期限。
'@
```

---

## Task 3: 更新文件

**Files:**
- Modify: `redmine-mcp/README.md`（「指南」段末句）
- Modify: `redmine-mcp/SETUP.md`（`create_issue` 的操作範例）

**Interfaces:**
- Consumes: Task 2 產出的 `create_issue(project_id, subject, due_date, ...)`
- Produces: 無

- [ ] **Step 1: 修正 README 那句已失效的敘述**

`redmine-mcp/README.md` 的「指南」段末句目前是：

```
這是提示而非強制，`create_issue` 的行為與驗證邏輯完全不變。
```

該句原意是「格式指南不強制」，但 `create_issue` 現在確實多了必填與驗證，後半段已不成立。改為：

```
格式指南本身是提示而非強制：`create_issue` 不會檢查內文是否符合骨架。（但 `due_date` 為必填，且日期格式會在送出前驗證——那與格式指南無關。）
```

- [ ] **Step 2: 修正 SETUP.md 的操作範例**

`redmine-mcp/SETUP.md` 的操作範例目前是：

```
3. `create_issue(project_id="mcp-test", subject="...", uploads=[{token, filename, content_type}])`
```

`due_date` 現在必填，這個範例照打會失敗。改為：

```
3. `create_issue(project_id="mcp-test", subject="...", due_date="2026-09-30", uploads=[{token, filename, content_type}])`
```

- [ ] **Step 3: 確認文件裡沒有其他失效的 create_issue 範例**

```powershell
cd c:\Users\kenny\Desktop\MCP\redmine-mcp
Select-String -Path README.md,SETUP.md -Pattern 'create_issue\('
```

預期：只列出 SETUP.md 剛改好的那一行（已含 `due_date`）。若還有其他帶括號的呼叫範例沒有 `due_date`，一併補上。

- [ ] **Step 4: 提交**

```powershell
cd c:\Users\kenny\Desktop\MCP
git add redmine-mcp/README.md redmine-mcp/SETUP.md
git commit -m @'
docs(redmine-mcp): 修正因 due_date 必填而失效的敘述與範例

README 原本寫「create_issue 的行為與驗證邏輯完全不變」，那是格式指南那次
改動留下的敘述，加了必填與日期驗證後後半段不再成立；改為只說格式指南本身
不強制，並明確區分 due_date 的必填與格式指南無關。

SETUP.md 的操作範例缺 due_date，照打會被 schema 擋下，一併補上。
'@
```
