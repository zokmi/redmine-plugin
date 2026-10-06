# 參數檔維護命令（`redmine-mcp config`）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者用 `redmine-mcp config` 的四個子命令查看與維護參數檔（改全域設定、改單一站台欄位、刪站台），不必手改 TOML。

**Architecture:** 沿用安裝精靈已建立的三層分工——`toml_edit` 純文字編輯（不碰檔案系統）、`config_ops` 流程編排（IO 與外部相依全部注入）、`cli` 只做參數解析與呼叫。新增的核心是一組「在指定區間內設／刪一個鍵」的純函式，全域區與站台區塊共用，因此不必整塊重建、不會吃掉使用者自己寫的鍵與註解。

**Tech Stack:** Python 3.11+、Typer（CLI）、Rich（輸出）、tomllib（唯讀解析）、pytest。不新增任何執行期相依。

**Spec:** `docs/superpowers/specs/2026-08-17-config-edit-commands-design.md`（相對於 repo 根目錄；本計畫位於 `redmine-mcp/` 之下）

## Global Constraints

- **工作目錄**：所有指令都在 `redmine-mcp/` 底下執行（`uv run pytest -q`、`uv run ruff check .`、`uv run mypy src`）。三者必須全綠才算完成一個 Task。
- **註解語言**：所有註解、docstring、錯誤訊息、commit 訊息一律繁體中文。新增 helper 要有中文函式說明；新增公開函式、欄位與參數要有中文說明。
- **金鑰不得外洩**：金鑰不可進入 argv、log、例外訊息或畫面回顯。要顯示一律用既有的 `toml_edit.mask_secret()`。TOML 解析錯誤訊息不可帶原文（出錯那行可能正是 `api_key`）。
- **不新增執行期相依**：不引入 tomlkit 或其他 TOML 寫入庫。理由見 `toml_edit.py` 模組 docstring 與 spec。
- **不在 server 啟動路徑上增加 import**：新程式碼只能被 `cli.py` 匯入（`cli.py` 只在有命令列參數時才被載入）。`tests/test_cli.py::test_無參數啟動路徑不載入_typer` 會把這件事鎖住，不可修改該測試來遷就實作。
- **驗證邏輯不重複**：站台代號規則一律用 `config._SITE_NAME_PATTERN`，全域鍵清單一律用 `config_file.GLOBAL_KEYS`，不另立第二份。
- **exit code**：0 成功；1 使用者取消或可預期錯誤（檔案不存在、代號不存在、格式拒絕、驗證失敗）。
- **新增站台不在範圍內**：`config` 只維護既有內容，新增站台一律指向 `redmine-mcp setup`。

---

## 檔案結構

| 檔案 | 責任 | 動作 |
|---|---|---|
| `src/redmine_mcp/config_file.py` | 抽出 `structure_config()`，讓「已解析的 TOML 對映 → RawConfig」可從字串重用 | 修改 |
| `src/redmine_mcp/setup/toml_edit.py` | 新增區間編輯純函式與寫檔前的驗證閘 | 修改 |
| `src/redmine_mcp/setup/config_ops.py` | 四個命令的流程編排與共用管線 | 新增 |
| `src/redmine_mcp/cli.py` | 掛上 `config` 子群組，四個命令的參數解析 | 修改 |
| `tests/test_config_file.py` | `structure_config()` 的直接測試 | 修改 |
| `tests/test_setup_toml_edit.py` | 區間編輯與驗證閘的字串測試 | 修改 |
| `tests/test_setup_config_ops.py` | 流程層逐分支測試（假 IO／假 runner／假 verify） | 新增 |
| `tests/test_cli.py` | 四個命令的參數解析與 exit code | 修改 |
| `SETUP.md`、`README.md`、`../README.md` | 文件 | 修改 |

---

### Task 1: 讓語意驗證能從字串執行

寫檔前要能把「編輯後的全文」當成真正的參數檔驗一遍，但現在的入口 `load_config_file()` 只吃 `Path`。先把中間那段「已解析的對映 → `RawConfig`」抽成獨立函式，再用它組出字串版的驗證閘。抽出而不是複製，是為了讓驗證邏輯只有一處。

**Files:**
- Modify: `src/redmine_mcp/config_file.py:161-190`
- Modify: `src/redmine_mcp/setup/toml_edit.py`（檔尾新增）
- Test: `tests/test_config_file.py`、`tests/test_setup_toml_edit.py`

**Interfaces:**
- Consumes: `config.RawConfig`、`config.load_settings`、`config.ConfigError`、`config_file.GLOBAL_KEYS`、`config_file.SITE_KEYS`、`config_file.LEGACY_SITE_KEYS`、`config_file._collect`
- Produces:
  - `config_file.structure_config(raw: Mapping[str, object], where: str) -> RawConfig`
  - `toml_edit.validate_config_text(text: str, *, allow_no_sites: bool = False) -> None`（失敗拋 `ConfigError`）

- [ ] **Step 1: 寫下 `structure_config()` 的失敗測試**

加到 `tests/test_config_file.py` 檔尾（該檔已 import `pytest` 與 `ConfigError`，若無則補上 `from redmine_mcp.config import ConfigError`）：

```python
def test_structure_config_可直接吃已解析的對映():
    from redmine_mcp.config_file import structure_config

    config = structure_config(
        {"download_dir": "D:/dl", "sites": {"main": {"url": "https://a/b", "api_key": "k"}}},
        "編輯後的參數檔",
    )

    assert config.globals == {"download_dir": "D:/dl"}
    assert config.sites == {"main": {"url": "https://a/b", "api_key": "k"}}


def test_structure_config_的錯誤訊息使用傳入的位置描述():
    # 訊息前綴可替換，是為了讓「編輯後的字串」與「某個路徑的檔案」共用同一套驗證。
    from redmine_mcp.config_file import structure_config

    with pytest.raises(ConfigError) as exc:
        structure_config({"url": "https://a/b", "api_key": "k", "sites": {}}, "編輯後的參數檔")

    assert "編輯後的參數檔" in str(exc.value)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_config_file.py -k structure_config -v`
Expected: FAIL，`ImportError: cannot import name 'structure_config'`

- [ ] **Step 3: 抽出 `structure_config()`**

把 `config_file.py` 中 `load_config_file()` 的第 161–190 行（從 `sites_table = raw.get("sites")` 到函式結尾的 `return`）整段移出成新函式，訊息中原本內插 `path` 的位置改用 `where`：

