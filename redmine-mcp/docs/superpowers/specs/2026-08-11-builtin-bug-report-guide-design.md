# 臭蟲單格式指南內建於 MCP server — 設計

日期：2026-08-11

## 問題

撰寫 Redmine 臭蟲單的公司標準格式目前只存在於一份 Claude Code skill
（`~/.claude/skills/redmine-bug-report/`），有兩個問題：

1. **只在本機生效。** 沒裝那份 skill 的人（或換一台機器、換一個 MCP client）建出來的臭蟲單
   不會套到這個格式。格式的價值來自一致性，只有一個人遵守等於沒有格式。
2. **與 server 脫鉤。** 建單能力由 redmine-mcp 提供，格式規範卻在另一個地方，
   兩者版本各自漂移，沒有任何機制保證裝了 server 的人拿得到現行格式。

目標：**讓模型在建臭蟲單前一定讀到這份格式，且不依賴使用者本機裝了什麼 skill。**

## 前提事實

MCP 協定沒有 skill 這個 primitive。SDK 2.0 的 `MCPServer` 只提供 tools、prompts、
resources 三種能力（`add_tool` / `add_prompt` / `add_resource`）。官方文件
（<https://modelcontextprotocol.io/docs/2026-07-28/develop/build-with-agent-skills>）
說明 skill 是 `SKILL.md` 加 `references/` 的檔案，安裝到 agent 各自的 skills 目錄，
散佈方式是 Claude Code plugin，而非由 MCP server 提供。

因此「內建 skill」在實作上必須落到上述三種 primitive 之一。

## 已決定的取捨

| 決策 | 選擇 | 理由 |
| --- | --- | --- |
| 承載 primitive | **工具**（`get_bug_report_guide`） | 每個 MCP client 都支援工具；prompt 需使用者手動喚起，達不到「一定讀到」；resource 各家 client 支援程度不一，模型也較少主動讀 |
| 強制度 | **指路**，不驗證、不阻擋 | 臭蟲單品質難以機器判定，硬驗證的誤擋成本高於收益 |
| 指南本文形式 | **Markdown 隨套件發佈**，工具讀檔回傳 | 這份東西的本質是會反覆微調的文案；當資料管理才能長期維持，改文案是純 Markdown diff |
| `references/example-<單號>.md`（範例單全文檔） | **不進 repo** | 內含實際專案欄位名、單號、程式碼路徑，repo 為公開 GitHub |
| 本機 skill | **完全移除** | 避免兩份文案並存後分歧 |

被否決的方案：

- **文案寫成 Python 模組常數。** 6KB 中文散文塞在 `.py` 裡，diff 難讀、行寬與全形標點
  都要另外處理，每次潤稿都動到程式碼。
- **分段回傳（`section=` 參數）。** 指南各章節互相依賴（骨架配著「各章節怎麼寫」才看得懂），
  拆開反而容易只讀一半就下筆，而 6KB 一次給並不貴。屬過度設計。
- **軟活門／硬驗證。** 見上表「強制度」。
- **依 tracker 判斷是否提示。** tracker id 在各站台不同，要先呼叫 `list_trackers`
  才知道哪個是臭蟲；為了措辭精準多打一次 API 不值得。

## 架構

新增兩處、改四處。

```
src/redmine_mcp/
  guides/
    __init__.py            # 空檔，讓 guides 成為套件，package data 才進得了 wheel
    bug_report.md          # 臭蟲單格式指南全文，唯一真相
  tools/
    guides.py              # 新模組：註冊 get_bug_report_guide
```

`guides.py` 的唯一職責是把 `bug_report.md` 讀出來回傳：

- 讀檔用 `importlib.resources.files("redmine_mcp.guides") / "bug_report.md"`，
  不用 `__file__` 相對路徑 — 前者在 wheel、zipapp、editable install 三種安裝形態下都成立。
- 文字在模組載入時讀一次存成模組層級常數，之後每次呼叫直接回傳。檔案在 server
  生命週期內不會變，沒有理由重複 I/O。
- `register()` 簽章只收 `mcp`，**不收 `SiteRegistry`** — 它是純本機文件，不連任何站台。
  這使它與 `metadata.register(mcp, sites)` 形狀不同；強行塞一個用不到的參數只為了長得一樣
  是假的一致性。

`server.py` 的 `create_server()` 加一行 `guides.register(mcp)`。

改動的檔案：

