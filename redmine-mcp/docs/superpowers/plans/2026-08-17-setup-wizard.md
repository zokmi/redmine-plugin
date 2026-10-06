# redmine-mcp 安裝精靈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `redmine-mcp setup` 一個指令完成參數檔建立、金鑰驗證、註冊到 Claude Code 與冒煙測試，把安裝收斂成「一行指令 + 一次問答」。

**Architecture:** CLI 以 **Typer** 建構，但 console script 進入點會先看 `sys.argv[1:]`——為空時**完全不 import typer**，直接以 stdio 啟動 server（見 Global Constraints 的啟動成本約束）；非空才交給 Typer app 處理子命令。精靈拆成五個各有單一職責的模組放在 `src/redmine_mcp/setup/` 底下：純函式的 TOML 區塊編輯、檔案原子寫入與權限、外部指令（`claude` CLI 與冒煙測試）、金鑰驗證、以及互動流程編排。所有外界相依（提問、遮蔽輸入、subprocess、Redmine HTTP）一律以參數注入，因此每一層都能在不打真實 API、不碰真實檔案系統的前提下測試。

**Tech Stack:** Python 3.11+、**Typer**（CLI 骨架）、**Rich**（精靈的終端輸出）、`tomllib`（標準庫，唯讀）、`httpx` + 既有 `CurlTransport`、pytest + pytest-asyncio。

**Spec:** [`docs/superpowers/specs/2026-08-17-setup-wizard-design.md`](../../../../docs/superpowers/specs/2026-08-17-setup-wizard-design.md)（在 repo 根目錄，非本子專案底下）

## Global Constraints

- **API key 絕不進 argv、不進 log、不進任何例外訊息。** 終端輸出一律只顯示末四碼。
- **新增的執行期相依是 `typer` 與 `rich`**。`rich` 本來就是 `typer` 的傳遞相依，但精靈**直接 import 它**，所以必須自己宣告——依賴傳遞相依是隱性耦合，`typer` 哪天改包裝就會斷。**不引入 `tomlkit`**——TOML 寫入用 Task 1 的區塊級文字替換。
- **`rich` 只能出現在 `console_prompts()` 這一個函式裡。** `run_setup()` 與其下的所有流程函式一律透過注入的 `Prompts` 輸出，不得直接 import rich——否則注入式測試就失去意義，且流程層會跟終端呈現綁死。
- **無參數啟動的行為與成本都必須完全不變**：`redmine-mcp` 不帶參數時直接以 stdio 啟動 server，且**不得 import typer**。理由是 MCP client 每次啟動 session 都會開一次本執行檔，而 SETUP.md 記錄 client 端連線 timeout 只有 30 秒、冷路徑曾實測 37% 斷線率；`rich` 的 import 成本沒有理由讓 server 啟動路徑去付。`"" | redmine-mcp` 的驗證方式不受影響。
- **註冊固定 `--scope user`**，不提供 `--scope project` 選項（會寫進進版控的 `.mcp.json`）。
- 全篇繁體中文：新增或修改的 helper、欄位、公開方法都要有中文說明；`ruff` 行寬 100。
- 站台代號規則不自行實作，一律沿用 `config.py` 的 `_SITE_NAME_PATTERN`（`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`）。
- commit 訊息不加單號前綴，直接 `type(scope): 主旨`。
- 每個 Task 結束前跑 `uv run pytest -q`、`uv run ruff check .`、`uv run mypy`，三者皆過才 commit。

## 範圍說明

本計畫**只涵蓋 redmine-mcp**。spec 同時規範 `llm-wiki-mcp`，但那邊零設定、精靈只有「註冊 → 冒煙測試 → 收尾」三步，與本計畫沒有共享程式碼（兩個套件刻意互不相依），且各自都能獨立交付可用的軟體。llm-wiki-mcp 的計畫在本計畫完成後另寫，屆時 CLI 骨架與輸出格式已被證實可用，可直接比照。

## File Structure

| 檔案 | 職責 |
| --- | --- |
| `src/redmine_mcp/setup/__init__.py` | 子套件標記，不放邏輯 |
| `src/redmine_mcp/setup/toml_edit.py` | **純函式**：TOML 值逸出、末四碼遮罩、參數檔型態判定、站台區塊定位／產生／插入替換、舊格式轉換 |
| `src/redmine_mcp/setup/fsops.py` | 參數檔的原子寫入與權限收緊 |
| `src/redmine_mcp/setup/external.py` | 外部指令：`claude` CLI 偵測與 mcp 註冊、執行檔解析、stdio 冒煙測試 |
| `src/redmine_mcp/setup/verify.py` | 寫檔前以真實 HTTP 驗證 url／api_key |
| `src/redmine_mcp/setup/wizard.py` | 互動流程編排；所有 IO 由 `Prompts` 注入 |
| `src/redmine_mcp/cli.py` | Typer app：定義 `setup` 子命令 |
| `src/redmine_mcp/__main__.py`（修改） | console script 進入點：無參數直接啟動 server，有參數才載入 Typer app |
| `pyproject.toml`（修改） | 加入 `typer` 相依、`[project.scripts]` 改指向新進入點 |
| `tests/test_setup_toml_edit.py` | Task 1 |
| `tests/test_setup_fsops.py` | Task 2 |
| `tests/test_setup_external.py` | Task 3 |
| `tests/test_setup_verify.py` | Task 4 |
| `tests/test_setup_wizard.py` | Task 5 |
| `tests/test_cli.py` | Task 6 |

拆成五個模組而非一個大檔，是因為只有 `wizard.py` 需要模擬互動，其餘三層都能用最直接的方式測試；混在一起會逼得每個測試都先組一套假 IO。

---

### Task 1: TOML 區塊編輯（純函式）

**Files:**
- Create: `src/redmine_mcp/setup/__init__.py`
- Create: `src/redmine_mcp/setup/toml_edit.py`
- Test: `tests/test_setup_toml_edit.py`

**Interfaces:**
- Consumes: `redmine_mcp.config.ConfigError`、`redmine_mcp.config_file.LEGACY_SITE_KEYS`
- Produces:
  - `escape_toml(value: str) -> str`
  - `mask_secret(value: str) -> str`
  - `classify(text: str) -> str`（回傳 `"empty"`／`"sites"`／`"legacy"`）
  - `render_site_block(site: str, url: str, api_key: str, description: str | None) -> str`
  - `find_site_block(text: str, site: str) -> tuple[int, int] | None`
  - `upsert_site_block(text: str, site: str, block: str) -> str`
  - `render_new_config(block: str, download_dir: str | None, upload_dir: str | None) -> str`
  - `convert_legacy(text: str) -> str`

- [ ] **Step 1: 建立子套件標記**

```python
# src/redmine_mcp/setup/__init__.py
"""安裝精靈：把參數檔建立、金鑰驗證與 Claude Code 註冊收斂成一個互動流程。

本子套件只在執行 `redmine-mcp setup` 時載入，不參與 MCP server 的啟動路徑。
"""
```

- [ ] **Step 2: 寫失敗的測試（逸出與遮罩）**

```python
# tests/test_setup_toml_edit.py
import pytest

from redmine_mcp.config import ConfigError
from redmine_mcp.setup.toml_edit import (
    classify,
    convert_legacy,
    escape_toml,
    find_site_block,
    mask_secret,
    render_new_config,
    render_site_block,
    upsert_site_block,
)


def test_逸出雙引號與反斜線():
    assert escape_toml(r'a"b\c') == r'a\"b\\c'


def test_逸出保持一般字元不變():
    assert escape_toml("https://redmine.example.com/redmine") == (
        "https://redmine.example.com/redmine"
    )


def test_遮罩只留末四碼():
    assert mask_secret("0123456789abcdef") == "****cdef"


def test_過短的金鑰整串遮掉():
    # 末四碼規則遇到短字串會等於全部露出，那比不遮更危險。
    assert mask_secret("abc") == "****"
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup'`

- [ ] **Step 4: 實作逸出與遮罩**

