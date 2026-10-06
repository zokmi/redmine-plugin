# 概述骨架精簡與 assess 註記模板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓調整單與新需求單的概述只留三章必填內容，並為「手上已有事實的人」另備一份評估註記模板。

**Architecture:** `get_issue_guide` 的 `stage` 由「只有 bug 需要」擴充為「bug 必填、change／feature 可選」。`stage=None` 維持回傳概述骨架，`stage="assess"` 回傳新增的註記模板。原本掛在概述的 `影響範圍`／`不在範圍內`／`待確認` 連同它們的寫作指引整段搬進註記模板，概述側的自我檢查由防「留白」改為防「越界」。落點規則因此從「三者＋一個例外」收斂成一條通則：有 stage 後段的填 `notes`，沒有 stage 的填 `description`。

**Tech Stack:** Python 3.11+、MCP SDK 2.0（`mcp>=2.0,<3`）、hatchling、pytest（`asyncio_mode=auto`）、ruff、mypy、uv。

**Spec:** `redmine-mcp/docs/superpowers/specs/2026-08-18-guide-assess-stage-design.md`

## Global Constraints

- 工作目錄：`c:\Users\kenny\Desktop\MCP`（外層 monorepo），套件根為其下的 `redmine-mcp/`。所有 `uv` 指令都在 `redmine-mcp/` 執行。
- 改動提交到 `develop`。commit 訊息**不加 Redmine 編號**（本 repo 無單號體系），格式 `type(scope): 主旨`，scope 一律 `redmine-mcp`。
- 註解、docstring、錯誤訊息、測試函式名一律繁體中文。
- `mypy` 設 `disallow_untyped_defs = true`、`files = ["src"]`：`src/` 下所有函式都要有型別標註。
- `ruff` `line-length = 100`、`target-version = "py311"`、規則 `E,F,I,UP,B,RUF`。
- 每個 `.py` 檔開頭 `from __future__ import annotations`。
- **`guides/bug_report/report.md` 與 `guides/bug_report/diagnose.md` 一字不改。**
- 唯一的新階段值是 `"assess"`。`stage` 來自模型，**絕不可**拼進檔案路徑（路徑穿越漏洞），一律走 `_GUIDE_FILES` 白名單查表。
- 六份指南全文都不得出現 `mcp__redmine__`、`ToolSearch`、`references/example`，也不得出現任何真實內部專案識別字（欄位名、服務類別名、資料表名、單號）。
- 指南內文一律 Markdown：章節 `##`、子節 `###`，程式碼與設定值用反引號或圍欄區塊，不用 textile、不用連字號分隔線。半形括號留給程式碼，全形括號留給中文。
- 驗證指令（在 `redmine-mcp/` 下）：`uv run pytest -q`、`uv run ruff check .`、`uv run mypy`。
- **起始基線**：`develop` HEAD 為 `d069673`，`uv run pytest -q` 為 `549 passed, 1 skipped`，工具數 17。每個 Task 結束時測試數只增不減，工具數維持 17。
- 定位一律以引號內的**原文字串**為準，不要相信行號——行號會隨前面的 Task 漂移。

## File Structure

```
src/redmine_mcp/guides/
  bug_report/report.md            不動
  bug_report/diagnose.md          不動
  change_request/change.md        Task 3：砍兩章、清八處交叉引用
  change_request/assess.md        Task 1：新增（影響範圍／待確認）
  feature_request/feature.md      Task 3：砍三章、清十處交叉引用
  feature_request/assess.md       Task 1：新增（影響範圍與相依／不在範圍內／待確認）
src/redmine_mcp/tools/guides.py   Task 2：白名單與驗證；Task 4：工具說明
src/redmine_mcp/tools/issues.py   Task 4：四處落點說明
src/redmine_mcp/server.py         Task 4：指示詞三處
tests/test_guide_tools.py         Task 2／3／4：新增與改寫
tests/test_packaging.py           Task 1：參數化表加兩筆
README.md、SETUP.md、pyproject.toml  Task 5
```

責任邊界：`assess.md` 只放「有事實才寫」的章節與其寫作指引；`change.md`／`feature.md` 只放需求方寫得出來的必填章節。同一段散文不可長期存在於兩邊——Task 1 先在新檔建立，Task 3 才從舊檔移除，兩個 Task 之間內容短暫重複是刻意的，讓各自都能獨立驗證通過。

---

### Task 1: 新增兩份評估註記模板

**Files:**
- Create: `src/redmine_mcp/guides/change_request/assess.md`
- Create: `src/redmine_mcp/guides/feature_request/assess.md`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: 無（本 Task 不動任何 Python）。
- Produces: 兩個 package data 檔案，供 Task 2 的 `_GUIDE_FILES` 以
  `("redmine_mcp.guides.change_request", "assess.md")` 與
  `("redmine_mcp.guides.feature_request", "assess.md")` 讀取。
  兩份共有的獨有標記是 `## 待確認（需求面）`；change 版另有 `## 影響範圍`，
  feature 版另有 `## 影響範圍與相依`、`## 不在範圍內`。兩份都含字串 `add_issue_note`。

- [ ] **Step 1: 寫失敗測試** — `tests/test_packaging.py` 的 `test_指南文件隨套件安裝` 參數化表末尾加兩筆

```python
@pytest.mark.parametrize(
    ("package", "filename", "marker"),
    [
        ("redmine_mcp.guides.bug_report", "diagnose.md", "## 問題摘要"),
        ("redmine_mcp.guides.bug_report", "report.md", "## 影響與急迫度"),
        ("redmine_mcp.guides.change_request", "change.md", "## 調整內容"),
        ("redmine_mcp.guides.feature_request", "feature.md", "## 驗收標準"),
        ("redmine_mcp.guides.change_request", "assess.md", "## 影響範圍"),
        ("redmine_mcp.guides.feature_request", "assess.md", "## 不在範圍內"),
    ],
)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_packaging.py -q`
Expected: FAIL — 新增兩筆拋 `FileNotFoundError`（`importlib.resources` 找不到 `assess.md`）。

- [ ] **Step 3: 建立 `src/redmine_mcp/guides/change_request/assess.md`**

內容如下（整份，含最外層的 `# ` 標題）：