- `src/redmine_mcp/server.py` — 註冊工具、`_BASE_INSTRUCTIONS` 加指路句
- `src/redmine_mcp/tools/issues.py` — `create_issue` 的 description 加指路句
- `pyproject.toml` — wheel 納入 `*.md` package data（sdist 的 `include` 已含 `src/`，不必改）
- `README.md`、`SETUP.md` — 工具清單加一列，README 另加一節說明格式指南已內建

## 工具介面

```python
@mcp.tool(
    annotations=READ_ONLY,
    description=(
        "取得撰寫 Redmine 臭蟲單（bug ticket）的公司標準格式指南，"
        "包含標題句型、章節骨架、重現步驟與影響評估的寫法，以及交件前的自我檢查清單。"
        "要建立或撰寫臭蟲單時必須先呼叫本工具並依其格式產出內容。"
        "本工具只讀取本機內建文件，不會連線任何站台。"
    ),
)
async def get_bug_report_guide() -> dict[str, str]:
```

- 無參數；回傳 `{"guide": "<Markdown 全文>"}`。用 dict 而非裸字串，與其他工具回傳形狀一致。
- `READ_ONLY` 標註正確：無副作用、可重複呼叫。
- 末句「只讀取本機內建文件，不會連線任何站台」沿用 `list_sites` 已有的措辭，
  同一個 repo 裡相同性質的事講同一句話。

### 指路句

同一句話放兩處，措辭一致：

> 要建立或撰寫臭蟲單（bug）時，必須先呼叫 get_bug_report_guide 取得公司標準格式，
> 再依格式撰寫內容。

- `server.py` 的 `_BASE_INSTRUCTIONS` 末尾（`build_instructions()` 會把它與站台說明串起來）
- `create_issue` 的 description，接在既有的「請先確認專案與欄位正確」之後

兩處都放是有意的重複：instructions 在連線時給一次，對話長了會被稀釋；工具說明是模型
真正要按下建單那一刻讀的東西，離決策點最近。

## 文案改寫

`bug_report.md` 內容取自現有的 `SKILL.md`，四處必須改，因為讀者從「Claude Code 的
skill 系統」變成「任何 MCP client 的模型」：

1. **移除 YAML frontmatter。** `name` / `description` 是 skill 系統的觸發機制，
   MCP 這邊由工具 description 承擔。改為一級標題 `# Redmine 臭蟲單文案格式` 起頭。
2. **「送出」節重寫。** 原文的「以 `mcp__redmine__create_issue` 建單（工具 schema 需先用
   ToolSearch 載入）」是 Claude Code 特有措辭，改為「以本 server 的 `create_issue` 工具建單，
   tracker 選『臭蟲』，專案取當前 repo 對應的專案」。後半「若使用者只是要文案就不要自作主張
   送出」保留 — 該規則與 client 無關且重要。
3. **「完整範例」整節刪除。** 該節指向不進 repo 的範例單全文檔（`references/example-<單號>.md`），
   沒有檔案該節就不成立。不需補寫替代內容：該範例要教的事（多問題依嚴重度排序並標註主要問題、
   跨專案參考須註明差異）原文的【問題N】與【參考】兩節已經寫到。
4. **去掉 Read 工具名。** 「每個檔案路徑與行號都經過 Read 確認」、「並實際 Read 過該段程式碼」
   → 「實際讀過原始碼確認」。工具名在別的 client 叫別的名字。

5. **範例中的內部專案細節去識別化。** 本文的示範例子帶了實際專案的欄位名與值域、
   檔案路徑、資料表名與單號。套件會發佈到公開 GitHub 與 PyPI，這些要換成同構造的
   虛構例子：欄位改 `cProgress`（申辦進度）/ `cStepDone`、服務類別改
   `RecordService` / `EditRecord`、資料表改 `TblRecordDetail`、單號改
   `A 專案 #12341` / `#12309`，領域語彙由原專案的業務用語改為中性的「表單填寫」。

   **只抽換名稱，不抽換具體性。** 值域仍逐項列出、行號仍具體、重現步驟仍帶數值 ——
   這份指南好用的原因正是它用真實形狀的例子示範，改成「欄位A／狀態B」會讓
   「兩個概念被混為一談」那節失去說服力。

其餘一字不動：標題句型、章節骨架、各章節寫法、語氣用字、自我檢查清單。

## 測試

新增 `tests/test_guide_tools.py`。測試函式名沿用專案的中文命名習慣，透過 `conftest.py`
的 `call_tool` 走真正的 MCP server 呼叫路徑（與 `test_metadata_tools.py` 同款），
不直接呼叫 Python 函式 — 要驗的是「工具註冊起來且叫得動」。