```python
def structure_config(raw: Mapping[str, object], where: str) -> RawConfig:
    """把已解析的 TOML 對映整理成 RawConfig。

    與 load_config_file() 拆開，是為了讓「還沒落地的字串」也能走同一套結構檢查——
    `redmine-mcp config` 的各項編輯會在寫檔前先驗證編輯結果，若這段邏輯只綁在
    「讀某個路徑的檔案」上，那道閘就只能複製一份實作，兩份遲早分歧。

    參數:
        raw: tomllib 解析後的對映。
        where: 錯誤訊息中要指出的位置，例如「參數檔（C:/…/config.toml）」或
            「編輯後的參數檔」。
    回傳:
        結構化後的 RawConfig。
    例外:
        ConfigError: 新舊格式並存、sites 不是表格，或某鍵的值不是單一純量。
    """
    sites_table = raw.get("sites")
    has_legacy = bool(LEGACY_SITE_KEYS & raw.keys())

    if sites_table is not None and has_legacy:
        # 兩者並存代表使用者改到一半，靜默採用其中一組會造成
        # 「參數檔寫 A、實際連 B」這種最難排查的狀況。
        raise ConfigError(
            f"{where}同時有最外層的 url／api_key 與 [sites.*] 區塊，"
            "請擇一：改用 [sites.<名稱>] 後移除最外層的 url 與 api_key"
        )

    top_level = {key: value for key, value in raw.items() if key != "sites"}

    if sites_table is None:
        if not has_legacy:
            return RawConfig(globals=_collect(top_level, GLOBAL_KEYS, "參數檔"), sites={})
        site = _collect(top_level, SITE_KEYS, "參數檔")
        globals_ = {key: value for key, value in site.items() if key in GLOBAL_KEYS}
        return RawConfig(globals=globals_, sites={LEGACY_SITE_NAME: site})

    if not isinstance(sites_table, dict):
        raise ConfigError(f"{where}的 sites 必須是 [sites.<名稱>] 形式的表格")

    sites: dict[str, dict[str, str]] = {}
    for name, body in sites_table.items():
        if not isinstance(body, dict):
            raise ConfigError(f"{where}的 sites.{name} 必須是表格")
        sites[name] = _collect(body, SITE_KEYS, f"站台 {name}")

    return RawConfig(globals=_collect(top_level, GLOBAL_KEYS, "參數檔"), sites=sites)
```

`load_config_file()` 原本那段換成一行：

```python
    return structure_config(raw, f"參數檔（{path}）")
```

訊息前綴刻意維持 `參數檔（<路徑>）`，與改動前逐字相同，既有測試不需調整。

- [ ] **Step 4: 執行測試確認通過（含既有測試）**

Run: `uv run pytest tests/test_config_file.py -q`
Expected: PASS，且原有測試無一失敗

- [ ] **Step 5: 寫下 `validate_config_text()` 的失敗測試**

加到 `tests/test_setup_toml_edit.py` 檔尾（並把 `validate_config_text` 加進檔頭的 import 清單）：

```python
_合法內容 = '[sites.main]\nurl = "https://a/b"\napi_key = "k"\n'


def test_合法內容通過驗證():
    validate_config_text(_合法內容)  # 不拋出即通過


def test_壞掉的_TOML_被擋下且訊息不帶原文():
    # 出錯那一行有可能正好是 api_key，訊息只能講「無法解析」。
    壞內容 = '[sites.main]\napi_key = "還沒收尾的字串\n'
    with pytest.raises(ConfigError) as exc:
        validate_config_text(壞內容)
    assert "api_key" not in str(exc.value)


def test_缺少必填鍵被擋下():
    with pytest.raises(ConfigError):
        validate_config_text('[sites.main]\nurl = "https://a/b"\n')


def test_沒有站台時預設不通過():
    # load_settings() 會補一個 default 站台再抱怨缺 url；這對一般編輯正是我們要的攔阻。
    with pytest.raises(ConfigError):
        validate_config_text('download_dir = "D:/dl"\n')


def test_允許沒有站台時放行():
    # 刪掉最後一個站台是 spec 明文允許的操作（會另外警告），不能被這道閘擋死。
    validate_config_text('download_dir = "D:/dl"\n', allow_no_sites=True)


def test_允許沒有站台不會放行其他錯誤():
    with pytest.raises(ConfigError):
        validate_config_text('[sites.main]\nurl = "https://a/b"\n', allow_no_sites=True)
```

- [ ] **Step 6: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -k validate -v`
Expected: FAIL，`ImportError: cannot import name 'validate_config_text'`

- [ ] **Step 7: 實作 `validate_config_text()`**

在 `toml_edit.py` 檔尾新增；檔頭 import 補上 `from redmine_mcp.config import ConfigError, load_settings` 與 `from redmine_mcp.config_file import LEGACY_SITE_KEYS, structure_config`（原本已 import 前者的 `ConfigError` 與後者的 `LEGACY_SITE_KEYS`，只需各補一個名字）：

```python
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
```

- [ ] **Step 8: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_toml_edit.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS

- [ ] **Step 9: Commit**

```bash
git add src/redmine_mcp/config_file.py src/redmine_mcp/setup/toml_edit.py tests/test_config_file.py tests/test_setup_toml_edit.py
git commit -m "feat(redmine-mcp): 參數檔的語意驗證可從字串執行"
```

---

### Task 2: 區間內的鍵編輯（純函式）

全域鍵與站台鍵的編輯是同一件事，只差搜尋範圍。做成「在 `(start, end)` 區間內設／刪一個鍵」，全域區與站台區塊共用。這樣 `set-site` 就不必用 `render_site_block` 整塊重建——那會吃掉使用者寫在 `[sites.*]` 底下的 `download_dir` 等站台層覆寫。

**Files:**
- Modify: `src/redmine_mcp/setup/toml_edit.py`
- Test: `tests/test_setup_toml_edit.py`

**Interfaces:**
- Consumes: `toml_edit.escape_toml`、`toml_edit._ANY_HEADER`
- Produces:
  - `toml_edit.global_region(text: str) -> tuple[int, int]`
  - `toml_edit.set_key_in(text: str, span: tuple[int, int], key: str, value: str | int) -> str`
  - `toml_edit.remove_key_in(text: str, span: tuple[int, int], key: str) -> str`

- [ ] **Step 1: 寫下失敗測試**

加到 `tests/test_setup_toml_edit.py`（import 清單補 `global_region`、`set_key_in`、`remove_key_in`）：

```python
def test_全域區到第一個表格標頭為止():
    text = '# 註解\ndownload_dir = "D:/dl"\n\n[sites.main]\nurl = "https://a/b"\n'
    start, end = global_region(text)
    assert text[start:end] == '# 註解\ndownload_dir = "D:/dl"\n\n'


def test_沒有任何表格標頭時全域區是整份內容():
    text = 'download_dir = "D:/dl"\n'
    assert global_region(text) == (0, len(text))


def test_設定既有的全域鍵是就地換行():
    text = 'download_dir = "D:/old"\n\n[sites.main]\nurl = "https://a/b"\n'
    result = set_key_in(text, global_region(text), "download_dir", "D:/new")
    assert result == 'download_dir = "D:/new"\n\n[sites.main]\nurl = "https://a/b"\n'


def test_全域鍵不存在時插在註解之後並隔一空行():
    text = "# redmine-mcp 參數檔\n\n[sites.main]\nurl = \"https://a/b\"\n"
    result = set_key_in(text, global_region(text), "download_dir", "D:/dl")
    assert result == (
        "# redmine-mcp 參數檔\n"
        "\n"
        'download_dir = "D:/dl"\n'
        "\n"
        "[sites.main]\n"
        'url = "https://a/b"\n'
    )


def test_已有全域鍵時新鍵緊接在後不多插空行():
    text = 'download_dir = "D:/dl"\n\n[sites.main]\nurl = "https://a/b"\n'
    result = set_key_in(text, global_region(text), "upload_dir", "D:/up")
    assert result == (
        'download_dir = "D:/dl"\n'
        'upload_dir = "D:/up"\n'
        "\n"
        "[sites.main]\n"
        'url = "https://a/b"\n'
    )