<!-- BEGIN change_request/assess.md -->
```
# Redmine 調整單評估註記格式

這份格式給**手上已有事實的人**——多半是接手的 RD，但需求方自己知道相依或既有資料狀況時同樣適用。判準是手上有沒有可寫的事實，不是你的角色。

概述（`description`）留給提出調整的人寫現況、調整內容與驗收標準。影響範圍與待確認事項屬於「查得出來才寫得出來」的內容，往下追加在註記。

一則好的評估註記只有一個判準：**看完的人知道這次異動會碰到什麼，以及還有哪件事沒拍板。**

## 落點

- 內容整份填入 `add_issue_note` 的 `notes`。
- **不要動概述。** 那是需求方的入口，覆寫掉等於把非 IT 讀者趕走。
- **沒有事實可寫就不要追加註記。** 註記會通知關注者，一則空模板比不寫更擾人。

## 排版格式

同調整單概述：一律 Markdown，章節用 `##`，設定值與欄位名用反引號，條列用 `-`。不要用 textile，不要用連字號分隔線。

## 內文骨架

兩章都是「有事實才寫」。沒有就整段拿掉，不要留空標題；兩章都無內容時，這則註記根本不該送出。

## 影響範圍

<會不會改變既有行為、既有資料要不要一併處理、有沒有其他功能跟著受影響>

## 待確認（需求面）

<需要需求方拍板才能定案的部分>

## 各章節怎麼寫

### 影響範圍

回答三件事，答得出來就寫，答不出來的那一項就不寫，不要猜：

- **既有行為會不會變**：使用者會不會看到不一樣的結果？會的話，要不要事先公告？
- **既有資料要不要處理**：改了規則之後，不符合新規則的舊資料怎麼辦——保留、轉換，還是擋在下次編輯時？這一項最常被漏掉，卻最常在上線後炸開。
- **有沒有連帶對象**：其他功能、報表、對外介接或排程有沒有依賴這個行為。

### 待確認（需求面）

技術上做得到、但**要不要這樣做得由需求方決定**的部分放這裡。把選項與各自的影響講清楚，明確提問，別把疑問藏在敘述裡讓人自行體會。

沒有待確認事項就整段刪掉。空章節比沒有章節更浪費讀者時間。

## 語氣與用字

- 全篇繁體中文；設定鍵名、路徑、欄位名、程式碼維持原樣不翻譯。
- 描述事實，不評價人。單子會被很多人看到，包含原作者。
- 具體值優先於形容詞：寫「約 1,200 筆舊資料不符新規則」而不是「有些舊資料會有問題」。
- 半形括號留給程式碼，全形括號留給中文。

## 送出

以本 server 的 `add_issue_note` 工具追加，`notes` 填上面那份骨架，`description` 不要一併帶上去。

標題需要修正時另走 `update_issue` 只改 `subject`，不要順手帶 `description`。

若使用者只是要文案、還沒要送出，就直接把內容輸出在對話裡——追加註記會通知關注者，是對外動作，先確認再送。

## 自我檢查

- [ ] 內容寫在 `add_issue_note` 的 `notes`，沒有動到概述
- [ ] 寫下的每一項都是查得出來的事實，沒有為了填滿而猜
- [ ] 影響範圍若有寫，既有資料怎麼處理有明確交代
- [ ] 要需求方拍板的事都在「待確認」，沒有藏在敘述裡
- [ ] 沒有空章節；兩章都無內容時不送出這則註記
```
<!-- END change_request/assess.md -->

> **實作注意**：上面「內文骨架」章節裡的 `## 影響範圍` 與 `## 待確認（需求面）` 兩段，在真正的檔案裡要包在四個反引號的 ` ````markdown ` 圍欄區塊內（與 `change.md` 現行骨架區塊同樣寫法）。這份計畫用三個反引號包住整份內容，無法再嵌套四個反引號，因此骨架區塊在此以裸文字呈現——寫檔時務必補回圍欄，否則模型會把骨架標題誤讀成本文件自己的章節。

- [ ] **Step 4: 建立 `src/redmine_mcp/guides/feature_request/assess.md`**

<!-- BEGIN feature_request/assess.md -->
```
# Redmine 新需求單評估註記格式

這份格式給**手上已有事實的人**——多半是接手的 RD，但需求方自己知道相依單號或已決定延後哪些事時同樣適用。判準是手上有沒有可寫的事實，不是你的角色。

概述（`description`）留給提需求的人寫背景與目的、需求內容與驗收標準。影響範圍、相依、範圍排除與待確認事項屬於「查得出來或討論後才定案」的內容，往下追加在註記。

一則好的評估註記只有一個判準：**驗收的時候，雙方不會對「這樣算不算做完」有歧見。**

## 落點

- 內容整份填入 `add_issue_note` 的 `notes`。
- **不要動概述。** 那是需求方的入口，覆寫掉等於把非 IT 讀者趕走。
- **沒有事實可寫就不要追加註記。** 註記會通知關注者，一則空模板比不寫更擾人。

## 排版格式

同新需求單概述：一律 Markdown，章節用 `##`，欄位名、參數、狀態值用反引號，條列用 `-`。不要用 textile，不要用連字號分隔線。

## 內文骨架

三章都是「有事實才寫」。沒有就整段拿掉，不要留空標題；三章都無內容時，這則註記根本不該送出。

## 影響範圍與相依

<會動到哪些既有功能、需要哪些前置條件或外部資源>

## 不在範圍內

<明確排除、這次不做的事>

## 待確認（需求面）

<需要需求方拍板才能定案的部分>

## 各章節怎麼寫

### 影響範圍與相依

- **會動到哪些既有功能**：新增的東西擠進既有畫面或流程時，原本的行為會不會變。
- **前置條件與外部相依**：需不需要新的權限設定、第三方服務、資料匯入，或另一張單先完成。相依的單要寫單號。

答得出來就寫，答不出來的那一項就不寫，不要猜。

### 不在範圍內

明確寫出**這次不做**的事，尤其是討論過程中被提到、但決定延後的部分。

這一段的作用是防止驗收時範圍擴張：沒寫下來的排除，事後很難主張。若某項是「之後另開單處理」，就直接這樣寫。

