# create_issue 的預計完成日改為必填 — 設計

日期：2026-08-12

## 問題

`create_issue` 的 `due_date`（預計完成日）目前是選填。實務上單子沒有期限就排不進工作，
也無法判斷是否延誤；靠人記得填不可靠，尤其呼叫端是模型時，schema 沒標必填它就會省略。

目標：**新建 issue 一律要有預計完成日，且日期格式在送出前就確認正確。**

## 已決定的取捨

| 決策 | 選擇 | 理由 |
| --- | --- | --- |
| 適用範圍 | **所有新建 issue**，不分 tracker | 依 tracker 區分需先呼叫 `list_trackers` 才知道哪個是哪個，每次建單多一次 API，不值得 |
| 必填的實作層級 | **schema 必填**（型別 `str`、無預設值） | 模型看 schema 就知道要填，工具根本不會在缺欄位時被執行；選填加執行期檢查則要撞一次錯誤才學到 |
| 格式驗證 | **本地先驗**，不合格不送出 | 模型常寫成 `2026/08/12` 或 `8月12日`；本地擋下省一次往返，錯誤訊息也講得比 Redmine 清楚 |
| 驗證的放置位置 | `_build_issue_payload`（`create_issue` 與 `update_issue` 共用） | 一份程式碼兩處生效 |
| `update_issue` 的 `due_date` | **維持選填** | 它只送有提供的欄位；強制必填會導致每次改別的欄位都要重填期限 |
| `start_date` | **一併驗格式** | 同一個 helper 的第二個呼叫點；不驗會讓同一個工具有兩套標準 |
| 過去的日期 | **不擋** | 補單常需回填；使用者未要求 |

被否決的方案：

- **保持選填、函式內檢查空值。** 改動更小，但 schema 上仍是選填，模型會以為可省略，
  得撞一次錯誤才知道。同一件事讓模型多繞一圈，沒換到任何好處。
- **只在工具說明文字要求。** 等於沒有強制。

## 介面變更

`create_issue` 的簽章把 `due_date` 移到第三位——Python 要求無預設值的參數排在有預設值者之前，
因此它必須在 `project_id`、`subject` 之後、其餘選填參數之前：

```python
due_date: Annotated[str, Field(description="預計完成日，格式 YYYY-MM-DD（必填）。")],
```

型別由 `str | None` 改為 `str`，MCP 產出的 input schema 就會把它列進 `required`。

這是工具契約的 breaking change：任何漏帶 `due_date` 的呼叫都會失敗。那正是目的。

## 格式驗證

模組層級的 helper，由 `_build_issue_payload` 對 `due_date` 與 `start_date` 各呼叫一次：

```python
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(value: str | None, field: str) -> None:
    """檢查日期字串是否為 Redmine 接受的 YYYY-MM-DD；None 視為未提供，不檢查。

    參數:
        value: 待檢查的日期字串。
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

### 為什麼是兩道檢查

- **只用 `date.fromisoformat` 不夠**：Python 3.11 起它放寬到接受多種 ISO 形式，
  `"20260812"` 這種無連字號的寫法照收，但 Redmine 要的是帶連字號的形式。
  只用它會放行 Redmine 會拒絕的值。
- **只用正規式也不夠**：`"2026-02-31"` 形狀正確但日期不存在，正規式看不出來。

兩道各補對方的漏。錯誤訊息刻意分成「格式不符」與「日期不存在」兩種，因為呼叫端要採取的
行動不同：前者改寫格式，後者改日期本身。

## 連帶影響

**既有測試有 11 處呼叫 `create_issue`**（`tests/test_issue_write_tools.py` 8 處、
`tests/test_upload_tools.py` 3 處）沒帶 `due_date`，schema 會擋下，全部要補上合法日期。
`tests/test_server.py` 那一處只是工具名清單，不受影響。

**兩處文件會變成錯的：**

- `README.md` 的「這是提示而非強制，`create_issue` 的行為與驗證邏輯完全不變」——該句原意是
  「格式指南不強制」，加了必填與驗證後不再成立。要改成只指涉指南本身，不宣稱整個工具沒有驗證。
- `SETUP.md` 的操作範例 `create_issue(project_id="mcp-test", subject="...", uploads=[...])`
  會變成無效呼叫，要補上 `due_date`。

## 測試

新增於 `tests/test_issue_write_tools.py`，沿用該檔既有的 `_call` 輔助與中文命名：

1. `test_create_issue_未給預計完成日被拒絕` — 不帶 `due_date`，預期失敗。守住 schema 必填。
2. `test_create_issue_預計完成日格式錯誤被拒絕` — 參數化真實會出現的錯法：`"2026/08/12"`、
   `"20260812"`、`"2026-8-12"`、`"下週五"`；斷言錯誤訊息含 `YYYY-MM-DD`。
3. `test_create_issue_不存在的日期被拒絕` — `"2026-02-31"`，斷言訊息與格式錯誤不同。
4. `test_update_issue_預計完成日同樣驗證格式` — 證明共用路徑生效。
5. `test_update_issue_可不帶預計完成日` — 證明必填只加在 `create_issue`，未波及更新。
6. `test_create_issue_開始日期同樣驗證格式` — `start_date` 一併驗的那條。

第 2 條的 `"20260812"` 案例是刻意的：它正是「只用 `fromisoformat` 就會漏掉」的漏洞，
寫成測試後，日後有人想簡化驗證邏輯會被擋下。

## 不在範圍內

- 不擋過去的日期
- 不改 `update_issue` 的必填性
- 不改 `create_project`
- 不在 server 端推算或預設日期（例如「預設七天後」）——期限是人的承諾，不該由工具代填