```python
# src/redmine_mcp/setup/toml_edit.py
"""參數檔的文字層級編輯。

全為純函式，不碰檔案系統，因此可直接以字串測試各種邊界。

刻意不引入 tomlkit：精靈只做兩種操作——在檔尾附加一個 [sites.X] 區塊，或替換掉
既有的某個區塊——其餘位元組原封不動即可保住使用者自己寫的註解與其他站台設定，
不必為了安裝流程讓執行期多背一個相依。
"""
from __future__ import annotations

import re
import tomllib

from redmine_mcp.config import ConfigError
from redmine_mcp.config_file import LEGACY_SITE_KEYS


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
```

- [ ] **Step 5: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: PASS（4 passed）

- [ ] **Step 6: 寫失敗的測試（參數檔型態判定）**

```python
# 追加到 tests/test_setup_toml_edit.py
def test_空字串判定為_empty():
    assert classify("") == "empty"


def test_只有全域鍵仍判定為_empty():
    # 有 download_dir 但沒有任何站台，仍屬「還沒設定過站台」。
    assert classify('download_dir = "D:/dl"\n') == "empty"


def test_有_sites_區塊判定為_sites():
    assert classify('[sites.main]\nurl = "https://a/b"\napi_key = "k"\n') == "sites"


def test_最外層有_api_key_判定為_legacy():
    assert classify('url = "https://a/b"\napi_key = "k"\n') == "legacy"


def test_語法錯誤直接拋出而不是猜測型態():
    with pytest.raises(ConfigError):
        classify("[sites.main\n")
```

- [ ] **Step 7: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: FAIL，`ImportError: cannot import name 'classify'`

- [ ] **Step 8: 實作型態判定**

```python
# 追加到 src/redmine_mcp/setup/toml_edit.py
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
```

- [ ] **Step 9: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: PASS（9 passed）

- [ ] **Step 10: 寫失敗的測試（區塊產生、定位、插入替換）**

```python
# 追加到 tests/test_setup_toml_edit.py
def test_產生站台區塊含選填說明():
    block = render_site_block("main", "https://a/b", "key1", "公司正式站")
    assert block == (
        "[sites.main]\n"
        'url         = "https://a/b"\n'
        'api_key     = "key1"\n'
        'description = "公司正式站"\n'
    )


def test_產生站台區塊省略未填的說明():
    block = render_site_block("main", "https://a/b", "key1", None)
    assert "description" not in block


def test_找不到區塊回傳_None():
    assert find_site_block('[sites.main]\nurl = "x"\n', "other") is None


def test_定位到檔中的區塊只涵蓋自己那段():
    text = '[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'
    start, end = find_site_block(text, "a")
    assert text[start:end] == '[sites.a]\nurl = "1"\n\n'


def test_定位到檔尾的區塊涵蓋到結尾():
    text = '[sites.a]\nurl = "1"\n[sites.b]\nurl = "2"\n'
    start, end = find_site_block(text, "b")
    assert text[start:end] == '[sites.b]\nurl = "2"\n'


def test_新站台附加在檔尾且保留原有註解():
    text = '# 我的註解\n[sites.a]\nurl = "1"\n'
    result = upsert_site_block(text, "b", '[sites.b]\nurl = "2"\n')
    assert result == '# 我的註解\n[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'


def test_附加時補上缺少的結尾換行():
    text = '[sites.a]\nurl = "1"'
    result = upsert_site_block(text, "b", '[sites.b]\nurl = "2"\n')
    assert result == '[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'


def test_取代既有區塊不動其他站台():
    text = '[sites.a]\nurl = "1"\n\n[sites.b]\nurl = "2"\n'
    result = upsert_site_block(text, "a", '[sites.a]\nurl = "NEW"\n')
    assert result == '[sites.a]\nurl = "NEW"\n\n[sites.b]\nurl = "2"\n'


def test_解析得到站台卻定位不到就拋出而非靜默附加():
    # 引號寫法 ["sites"."main"] tomllib 讀得到，但行首樣式比對不到。
    # 這時若靜默附加，檔案會出現重複站台而 server 啟動即失敗——寧可當場說清楚。
    text = '["sites"."main"]\nurl = "1"\n'
    with pytest.raises(ConfigError):
        upsert_site_block(text, "main", '[sites.main]\nurl = "2"\n')
```

- [ ] **Step 11: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: FAIL，`ImportError: cannot import name 'render_site_block'`

- [ ] **Step 12: 實作區塊產生、定位與插入替換**