### 待確認（需求面）

需要需求方拍板才能定案的部分放這裡，把選項與各自的影響講清楚，明確提問。

沒有待確認事項就整段刪掉。空章節比沒有章節更浪費讀者時間。

## 語氣與用字

- 全篇繁體中文；欄位名、參數、狀態值、路徑維持原樣不翻譯。
- 描述事實，不評價人。單子會被很多人看到，包含原作者。
- 具體數值優先於形容詞：寫「單次最多匯出 10,000 筆」而不是「不要太多筆」。
- 半形括號留給程式碼，全形括號留給中文。

## 送出

以本 server 的 `add_issue_note` 工具追加，`notes` 填上面那份骨架，`description` 不要一併帶上去。

標題需要修正時另走 `update_issue` 只改 `subject`，不要順手帶 `description`。

若使用者只是要文案、還沒要送出，就直接把內容輸出在對話裡——追加註記會通知關注者，是對外動作，先確認再送。

## 自我檢查

- [ ] 內容寫在 `add_issue_note` 的 `notes`，沒有動到概述
- [ ] 寫下的每一項都是查得出來的事實，沒有為了填滿而猜
- [ ] 相依若有寫，單號寫出來了
- [ ] 討論過但決定延後的事已寫進「不在範圍內」，避免驗收時範圍擴張
- [ ] 沒有空章節；三章都無內容時不送出這則註記
```
<!-- END feature_request/assess.md -->

> **實作注意**：同 Step 3——「內文骨架」章節裡的三段標題要包在 ` ````markdown ` 圍欄區塊內。

- [ ] **Step 5: 執行測試確認通過**

Run: `uv run pytest tests/test_packaging.py -q`
Expected: PASS，該檔測試數 +2。

- [ ] **Step 6: 確認禁用字眼未被帶入**

Run: `grep -n "mcp__redmine__\|ToolSearch\|references/example" src/redmine_mcp/guides/change_request/assess.md src/redmine_mcp/guides/feature_request/assess.md`
Expected: 無輸出（`grep` 以 exit code 1 結束）。

- [ ] **Step 7: Commit**

```bash
git add src/redmine_mcp/guides/change_request/assess.md \
        src/redmine_mcp/guides/feature_request/assess.md \
        tests/test_packaging.py
git commit -m "feat(redmine-mcp): 新增調整單與新需求單的評估註記模板"
```

---

### Task 2: 擴充 stage 契約至 change 與 feature

**Files:**
- Modify: `src/redmine_mcp/tools/guides.py`（`_GUIDE_FILES`、`_STAGED_KINDS`、`_resolve_guide`）
- Test: `tests/test_guide_tools.py`

**Interfaces:**
- Consumes: Task 1 建立的兩份 `assess.md`。
- Produces: `get_issue_guide(kind, stage=None)` 支援 `("change", "assess")` 與 `("feature", "assess")`。
  模組層級新增 `_REQUIRED_STAGE_KINDS: dict[str, tuple[str, ...]]` 與
  `_OPTIONAL_STAGE_KINDS: dict[str, tuple[str, ...]]`，取代 `_STAGED_KINDS`。
  Task 4 撰寫說明文字時沿用這兩個常數名，不再改動它們。

- [ ] **Step 1: 寫失敗測試** — 在 `tests/test_guide_tools.py` 的 `test_無效的_kind_回報三個合法值` 之前插入三支測試

```python
@pytest.mark.parametrize(
    ("kind", "present", "absent"),
    [
        ("change", ("## 影響範圍", "## 待確認（需求面）"), "## 調整內容"),
        (
            "feature",
            ("## 影響範圍與相依", "## 不在範圍內", "## 待確認（需求面）"),
            "## 需求內容",
        ),
    ],
)
async def test_評估階段回傳註記模板(
    settings, kind: str, present: tuple[str, ...], absent: str
):
    guide = await _guide(settings, {"kind": kind, "stage": "assess"})

    for marker in present:
        assert marker in guide, f"{kind} 的評估模板缺少章節標記 {marker}"
    # 註記模板不是概述骨架：概述側的必填章節不該出現，否則等於鼓勵覆寫概述。
    assert absent not in guide, f"{kind} 的評估模板不該含概述章節 {absent}"
    assert "add_issue_note" in guide


@pytest.mark.parametrize(
    ("kind", "present"),
    [("change", "## 調整內容"), ("feature", "## 需求內容")],
)
async def test_省略_stage_仍回傳概述骨架(settings, kind: str, present: str):
    # 相容性回歸：既有呼叫不帶 stage，行為不可改變。
    guide = await _guide(settings, {"kind": kind})

    assert present in guide
    assert "## 待確認（需求面）" not in guide


@pytest.mark.parametrize("kind", ["change", "feature"])
async def test_可選階段給不存在的階段時報錯(settings, kind: str):
    # 靜默忽略會讓模型以為隨便給個 stage 都行，下次就會拿 diagnose 去猜。
    with pytest.raises(Exception) as exc:
        await _guide(settings, {"kind": kind, "stage": "diagnose"})

    message = str(exc.value)
    assert kind in message
    assert "assess" in message
```

- [ ] **Step 2: 改寫已失效的既有測試**

`test_無階段的類型多帶_stage_時報錯` 的參數化清單是 `["change", "feature"]`，這兩個 kind 改後有合法階段，原斷言必然失敗。連同它的 `@pytest.mark.parametrize` 裝飾器整支替換為：

```python
async def test_無效的_kind_帶_stage_時仍以_kind_錯誤回報(settings):
    # kind 的檢查必須排在 stage 之前：先報「kind 不合法」才是可修正的訊息，
    # 否則模型會以為問題出在 stage，反覆換階段值重試同一個錯誤的 kind。
    with pytest.raises(Exception) as exc:
        await _guide(settings, {"kind": "improve", "stage": "assess"})

    message = str(exc.value)
    assert "improve" in message
    assert "bug" in message
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `uv run pytest tests/test_guide_tools.py -q`
Expected: FAIL — `test_評估階段回傳註記模板`（4 個參數組）與 `test_可選階段給不存在的階段時報錯`（2 個）因
`kind=change 沒有階段之分，請省略 stage` 而失敗。`test_省略_stage_仍回傳概述骨架` 此時就會通過（概述尚未精簡），這是預期的。