def test_整數值不加引號():
    # config_file 的 _scalar() 會 str() 兩種寫法都讀得到，裸整數與 config.example.toml 一致。
    text = "[sites.main]\n"
    result = set_key_in(text, (0, 0), "max_attachment_mb", 25)
    assert result == "max_attachment_mb = 25\n[sites.main]\n"


def test_字串值逸出引號與反斜線():
    result = set_key_in("", (0, 0), "download_dir", r'D:\a"b')
    assert result == 'download_dir = "D:\\\\a\\"b"\n'


def test_註解掉的同名行不算既有鍵():
    # 把註解行當成該鍵去替換，會讓使用者的說明消失、真正的設定又沒寫進去。
    text = '# download_dir = "D:/old"\n\n[sites.main]\n'
    result = set_key_in(text, global_region(text), "download_dir", "D:/new")
    assert '# download_dir = "D:/old"' in result
    assert 'download_dir = "D:/new"' in result


def test_只在指定區間內尋找同名鍵():
    # [sites.main] 底下也可以有 download_dir；改全域鍵絕不能動到站台層的覆寫。
    text = (
        'download_dir = "D:/global"\n'
        "\n"
        "[sites.main]\n"
        'download_dir = "D:/site"\n'
    )
    result = set_key_in(text, global_region(text), "download_dir", "D:/new")
    assert 'download_dir = "D:/new"' in result
    assert 'download_dir = "D:/site"' in result


def test_刪除區間內的鍵連整行一起移除():
    text = 'download_dir = "D:/dl"\nupload_dir = "D:/up"\n\n[sites.main]\n'
    result = remove_key_in(text, global_region(text), "download_dir")
    assert result == 'upload_dir = "D:/up"\n\n[sites.main]\n'


def test_刪除不存在的鍵是冪等的():
    # --clear 可能被重複執行，第二次不該報錯。
    text = 'upload_dir = "D:/up"\n\n[sites.main]\n'
    assert remove_key_in(text, global_region(text), "download_dir") == text
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -k "region or key_in" -v`
Expected: FAIL，`ImportError: cannot import name 'global_region'`

- [ ] **Step 3: 實作三個函式**

在 `toml_edit.py` 的 `find_site_block()` 之後新增：

```python
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
    """
    return re.compile(rf"^{re.escape(key)}[ \t]*=.*$", re.MULTILINE)


def set_key_in(text: str, span: tuple[int, int], key: str, value: str | int) -> str:
    """在指定區間內設定一個鍵：已存在就換掉整行，不存在就插在區間尾端。

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
    line = f"{key} = {_render_value(value)}"

    match = _key_line(key).search(region)
    if match:
        return text[:start] + region[: match.start()] + line + region[match.end() :] + text[end:]

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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_toml_edit.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/toml_edit.py tests/test_setup_toml_edit.py
git commit -m "feat(redmine-mcp): 參數檔的區間內鍵編輯純函式"
```

---

### Task 3: 刪除站台區塊（純函式）

**Files:**
- Modify: `src/redmine_mcp/setup/toml_edit.py`
- Test: `tests/test_setup_toml_edit.py`

**Interfaces:**
- Consumes: `toml_edit.find_site_block`、`config.ConfigError`
- Produces: `toml_edit.remove_site_block(text: str, site: str) -> str`

- [ ] **Step 1: 寫下失敗測試**

```python
def test_刪除中間的站台區塊不影響其他站台():
    text = (
        "[sites.main]\n"
        'url = "https://a/b"\n'
        "\n"
        "[sites.mid]\n"
        'url = "https://m/m"\n'
        "\n"
        "[sites.last]\n"
        'url = "https://l/l"\n'
    )
    result = remove_site_block(text, "mid")
    assert result == (
        "[sites.main]\n"
        'url = "https://a/b"\n'
        "\n"
        "[sites.last]\n"
        'url = "https://l/l"\n'
    )


def test_刪除最後一個區塊不留下多餘空行():
    text = '[sites.main]\nurl = "https://a/b"\n\n[sites.tail]\nurl = "https://t/t"\n'
    result = remove_site_block(text, "tail")
    assert result == '[sites.main]\nurl = "https://a/b"\n'


def test_刪除唯一站台後保留全域鍵與註解():
    text = '# 我的參數檔\n\ndownload_dir = "D:/dl"\n\n[sites.main]\nurl = "https://a/b"\n'
    result = remove_site_block(text, "main")
    assert result == '# 我的參數檔\n\ndownload_dir = "D:/dl"\n'


def test_刪光之後沒有殘留內容時回傳空字串():
    assert remove_site_block('[sites.main]\nurl = "https://a/b"\n', "main") == ""