1. `test_取得臭蟲單格式指南回傳完整內容` — 斷言回傳有 `guide` 欄位，且含關鍵章節標記
   `【問題摘要】`、`【建議修正】`、`【參考】` 與「自我檢查」。這些是格式骨架，缺了代表檔案
   被截斷或讀錯檔。
2. `test_指南不含_client_專屬字眼` — 斷言全文不含 `mcp__redmine__`、`ToolSearch`、
   `references/example`。把文案改寫的決定釘成可執行的規則，否則日後有人從本機 skill
   複製回來就悄悄退化。
3. `test_指南工具不需要站台設定` — 確認它真的沒依賴 `SiteRegistry`。
4. `test_server_指示詞提示先取得臭蟲單格式` — 斷言 `build_instructions()` 輸出含
   `get_bug_report_guide`。指路句是本設計的核心機制，不能只存在於某次 commit 的措辭裡。

`tests/test_packaging.py` 補一條 `test_指南文件隨套件安裝` — 用 `importlib.resources`
確認 `bug_report.md` 讀得到且非空。打包漏 package data 是本方案唯一的靜默失敗模式：
程式碼全對、editable 模式下測試全綠，裝成 wheel 後才炸。CI 的 build job 已會在乾淨環境
安裝 wheel 並驗證，這條測試在那裡真正發揮作用。

`create_issue` 的說明措辭**不寫測試** — 那是文案，斷言字面值只會讓每次潤稿都要改測試。
指路機制由第 4 條守住 instructions 一處即可。

## 錯誤處理

`bug_report.md` 讀不到只有一種原因：打包漏了 package data。這種情況下模組載入即失敗，
server 起不來，而非在使用者呼叫工具時才拋錯 — 對這類「安裝就壞了」的問題，
啟動時失敗比執行時失敗更早暴露，也更好診斷。不加後備空字串或 try/except：
靜默回傳空指南比明確失敗糟得多。

工具本身沒有其他失敗路徑：無參數、無網路、無檔案系統寫入。

## 前置工作：收斂版控結構

`redmine-mcp/` 目前有自己的 `.git`，是原始開發 repo（無遠端，3 個本機分支
`main`／`feat/config-toml`／`feat/multi-site`，8 筆以上細緻歷史）；外層 `MCP` repo
只用兩筆 commit 把成品搬進來。兩個 repo 追蹤同一批檔案，本次要改的檔案全在
`redmine-mcp/` 底下，每次 commit 都得決定提交到哪邊。外層 workflow 用
`paths: redmine-mcp/**` 過濾觸發範圍，是明確的 monorepo 設計意圖，與「redmine-mcp
是獨立 repo」相矛盾。

決議：**保留歷史後拆掉巢狀 repo，收斂為單一 monorepo。** 在動任何程式碼之前執行：

1. 在巢狀 repo 提交現有雜項（`ci.yml` 的刪除、兩份 08-05 docs），讓歷史不留未提交的東西。
2. 從外層 repo 執行 `git fetch ./redmine-mcp "refs/heads/*:refs/nested/*"`，
   把三個分支連同全部 commit objects 保存到外層的 `refs/nested/*`。
   隨時 `git log refs/nested/feat/multi-site` 翻得到，也能 push 到 GitHub 當備份；
   這些 ref 不在 `refs/heads/`，不污染分支清單。
3. 以 `git diff` 比對 `refs/nested/*` 的 tip 與外層 HEAD 的 `redmine-mcp/` 內容，
   確認只差 `ci.yml` 與 docs、無其他遺漏，**確認後**才刪除 `redmine-mcp/.git`。
   刪 `.git` 不可逆，必須先證明歷史真的搬過去了。
4. 從外層 `.gitignore` 抽掉 `redmine-mcp/docs/` 一行，並把 08-05 那兩份 docs 加入版控。
   `downloads/` 保留忽略。

本次所有改動提交到外層 `MCP` repo 的 `develop` 分支。

## 收尾

移除本機 `~/.claude/skills/redmine-bug-report/`（含範例單全文檔 `references/example-<單號>.md`），
**在新工具實測可用之後**才執行 — 先確認替代品能用，再拆掉現有的東西。
範例單全文檔刪除後沒有副本，若要留檔須由使用者自行搬到 repo 外的位置；
本設計不將它複製到 repo 或其他地方。

## 不在範圍內

- 不改 `create_issue` 的行為或驗證邏輯
- 不新增其他格式指南（功能單、需求單等），本次只做臭蟲單
- 不把 redmine-mcp 拆成 submodule 或另建遠端 repo
- 不重寫外層 repo 的既有歷史