- [ ] **Step 4: `_GUIDE_FILES` 加入兩筆**

```python
_GUIDE_FILES: dict[tuple[str, str | None], tuple[str, str]] = {
    ("bug", "report"): ("redmine_mcp.guides.bug_report", "report.md"),
    ("bug", "diagnose"): ("redmine_mcp.guides.bug_report", "diagnose.md"),
    ("change", None): ("redmine_mcp.guides.change_request", "change.md"),
    ("change", "assess"): ("redmine_mcp.guides.change_request", "assess.md"),
    ("feature", None): ("redmine_mcp.guides.feature_request", "feature.md"),
    ("feature", "assess"): ("redmine_mcp.guides.feature_request", "assess.md"),
}
```

- [ ] **Step 5: 以兩張表取代 `_STAGED_KINDS`**

刪掉這一行與其上方兩行註解：

```python
#: 有階段之分的單別 → 該單別的合法階段。目前只有臭蟲單，但寫成表格
#: 而非寫死 "bug"，日後多一種分階段的單別時不必再改分支邏輯。
_STAGED_KINDS: dict[str, tuple[str, ...]] = {"bug": ("report", "diagnose")}
```

換成：

```python
#: 必須指定階段的單別 → 合法階段。缺 stage 就報錯：臭蟲單的兩個階段是給不同讀者的
#: 兩份文件，預設任一個都會讓另一半的人拿到錯的格式。
_REQUIRED_STAGE_KINDS: dict[str, tuple[str, ...]] = {"bug": ("report", "diagnose")}

#: 可選階段的單別 → 合法的後段階段。stage=None 回傳概述骨架（給需求方看的必填章節），
#: 給了階段才回傳註記模板（給手上有事實的人）。寫成兩張表而非一張加旗標，是因為
#: 兩者的驗證分支本來就不同，旗標只會把判斷推到分支裡再攤開一次。
_OPTIONAL_STAGE_KINDS: dict[str, tuple[str, ...]] = {
    "change": ("assess",),
    "feature": ("assess",),
}
```

- [ ] **Step 6: 改 `_resolve_guide` 的驗證邏輯**

把 `stages = _STAGED_KINDS.get(kind)` 開始、到 `raise ValueError` 結束的整個 `if/elif` 區塊（`kind not in _VALID_KINDS` 那段保持不動，仍排在最前面）換成：

```python
    required = _REQUIRED_STAGE_KINDS.get(kind)
    if required is not None:
        if stage not in required:
            valid = "、".join(required)
            got = "未提供" if stage is None else repr(stage)
            raise ValueError(
                f"kind={kind} 必須指定 stage（{valid}）；收到的是 {got}。"
                "只觀察到現象、沒讀過原始碼用 report；已讀過原始碼要寫根因用 diagnose。"
            )
    elif (optional := _OPTIONAL_STAGE_KINDS.get(kind)) is not None:
        if stage is not None and stage not in optional:
            valid = "、".join(optional)
            raise ValueError(
                f"kind={kind} 的 stage 只接受 {valid}，或整個省略；收到的是 {stage!r}。"
                "省略 stage 取得概述骨架；要寫影響範圍與待確認等評估內容時用 assess，"
                "那份模板填進 add_issue_note 的 notes。"
            )
    elif stage is not None:
        # 目前沒有單別落在這個分支（三個 kind 都有階段）。保留它是為了日後新增
        # 不分階段的單別時，多帶 stage 會當場報錯而不是被靜默忽略。
        raise ValueError(
            f"kind={kind} 沒有階段之分，請省略 stage；收到的是 {stage!r}。"
        )
```

- [ ] **Step 7: 更新 `_resolve_guide` 的 docstring**

```python
    """依單別與階段查出指南全文。

    參數:
        kind: 單別，bug／change／feature。
        stage: 撰寫階段。bug 必填（report／diagnose）；change 與 feature 可選，
            省略取得概述骨架、給 assess 取得評估註記模板。

    例外:
        ValueError: kind 不合法、必填階段的單別缺少或給錯 stage、可選階段的單別
            給了不存在的階段、或不分階段的單別多帶了 stage。訊息一律列出合法值，
            讓模型能自行修正而不是重試同一組參數。
    """
```

- [ ] **Step 8: 執行測試確認通過**

Run: `uv run pytest tests/test_guide_tools.py -q`
Expected: PASS，全檔通過。

- [ ] **Step 9: 全套驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: `pytest` 全綠、測試數 ≥ 555（基線 549 ＋ Task 1 的 2 ＋ 本 Task 淨增 ≥ 4）；`ruff`、`mypy` 無錯。

- [ ] **Step 10: Commit**

```bash
git add src/redmine_mcp/tools/guides.py tests/test_guide_tools.py
git commit -m "feat(redmine-mcp): stage 擴充為 change 與 feature 的可選評估階段"
```

---

### Task 3: 概述骨架精簡

**Files:**
- Modify: `src/redmine_mcp/guides/change_request/change.md`（八處）
- Modify: `src/redmine_mcp/guides/feature_request/feature.md`（十處）
- Test: `tests/test_guide_tools.py`

**Interfaces:**
- Consumes: Task 2 完成的 `stage="assess"` 路由——概述指南的指路句要引用得到它。
- Produces: `kind="change"`／`kind="feature"` 的回傳不再含 `## 影響範圍`、`## 影響範圍與相依`、
  `## 不在範圍內`、`## 待確認`，也不再含字串 `（選填）`。Task 4 的說明文字以此為前提。

- [ ] **Step 1: 寫失敗測試** — 在 `test_省略_stage_仍回傳概述骨架` 之後插入

```python
@pytest.mark.parametrize(
    ("kind", "removed"),
    [
        ("change", ("## 影響範圍", "## 待確認")),
        ("feature", ("## 影響範圍與相依", "## 不在範圍內", "## 待確認")),
    ],
)
async def test_概述骨架不含已移到註記的章節(
    settings, kind: str, removed: tuple[str, ...]
):
    # 這是本次變更的核心證據：概述只留需求方寫得出來的必填章節。
    # 斷言帶 "## " 前綴，才不會誤中內文裡提到章節名的散句。
    guide = await _guide(settings, {"kind": kind})

    for marker in removed:
        assert marker not in guide, f"{kind} 的概述骨架不該再有 {marker}"
    # 概述側已無選填章節；殘留的「（選填）」代表有章節或交叉引用沒清乾淨。
    assert "（選填）" not in guide
    # 指路句要留下，否則搬走的內容就沒有人知道該去哪裡寫。
    assert "assess" in guide
```