def test_刪除不存在的站台會拋出():
    with pytest.raises(ConfigError):
        remove_site_block('[sites.main]\nurl = "https://a/b"\n', "nope")
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -k remove_site -v`
Expected: FAIL，`ImportError: cannot import name 'remove_site_block'`

- [ ] **Step 3: 實作**

```python
def remove_site_block(text: str, site: str) -> str:
    """移除指定站台的整個區塊。

    區塊邊界沿用 find_site_block()：它把下一個標頭前的空行歸給本區塊，因此連著刪掉
    不會留下孤立的空行。刪完把尾端的連續換行收斂成一個，避免檔尾累積空白；整份沒有
    實質內容時回傳空字串。

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
    remaining = text[:start] + text[end:]
    return remaining.rstrip("\n") + "\n" if remaining.strip() else ""
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_toml_edit.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/toml_edit.py tests/test_setup_toml_edit.py
git commit -m "feat(redmine-mcp): 移除站台區塊的純函式"
```

---

### Task 4: 流程層骨架與 `config show`

建立 `config_ops.py`，包含四個命令共用的「讀檔前置」與「寫檔收尾」，以及唯讀的 `show`。

**Files:**
- Create: `src/redmine_mcp/setup/config_ops.py`
- Create: `tests/test_setup_config_ops.py`

**Interfaces:**
- Consumes: `wizard.Prompts`、`external.CommandRunner`、`fsops.write_config`、`fsops.tighten_permissions`、`toml_edit.classify`、`toml_edit.validate_config_text`、`toml_edit.mask_secret`、`toml_edit.global_region`、`toml_edit.set_key_in`、`toml_edit.remove_key_in`、`toml_edit.find_site_block`、`config_file.GLOBAL_KEYS`
- Produces:
  - `config_ops.Loaded`（`text: str`、`sites: dict[str, dict[str, str]]`、`kind: str`）
  - `config_ops.load_current(prompts, config_path, *, need_sites: bool) -> Loaded | None`
  - `config_ops.commit(prompts, config_path, new_text, preview, *, run, assume_yes, allow_no_sites=False) -> int`
  - `config_ops.apply_changes(text, locate, changes) -> str`
  - `config_ops.show(prompts, *, config_path) -> int`

- [ ] **Step 1: 寫下失敗測試**

新檔 `tests/test_setup_config_ops.py`。假 IO 直接沿用 `test_setup_wizard.py` 的 `腳本化提問`（同一套風格，import 過來即可）：

```python
"""參數檔維護命令的流程測試：以假 Prompts／假 CommandRunner／假 verify 逐分支驗證。"""
import pytest

from redmine_mcp.setup import config_ops
from redmine_mcp.setup.external import CommandResult
from tests.test_setup_wizard import 腳本化提問


def _成功執行(argv, stdin=None):
    return CommandResult(0, "", "")


_一個站台 = (
    "# 我的參數檔\n"
    "\n"
    'download_dir = "D:/dl"\n'
    "\n"
    "[sites.main]\n"
    'url         = "https://a/redmine"\n'
    'api_key     = "0123456789abcdef"\n'
    'description = "公司正式站"\n'
)


def test_參數檔不存在時指向_setup(tmp_path):
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    result = config_ops.load_current(
        script.build(), tmp_path / "config.toml", need_sites=True
    )

    assert result is None
    assert "setup" in "\n".join(script.said)
    script.assert_exhausted()


def test_舊的扁平格式被拒絕並指向_setup(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('url = "https://a/b"\napi_key = "k"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.load_current(script.build(), target, need_sites=True) is None
    全部輸出 = "\n".join(script.said)
    assert "扁平" in 全部輸出
    assert "setup" in 全部輸出
    script.assert_exhausted()


def test_沒有站台且命令需要站台時擋下(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('download_dir = "D:/dl"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.load_current(script.build(), target, need_sites=True) is None
    script.assert_exhausted()


def test_沒有站台但命令不需要站台時放行(tmp_path):
    # set-global 只動全域鍵，沒有站台也該能執行。
    target = tmp_path / "config.toml"
    target.write_text('download_dir = "D:/dl"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    loaded = config_ops.load_current(script.build(), target, need_sites=False)
    assert loaded is not None
    assert loaded.sites == {}


def test_寫入成功會收緊權限並提醒重新連線(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    呼叫過的指令 = []

    def run(argv, stdin=None):
        呼叫過的指令.append(argv)
        return CommandResult(0, "", "")

    script = 腳本化提問(answers=[], confirms=[True], choices=[])
    code = config_ops.commit(
        script.build(), target, _一個站台, ["預覽"], run=run, assume_yes=False
    )

    assert code == 0
    全部輸出 = "\n".join(script.said)
    assert "/mcp" in 全部輸出
    # os.replace 換掉 inode，新檔權限不會沿用舊檔；漏了這步等於把金鑰檔權限放回預設。
    # 斷言輸出而非斷言指令：Windows 走 icacls（經 run），其他平台走 Path.chmod（不經 run），
    # 只有這行訊息在兩個平台都代表「權限收緊真的執行過且成功」。
    assert "已收緊檔案權限" in 全部輸出
    script.assert_exhausted()


def test_編輯結果無法通過驗證時不落地(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.commit(
        script.build(), target, "[sites.main\n", ["預覽"], run=_成功執行, assume_yes=True
    )

    assert code == 1
    assert target.read_text(encoding="utf-8") == _一個站台
    assert "回報" in "\n".join(script.said)
    script.assert_exhausted()


def test_使用者拒絕確認時不寫檔(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[False], choices=[])

    code = config_ops.commit(
        script.build(), target, _一個站台.replace("D:/dl", "D:/new"), ["預覽"],
        run=_成功執行, assume_yes=False,
    )

    assert code == 1
    assert "D:/dl" in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_assume_yes_跳過確認(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.commit(
        script.build(), target, _一個站台.replace("D:/dl", "D:/new"), ["預覽"],
        run=_成功執行, assume_yes=True,
    )

    assert code == 0
    assert "D:/new" in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_show_列出站台並遮罩金鑰(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.show(script.build(), config_path=target)

    全部輸出 = "\n".join(script.said)
    assert code == 0
    assert "main" in 全部輸出
    assert "https://a/redmine" in 全部輸出
    assert "****cdef" in 全部輸出
    assert "0123456789abcdef" not in 全部輸出
    assert "D:/dl" in 全部輸出
    script.assert_exhausted()


def test_show_對未設定的全域鍵明確標示(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[sites.main]\nurl = "https://a/b"\napi_key = "k"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.show(script.build(), config_path=target) == 0
    assert "未設定" in "\n".join(script.said)


def test_show_對舊格式照實顯示而不拒絕(tmp_path):
    # show 是唯讀的，沒有改壞檔案的風險，直接說明它等同站台 default 更有用。
    target = tmp_path / "config.toml"
    target.write_text('url = "https://a/b"\napi_key = "k"\n', encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    assert config_ops.show(script.build(), config_path=target) == 0
    assert "default" in "\n".join(script.said)


def test_show_檔案不存在時回傳一(tmp_path):
    script = 腳本化提問(answers=[], confirms=[], choices=[])
    assert config_ops.show(script.build(), config_path=tmp_path / "nope.toml") == 1


def test_apply_changes_每次重新定位區間():
    # 前一次編輯會讓舊 span 失效（長度變了），因此每個 change 都要重新定位。
    text = "[sites.main]\n"
    result = config_ops.apply_changes(
        text,
        config_ops.global_locator(),
        [("download_dir", "D:/dl"), ("upload_dir", "D:/up"), ("download_dir", None)],
    )
    assert result == 'upload_dir = "D:/up"\n[sites.main]\n'
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_config_ops.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.config_ops'`

- [ ] **Step 3: 實作 `config_ops.py`**

```python
"""參數檔維護命令的流程編排。

與 wizard.py 同一取向：所有 IO 由 Prompts 注入，外部指令以參數傳入，因此可在不碰
終端機、不打真實 API 的前提下逐分支測試。