```python
# 追加到 src/redmine_mcp/setup/toml_edit.py

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


def find_site_block(text: str, site: str) -> tuple[int, int] | None:
    """找出指定站台區塊在全文中的位置。

    區塊從自己的標頭開始，到下一個行首 `[` 之前為止（沒有下一個就到檔尾），
    因此區塊之間的空行會歸屬於前一個區塊，替換後版面不會塌掉。

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
        return match.start(), nxt.start() if nxt else len(text)
    return None


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
        return text[:start] + block + text[end:]

    if site in (tomllib.loads(text).get("sites") or {}):
        raise ConfigError(
            f"參數檔已有站台 {site}，但它不是以 [sites.{site}] 的標準寫法撰寫，"
            "精靈無法安全改寫。請手動調整成標準寫法後重跑，或改用其他站台代號"
        )

    if not text:
        return block
    prefix = text if text.endswith("\n") else text + "\n"
    return prefix + "\n" + block
```

- [ ] **Step 13: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: PASS（18 passed）

- [ ] **Step 14: 寫失敗的測試（新檔產生與舊格式轉換）**

```python
# 追加到 tests/test_setup_toml_edit.py
def test_新檔含說明註解與站台區塊():
    text = render_new_config('[sites.main]\nurl = "https://a/b"\n', None, None)
    assert text.startswith("#")
    assert '[sites.main]' in text
    assert "download_dir" not in text


def test_新檔帶下載與上傳目錄():
    text = render_new_config("[sites.main]\n", "D:/dl", "D:/up")
    assert 'download_dir = "D:/dl"' in text
    assert 'upload_dir   = "D:/up"' in text


def test_舊格式轉成_sites_default_並保留全域鍵():
    text = 'download_dir = "D:/dl"\nurl = "https://a/b"\napi_key = "k"\n'
    result = convert_legacy(text)
    assert classify(result) == "sites"
    assert 'download_dir = "D:/dl"' in result
    assert "[sites.default]" in result
    assert 'api_key     = "k"' in result


def test_轉換保留原有的說明():
    text = 'url = "https://a/b"\napi_key = "k"\ndescription = "舊站"\n'
    assert 'description = "舊站"' in convert_legacy(text)


def test_不是舊格式就拒絕轉換():
    with pytest.raises(ConfigError):
        convert_legacy('[sites.main]\nurl = "x"\n')
```

- [ ] **Step 15: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_toml_edit.py -q`
Expected: FAIL，`ImportError: cannot import name 'render_new_config'`

- [ ] **Step 16: 實作新檔產生與舊格式轉換**

```python
# 追加到 src/redmine_mcp/setup/toml_edit.py

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
```

- [ ] **Step 17: 執行完整檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過（原有 335 個測試 + 本 Task 新增的 23 個）

- [ ] **Step 18: Commit**

```bash
git add src/redmine_mcp/setup/__init__.py src/redmine_mcp/setup/toml_edit.py tests/test_setup_toml_edit.py
git commit -m "feat(redmine-mcp): 安裝精靈的 TOML 區塊編輯純函式"
```

---

### Task 2: 參數檔的原子寫入與權限收緊

**Files:**
- Create: `src/redmine_mcp/setup/fsops.py`
- Test: `tests/test_setup_fsops.py`

**Interfaces:**
- Consumes: 無（只用標準庫）
- Produces:
  - `Runner`（型別別名：`Callable[[list[str]], int]`，回傳 exit code）
  - `write_config(path: Path, content: str) -> None`
  - `tighten_permissions(path: Path, run: Runner) -> str | None`（成功回 None，失敗回可顯示的原因）

- [ ] **Step 1: 寫失敗的測試**

```python
# tests/test_setup_fsops.py
import sys

import pytest

from redmine_mcp.setup.fsops import tighten_permissions, write_config


def test_寫入新檔並建立父目錄(tmp_path):
    target = tmp_path / "nested" / "config.toml"
    write_config(target, "url = 1\n")
    assert target.read_text(encoding="utf-8") == "url = 1\n"


def test_覆寫既有檔不留暫存檔(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("old\n", encoding="utf-8")
    write_config(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert list(tmp_path.iterdir()) == [target]


def test_寫入失敗時原檔不變且不留暫存檔(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_text("old\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        write_config(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.iterdir()) == [target]


def test_權限收緊成功時回傳_None(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")
    assert tighten_permissions(target, lambda cmd: 0) is None


def test_權限收緊失敗回傳原因而不是拋出(tmp_path):
    # 權限收緊失敗不該讓整個安裝前功盡棄——檔案已經寫好了，
    # 回傳原因讓精靈提醒使用者自己處理即可。
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")
    reason = tighten_permissions(target, lambda cmd: 5)
    assert reason is not None
    assert "5" in reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 才走 chmod")
def test_posix_直接改權限不呼叫外部指令(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("x\n", encoding="utf-8")

    def 不該被呼叫(cmd):
        raise AssertionError("POSIX 不應呼叫外部指令")

    assert tighten_permissions(target, 不該被呼叫) is None
    assert oct(target.stat().st_mode)[-3:] == "600"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_fsops.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.fsops'`

- [ ] **Step 3: 實作**

```python
# src/redmine_mcp/setup/fsops.py
"""參數檔的落地：原子寫入與權限收緊。

寫入採「同目錄暫存檔 + os.replace」而非直接覆寫：中途失敗或 Ctrl-C 時，
原本的參數檔（裡面是還能用的金鑰）不會被截斷成半個檔。暫存檔必須與目標
同目錄，跨磁碟的 os.replace 在 Windows 上不是原子操作。
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

#: 執行外部指令的介面：收指令與參數，回傳 exit code。測試以假函式注入。
Runner = Callable[[list[str]], int]


def write_config(path: Path, content: str) -> None:
    """以原子替換的方式寫入參數檔。

    參數:
        path: 目標路徑；父目錄不存在時會一併建立。
        content: 完整檔案內容。
    例外:
        OSError: 建立目錄、寫入暫存檔或替換失敗；此時原檔維持不變且不留暫存檔。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def tighten_permissions(path: Path, run: Runner) -> str | None:
    """把參數檔權限收到只有本人可讀。

    失敗不拋出：檔案此時已經寫好且可用，為了權限沒設成而讓整個安裝失敗並不划算。
    回傳原因讓精靈提醒使用者自行處理即可。

    參數:
        path: 參數檔路徑。
        run: 執行外部指令的函式（Windows 才會用到）。
    回傳:
        成功時為 None；失敗時為可直接顯示給使用者的原因字串。
    """
    if sys.platform != "win32":
        try:
            path.chmod(0o600)
        except OSError as exc:
            return f"chmod 失敗：{exc.strerror or exc}"
        return None

    user = os.environ.get("USERNAME") or ""
    if not user:
        return "找不到 USERNAME 環境變數，無法組出 icacls 指令"
    code = run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R)"])
    return None if code == 0 else f"icacls 以 exit code {code} 結束"
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_fsops.py -q`
Expected: PASS

- [ ] **Step 5: 執行完整檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 6: Commit**

```bash
git add src/redmine_mcp/setup/fsops.py tests/test_setup_fsops.py
git commit -m "feat(redmine-mcp): 安裝精靈的參數檔原子寫入與權限收緊"
```

---

### Task 3: 外部指令（claude CLI 註冊與冒煙測試）

**Files:**
- Create: `src/redmine_mcp/setup/external.py`
- Test: `tests/test_setup_external.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `CommandResult`（dataclass：`code: int`、`stdout: str`、`stderr: str`）
  - `CommandRunner`（型別別名：`Callable[[list[str], str | None], CommandResult]`，第二參數為 stdin 內容）
  - `run_command(argv: list[str], stdin: str | None = None) -> CommandResult`（正式用實作）
  - `find_claude() -> str | None`
  - `resolve_executable(tool: str) -> str`
  - `is_registered(name: str, run: CommandRunner) -> bool`
  - `register(name: str, command: str, run: CommandRunner) -> CommandResult`
  - `unregister(name: str, run: CommandRunner) -> CommandResult`
  - `smoke_test(command: str, run: CommandRunner) -> tuple[bool, str]`

- [ ] **Step 1: 寫失敗的測試**

```python
# tests/test_setup_external.py
from redmine_mcp.setup.external import (
    CommandResult,
    is_registered,
    register,
    resolve_executable,
    smoke_test,
    unregister,
)


class 假執行器:
    """記錄呼叫並回傳預先安排的結果。"""

    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv: list[str], stdin: str | None = None) -> CommandResult:
        self.calls.append((argv, stdin))
        return self.results.pop(0) if self.results else CommandResult(0, "", "")


def test_註冊指令固定使用_user_scope():
    run = 假執行器()
    register("redmine", "redmine-mcp", run)
    assert run.calls[0][0] == [
        "claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp",
    ]


def test_註冊時金鑰不會出現在指令中():
    # 這是安全底線：金鑰只存在參數檔，永遠不進 argv。
    run = 假執行器()
    register("redmine", "redmine-mcp", run)
    assert all("api" not in part.lower() for part in run.calls[0][0])


def test_已註冊時_mcp_get_回傳零():
    run = 假執行器(CommandResult(0, "redmine:\n  Type: stdio\n", ""))
    assert is_registered("redmine", run) is True


def test_未註冊時_mcp_get_回傳非零():
    run = 假執行器(CommandResult(1, "", "No MCP server found"))
    assert is_registered("redmine", run) is False


def test_取消註冊帶_user_scope():
    run = 假執行器()
    unregister("redmine", run)
    assert run.calls[0][0] == ["claude", "mcp", "remove", "redmine", "--scope", "user"]


def test_冒煙測試以空_stdin_呼叫並看啟動訊息():
    run = 假執行器(CommandResult(0, "", "INFO Redmine MCP server 啟動，站台：main\n"))
    ok, message = smoke_test("redmine-mcp", run)
    assert ok is True
    assert "啟動" in message
    assert run.calls[0] == (["redmine-mcp"], "")


def test_冒煙測試失敗時回傳_stderr_供排錯():
    run = 假執行器(CommandResult(1, "", "設定錯誤：缺少必填設定 api_key\n"))
    ok, message = smoke_test("redmine-mcp", run)
    assert ok is False
    assert "api_key" in message


def test_冒煙測試_exit_零但沒有啟動訊息仍算失敗():
    run = 假執行器(CommandResult(0, "", ""))
    ok, _ = smoke_test("redmine-mcp", run)
    assert ok is False


def test_執行檔不在_PATH_時回傳工具名稱本身(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert resolve_executable("redmine-mcp") == "redmine-mcp"


def test_執行檔在_PATH_時回傳工具名稱而非絕對路徑(monkeypatch):
    # 用名稱註冊比絕對路徑可攜：日後重裝路徑變了也不必重註冊。
    monkeypatch.setattr("shutil.which", lambda name: "C:/x/redmine-mcp.exe")
    assert resolve_executable("redmine-mcp") == "redmine-mcp"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_external.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.external'`

- [ ] **Step 3: 實作**

```python
# src/redmine_mcp/setup/external.py
"""外部指令：Claude Code CLI 的註冊操作與 stdio 冒煙測試。

所有呼叫都經由注入的 CommandRunner，測試因此不必真的開子行程；正式執行時
由 run_command() 提供實作。金鑰永遠不會出現在這裡的任何 argv 中——設定一律
只存在參數檔，這是本專案的安全底線。
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

#: 冒煙測試判定成功的關鍵字，對應 __main__.py 啟動時的 log 訊息。
_STARTED_MARKER = "啟動"


@dataclass(frozen=True)
class CommandResult:
    """外部指令的執行結果。

    欄位:
        code: exit code。
        stdout: 標準輸出全文。
        stderr: 標準錯誤全文。
    """

    code: int
    stdout: str
    stderr: str


#: 執行外部指令的介面：收 argv 與可選的 stdin 內容，回傳結果。
CommandRunner = Callable[..., CommandResult]


def run_command(argv: list[str], stdin: str | None = None) -> CommandResult:
    """實際開子行程執行指令。

    參數:
        argv: 指令與參數。
        stdin: 要餵給該行程的標準輸入；None 代表不餵。
    回傳:
        執行結果；找不到執行檔時以 code 127 表示，不拋出。
    """
    try:
        completed = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"找不到執行檔：{argv[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"{argv[0]} 執行逾時")
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def find_claude() -> str | None:
    """找出 Claude Code CLI 的位置。

    回傳:
        找得到時為執行檔路徑，否則 None（精靈改為印出指令請使用者自行執行）。
    """
    return shutil.which("claude")


def resolve_executable(tool: str) -> str:
    """決定要註冊給 Claude Code 的指令。

    一律回傳工具名稱本身：名稱比絕對路徑可攜，日後重裝或換路徑都不必重註冊。
    呼叫端應在註冊前先用 find 檢查它是否在 PATH 上，不在就提醒使用者。

    參數:
        tool: 工具名稱，例如 redmine-mcp。
    回傳:
        要寫進 Claude Code 設定的指令字串。
    """
    return tool


def is_registered(name: str, run: CommandRunner) -> bool:
    """檢查該名稱是否已註冊到 Claude Code。

    參數:
        name: MCP server 在 Claude Code 中的名稱。
        run: 指令執行器。
    回傳:
        已註冊為 True。
    """
    return run(["claude", "mcp", "get", name]).code == 0


def register(name: str, command: str, run: CommandRunner) -> CommandResult:
    """把 MCP server 註冊到 Claude Code。

    scope 固定為 user：project scope 會寫進進版控的 .mcp.json，本專案明文禁止，
    因此不開放成選項。

    參數:
        name: 要註冊的名稱。
        command: 啟動指令。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["claude", "mcp", "add", name, "--scope", "user", "--", command])


def unregister(name: str, run: CommandRunner) -> CommandResult:
    """移除既有註冊，供覆蓋流程使用。

    參數:
        name: 要移除的名稱。
        run: 指令執行器。
    回傳:
        執行結果。
    """
    return run(["claude", "mcp", "remove", name, "--scope", "user"])


def smoke_test(command: str, run: CommandRunner) -> tuple[bool, str]:
    """以空 stdin 跑一次 server，確認它起得來。

    這是 stdio server：沒有輸入會停在那裡等待，因此必須餵空字串讓它收到 EOF
    後自行結束。判定條件是 exit code 為 0 **且** stderr 出現啟動訊息——只看
    exit code 會把「什麼都沒做就結束」誤判為成功。

    參數:
        command: 啟動指令。
        run: 指令執行器。
    回傳:
        `(是否成功, 可顯示給使用者的訊息)`。失敗時訊息取 stderr，供對照排錯表。
    """
    result = run([command], "")
    output = (result.stderr or result.stdout).strip()
    if result.code == 0 and _STARTED_MARKER in output:
        return True, output
    return False, output or f"指令以 exit code {result.code} 結束，且沒有任何輸出"
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_external.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: 執行完整檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 6: Commit**

```bash
git add src/redmine_mcp/setup/external.py tests/test_setup_external.py
git commit -m "feat(redmine-mcp): 安裝精靈的 Claude Code 註冊與冒煙測試"
```

---

### Task 4: 寫檔前的金鑰驗證

**Files:**
- Create: `src/redmine_mcp/setup/verify.py`
- Test: `tests/test_setup_verify.py`

**Interfaces:**
- Consumes: `redmine_mcp.config.SiteSettings`、`redmine_mcp.client.RedmineClient`、`redmine_mcp.errors.*`
- Produces: `async def verify_credentials(url: str, api_key: str, transport=None) -> str`（回傳登入帳號；失敗拋 `RedmineError` 子類）

**測試慣例：** 本專案 `pytest.ini_options` 已設 `asyncio_mode = "auto"`，async 測試不必加 decorator；假傳輸層沿用既有測試中的 `httpx.MockTransport` 用法。

- [ ] **Step 1: 寫失敗的測試**

```python
# tests/test_setup_verify.py
import httpx
import pytest

from redmine_mcp.errors import RedmineAuthError, RedmineError
from redmine_mcp.setup.verify import verify_credentials


def _transport(status: int, body: dict | None = None, text: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text, headers={"content-type": "text/html"})
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


async def test_驗證成功回傳登入帳號():
    transport = _transport(200, {"user": {"id": 7, "login": "kenny"}})
    assert await verify_credentials("https://a/redmine", "key", transport) == "kenny"


async def test_金鑰錯誤拋出認證錯誤():
    transport = _transport(401, {})
    with pytest.raises(RedmineAuthError):
        await verify_credentials("https://a/redmine", "bad", transport)


async def test_回應不是_JSON_時錯誤訊息提示網址可能少了子路徑():
    # 這正是 SETUP.md 排錯表的頭號情況：url 少了 /redmine 會拿到 HTML。
    transport = _transport(200, text="<html>login page</html>")
    with pytest.raises(RedmineError) as exc:
        await verify_credentials("https://a", "key", transport)
    assert "子路徑" in str(exc.value)


async def test_回應缺少_user_欄位視為失敗():
    transport = _transport(200, {})
    with pytest.raises(RedmineError):
        await verify_credentials("https://a/redmine", "key", transport)


async def test_錯誤訊息不含金鑰():
    transport = _transport(401, {})
    with pytest.raises(RedmineError) as exc:
        await verify_credentials("https://a/redmine", "super-secret-key", transport)
    assert "super-secret-key" not in str(exc.value)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_verify.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.verify'`

- [ ] **Step 3: 實作**

```python
# src/redmine_mcp/setup/verify.py
"""寫檔前先驗一次金鑰。

這是精靈價值最高的一步：把「裝完、重開 Claude Code、呼叫工具才發現網址少了
/redmine」提前到還能立刻重填的時候。走的是與 get_current_user 完全相同的路徑，
因此這裡通過就代表實際使用時也會通過。
"""
from __future__ import annotations

from typing import Any

import httpx

from redmine_mcp.client import RedmineClient
from redmine_mcp.config import SiteSettings
from redmine_mcp.errors import RedmineError


async def verify_credentials(
    url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
) -> str:
    """以 /users/current.json 驗證站台網址與金鑰。

    參數:
        url: 站台根位址。
        api_key: 該站台的 API key。
        transport: 測試用的替代傳輸層；正式執行時留空，會走既有的 CurlTransport。
    回傳:
        該金鑰對應的登入帳號（login）。
    例外:
        RedmineError 及其子類：認證失敗、連線失敗、回應不是 JSON 等。訊息一律
            不含金鑰內容。
    """
    settings = SiteSettings(
        name="setup",
        url=url.rstrip("/"),
        api_key=api_key,
        description=None,
        download_dir=None,
        upload_dir=None,
        max_attachment_bytes=0,
    )
    client = RedmineClient(settings, transport=transport)
    try:
        raw: dict[str, Any] = await client.get("/users/current.json")
    except RedmineError as exc:
        # client 已針對「回應不是 JSON」給出提示，這裡補上最常見的成因，
        # 讓使用者不必翻排錯表就知道要檢查什麼。
        raise type(exc)(
            f"{exc}（最常見的原因是站台網址少了子路徑，"
            "例如應為 https://redmine.example.com/redmine 而非只有網域）"
        ) from exc
    finally:
        await client.aclose()

    login = (raw.get("user") or {}).get("login")
    if not login:
        raise RedmineError(
            "Redmine 有回應，但內容不是預期的使用者資料；"
            "請確認網址指向的是 Redmine 本身而非入口頁或反向代理"
        )
    return str(login)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_verify.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 執行完整檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 6: Commit**

```bash
git add src/redmine_mcp/setup/verify.py tests/test_setup_verify.py
git commit -m "feat(redmine-mcp): 安裝精靈在寫檔前驗證站台網址與金鑰"
```

---

### Task 5: 互動流程編排

**Files:**
- Create: `src/redmine_mcp/setup/wizard.py`
- Test: `tests/test_setup_wizard.py`

**Interfaces:**
- Consumes: Task 1–4 的全部公開函式、`redmine_mcp.config_file.resolve_config_path`、`redmine_mcp.config._SITE_NAME_PATTERN`（經由下方 `validate_site_name` 包裝，不直接散用私有名稱）
- Produces:
  - `Prompts`（dataclass，欄位：`say`、`ask`、`ask_secret`、`confirm`、`choose`）
  - `console_prompts() -> Prompts`
  - `validate_site_name(name: str) -> str | None`（合法回 None，否則回錯誤說明）
  - `run_setup(prompts: Prompts, *, config_path: Path, run: CommandRunner, verify: Verifier) -> int`

- [ ] **Step 1: 寫失敗的測試（快樂路徑與取消）**

```python
# tests/test_setup_wizard.py
from pathlib import Path

from redmine_mcp.setup.external import CommandResult
from redmine_mcp.setup.wizard import Prompts, run_setup, validate_site_name


class 腳本化提問:
    """依序回答預先排好的答案，並記錄所有輸出。"""

    def __init__(self, answers: list, confirms: list[bool], choices: list[int]) -> None:
        self.answers = list(answers)
        self.confirms = list(confirms)
        self.choices = list(choices)
        self.said: list[str] = []

    def _panel(self, title: str, lines) -> None:
        # 面板內容也要進 said：金鑰遮罩的斷言必須看得到預覽區塊。
        self.said.append(title)
        self.said.extend(lines)

    def build(self) -> Prompts:
        return Prompts(
            say=self.said.append,
            panel=self._panel,
            ask=lambda label, default=None: self.answers.pop(0),
            ask_secret=lambda label: self.answers.pop(0),
            confirm=lambda label: self.confirms.pop(0),
            choose=lambda label, options: self.choices.pop(0),
        )


async def _成功驗證(url: str, api_key: str) -> str:
    return "kenny"


def test_全新安裝寫出參數檔並註冊(tmp_path):
    target = tmp_path / "config.toml"
    script = 腳本化提問(
        answers=["main", "https://a/redmine", "secret-key-1234", "", "", ""],
        confirms=[True, True, True],   # 確認路徑、確認寫入、確認註冊
        choices=[],
    )
    calls: list[list[str]] = []

    def run(argv, stdin=None):
        calls.append(argv)
        if argv[:3] == ["claude", "mcp", "get"]:
            return CommandResult(1, "", "not found")
        if argv[0] == "redmine-mcp":
            return CommandResult(0, "", "INFO Redmine MCP server 啟動，站台：main\n")
        return CommandResult(0, "", "")

    code = run_setup(script.build(), config_path=target, run=run, verify=_成功驗證)

    assert code == 0
    content = target.read_text(encoding="utf-8")
    assert "[sites.main]" in content
    assert 'api_key     = "secret-key-1234"' in content
    assert ["claude", "mcp", "add", "redmine", "--scope", "user", "--", "redmine-mcp"] in calls


def test_金鑰只以末四碼回顯(tmp_path):
    target = tmp_path / "config.toml"
    script = 腳本化提問(
        answers=["main", "https://a/redmine", "secret-key-1234", "", "", ""],
        confirms=[True, True, False],
        choices=[],
    )
    run_setup(
        script.build(),
        config_path=target,
        run=lambda argv, stdin=None: CommandResult(1, "", ""),
        verify=_成功驗證,
    )
    # said 同時收集 say() 與 panel() 的內容，因此預覽面板裡的金鑰也在檢查範圍內。
    全部輸出 = "\n".join(script.said)
    assert "secret-key-1234" not in 全部輸出
    assert "****1234" in 全部輸出


def test_不確認寫入就不建立檔案(tmp_path):
    target = tmp_path / "config.toml"
    script = 腳本化提問(
        answers=["main", "https://a/redmine", "secret-key-1234", "", "", ""],
        confirms=[True, False],
        choices=[],
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=lambda argv, stdin=None: CommandResult(0, "", ""),
        verify=_成功驗證,
    )
    assert code == 1
    assert not target.exists()


def test_不確認寫入路徑就直接結束(tmp_path):
    target = tmp_path / "config.toml"
    script = 腳本化提問(answers=[], confirms=[False], choices=[])
    code = run_setup(
        script.build(),
        config_path=target,
        run=lambda argv, stdin=None: CommandResult(0, "", ""),
        verify=_成功驗證,
    )
    assert code == 1
    assert not target.exists()


def test_站台代號規則沿用_config_的驗證():
    assert validate_site_name("main") is None
    assert validate_site_name("中文站") is not None
    assert validate_site_name("-bad") is not None
    assert validate_site_name("") is not None
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_setup_wizard.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.setup.wizard'`

- [ ] **Step 3: 實作 Prompts 與代號驗證**

```python
# src/redmine_mcp/setup/wizard.py
"""互動式安裝精靈的流程編排。

所有 IO（提問、遮蔽輸入、確認、選擇、輸出）都由 Prompts 注入，外部指令與
Redmine 驗證同樣以參數傳入，因此整個流程可在不碰終端機、不打真實 API 的
前提下逐分支測試。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redmine_mcp.config import ConfigError, _SITE_NAME_PATTERN
from redmine_mcp.errors import RedmineError
from redmine_mcp.setup import toml_edit
from redmine_mcp.setup.external import CommandRunner, is_registered, register, resolve_executable
from redmine_mcp.setup.external import smoke_test, unregister
from redmine_mcp.setup.fsops import tighten_permissions, write_config

#: 驗證函式的介面：收 url 與 api_key，成功回傳登入帳號，失敗拋 RedmineError。
Verifier = Callable[[str, str], Coroutine[Any, Any, str]]

#: 註冊到 Claude Code 時使用的名稱與執行檔名。
_SERVER_NAME = "redmine"
_TOOL = "redmine-mcp"


@dataclass(frozen=True)
class Prompts:
    """精靈與使用者互動的全部管道。

    欄位:
        say: 輸出一行訊息。
        panel: 輸出一組帶標題的訊息（正式實作會畫成 Rich 面板）。
        ask: 提問並取得文字答案；第二參數為預設值，使用者直接按 Enter 時採用。
        ask_secret: 提問並取得不回顯的答案，供輸入 API key。
        confirm: 是／否確認，回傳 True 代表同意。
        choose: 從選項中擇一，回傳選中的索引。
    """

    say: Callable[[str], None]
    panel: Callable[[str, Sequence[str]], None]
    ask: Callable[..., str]
    ask_secret: Callable[[str], str]
    confirm: Callable[[str], bool]
    choose: Callable[[str, Sequence[str]], int]


def console_prompts() -> Prompts:
    """建立實際對終端機互動的 Prompts。

    提問用 Typer（它處理好了預設值顯示、確認語句，以及 Ctrl-C 時丟出 Abort），
    輸出用 Rich（樣式與面板）。

    **rich 與 typer 的 import 只出現在這裡**：一是它們不該落在 server 的啟動路徑上，
    二是流程層（run_setup 及其下）一律透過注入的 Prompts 輸出，不知道終端長什麼樣，
    這樣測試才能餵假 IO 逐分支驗證。

    回傳:
        以 typer.prompt()／rich.Console 實作的 Prompts。
    """
    import typer
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    def panel(title: str, lines: Sequence[str]) -> None:
        console.print(Panel("\n".join(lines), title=title, title_align="left"))

    def ask(label: str, default: str | None = None) -> str:
        answer = typer.prompt(label, default=default if default is not None else "")
        return str(answer).strip()

    def choose(label: str, options: Sequence[str]) -> int:
        console.print(label)
        for index, option in enumerate(options, start=1):
            console.print(f"  [bold]{index}[/bold]. {option}")
        while True:
            raw = typer.prompt("請輸入編號").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            console.print(f"[yellow]請輸入 1 到 {len(options)} 之間的編號。[/yellow]")

    return Prompts(
        say=console.print,
        panel=panel,
        ask=ask,
        ask_secret=lambda label: typer.prompt(label, hide_input=True),
        confirm=lambda label: typer.confirm(label),
        choose=choose,
    )


def validate_site_name(name: str) -> str | None:
    """檢查站台代號是否合法。

    規則沿用 config.py 的 _SITE_NAME_PATTERN，不另寫一套——兩份規則遲早會分歧，
    而分歧的結果是精靈寫出 server 讀不了的參數檔。

    參數:
        name: 使用者輸入的代號。
    回傳:
        合法時為 None；否則為可直接顯示的錯誤說明。
    """
    if _SITE_NAME_PATTERN.match(name):
        return None
    return "代號只能用英文、數字、減號與底線，需以英數開頭且長度 1–64"
```

- [ ] **Step 4: 實作主流程**

```python
# 追加到 src/redmine_mcp/setup/wizard.py

def _ask_site_name(prompts: Prompts, taken: Sequence[str]) -> str | None:
    """反覆詢問直到取得合法且未被占用的站台代號。

    參數:
        prompts: 互動管道。
        taken: 已存在且本次不打算取代的代號。
    回傳:
        代號；使用者連續留白視為放棄時回傳 None。
    """
    for _ in range(5):
        name = prompts.ask("站台代號", "main")
        problem = validate_site_name(name)
        if problem:
            prompts.say(f"[red]×[/red] {problem}")
            continue
        if name in taken:
            prompts.say(f"[red]×[/red] 代號 {name} 已存在，請換一個（要覆蓋請重跑並選「取代」）")
            continue
        return name
    return None


def _ask_credentials(prompts: Prompts, verify: Verifier) -> tuple[str, str] | None:
    """詢問網址與金鑰，並在通過驗證前不放行。

    參數:
        prompts: 互動管道。
        verify: 驗證函式。
    回傳:
        `(url, api_key)`；使用者放棄重填時回傳 None。
    """
    while True:
        url = prompts.ask("站台網址（要含子路徑，例如 https://redmine.example.com/redmine）")
        api_key = prompts.ask_secret("API 存取金鑰")
        if not url or not api_key:
            prompts.say("[red]×[/red] 網址與金鑰都不能留白。")
            if not prompts.confirm("要重新輸入嗎？"):
                return None
            continue
        prompts.say("正在驗證金鑰…")
        try:
            login = asyncio.run(verify(url, api_key))
        except RedmineError as exc:
            prompts.say(f"[red]×[/red] 驗證失敗：{exc}")
            if not prompts.confirm("要重新輸入嗎？"):
                return None
            continue
        prompts.say(f"[green]✓[/green] 驗證通過，這把金鑰對應的帳號是 {login}")
        return url, api_key


def run_setup(
    prompts: Prompts,
    *,
    config_path: Path,
    run: CommandRunner,
    verify: Verifier,
) -> int:
    """執行整個安裝流程。

    參數:
        prompts: 互動管道。
        config_path: 參數檔目標路徑，由呼叫端以 resolve_config_path() 決定。
        run: 外部指令執行器。
        verify: 金鑰驗證函式。
    回傳:
        行程結束碼；0 代表流程走完（註冊可被使用者跳過，仍算成功）。
    """
    prompts.say("[bold]redmine-mcp 安裝精靈[/bold]")
    prompts.say(f"參數檔將寫入：{config_path}")
    if not prompts.confirm("這個位置正確嗎？"):
        prompts.say("已取消。要換位置可先設定 REDMINE_CONFIG 環境變數後重跑。")
        return 1

    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    try:
        kind = toml_edit.classify(existing)
    except ConfigError as exc:
        prompts.say(f"× {exc}")
        return 1

    if kind == "legacy":
        prompts.say("偵測到舊的扁平格式（url／api_key 直接寫在最外層）。")
        prompts.say("新舊格式混用會讓 server 啟動失敗，必須先轉成 [sites.default]。")
        prompts.say("注意：轉換會重新產生整個檔案，原有的註解不會保留。")
        if not prompts.confirm("要現在轉換嗎？"):
            prompts.say("已取消，參數檔未被修改。")
            return 1
        existing = toml_edit.convert_legacy(existing)
        kind = "sites"

    taken: list[str] = []
    if kind == "sites":
        import tomllib

        taken = sorted((tomllib.loads(existing).get("sites") or {}).keys())
        prompts.say(f"現有站台：{'、'.join(taken)}")
        action = prompts.choose(
            "要做什麼？", ["新增一個站台", "取代其中一個站台", "取消"]
        )
        if action == 2:
            prompts.say("已取消，參數檔未被修改。")
            return 1
        if action == 1:
            index = prompts.choose("要取代哪一個？", taken)
            site = taken[index]
            taken = []
        else:
            picked = _ask_site_name(prompts, taken)
            if picked is None:
                prompts.say("已取消。")
                return 1
            site = picked
    else:
        picked = _ask_site_name(prompts, [])
        if picked is None:
            prompts.say("已取消。")
            return 1
        site = picked

    credentials = _ask_credentials(prompts, verify)
    if credentials is None:
        prompts.say("已取消，參數檔未被修改。")
        return 1
    url, api_key = credentials

    description = prompts.ask("站台說明（選填，直接 Enter 略過）") or None
    download_dir = prompts.ask("附件下載目錄（選填，留白則停用附件下載）") or None
    upload_dir = prompts.ask("允許上傳的來源目錄（選填，留白則沿用下載目錄）") or None

    block = toml_edit.render_site_block(site, url, api_key, description)
    if existing:
        content = toml_edit.upsert_site_block(existing, site, block)
    else:
        content = toml_edit.render_new_config(block, download_dir, upload_dir)

    preview = [
        f"站台代號：{site}",
        f"網址：{url}",
        f"金鑰：{toml_edit.mask_secret(api_key)}",
    ]
    if description:
        preview.append(f"說明：{description}")
    prompts.panel("即將寫入", preview)
    if not prompts.confirm("確認寫入嗎？"):
        prompts.say("已取消，參數檔未被修改。")
        return 1

    write_config(config_path, content)
    prompts.say(f"[green]✓[/green] 已寫入 {config_path}")
    problem = tighten_permissions(config_path, lambda argv: run(argv).code)
    if problem:
        prompts.say(
            f"[yellow]![/yellow] 權限收緊失敗（{problem}）。"
            "這個檔案含金鑰，請自行確認只有你讀得到。"
        )
    else:
        prompts.say("[green]✓[/green] 已收緊檔案權限")

    _do_register(prompts, run)
    _finish(prompts, config_path, site, run)
    return 0
```

- [ ] **Step 5: 實作註冊與收尾**

```python
# 追加到 src/redmine_mcp/setup/wizard.py

def _do_register(prompts: Prompts, run: CommandRunner) -> None:
    """把 server 註冊到 Claude Code；找不到 CLI 或使用者拒絕時改為印出指令。

    參數:
        prompts: 互動管道。
        run: 外部指令執行器。
    """
    command = resolve_executable(_TOOL)
    manual = f"claude mcp add {_SERVER_NAME} --scope user -- {command}"

    if run(["claude", "--version"]).code != 0:
        prompts.say("找不到 claude CLI（裝完 Claude Code 後要開新的終端機才會進 PATH）。")
        prompts.say(f"請自行執行：{manual}")
        return

    if is_registered(_SERVER_NAME, run):
        prompts.say(f"Claude Code 已經註冊過 {_SERVER_NAME}。")
        if not prompts.confirm("要用這次的設定覆蓋嗎？"):
            prompts.say("保留原有註冊。")
            return
        unregister(_SERVER_NAME, run)

    if not prompts.confirm(f"要執行「{manual}」嗎？"):
        prompts.say(f"跳過註冊。需要時請自行執行：{manual}")
        return

    result = register(_SERVER_NAME, command, run)
    if result.code == 0:
        prompts.say(f"✓ 已註冊 {_SERVER_NAME}（scope: user）")
    else:
        prompts.say(f"× 註冊失敗：{(result.stderr or result.stdout).strip()}")
        prompts.say(f"請自行執行：{manual}")


def _finish(prompts: Prompts, config_path: Path, site: str, run: CommandRunner) -> None:
    """跑冒煙測試並印出總結與使用者必須自己做的事。

    參數:
        prompts: 互動管道。
        config_path: 參數檔路徑。
        site: 本次設定的站台代號。
        run: 外部指令執行器。
    """
    ok, message = smoke_test(resolve_executable(_TOOL), run)
    if ok:
        prompts.say("[green]✓[/green] server 起得來")
    else:
        prompts.say(f"[red]×[/red] server 啟動測試沒過：{message}")
        prompts.say("請對照 SETUP.md 的排錯表處理後再重跑一次。")

    prompts.panel(
        "完成",
        [
            f"參數檔：{config_path}",
            f"站台代號：{site}",
            "註冊 scope：user（所有專案都吃得到）",
            "",
            "[bold]接下來這兩步要你自己做，精靈無法代勞：[/bold]",
            "  1. 重開 Claude Code（MCP server 不會熱重載）",
            "  2. 輸入 /mcp 確認 redmine 顯示為 connected",
        ],
    )
```

- [ ] **Step 6: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_wizard.py -q`
Expected: PASS（8 passed）。若 `_do_register` 的 `claude --version` 呼叫讓假執行器的呼叫序對不上，請調整測試中的 `run` 假函式回應而非放寬斷言。

- [ ] **Step 7: 補既有設定與取代分支的測試**

```python
# 追加到 tests/test_setup_wizard.py
def test_取代既有站台不影響其他站台(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(
        '[sites.a]\nurl = "https://a/x"\napi_key = "old"\n\n'
        '[sites.b]\nurl = "https://b/x"\napi_key = "keep"\n',
        encoding="utf-8",
    )
    script = 腳本化提問(
        answers=["https://a/new", "new-key-9999", "", "", ""],
        confirms=[True, True, False],
        choices=[1, 0],   # 選「取代其中一個站台」→ 選 a
    )
    code = run_setup(
        script.build(),
        config_path=target,
        run=lambda argv, stdin=None: CommandResult(1, "", ""),
        verify=_成功驗證,
    )
    content = target.read_text(encoding="utf-8")
    assert code == 0
    assert 'api_key     = "new-key-9999"' in content
    assert 'api_key = "keep"' in content


def test_舊格式不同意轉換就完全不動檔案(tmp_path):
    target = tmp_path / "config.toml"
    原文 = 'url = "https://a/x"\napi_key = "old"\n'
    target.write_text(原文, encoding="utf-8")
    script = 腳本化提問(answers=[], confirms=[True, False], choices=[])
    code = run_setup(
        script.build(),
        config_path=target,
        run=lambda argv, stdin=None: CommandResult(0, "", ""),
        verify=_成功驗證,
    )
    assert code == 1
    assert target.read_text(encoding="utf-8") == 原文
```

- [ ] **Step 8: 執行測試確認通過**

Run: `uv run pytest tests/test_setup_wizard.py -q`
Expected: PASS（10 passed）

- [ ] **Step 9: 執行完整檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 10: Commit**

```bash
git add src/redmine_mcp/setup/wizard.py tests/test_setup_wizard.py
git commit -m "feat(redmine-mcp): 安裝精靈的互動流程編排"
```

---

### Task 6: Typer CLI 與進入點

**Files:**
- Modify: `pyproject.toml`（加 `typer` 相依、改 `[project.scripts]`）
- Create: `src/redmine_mcp/cli.py`
- Modify: `src/redmine_mcp/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `redmine_mcp.setup.wizard.run_setup`、`console_prompts`、`redmine_mcp.config_file.resolve_config_path`、`redmine_mcp.setup.external.run_command`、`redmine_mcp.setup.verify.verify_credentials`
- Produces:
  - `redmine_mcp.cli.app`（`typer.Typer` 實例，含 `setup` 命令）
  - `redmine_mcp.__main__.entrypoint() -> None`（console script 進入點）

- [ ] **Step 1: 加入 typer 相依**

```toml
# pyproject.toml，修改 [project] 的 dependencies
dependencies = [
    "mcp>=2.0,<3",
    "httpx>=0.27",
    # 以下兩者只有 `redmine-mcp setup` 這條路徑會 import；server 啟動路徑刻意不碰，
    # 詳見 __main__.py 的 entrypoint() 說明。
    # rich 雖然本來就是 typer 的傳遞相依，但精靈直接 import 它，就必須自己宣告——
    # 依賴傳遞相依是隱性耦合，typer 改包裝就會斷。
    "typer>=0.21,<1",
    "rich>=13",
]
```

Run: `uv lock && uv sync`
Expected: `typer`、`click`、`rich`、`shellingham` 等出現在 lock 中

- [ ] **Step 2: 寫失敗的測試**

```python
# tests/test_cli.py
import sys

from typer.testing import CliRunner

from redmine_mcp.cli import app

runner = CliRunner()


def test_help_列出_setup_子命令():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "setup" in result.stdout
    # Typer 在「只有一個命令且沒有 callback」時會把該命令當成 app 本身，
    # 那樣說明裡不會有 Commands 區塊，而 `setup` 會變成多餘的參數。
    assert "Commands" in result.stdout


def test_未知子命令以非零結束():
    result = runner.invoke(app, ["bogus"])
    assert result.exit_code != 0


def test_setup_呼叫精靈並以其結束碼結束(monkeypatch):
    called = {}

    def 假精靈(prompts, *, config_path, run, verify):
        called["path"] = config_path
        return 7

    monkeypatch.setattr("redmine_mcp.setup.wizard.run_setup", 假精靈)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 7
    assert called["path"].name == "config.toml"


def test_精靈回傳零時以零結束(monkeypatch):
    monkeypatch.setattr(
        "redmine_mcp.setup.wizard.run_setup",
        lambda prompts, *, config_path, run, verify: 0,
    )
    assert runner.invoke(app, ["setup"]).exit_code == 0


def test_無參數不載入_typer或rich(monkeypatch):
    """最重要的一條：MCP client 一律以無參數方式啟動，不可付 typer／rich 的 import 成本。"""
    started = []
    monkeypatch.setattr(sys, "argv", ["redmine-mcp"])
    monkeypatch.setattr("redmine_mcp.__main__.main", lambda: started.append(True))
    # 設成 None 會讓對應的 import 立刻拋 ImportError，因此誤載入時本測試必定失敗。
    monkeypatch.setitem(sys.modules, "typer", None)
    monkeypatch.setitem(sys.modules, "rich", None)

    from redmine_mcp.__main__ import entrypoint

    entrypoint()
    assert started == [True]


def test_有參數時才走_typer(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["redmine-mcp", "--help"])
    monkeypatch.setattr(
        "redmine_mcp.__main__.main", lambda: (_ for _ in ()).throw(AssertionError("不該啟動 server"))
    )

    from redmine_mcp.__main__ import entrypoint

    try:
        entrypoint()
    except SystemExit as exc:
        assert exc.code == 0
```

> `monkeypatch.setitem(sys.modules, "typer", None)` 讓任何 `import typer` 立即
> 拋出 `ImportError`，因此這條測試會在「無參數路徑不小心載入 typer」時失敗——
> 這正是我們要鎖住的約束。若 `redmine_mcp.cli` 已在其他測試中被載入而殘留在
> `sys.modules`，本測試仍成立，因為受測的是 `entrypoint()` 有沒有走到 import 那一行。

- [ ] **Step 3: 執行測試確認失敗**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'redmine_mcp.cli'`

- [ ] **Step 4: 實作 Typer app**

```python
# src/redmine_mcp/cli.py
"""命令列介面。

本模組會 import typer，因此**只在有命令列參數時才被載入**——server 的啟動路徑
不經過這裡，詳見 __main__.py 的 entrypoint()。
"""
from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Redmine MCP server。不帶參數時以 stdio 啟動 server，供 MCP client 使用。"""
    # 這個 callback 什麼都不做，但**不能刪**：Typer 在「只有一個命令且沒有 callback」
    # 時會把那個命令當成 app 本身，於是 `redmine-mcp setup` 會被當成多餘的參數而失敗。
    # 加上 callback 才會維持 `<程式> <子命令>` 的結構，日後加第二個命令也不必再調整。


@app.command()
def setup() -> None:
    """互動式安裝精靈：建立參數檔、驗證金鑰、註冊到 Claude Code。"""
    from redmine_mcp.config_file import resolve_config_path
    from redmine_mcp.setup import wizard
    from redmine_mcp.setup.external import run_command
    from redmine_mcp.setup.verify import verify_credentials

    code = wizard.run_setup(
        wizard.console_prompts(),
        config_path=resolve_config_path(),
        run=run_command,
        verify=verify_credentials,
    )
    raise typer.Exit(code)
```

`add_completion=False`：本工具由 MCP client 啟動，安裝 shell 補完只會在
`--help` 裡多兩個沒人會用的旗標。

- [ ] **Step 5: 改寫進入點**

```python
# 追加到 src/redmine_mcp/__main__.py（放在既有 main() 之後）

def entrypoint() -> None:
    """console script 的進入點。

    無參數時直接啟動 server，**刻意不 import typer**：MCP client 每次啟動 session
    都會開一次本執行檔，而 typer 會連帶載入 rich、click 等套件。SETUP.md 記錄
    client 端連線 timeout 只有 30 秒、冷路徑曾實測 37% 斷線率，這條路徑沒有理由
    去付與它無關的 import 成本。

    有參數時才載入 Typer app，由它處理子命令、--help 與參數錯誤。
    """
    if not sys.argv[1:]:
        main()
        return

    from redmine_mcp.cli import app

    app()
```

```python
# 修改 src/redmine_mcp/__main__.py 的檔尾
if __name__ == "__main__":
    entrypoint()
```

```toml
# pyproject.toml
[project.scripts]
redmine-mcp = "redmine_mcp.__main__:entrypoint"
```

- [ ] **Step 6: 執行測試確認通過**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS（6 passed）

- [ ] **Step 7: 手動驗證三條路徑**

```bash
uv run redmine-mcp --help          # Typer 產生的說明，列出 setup，exit 0
uv run redmine-mcp bogus; echo $?  # Typer 的參數錯誤，exit 2
echo "" | uv run redmine-mcp       # 照舊啟動 server 後因 EOF 結束，exit 0
```

第三條是回歸重點：輸出必須與改動前完全一致（`INFO 讀取參數檔：…` 與
`INFO Redmine MCP server 啟動，站台：…`），沒有任何 Typer 的痕跡。

- [ ] **Step 8: 執行完整檢查**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/redmine_mcp/cli.py src/redmine_mcp/__main__.py tests/test_cli.py
git commit -m "feat(redmine-mcp): 以 Typer 建構 CLI 並加入 setup 子命令"
```

---

### Task 7: 文件改寫——只開放 CLI 安裝

**Files:**
- Modify: `README.md`（repo 根目錄）
- Modify: `redmine-mcp/SETUP.md`
- Test: 無自動化測試（純文件）；以下方檢查清單人工確認

- [ ] **Step 1: 移除 README 的「方式 A：AI 全自動安裝」整段**

刪除 `## 方式 A：AI 全自動安裝` 到 `## 方式 B：手動安裝` 之前的全部內容，包含那段約 60 行的 prompt。理由寫在 spec：第一步就 404（private repo），且金鑰會經過 LLM 對話。

- [ ] **Step 2: 把 README 的安裝章節改寫成單一路徑**

`## 安裝操作指引` 底下的兩條路導覽段落整段換成：

````markdown
## 安裝操作指引

以下以 `redmine-mcp` 為例，其他工具流程相同，只需替換名稱。兩行指令：

```powershell
uv tool install --from "git+https://github.com/Shinspire/MCP.git#subdirectory=redmine-mcp" redmine-mcp
redmine-mcp setup
```

第一行把工具裝進隔離環境並放到 PATH 上（網址一定要用引號包住，否則 PowerShell 會把
`#` 之後當註解丟掉）。第二行是互動式安裝精靈，它會：

1. 問你 Redmine 網址、API 存取金鑰（輸入時不顯示）與站台代號
2. **在寫檔前先打一次 Redmine 驗證金鑰**——網址少了子路徑之類的問題會當場說清楚，
   不必等到重開 Claude Code 才發現
3. 建立 `config.toml` 並收緊檔案權限
4. 問過你之後執行 `claude mcp add redmine --scope user -- redmine-mcp`
5. 跑一次啟動測試，最後印出總結

金鑰只存在參數檔，**不會進入指令參數、不會進入 log，也不會經過任何 AI 對話**。

前置需求只有 [uv](https://docs.astral.sh/uv/)（`winget install astral-sh.uv`）與 curl
（Windows 10/11 內建）。裝完 uv 要開一個新的終端機，PATH 才會更新。

精靈跑完後有兩件事要你自己做：**重開 Claude Code**（MCP server 不會熱重載），然後輸入
`/mcp` 確認 `redmine` 顯示為 connected。
````

- [ ] **Step 3: 移除 README 步驟 2 的手動建檔內容**

刪除「### 步驟 2：建立參數檔」整段（含那個 404 的 `curl -o ... config.example.toml`）。
把其中仍有價值的部分——多站台範例、站台代號說明、`icacls` 權限建議——移到
`## 多站台怎麼用` 章節底下，改寫成「**新增站台**：再跑一次 `redmine-mcp setup` 選「新增
一個站台」，或自行編輯 `config.toml` 加一段 `[sites.<代號>]`」。

- [ ] **Step 4: 更新 SETUP.md 的安裝與更新章節**

- `## 3. 註冊到 Claude Code` 的「方式 A」改成上面那兩行指令 + 精靈說明；方式 A2（本機
  wheel）與 A3（原始碼開發）保留，它們是開發者路徑不是安裝路徑。
- `## 2. 參數檔設定` 開頭補一句：「一般安裝不需要手動建立這個檔，`redmine-mcp setup`
  會產生。本節是欄位參考，供新增站台、輪換金鑰與排錯時查閱。」
- `## 4. 更新到新版本` 的 `"" | redmine-mcp` 驗證方式維持不變（無參數行為沒有改變），
  但把「它不吃 `--help`」那句改成「`--help` 會印出可用子命令；要驗證 server 起得來仍
  用 `"" | redmine-mcp`」。

- [ ] **Step 5: 人工檢查清單**

- [ ] `grep -rn "raw.githubusercontent" README.md redmine-mcp/SETUP.md` 沒有任何結果
- [ ] `grep -rn "方式 A：AI" README.md` 沒有任何結果
- [ ] README 中所有安裝指令都出現 `redmine-mcp setup`
- [ ] SETUP.md 的「它不吃 `--help`」已更新
- [ ] 參數檔欄位說明仍完整保留在 SETUP.md

- [ ] **Step 6: Commit**

```bash
git add README.md redmine-mcp/SETUP.md
git commit -m "docs: 安裝改為只開放 CLI 路徑，移除 AI 安裝與手動建檔步驟"
```

---

## 收尾

全部 Task 完成後：

1. 跑 `uv run pytest -q && uv run ruff check . && uv run mypy`，確認全綠。
2. 在乾淨環境實測一次完整安裝：`uv tool install --force --from "git+...@<分支>#subdirectory=redmine-mcp" redmine-mcp` 後跑 `redmine-mcp setup`，走完全新安裝與「新增第二個站台」兩條路徑。
3. 版本 bump 到 0.5.0（新增對外 CLI 介面，屬 minor），同步 `uv.lock`，合併進 `main` 後推 tag `redmine-mcp-v0.5.0`。
4. 依 spec 另寫 `llm-wiki-mcp` 的計畫。