- [ ] **Step 2: 禁用字眼測試涵蓋新組合** — `test_指南不含_client_專屬字眼` 的參數化清單末尾加兩筆

```python
@pytest.mark.parametrize(
    "args",
    [
        {"kind": "bug", "stage": "report"},
        {"kind": "bug", "stage": "diagnose"},
        {"kind": "change"},
        {"kind": "feature"},
        {"kind": "change", "stage": "assess"},
        {"kind": "feature", "stage": "assess"},
    ],
)
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `uv run pytest tests/test_guide_tools.py -q`
Expected: FAIL — `test_概述骨架不含已移到註記的章節` 兩個參數組都失敗（章節還在、且找不到 `assess`）。
`test_指南不含_client_專屬字眼` 的兩筆新組合應直接通過（Task 1 已在 Step 6 確認過禁用字眼）。

- [ ] **Step 4: 改 `change.md`（八處）**

**4-1 骨架前言。** 原文：

```
前三章必填，標「選填」的沒有就整段拿掉，**不要為了填滿而猜**——猜錯的影響評估會讓人以為有人確認過了，比留白更糟。
```

改為：

```
三章全必填，沒有選填章節。**不要為了填滿而猜**——猜錯的內容會讓人以為有人確認過了，比留白更糟。

影響範圍與待確認事項不寫在這裡：那是查得出事實的人以 `get_issue_guide(kind="change", stage="assess")` 取得模板後，追加在註記裡的內容。
```

**4-2 骨架區塊。** 在 ` ````markdown ` 圍欄內，刪掉這兩章（含其間空行），只留前三章：

```
## 影響範圍（選填）

<會不會改變既有行為、既有資料要不要一併處理、有沒有其他功能跟著受影響>

## 待確認（選填）

<需要需求方拍板才能定案的部分>
```

**4-3 問答補齊的條目。** 原文：

```
- **選填章節不問**。對方沒主動提影響範圍就整段拿掉，那是 RD 查得出來的。
```

改為：

```
- **影響範圍與待確認不問**。那是查得出事實的人才寫得出來的，不寫進概述，也不要為此多問一輪。
```

**4-4 「不知道」的處置。** 原文：

```
- 對方答「不知道」就放進「待確認」，不要替他猜。
```

改為：

```
- 對方答「不知道」就照實寫「未確認」，不要替他猜；要需求方拍板的事留給評估註記處理。
```

**4-5 驗收標準的回歸確認條目。** 原文：

```
- **回歸確認**：原本就有的行為沒被弄壞，尤其是「影響範圍」點名的那些對象。
```

改為：

```
- **回歸確認**：原本就有的行為沒被弄壞——你知道有哪些連帶對象就一併點名。
```

**4-6 刪掉 `### 影響範圍（選填）` 整個子節**（從該行到下一個 `### ` 之前）。內容已於 Task 1 移入 `assess.md`。

**4-7 刪掉 `### 待確認（選填）` 整個子節**（從該行到 `## 語氣與用字` 之前）。內容已於 Task 1 移入 `assess.md`。

**4-8 自我檢查條目。** 原文：

```
- [ ] 若有寫影響範圍，既有資料怎麼處理有明確交代；沒有就整段拿掉
```

改為：

```
- [ ] 概述沒有夾帶查得出來才寫得出來的評估內容（影響範圍、待確認留給 assess 註記）
```

**4-9 送出段補指路。** 在「標題填 `subject`，上面那份骨架**整份**填 `description`……」那段之後，插入一段：

```
影響範圍與待確認事項不放這裡。查得出事實的人以 `get_issue_guide(kind="change", stage="assess")` 取得註記模板，內容填進 `add_issue_note` 的 `notes`，不要覆寫概述。
```

- [ ] **Step 5: 改 `feature.md`（十處）**

**5-1 開場句。** 原文：

```
做得到這件事的關鍵不是把需求寫長，而是把**驗收標準**與**不在範圍內**寫清楚。
```

改為：

```
做得到這件事的關鍵不是把需求寫長，而是把**驗收標準**寫清楚；範圍排除與相依由手上有事實的人追加在評估註記。
```

**5-2 骨架前言。** 原文：

```
前三章必填，標「選填」的沒有就整段拿掉，**不要為了填滿而猜**——猜出來的相依與排除事項會被當成需求方確認過的，比留白更糟。
```

改為：

```
三章全必填，沒有選填章節。**不要為了填滿而猜**——猜出來的相依與排除事項會被當成需求方確認過的，比留白更糟。

影響範圍與相依、不在範圍內、待確認事項不寫在這裡：那是查得出事實的人以 `get_issue_guide(kind="feature", stage="assess")` 取得模板後，追加在註記裡的內容。
```

**5-3 骨架區塊。** 在 ` ````markdown ` 圍欄內刪掉這三章（含其間空行），只留前三章：

```
## 影響範圍與相依（選填）

<會動到哪些既有功能、需要哪些前置條件或外部資源>

## 不在範圍內（選填）

<明確排除、這次不做的事>

## 待確認（選填）

<需要需求方拍板才能定案的部分>
```

**5-4 問答補齊的條目。** 原文：

```
- **選填章節不問**。對方沒主動提相依或排除事項就整段拿掉。
```

改為：

```
- **相依、範圍排除與待確認不問**。那是查得出事實的人才寫得出來的，不寫進概述，也不要為此多問一輪。
```

**5-5 「不知道」的處置。** 原文：

```
- 對方答「不知道」就放進「待確認」，不要替他猜。
```

改為：

```
- 對方答「不知道」就照實寫「未確認」，不要替他猜；要需求方拍板的事留給評估註記處理。
```

**5-6 需求內容的留白提醒。** 原文：