四個命令共用同一條管線——讀檔與格式判定在 load_current()，驗證、確認、落地、權限與
提醒在 commit()。命令各自只負責「把舊全文變成新全文」。
"""
from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from redmine_mcp.config import ConfigError
from redmine_mcp.config_file import GLOBAL_KEYS, LEGACY_SITE_KEYS
from redmine_mcp.setup import toml_edit
from redmine_mcp.setup.external import CommandRunner
from redmine_mcp.setup.fsops import tighten_permissions, write_config
from redmine_mcp.setup.wizard import Prompts

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

    text = config_path.read_text(encoding="utf-8")
    try:
        kind = toml_edit.classify(text)
    except ConfigError as exc:
        prompts.say(f"[red]×[/red] {exc}")
        return None

    if kind == "legacy":
        prompts.say("[red]×[/red] 這份參數檔是舊的扁平格式（url／api_key 直接寫在最外層）。")
        prompts.say("請先執行 redmine-mcp setup，它會在徵得同意後轉成 [sites.*] 格式。")
        return None

    raw = tomllib.loads(text)
    sites_table = raw.get("sites") or {}
    sites = {
        str(name): {str(k): str(v) for k, v in body.items()}
        for name, body in sites_table.items()
        if isinstance(body, dict)
    }

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
        prompts.say(f"[red]×[/red] 編輯後的參數檔無法通過驗證：{exc}")
        prompts.say("參數檔未被修改。這代表本工具的編輯有問題，請回報。")
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
    prompts.say(f"[green]✓[/green] 已寫入 {config_path}")

    # write_config() 以 os.replace 換掉 inode，新檔權限來自建立時的預設與目錄繼承，
    # 不會沿用舊檔。少了這一步，改一次設定就等於把含金鑰的檔案權限放回預設。
    problem = tighten_permissions(config_path, lambda argv: run(argv).code)
    if problem:
        prompts.say(
            f"[yellow]![/yellow] 權限收緊失敗（{problem}）。"
            "這個檔案含金鑰，請自行確認只有你讀得到。"
        )
    else:
        prompts.say("[green]✓[/green] 已收緊檔案權限")

    prompts.say("改動要重新連線才生效：在 Claude Code 用 /mcp 重連（或重開）。")
    return 0


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

    text = config_path.read_text(encoding="utf-8")
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

    if kind == "legacy":
        prompts.say("偵測到舊的扁平格式，等同一個名為 default 的站台：")
        站台 = {"default": {str(k): str(v) for k, v in raw.items() if k in LEGACY_SITE_KEYS}}
    else:
        站台 = {
            str(name): {str(k): str(v) for k, v in body.items()}
            for name, body in (raw.get("sites") or {}).items()
            if isinstance(body, dict)
        }

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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_config_ops.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS。若 `test_寫入成功會收緊權限並提醒重新連線` 在非 Windows 上因 `chmod` 走的是 `Path.chmod()`（不經 run）而失敗，該斷言已用 `or 呼叫過的指令 == []` 涵蓋兩個平台。

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/config_ops.py tests/test_setup_config_ops.py
git commit -m "feat(redmine-mcp): 參數檔維護的流程骨架與 config show"
```

---

### Task 5: `config set-site`

**Files:**
- Modify: `src/redmine_mcp/setup/config_ops.py`
- Test: `tests/test_setup_config_ops.py`

**Interfaces:**
- Consumes: Task 4 的 `load_current`／`commit`／`apply_changes`／`site_locator`、`wizard.Verifier`
- Produces:
```python
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
) -> int
```

- [ ] **Step 1: 寫下失敗測試**

加到 `tests/test_setup_config_ops.py`：

```python
async def _成功驗證(url, api_key):
    return "kenny"


async def _失敗驗證(url, api_key):
    from redmine_mcp.errors import RedmineError

    raise RedmineError("金鑰不對")


async def _不該被呼叫的驗證(url, api_key):
    raise AssertionError("這個情境不該打 Redmine")


def _寫好一個站台(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_一個站台, encoding="utf-8")
    return target


def _set_site(script, target, **kwargs):
    參數 = {
        "site": "main",
        "url": None,
        "description": None,
        "clear_description": False,
        "ask_key": False,
        "run": _成功執行,
        "verify": _成功驗證,
        "assume_yes": True,
    }
    參數.update(kwargs)
    return config_ops.set_site(script.build(), config_path=target, **參數)


def test_換網址會先驗證再寫入(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, url="https://new/redmine")

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert 'url         = "https://new/redmine"' in 內容
    assert "kenny" in "\n".join(script.said)


def test_只改說明不打_Redmine(tmp_path):
    # 只改一行說明卻付一次網路往返沒有道理；用會拋錯的 verify 當哨兵鎖住這件事。
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, description="新的說明", verify=_不該被呼叫的驗證)

    assert code == 0
    assert 'description = "新的說明"' in target.read_text(encoding="utf-8")


def test_清空說明會移除該行(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, clear_description=True, verify=_不該被呼叫的驗證)

    assert code == 0
    assert "description" not in target.read_text(encoding="utf-8")


def test_換金鑰時以不回顯方式取得並驗證(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=["新金鑰9876"], confirms=[], choices=[])

    code = _set_site(script, target, ask_key=True)

    assert code == 0
    assert 'api_key     = "新金鑰9876"' in target.read_text(encoding="utf-8")
    全部輸出 = "\n".join(script.said)
    # 預覽只能出現遮罩後的樣子。
    assert "****9876" in 全部輸出
    assert "新金鑰9876" not in 全部輸出
    script.assert_exhausted()


def test_驗證失敗可重填後成功(tmp_path):
    target = _寫好一個站台(tmp_path)
    嘗試 = []

    async def verify(url, api_key):
        嘗試.append((url, api_key))
        if len(嘗試) == 1:
            from redmine_mcp.errors import RedmineError

            raise RedmineError("金鑰不對")
        return "kenny"

    script = 腳本化提問(
        answers=["壞金鑰1111", "https://a/redmine", "好金鑰2222"],
        confirms=[True],  # 要重新輸入嗎？
        choices=[],
    )

    code = _set_site(script, target, ask_key=True, verify=verify)

    assert code == 0
    assert len(嘗試) == 2
    assert 'api_key     = "好金鑰2222"' in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_驗證失敗且放棄時不寫檔(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=["壞金鑰1111"], confirms=[False], choices=[])

    code = _set_site(script, target, ask_key=True, verify=_失敗驗證)

    assert code == 1
    assert target.read_text(encoding="utf-8") == _一個站台
    script.assert_exhausted()


def test_站台不存在時列出現有代號(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, site="nope", url="https://x/y")

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "main" in 全部輸出
    assert "setup" in 全部輸出


def test_不吃掉站台層的覆寫鍵(tmp_path):
    # render_site_block 只吐 url／api_key／description 三行，整塊重建會讓這裡的
    # download_dir 消失。這條測試就是為了鎖住「逐鍵編輯」這個決定。
    target = tmp_path / "config.toml"
    target.write_text(
        "[sites.main]\n"
        'url         = "https://a/redmine"\n'
        'api_key     = "0123456789abcdef"\n'
        'download_dir = "D:/only-main"\n',
        encoding="utf-8",
    )
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_site(script, target, url="https://new/redmine")

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert 'download_dir = "D:/only-main"' in 內容
    assert 'url         = "https://new/redmine"' in 內容


def test_完全不帶旗標時逐項詢問並以_Enter_保留原值(tmp_path):
    target = _寫好一個站台(tmp_path)
    # 網址與說明都直接 Enter（空字串）、不換金鑰 → 什麼都沒變，也不必打 Redmine。
    script = 腳本化提問(answers=["", ""], confirms=[False], choices=[])

    code = config_ops.set_site(
        script.build(),
        config_path=target,
        site="main",
        url=None,
        description=None,
        clear_description=False,
        ask_key=False,
        run=_成功執行,
        verify=_不該被呼叫的驗證,
        assume_yes=True,
    )

    assert code == 0
    assert target.read_text(encoding="utf-8") == _一個站台
    script.assert_exhausted()


def test_互動模式輸入減號代表清空說明(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=["", "-"], confirms=[False], choices=[])

    code = config_ops.set_site(
        script.build(),
        config_path=target,
        site="main",
        url=None,
        description=None,
        clear_description=False,
        ask_key=False,
        run=_成功執行,
        verify=_不該被呼叫的驗證,
        assume_yes=True,
    )

    assert code == 0
    assert "description" not in target.read_text(encoding="utf-8")
    script.assert_exhausted()
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_config_ops.py -k set_site -v`
Expected: FAIL，`AttributeError: module 'redmine_mcp.setup.config_ops' has no attribute 'set_site'`

- [ ] **Step 3: 實作 `set_site()`**

在 `config_ops.py` 新增（檔頭 import 補 `import asyncio`、`from redmine_mcp.errors import RedmineError`、`from redmine_mcp.setup.wizard import Prompts, Verifier`）：

```python
#: 互動模式下代表「清空這個欄位」的輸入。用單一減號而非空字串：空字串是 Enter 的結果，
#: 必須留給「保留原值」，否則使用者無法在不改動的情況下跳過某一題。
_CLEAR_TOKEN = "-"

#: 「不變更」的哨符，與「清空（None）」區分開。
_KEEP: object = object()


def _ask_optional(prompts: Prompts, label: str, current: str) -> str | None | object:
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
        ask_key: 為真時以不回顯的方式詢問新金鑰。
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

    current = loaded.sites[site]
    新說明: str | None | object = None if clear_description else (description or _KEEP)

    # 完全沒帶欄位旗標時退回互動，保住精靈的手感。
    if url is None and description is None and not clear_description and not ask_key:
        新網址 = prompts.ask(f"站台網址（Enter 保留 {current.get('url', '')}）") or None
        新說明 = _ask_optional(prompts, "站台說明", current.get("description", ""))
        ask_key = prompts.confirm("要換 API 金鑰嗎？")
    else:
        新網址 = url

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
                key_值 = prompts.ask_secret("API 存取金鑰")
                continue
            prompts.say(f"[green]✓[/green] 驗證通過，這把金鑰對應的帳號是 {login}")
            break

    changes: list[Change] = []
    if 新網址:
        changes.append(("url", url_值))
    if ask_key:
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
    if 新網址:
        preview.append(f"網址：{url_值}")
    if ask_key:
        preview.append(f"金鑰：{toml_edit.mask_secret(key_值)}")
    if 新說明 is None:
        preview.append("說明：清空")
    elif 新說明 is not _KEEP:
        preview.append(f"說明：{新說明}")

    return commit(prompts, config_path, new_text, preview, run=run, assume_yes=assume_yes)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_config_ops.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/config_ops.py tests/test_setup_config_ops.py
git commit -m "feat(redmine-mcp): config set-site 修改既有站台欄位"
```

---

### Task 6: `config remove-site`

**Files:**
- Modify: `src/redmine_mcp/setup/config_ops.py`
- Test: `tests/test_setup_config_ops.py`

**Interfaces:**
- Consumes: Task 3 的 `toml_edit.remove_site_block`、Task 4 的 `load_current`／`commit`
- Produces:
```python
def remove_site(
    prompts: Prompts, *, config_path: Path, site: str, run: CommandRunner, assume_yes: bool
) -> int
```

- [ ] **Step 1: 寫下失敗測試**

```python
_兩個站台 = (
    "[sites.main]\n"
    'url     = "https://a/redmine"\n'
    'api_key = "aaaa1111"\n'
    "\n"
    "[sites.other]\n"
    'url     = "https://b/redmine"\n'
    'api_key = "bbbb2222"\n'
)


def test_刪除站台後只剩另一個(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_兩個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[True], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="other", run=_成功執行, assume_yes=False
    )

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert "[sites.main]" in 內容
    assert "sites.other" not in 內容
    script.assert_exhausted()


def test_刪除唯一站台會先警告啟動將失敗(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[True], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="main", run=_成功執行, assume_yes=False
    )

    assert code == 0
    全部輸出 = "\n".join(script.said)
    assert "啟動" in 全部輸出
    # 全域鍵與註解要留著，使用者可能只是想換一個站台重設。
    assert 'download_dir = "D:/dl"' in target.read_text(encoding="utf-8")
    script.assert_exhausted()


def test_assume_yes_仍然印出警告(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="main", run=_成功執行, assume_yes=True
    )

    assert code == 0
    assert "啟動" in "\n".join(script.said)
    script.assert_exhausted()


def test_拒絕確認時不刪(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_兩個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[False], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="other", run=_成功執行, assume_yes=False
    )

    assert code == 1
    assert target.read_text(encoding="utf-8") == _兩個站台
    script.assert_exhausted()


def test_刪除不存在的站台會列出現有代號(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(_兩個站台, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = config_ops.remove_site(
        script.build(), config_path=target, site="nope", run=_成功執行, assume_yes=True
    )

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "main" in 全部輸出 and "other" in 全部輸出
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_config_ops.py -k remove_site -v`
Expected: FAIL，`AttributeError: ... has no attribute 'remove_site'`

- [ ] **Step 3: 實作 `remove_site()`**

```python
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_config_ops.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/config_ops.py tests/test_setup_config_ops.py
git commit -m "feat(redmine-mcp): config remove-site 移除站台"
```

---

### Task 7: `config set-global`

**Files:**
- Modify: `src/redmine_mcp/setup/config_ops.py`
- Test: `tests/test_setup_config_ops.py`

**Interfaces:**
- Consumes: Task 4 的 `load_current`／`commit`／`apply_changes`／`global_locator`、`config_file.GLOBAL_KEYS`
- Produces:
```python
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
) -> int
```

- [ ] **Step 1: 寫下失敗測試**

```python
def _set_global(script, target, **kwargs):
    參數 = {
        "download_dir": None,
        "upload_dir": None,
        "max_attachment_mb": None,
        "clear": [],
        "run": _成功執行,
        "assume_yes": True,
    }
    參數.update(kwargs)
    return config_ops.set_global(script.build(), config_path=target, **參數)


def test_設定下載目錄(tmp_path):
    target = _寫好一個站台(tmp_path)
    存在的目錄 = tmp_path / "dl"
    存在的目錄.mkdir()
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, download_dir=str(存在的目錄))

    assert code == 0
    assert str(存在的目錄) in target.read_text(encoding="utf-8")


def test_目錄不存在時警告但仍寫入(tmp_path):
    # server 端不檢查目錄存在性，這裡拒絕寫入會與實際行為不一致。
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, download_dir=str(tmp_path / "還沒建"))

    assert code == 0
    assert "不存在" in "\n".join(script.said)
    assert "還沒建" in target.read_text(encoding="utf-8")


def test_附件上限寫成裸整數(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, max_attachment_mb=25)

    assert code == 0
    assert "max_attachment_mb = 25" in target.read_text(encoding="utf-8")


def test_附件上限必須大於零(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, max_attachment_mb=0)

    assert code == 1
    assert "大於 0" in "\n".join(script.said)
    assert "max_attachment_mb" not in target.read_text(encoding="utf-8")


def test_清空全域鍵(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, clear=["download_dir"])

    assert code == 0
    assert "download_dir" not in target.read_text(encoding="utf-8")


def test_清空未知鍵時列出可用鍵(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, clear=["api_key"])

    assert code == 1
    全部輸出 = "\n".join(script.said)
    assert "download_dir" in 全部輸出
    assert "upload_dir" in 全部輸出


def test_同一鍵不可同時設定與清空(tmp_path):
    target = _寫好一個站台(tmp_path)
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    code = _set_global(script, target, download_dir="D:/dl", clear=["download_dir"])

    assert code == 1
    assert "同時" in "\n".join(script.said)


def test_沒有站台也能改全域設定(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("# 只有註解\n", encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[], choices=[])

    # 沒有站台的檔案不該在「讀檔前置」就被站台問題擋住（set-global 與站台無關），
    # 但整份檔案確實不可用，因此會在寫檔前的驗證閘被攔下。
    code = _set_global(script, target, max_attachment_mb=5)

    assert code == 1
    assert "無法通過驗證" in "\n".join(script.said)
    assert target.read_text(encoding="utf-8") == "# 只有註解\n"


def test_不帶任何旗標時逐項詢問(tmp_path):
    target = _寫好一個站台(tmp_path)
    # 下載目錄 Enter 保留、上傳目錄輸入新值、附件上限輸入 -（清空，本來就沒設）
    script = 腳本化提問(answers=["", str(tmp_path), "-"], confirms=[], choices=[])

    code = _set_global(script, target)

    assert code == 0
    內容 = target.read_text(encoding="utf-8")
    assert 'download_dir = "D:/dl"' in 內容
    assert str(tmp_path).replace("\\", "\\\\") in 內容 or str(tmp_path) in 內容
    script.assert_exhausted()
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_config_ops.py -k set_global -v`
Expected: FAIL，`AttributeError: ... has no attribute 'set_global'`

- [ ] **Step 3: 實作 `set_global()`**

```python
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
            elif key == "max_attachment_mb":
                值[key] = str(answer)
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_config_ops.py -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/setup/config_ops.py tests/test_setup_config_ops.py
git commit -m "feat(redmine-mcp): config set-global 修改全域設定"
```

---

### Task 8: CLI 接線

**Files:**
- Modify: `src/redmine_mcp/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 4–7 的 `config_ops.show`／`set_site`／`remove_site`／`set_global`、`wizard.console_prompts`、`config_file.resolve_config_path`、`external.run_command`、`verify.verify_credentials`
- Produces: `redmine-mcp config {show,set-site,remove-site,set-global}` 四個命令

- [ ] **Step 1: 寫下失敗測試**

加到 `tests/test_cli.py`：

```python
def test_config_show_把_exit_code_原樣傳出(monkeypatch):
    monkeypatch.setattr("redmine_mcp.setup.config_ops.show", lambda prompts, *, config_path: 1)
    assert runner.invoke(app, ["config", "show"]).exit_code == 1


def test_config_set_site_傳遞旗標(monkeypatch):
    收到 = {}

    def 假的(prompts, **kwargs):
        收到.update(kwargs)
        return 0

    monkeypatch.setattr("redmine_mcp.setup.config_ops.set_site", 假的)
    result = runner.invoke(
        app,
        ["config", "set-site", "main", "--url", "https://a/b", "--key", "--yes"],
    )

    assert result.exit_code == 0
    assert 收到["site"] == "main"
    assert 收到["url"] == "https://a/b"
    assert 收到["ask_key"] is True
    assert 收到["assume_yes"] is True
    assert 收到["description"] is None
    assert 收到["clear_description"] is False


def test_config_remove_site_需要代號():
    # 少了必填參數應由 Typer 攔下（exit code 2），不該進到流程層。
    assert runner.invoke(app, ["config", "remove-site"]).exit_code == 2


def test_config_set_global_的_clear_可重複(monkeypatch):
    收到 = {}

    def 假的(prompts, **kwargs):
        收到.update(kwargs)
        return 0

    monkeypatch.setattr("redmine_mcp.setup.config_ops.set_global", 假的)
    result = runner.invoke(
        app,
        ["config", "set-global", "--clear", "download_dir", "--clear", "upload_dir"],
    )

    assert result.exit_code == 0
    assert list(收到["clear"]) == ["download_dir", "upload_dir"]


def test_config_set_global_的附件上限只收整數():
    assert runner.invoke(app, ["config", "set-global", "--max-attachment-mb", "abc"]).exit_code == 2


def test_config_不帶子命令時印出說明():
    result = runner.invoke(app, ["config"])
    assert result.exit_code != 0
    assert "set-site" in result.output
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_cli.py -k config -v`
Expected: FAIL，Typer 回報 `No such command 'config'`（exit code 2）

- [ ] **Step 3: 實作 CLI 接線**

在 `cli.py` 的 `setup()` 之後新增：

```python
#: config 子群組。多命令群組不需要 setup 那種空 callback——Typer 只在「單一命令且沒有
#: callback」時才會把命令攤平成 app 本身。
config_app = typer.Typer(no_args_is_help=True, help="維護參數檔：查看、改站台欄位、刪站台、改全域設定。")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """列出參數檔現況（金鑰只顯示末四碼）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard

    raise typer.Exit(
        config_ops.show(wizard.console_prompts(), config_path=resolve_config_path())
    )