```
最後兩項最常被漏掉，也最常在開發到一半才回頭補問。**不確定的地方寫「未確認」並放進「待確認」，不要留白**——留白會被當成沒有限制。
```

改為：

```
最後兩項最常被漏掉，也最常在開發到一半才回頭補問。**不確定的地方就寫「未確認」，不要留白**——留白會被當成沒有限制。
```

**5-7 刪掉 `### 影響範圍與相依（選填）` 整個子節**（從該行到下一個 `### ` 之前）。

**5-8 刪掉 `### 不在範圍內（選填）` 整個子節**（從該行到下一個 `### ` 之前）。

**5-9 刪掉 `### 待確認（選填）` 整個子節**（從該行到 `## 語氣與用字` 之前）。

**5-10 自我檢查兩條。** 原文：

```
- [ ] 若有討論過但決定延後的事，已寫進「不在範圍內」，避免驗收時範圍擴張
```

改為：

```
- [ ] 概述沒有夾帶查得出來才寫得出來的評估內容（相依、不在範圍內、待確認留給 assess 註記）
```

原文：

```
- [ ] 不確定的地方標了「未確認」或放進待確認，沒有留白
```

改為：

```
- [ ] 不確定的地方標了「未確認」，沒有留白
```

**5-11 送出段補指路。** 在「標題填 `subject`……」那段之後插入：

```
影響範圍與相依、不在範圍內、待確認事項不放這裡。查得出事實的人以 `get_issue_guide(kind="feature", stage="assess")` 取得註記模板，內容填進 `add_issue_note` 的 `notes`，不要覆寫概述。
```

- [ ] **Step 6: 執行測試確認通過**

Run: `uv run pytest tests/test_guide_tools.py -q`
Expected: PASS，全檔通過。若「（選填）」的斷言失敗，用
`grep -n "（選填）" src/redmine_mcp/guides/change_request/change.md src/redmine_mcp/guides/feature_request/feature.md`
找出漏清的位置。

- [ ] **Step 7: 全套驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全綠。

- [ ] **Step 8: Commit**

```bash
git add src/redmine_mcp/guides/change_request/change.md \
        src/redmine_mcp/guides/feature_request/feature.md \
        tests/test_guide_tools.py
git commit -m "docs(redmine-mcp): 概述只留必填章節，評估內容改走 assess 註記"
```

---

### Task 4: 落點通則寫進所有說明文字

**Files:**
- Modify: `src/redmine_mcp/server.py`（`_BASE_INSTRUCTIONS` 三處）
- Modify: `src/redmine_mcp/tools/guides.py`（工具 `description`、`_STAGE_DESCRIPTION`、`get_issue_guide` docstring）
- Modify: `src/redmine_mcp/tools/issues.py`（四處）
- Test: `tests/test_guide_tools.py`

**Interfaces:**
- Consumes: Task 2 的階段值 `assess`、Task 3 精簡後的概述骨架。
- Produces: 指示詞與工具說明含字串 `assess`，且不再含 `bug+diagnose 是例外`。
  這是模型唯一看得到的規則來源，沒有後續 Task 依賴其內部措辭。

- [ ] **Step 1: 寫失敗測試** — 在 `test_server_指示詞提示先取得單別格式` 之後插入

```python
async def test_指示詞說明落點通則(settings):
    # 落點規則是本設計的核心機制：概述給非 IT 讀者，後段一律走註記。
    # 只活在某次 commit 的措辭裡不算，必須被斷言釘住。
    sites = _single_site(settings)
    instructions = build_instructions(sites)
    await sites.aclose()

    assert "assess" in instructions
    # 舊措辭把 diagnose 講成「例外」，改後是通則，不該再有例外的說法。
    assert "是例外" not in instructions


async def test_工具說明含評估階段(settings):
    sites = _single_site(settings)
    mcp = create_server(sites)
    try:
        tools = await mcp.list_tools()
    finally:
        await sites.aclose()

    guide_tool = next(tool for tool in tools if tool.name == "get_issue_guide")
    assert "assess" in guide_tool.description
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_guide_tools.py -q`
Expected: FAIL — 兩支新測試都失敗（指示詞與工具說明尚未提到 `assess`，且仍含「是例外」）。

- [ ] **Step 3: 改 `server.py` 的落點段**

原文：

```python
    "概述欄位（description）只放給非 IT 人員看的骨架——bug+report、change、feature——"
    "整份填入，不可拆散到自訂欄位；bug+diagnose 是例外，"
    "診斷內容一律填入 add_issue_note 的 notes，禁止寫進 description。"
    "標題一律另走 subject。\n"
```

改為：

```python
    "落點通則：有 stage 後段的一律填 add_issue_note 的 notes，沒有 stage 的填 description。"
    "概述欄位（description）只放給非 IT 人員看的骨架——bug+report、change、feature——"
    "整份填入，不可拆散到自訂欄位。bug+diagnose 的診斷內容、change+assess 與 "
    "feature+assess 的評估內容（影響範圍、不在範圍內、待確認）一律走 notes，"
    "禁止寫進 description。標題一律另走 subject。\n"
```

- [ ] **Step 4: 改 `server.py` 的選填章節句**

原文：

```python
    "選填章節不問，沒有就整段拿掉。\n"
```

改為：

```python
    "選填章節不問，沒有就整段拿掉；影響範圍、不在範圍內、待確認這類查得出來才寫得出來的"
    "內容不寫進概述，由手上有事實的人以 stage=assess 取得註記模板後追加。\n"
```

- [ ] **Step 5: 改 `server.py` 的階段說明句**

原文：

```python
    "kind=bug 需再指定階段：只觀察到現象、沒讀過原始碼用 stage=report；"
    "已讀過原始碼要寫根因用 stage=diagnose。"
    "change 與 feature 沒有階段之分，不可帶 stage。\n"
```

改為：

```python
    "kind=bug 需再指定階段：只觀察到現象、沒讀過原始碼用 stage=report；"
    "已讀過原始碼要寫根因用 stage=diagnose。"
    "change 與 feature 的 stage 可省略：省略取得概述骨架，"
    "要寫影響範圍與待確認等評估內容時用 stage=assess。\n"
```

- [ ] **Step 6: 改 `guides.py` 的 `_STAGE_DESCRIPTION`**

原文：

```python
_STAGE_DESCRIPTION = (
    "撰寫階段，只有 kind=bug 需要，其餘單別必須省略。"
    "report：你只觀察到現象、沒有讀過相關原始碼（PM／客服／測試回報）。"
    "diagnose：你已實際讀過原始碼、要寫出根因與建議修正。"
    "判斷依據是你手上有沒有程式碼層級的事實，不是你的角色。"
)
```

改為：

```python
_STAGE_DESCRIPTION = (
    "撰寫階段。kind=bug 必填：report 為你只觀察到現象、沒有讀過相關原始碼"
    "（PM／客服／測試回報）；diagnose 為你已實際讀過原始碼、要寫出根因與建議修正。"
    "kind=change 與 kind=feature 可省略：省略取得概述骨架（給需求方看的必填章節）；"
    "assess 取得評估註記模板，寫影響範圍、不在範圍內與待確認等"
    "查得出來才寫得出來的內容。"
    "判斷依據一律是你手上有沒有事實，不是你的角色。"
)
```

- [ ] **Step 7: 改 `guides.py` 工具 `description` 的落點段**

原文：

```python
            "落點：概述欄位（description）只放給非 IT 人員看的骨架，"
            "亦即 bug+report、change、feature 三者，整份填入 "
            "create_issue／update_issue 的 description，不可拆散到自訂欄位；"
            "bug+diagnose 是例外，診斷內容一律填入 add_issue_note 的 notes，"
            "禁止寫進 description。標題一律另走 subject。"
```

改為：

```python
            "落點通則：有 stage 後段的一律填 add_issue_note 的 notes，"
            "沒有 stage 的填 description。概述欄位（description）只放給非 IT 人員看的"
            "骨架，亦即 bug+report、change、feature 三者，整份填入 "
            "create_issue／update_issue 的 description，不可拆散到自訂欄位；"
            "bug+diagnose 的診斷內容、change+assess 與 feature+assess 的評估內容"
            "一律填入 notes，禁止寫進 description。標題一律另走 subject。"
```

- [ ] **Step 8: 改 `guides.py` 工具 `description` 的階段說明段**

原文：

```python
            "kind=change 取得調整單格式（既有功能的異動，需寫調整前後對照與影響範圍）；"
            "kind=feature 取得新需求單格式（新增的能力，需寫驗收標準與不在範圍內）。"
            "change 與 feature 沒有階段之分，不可帶 stage。"
```

改為：

```python
            "kind=change 取得調整單格式（既有功能的異動，需寫調整前後對照）；"
            "kind=feature 取得新需求單格式（新增的能力，需寫驗收標準）。"
            "change 與 feature 的 stage 可省略：省略取得概述骨架；"
            "stage=assess 取得評估註記模板（影響範圍、不在範圍內、待確認），"
            "那份內容填進 add_issue_note 的 notes。"
```

- [ ] **Step 9: 改 `guides.py` 的 `get_issue_guide` docstring**

原文：

```python
        """取得指定單別（必要時含階段）的格式指南全文。

        參數:
            kind: 單別，bug（臭蟲）／change（調整）／feature（新需求）。
            stage: 臭蟲單的撰寫階段，report（只有現象）或 diagnose（已讀過原始碼）；
                其餘單別須省略。
        """
```

改為：

```python
        """取得指定單別（必要時含階段）的格式指南全文。

        參數:
            kind: 單別，bug（臭蟲）／change（調整）／feature（新需求）。
            stage: 撰寫階段。bug 必填，report（只有現象）或 diagnose（已讀過原始碼）；
                change 與 feature 可省略，省略取得概述骨架、assess 取得評估註記模板。
        """
```

- [ ] **Step 10: 改 `issues.py` 的 `create_issue` 工具說明**

原文：

```python
            "kind=bug 需再指定階段：只觀察到現象、沒讀過原始碼用 stage=report；"
            "已讀過原始碼要寫根因用 stage=diagnose。"
```

改為：

```python
            "kind=bug 需再指定階段：只觀察到現象、沒讀過原始碼用 stage=report；"
            "已讀過原始碼要寫根因用 stage=diagnose。"
            "change 與 feature 省略 stage 取得概述骨架；影響範圍、不在範圍內與待確認"
            "屬 stage=assess 的評估內容，建單後以 add_issue_note 追加，不寫進 description。"
```

- [ ] **Step 11: 改 `issues.py` 的 `create_issue.description` 欄位說明**

原文：

```python
                    "本欄位是給非 IT 人員看的；臭蟲單的診斷內容"
                    "（stage=diagnose）不寫這裡，改用 add_issue_note。"
```

改為：

```python
                    "本欄位是給非 IT 人員看的；有 stage 後段的內容都不寫這裡——"
                    "臭蟲單的診斷（stage=diagnose）、調整單與新需求單的評估"
                    "（stage=assess）一律改用 add_issue_note。"
```

- [ ] **Step 12: 改 `issues.py` 的 `update_issue.description` 欄位說明**

原文：

```python
                    "內文，對應 Redmine 畫面上的「概述」欄位，未提供則不異動。"
                    "改寫內文時請依 get_issue_guide 的骨架整份填入這個欄位。"
```

改為：

```python
                    "內文，對應 Redmine 畫面上的「概述」欄位，未提供則不異動。"
                    "改寫內文時請依 get_issue_guide 的概述骨架（不帶 stage 後段的那份）"
                    "整份填入這個欄位。診斷與評估內容不改寫進這裡，改用 add_issue_note。"
```

- [ ] **Step 13: 改 `issues.py` 的 `add_issue_note.notes` 欄位說明**

原文：

```python
                    "註解內容，不可空白。臭蟲單的診斷內容"
                    "（get_issue_guide 帶 kind=bug、stage=diagnose）寫在這裡，"
                    "不要寫進概述（description）。"
```

改為：

```python
                    "註解內容，不可空白。有 stage 後段的內容都寫在這裡——"
                    "臭蟲單的診斷（get_issue_guide 帶 kind=bug、stage=diagnose）、"
                    "調整單與新需求單的評估（kind=change 或 feature、stage=assess）；"
                    "一律不要寫進概述（description）。"
```