@config_app.command("set-site")
def config_set_site(
    site: str = typer.Argument(..., help="要修改的站台代號"),
    url: str | None = typer.Option(None, "--url", help="新的站台網址（要含子路徑）"),
    description: str | None = typer.Option(None, "--description", help="新的站台說明"),
    clear_description: bool = typer.Option(
        False, "--clear-description", help="移除站台說明那一行"
    ),
    key: bool = typer.Option(
        False, "--key", help="換 API 金鑰；值會以不回顯的方式詢問，不接受寫在指令裡"
    ),
    yes: bool = typer.Option(False, "--yes", help="跳過寫入前的確認"),
) -> None:
    """修改既有站台的欄位（新增站台請用 redmine-mcp setup）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard
    from redmine_mcp.setup.external import run_command
    from redmine_mcp.setup.verify import verify_credentials

    raise typer.Exit(
        config_ops.set_site(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            site=site,
            url=url,
            description=description,
            clear_description=clear_description,
            ask_key=key,
            run=run_command,
            verify=verify_credentials,
            assume_yes=yes,
        )
    )


@config_app.command("remove-site")
def config_remove_site(
    site: str = typer.Argument(..., help="要移除的站台代號"),
    yes: bool = typer.Option(False, "--yes", help="跳過確認（警告仍會印出）"),
) -> None:
    """移除一個站台。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard
    from redmine_mcp.setup.external import run_command

    raise typer.Exit(
        config_ops.remove_site(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            site=site,
            run=run_command,
            assume_yes=yes,
        )
    )