- [ ] **Step 14: 執行測試確認通過**

Run: `uv run pytest tests/test_guide_tools.py tests/test_server.py -q`
Expected: PASS。`test_每個工具都有中文說明與可用_schema` 與工具數 17 的斷言都不受影響。

- [ ] **Step 15: 確認舊措辭已無殘留**

Run: `grep -rn "沒有階段之分，不可帶\|是例外" src/redmine_mcp/`
Expected: 只剩 `guides.py` 的 `_resolve_guide` 內那句錯誤訊息 `f"kind={kind} 沒有階段之分，請省略 stage；..."`（措辭不同，屬保留的防禦分支）。不得有其他命中。

- [ ] **Step 16: 全套驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全綠。

- [ ] **Step 17: Commit**

```bash
git add src/redmine_mcp/server.py src/redmine_mcp/tools/guides.py \
        src/redmine_mcp/tools/issues.py tests/test_guide_tools.py
git commit -m "feat(redmine-mcp): 落點規則改為通則，說明文字納入 assess 階段"
```

---

### Task 5: 對外文件與版號

**Files:**
- Modify: `README.md`（指南段四處）
- Modify: `SETUP.md`（工具清單一處）
- Modify: `pyproject.toml`（`version`）

**Interfaces:**
- Consumes: Task 2–4 的最終行為。
- Produces: 無程式介面。發佈 tag `redmine-mcp-v0.6.0` **不在本計畫範圍**。

- [ ] **Step 1: 改 `README.md` 的 change／feature 說明兩行**

原文：

```
- `kind="change"` — 調整單（tracker 調整）。既有功能要改成別的樣子：設定值、規則、文案、版面的異動。需寫調整前後對照與影響範圍。
- `kind="feature"` — 新需求單（tracker 新需求）。系統目前沒有、要新增的能力。需寫驗收標準與不在範圍內。
```

改為：

```
- `kind="change"` — 調整單（tracker 調整）。既有功能要改成別的樣子：設定值、規則、文案、版面的異動。概述需寫調整前後對照。
- `kind="feature"` — 新需求單（tracker 新需求）。系統目前沒有、要新增的能力。概述需寫驗收標準。
```

- [ ] **Step 2: 改 `README.md` 的階段小節標題與清單**

原文：

```
臭蟲單再分兩個階段：

- `stage="report"` — 回報階段的簡易版。給只觀察到現象、沒讀過原始碼的人（PM、客服、測試），只需要問題描述、重現步驟、影響與佐證。
- `stage="diagnose"` — 診斷階段的完整版。給已讀過原始碼的人，含根因、建議修正與待確認事項。
```

改為：

```
臭蟲單分兩個階段，`stage` 必填：

- `stage="report"` — 回報階段的簡易版。給只觀察到現象、沒讀過原始碼的人（PM、客服、測試），只需要問題描述、重現步驟、影響與佐證。
- `stage="diagnose"` — 診斷階段的完整版。給已讀過原始碼的人，含根因、建議修正與待確認事項。

調整單與新需求單的 `stage` 可省略：

- 省略 — 概述骨架，三章全必填（現況／要什麼／驗收標準），給需求方看。
- `stage="assess"` — 評估註記模板，寫影響範圍、不在範圍內與待確認等查得出來才寫得出來的內容。
```

- [ ] **Step 3: 改 `README.md` 的收束段**

原文：

```
兩者是同一張單的前後段：回報者先寫現象與影響，接手的 RD 診斷後往下追加根因與建議修正。`stage` 在 `kind="bug"` 時必填，判準是**手上有沒有程式碼層級的事實**，不是職稱；`change` 與 `feature` 沒有階段之分，不可帶 `stage`。
```

改為：

```
三種單別的落點是同一條通則：**有 `stage` 後段的一律填 `add_issue_note` 的 `notes`，沒有 `stage` 的填 `description`。** 概述留給非 IT 讀者，後段留給手上有事實的人往下追加，判準一律是**手上有沒有可寫的事實**，不是職稱。
```

- [ ] **Step 4: 改 `SETUP.md:287` 的工具清單**

原文：

```
- **指南**：`get_issue_guide(kind, stage=None)`（各單別格式，內建於 server，不需另裝 skill；`bug` 臭蟲單、`change` 調整單、`feature` 新需求單。`kind="bug"` 需再指定 `stage`：`report` 為回報階段簡易版、`diagnose` 為診斷階段完整版）
```

改為：

```
- **指南**：`get_issue_guide(kind, stage=None)`（各單別格式，內建於 server，不需另裝 skill；`bug` 臭蟲單、`change` 調整單、`feature` 新需求單。`kind="bug"` 需再指定 `stage`：`report` 為回報階段簡易版、`diagnose` 為診斷階段完整版；`change` 與 `feature` 的 `stage` 可省略，省略取得概述骨架、`assess` 取得評估註記模板）
```

- [ ] **Step 5: 升版號**

`pyproject.toml` 第 3 行，`version = "0.5.0"` → `version = "0.6.0"`。

- [ ] **Step 6: 確認文件無殘留假句**

Run: `grep -rn "沒有階段之分" README.md SETUP.md`
Expected: 無輸出。

- [ ] **Step 7: 全套驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全綠，測試數與 Task 4 結束時相同。

- [ ] **Step 8: Commit**

```bash
git add README.md SETUP.md pyproject.toml
git commit -m "docs(redmine-mcp): 文件補上 assess 階段與落點通則，升版至 0.6.0"
```

---

## 完成後的驗收

全部 Task 完成時，以下都要成立：

- `uv run pytest -q` 全綠，工具數維持 17。
- `get_issue_guide` 六種組合各回傳對應文件：`bug`+`report`／`bug`+`diagnose`／`change`／`change`+`assess`／`feature`／`feature`+`assess`。
- `kind="change"`（無 stage）的回傳不含 `## 影響範圍`、`## 待確認`、`（選填）`。
- `kind="feature"`（無 stage）的回傳不含 `## 影響範圍與相依`、`## 不在範圍內`、`## 待確認`、`（選填）`。
- `grep -rn "是例外" src/redmine_mcp/` 無命中。
- `guides/bug_report/` 兩份檔案的 `git diff` 為空。