@config_app.command("set-global")
def config_set_global(
    download_dir: str | None = typer.Option(None, "--download-dir", help="附件下載目錄"),
    upload_dir: str | None = typer.Option(None, "--upload-dir", help="允許上傳的來源目錄"),
    max_attachment_mb: int | None = typer.Option(
        None, "--max-attachment-mb", help="單一附件大小上限（MB，需大於 0）"
    ),
    clear: list[str] = typer.Option(
        [], "--clear", help="要移除的全域鍵，可重複指定"
    ),
    yes: bool = typer.Option(False, "--yes", help="跳過寫入前的確認"),
) -> None:
    """修改全域設定（各站台的預設值）。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import config_ops, wizard
    from redmine_mcp.setup.external import run_command

    raise typer.Exit(
        config_ops.set_global(
            wizard.console_prompts(),
            config_path=resolve_config_path(),
            download_dir=download_dir,
            upload_dir=upload_dir,
            max_attachment_mb=max_attachment_mb,
            clear=clear,
            run=run_command,
            assume_yes=yes,
        )
    )
```

若 mypy 對 `str | None` 形式的 Typer 參數有意見（部分版本要求 `Optional[str]`），改用 `typing.Optional` 並在檔頭 import；不要為此移除型別註記。

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 全數 PASS，且 `tests/test_cli.py::test_無參數啟動路徑不載入_typer` 仍然通過（新程式碼只在 `cli.py` 內，不影響 server 啟動路徑）

- [ ] **Step 5: 實機確認四個命令的說明畫面**

```bash
uv run redmine-mcp config --help
uv run redmine-mcp config set-site --help
```
Expected: 四個子命令都列在 `config --help`；`set-site --help` 顯示 `--url`、`--description`、`--clear-description`、`--key`、`--yes`

- [ ] **Step 6: Commit**

```bash
git add src/redmine_mcp/cli.py tests/test_cli.py
git commit -m "feat(redmine-mcp): 掛上 config 子命令群組"
```

---

### Task 9: 文件

**Files:**
- Modify: `SETUP.md`
- Modify: `README.md`
- Modify: `../README.md`（工具組根目錄）

- [ ] **Step 1: 在 SETUP.md 新增「4. 維護參數檔」**

插在現行「## 3. 註冊到 Claude Code」之後、「## 4. 更新到新版本」之前，內容：

````markdown
## 4. 維護參數檔

裝好之後要改設定，用 `config` 子命令，不必手改 TOML——它會在寫檔前驗證、自動重新收緊
檔案權限，改壞的內容不會落地。

```powershell
redmine-mcp config show                                   # 看現在設了什麼（金鑰只顯示末四碼）
redmine-mcp config set-site main --key                    # 輪換金鑰（值以不回顯方式詢問）
redmine-mcp config set-site main --url https://new/redmine
redmine-mcp config set-site main --description "公司正式站"
redmine-mcp config remove-site client-b                   # 刪站台
redmine-mcp config set-global --download-dir D:/redmine-dl
redmine-mcp config set-global --clear upload_dir           # 清掉某個全域鍵
```

幾件事先知道：

- **金鑰永遠不接受寫在指令裡**。`--key` 只表示「我要換」，值以不回顯的方式詢問——寫成
  參數會進入 shell history 與行程參數列表（同機其他使用者可用 `ps` 看到）。
- **改網址或換金鑰時會先打一次 Redmine 驗證**，通過才寫檔；只改說明不會付這次往返。
- **`set-site` 只改既有站台**。新增站台請用 `redmine-mcp setup`，它會一併處理全域設定
  與註冊。
- **不帶任何旗標時會逐項詢問**（Enter 保留原值、輸入 `-` 清空），適合不想記旗標的時候。
- **改完要重新連線才生效**：server 只在啟動時讀參數檔，在 Claude Code 用 `/mcp` 重連。
- **刪掉最後一個站台是允許的**，但會先警告 server 下次啟動會失敗。`--yes` 只跳過確認，
  不會跳過警告。
- 舊的扁平格式（`url`／`api_key` 寫在最外層）會被寫入類命令拒絕，請先跑一次
  `redmine-mcp setup` 轉成 `[sites.*]`。`config show` 不受此限，會照實顯示。
````

- [ ] **Step 2: 順移 SETUP.md 後續章節編號並修正錨點**

原「## 4. 更新到新版本」改為 5、「## 5. 工具與典型流程」改為 6、「## 6. 排錯」改為 7、
「## 7. 安全注意事項」改為 8。接著找出所有指向這些章節的內部連結一併修正：

```bash
grep -rn "4-更新到新版本\|5-工具與典型流程\|6-排錯\|7-安全注意事項" . ../README.md
```
每一處的數字都要對應新的編號（例如 `#6-排錯` → `#7-排錯`）。

- [ ] **Step 3: 更新 SETUP.md 第 2 節的指引**

第 2 節開頭現有那句「一般安裝不需要手動建立這個檔，`redmine-mcp setup` 會產生。本節是
欄位參考，供新增站台、輪換金鑰與排錯時查閱。」後面補一句：

```markdown
輪換金鑰、改網址、增刪站台與調整全域設定都有對應命令，見 [4. 維護參數檔](#4-維護參數檔)；
本節僅在你想直接編輯檔案時需要。
```

- [ ] **Step 4: 更新工具組 README**

在 `../README.md` 的「安裝操作指引」章節末尾（`llm-wiki-mcp` 那個引言區塊之前）補一段：

```markdown
裝好之後要改設定——輪換金鑰、換網址、增刪站台、調整下載目錄——用 `redmine-mcp config`
的子命令，不必手改 TOML：

```powershell
redmine-mcp config show                     # 看現在設了什麼（金鑰只顯示末四碼）
redmine-mcp config set-site main --key      # 輪換金鑰
redmine-mcp config remove-site client-b     # 刪站台
redmine-mcp config set-global --download-dir D:/redmine-dl
```

完整說明見 [redmine-mcp/SETUP.md](redmine-mcp/SETUP.md#4-維護參數檔)。
```

並在「新增站台」段落末尾補一句：

```markdown
刪站台用 `redmine-mcp config remove-site <代號>`；改既有站台的欄位用
`redmine-mcp config set-site <代號>`。
```

- [ ] **Step 5: 更新 redmine-mcp/README.md**

在「在 Claude Code 註冊」章節之後新增：

```markdown
## 維護參數檔

```powershell
redmine-mcp config show                                   # 現況（金鑰遮罩）
redmine-mcp config set-site main --key                    # 輪換金鑰
redmine-mcp config set-site main --url https://new/redmine
redmine-mcp config remove-site client-b
redmine-mcp config set-global --download-dir D:/redmine-dl
```

金鑰一律以不回顯的方式詢問，不接受寫在指令參數裡。改網址或換金鑰會先打一次 Redmine
驗證才寫檔。詳見 [SETUP.md](SETUP.md#4-維護參數檔)。
```

- [ ] **Step 6: 人工檢查清單**

- [ ] `grep -rn "#4-維護參數檔" . ../README.md` 指向的章節確實存在且編號相符
- [ ] SETUP.md 的章節編號連續（1–8），沒有重複或跳號
- [ ] 沒有任何文件宣稱金鑰可以寫成指令參數
- [ ] `uv run pytest -q && uv run ruff check . && uv run mypy src` 全綠

- [ ] **Step 7: Commit**

```bash
git add SETUP.md README.md ../README.md
git commit -m "docs(redmine-mcp): 補上 config 子命令的維護參數檔說明"
```

---

## 收尾

九個 Task 完成後：

1. 在合併結果上重跑 `uv run pytest -q`、`uv run ruff check .`、`uv run mypy src`
2. 實機跑一次四個命令（建議先 `REDMINE_CONFIG` 指到暫存路徑，避免動到自己的參數檔）：
   `config show`、`config set-site <代號> --description "測試"`、`config set-global --clear upload_dir`、
   `config remove-site <代號>`
3. 這是使用者可見的新功能，依既有流程發 `redmine-mcp` 0.5.0（版號、CHANGELOG／Release
   說明與 tag `redmine-mcp-v0.5.0`）
4. 用 superpowers:finishing-a-development-branch 決定如何整合分支
