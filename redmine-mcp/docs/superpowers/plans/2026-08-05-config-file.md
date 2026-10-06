# config.toml 參數檔 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 redmine-mcp 的設定改由專案根目錄的 `config.toml` 提供（含 API key），環境變數僅作為覆寫手段，消除「環境變數沒被行程繼承導致 MCP server 啟動即死」的問題。

**Architecture:** 新增 `config_file.py` 單一職責模組，負責讀取 TOML 並攤平成 `REDMINE_*` 形式的 `dict[str, str]`；再以 `os.environ` 疊加覆寫，把合併結果餵給現有的 `load_settings(env)`。既有驗證邏輯（URL 協定檢查、路徑 resolve、上傳目錄沿用規則、大小上限解析）與既有測試完全不動。

**Tech Stack:** Python 3.11（`tomllib` 為標準庫，**不新增任何依賴**）、pytest、ruff、mypy、uv。

## Global Constraints

- 設計依據：`docs/superpowers/specs/2026-08-05-config-file-design.md`。有衝突時以 spec 為準。
- **不得新增任何 runtime 依賴。** `pyproject.toml` 的 `dependencies` 維持 `mcp>=2.0,<3` 與 `httpx>=0.27`。TOML 解析一律用標準庫 `tomllib`。
- 註解、docstring、錯誤訊息、log 一律使用繁體中文（zh-TW）。新增 helper 函式須附中文函式說明；新增公開函式須附中文 docstring 並標註參數與回傳值。
- **任何錯誤訊息、警告與 log 都不得輸出 `api_key` 的值。** 每個涉及錯誤路徑的任務都要有對應斷言。
- ruff：`line-length = 100`，`target-version = "py311"`。驗證指令 `uv run ruff check .` 須全綠。
- mypy：`files = ["src"]`、`disallow_untyped_defs = true`，所有新增函式都要完整型別標註。`uv run mypy` 須全綠。
- **既有 `tests/test_config.py` 一行不改且須全部通過**，這是「`load_settings()` 未被破壞」的驗收證據。
- 測試絕不可讀取真實的 `config.toml`（本機存在、CI 不存在，會造成本機綠 CI 紅）。所有測試一律以 `tmp_path` 建檔並顯式傳入路徑，或顯式傳入不存在的路徑。
- CI（`.github/workflows/ci.yml`）在 ubuntu 上不設任何環境變數，依序跑 `uv run ruff check .`、`uv run mypy`、`uv run pytest -q`。
- 計畫中的 shell 指令一律以 Bash（Git Bash）語法撰寫；若在 PowerShell 執行需自行替換（`printf`→`Set-Content`、`rm`→`Remove-Item`、`cp`→`Copy-Item`、`VAR=x cmd`→`$env:VAR = 'x'; cmd`）。
- 鍵名映射規則固定為 `"REDMINE_" + key.upper()`：`url`→`REDMINE_URL`、`api_key`→`REDMINE_API_KEY`、`download_dir`→`REDMINE_DOWNLOAD_DIR`、`upload_dir`→`REDMINE_UPLOAD_DIR`、`max_attachment_mb`→`REDMINE_MAX_ATTACHMENT_MB`。
- **commit 照步驟執行，訊息不加編號前綴**：本 repo 現有 commit 均無編號前綴，使用者已確認沿用此慣例，直接用各步驟列出的 `feat:`／`docs:` 訊息。工作分支為 `feat/config-toml`（非 `main`），基線為 commit `3866416`，該處 `uv run pytest -q` 為 169 passed、ruff 與 mypy 全綠。
- commit 訊息結尾加上 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`。
- 只 `git add` 該任務實際碰到的檔案，不要 `git add -A`；`docs/` 底下的設計文件與計畫依使用者指示維持未提交，任何任務都不得提交它們。

---

### Task 1: 參數檔範本與 gitignore 防護

先做這一步，避免後續開發過程中把含真實金鑰的 `config.toml` 誤加入版控。此任務不含 Python 程式碼。

**Files:**
- Modify: `.gitignore`（現有 6 行，末尾追加）
- Create: `config.example.toml`

**Interfaces:**
- Consumes: 無
- Produces: `config.example.toml` 的鍵名集合，Task 2 的 `KNOWN_KEYS` 必須與之一致：`url`、`api_key`、`download_dir`、`upload_dir`、`max_attachment_mb`

- [ ] **Step 1: 把 config.toml 加入 .gitignore**

在 `.gitignore` 末尾追加（保留現有 6 行不動）：

```
# 含 API key 的實際設定檔，只有 config.example.toml 進版控
config.toml
```

- [ ] **Step 2: 驗證 gitignore 生效**

```bash
printf 'api_key = "probe"\n' > config.toml
git status --porcelain config.toml
```

Expected: 沒有任何輸出（代表已被忽略）。若輸出 `?? config.toml` 則 gitignore 未生效，須修正後重跑。

- [ ] **Step 3: 移除探測用的暫存檔**

```bash
rm config.toml
```

- [ ] **Step 4: 建立 config.example.toml**

```toml
# Redmine MCP server 參數檔範本
# 使用方式：複製本檔為 config.toml 後填入實際值。config.toml 不會進版控。
#
# 安全提醒：config.toml 含 API key（權限等同該帳號）。本專案位於 Desktop 底下，
# 若已啟用 OneDrive 資料夾備份，此檔會被同步上雲。建議收緊檔案權限：
#   icacls config.toml /inheritance:r /grant:r "$env:USERNAME:(R)"

# 必填：Redmine 站台根位址。要含子路徑，例如結尾的 /redmine；
# 只填網域會 404 或收到 HTML。
url = "https://redmine.example.com/redmine"

# 必填：個人 API 存取金鑰。Redmine 右上角「我的帳戶」→ 右側「API 存取金鑰」→ 顯示。
# 權限等同該帳號，不要使用他人的金鑰。
api_key = "<your-api-key>"

# 選填：附件下載目錄。移除本行則 download_attachment 工具停用。
download_dir = "C:/Users/kenny/Desktop/MCP/downloads"

# 選填：允許上傳的來源目錄。upload_attachment 只能讀取此目錄底下的檔案；
# 未設定時沿用 download_dir，兩者都沒設定時上傳工具停用。
upload_dir = "C:/Users/kenny/Desktop/MCP/downloads"

# 選填：單一附件下載／上傳的大小上限（MB），預設 10。
max_attachment_mb = 10
```

- [ ] **Step 5: 確認範本本身是合法 TOML 且鍵名正確**

```bash
uv run python -c "import tomllib,pathlib; d=tomllib.loads(pathlib.Path('config.example.toml').read_text(encoding='utf-8')); print(sorted(d))"
```

Expected: `['api_key', 'download_dir', 'max_attachment_mb', 'upload_dir', 'url']`

- [ ] **Step 6: Commit**（先確認編號，見 Global Constraints）

```bash
git add .gitignore config.example.toml
git commit -m "feat: 新增 config.toml 參數檔範本並將實際設定檔排除於版控"
```

---

### Task 2: config_file.py 的讀檔與攤平

**Files:**
- Create: `src/redmine_mcp/config_file.py`
- Test: `tests/test_config_file.py`

**Interfaces:**
- Consumes: `ConfigError`（來自 `src/redmine_mcp/config.py`，既有例外類別）；Task 1 的鍵名集合
- Produces:
  - `DEFAULT_CONFIG_PATH: Path` — 模組層常數
  - `KNOWN_KEYS: frozenset[str]` — 允許的參數檔鍵名
  - `resolve_config_path(env: Mapping[str, str] | None = None) -> Path`
  - `load_config_file(path: Path) -> dict[str, str]`
  - Task 3 的 `build_env()` 會使用上述三者

- [ ] **Step 1: 寫下失敗的測試**

建立 `tests/test_config_file.py`：

```python
"""參數檔讀取與攤平的測試。"""
import logging
from pathlib import Path

import pytest

from redmine_mcp.config import ConfigError
from redmine_mcp.config_file import (
    DEFAULT_CONFIG_PATH,
    load_config_file,
    resolve_config_path,
)


def _write(path: Path, content: str) -> Path:
    """把 TOML 內容寫進指定檔案並回傳該路徑。"""
    path.write_text(content, encoding="utf-8")
    return path


def test_讀取參數檔並映射為環境變數鍵名(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        'url = "https://redmine.example.com/redmine"\napi_key = "secret-key"\n',
    )
    assert load_config_file(path) == {
        "REDMINE_URL": "https://redmine.example.com/redmine",
        "REDMINE_API_KEY": "secret-key",
    }


def test_檔案不存在時回空字典(tmp_path: Path):
    assert load_config_file(tmp_path / "不存在.toml") == {}


def test_整數值轉為字串(tmp_path: Path):
    path = _write(tmp_path / "config.toml", "max_attachment_mb = 25\n")
    assert load_config_file(path) == {"REDMINE_MAX_ATTACHMENT_MB": "25"}


def test_語法錯誤時拋出_ConfigError_並帶位置(tmp_path: Path):
    path = _write(tmp_path / "config.toml", 'url "https://redmine.example.com"\n')
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "line 1" in str(exc.value)


def test_語法錯誤訊息不含_API_key(tmp_path: Path):
    # 金鑰所在的那一行語法錯誤時，訊息絕不可回吐該行內容
    path = _write(tmp_path / "config.toml", 'api_key = "super-secret-value\n')
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "super-secret-value" not in str(exc.value)


def test_未知鍵發出警告但不中斷(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = _write(tmp_path / "config.toml", 'api_kye = "typo"\nurl = "https://a.example.com"\n')
    with caplog.at_level(logging.WARNING):
        result = load_config_file(path)
    assert result == {"REDMINE_URL": "https://a.example.com"}
    assert "api_kye" in caplog.text


def test_未知鍵的警告不含該鍵的值(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # 拼錯的鍵有可能是 api_kye，其值就是真金鑰，警告只能提鍵名
    path = _write(tmp_path / "config.toml", 'api_kye = "super-secret-value"\n')
    with caplog.at_level(logging.WARNING):
        load_config_file(path)
    assert "super-secret-value" not in caplog.text


def test_巢狀表格值拋錯(tmp_path: Path):
    path = _write(tmp_path / "config.toml", '[url]\nhost = "a.example.com"\n')
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert "url" in str(exc.value)


def test_陣列值拋錯(tmp_path: Path):
    path = _write(tmp_path / "config.toml", 'url = ["a", "b"]\n')
    with pytest.raises(ConfigError):
        load_config_file(path)


def test_布林值拋錯(tmp_path: Path):
    path = _write(tmp_path / "config.toml", "url = true\n")
    with pytest.raises(ConfigError):
        load_config_file(path)


def test_預設路徑指向專案根目錄的_config_toml():
    assert DEFAULT_CONFIG_PATH.name == "config.toml"
    # src/redmine_mcp/config_file.py 上溯三層即專案根目錄，該處應有 pyproject.toml
    assert (DEFAULT_CONFIG_PATH.parent / "pyproject.toml").is_file()


def test_未設_REDMINE_CONFIG_時用預設路徑():
    assert resolve_config_path({}) == DEFAULT_CONFIG_PATH


def test_REDMINE_CONFIG_可改變讀取路徑(tmp_path: Path):
    target = tmp_path / "自訂.toml"
    assert resolve_config_path({"REDMINE_CONFIG": str(target)}) == target
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_config_file.py -q`
Expected: FAIL，錯誤為 `ModuleNotFoundError: No module named 'redmine_mcp.config_file'`

- [ ] **Step 3: 寫最小實作**

建立 `src/redmine_mcp/config_file.py`：

```python
"""參數檔（config.toml）的讀取與攤平。

只負責「讀檔並轉成環境變數形式的字典」，不做語意驗證——URL 格式、路徑、
大小上限等一律交給 config.py 的 load_settings()，維持驗證邏輯只有一處。
"""
from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from redmine_mcp.config import ConfigError

# src/redmine_mcp/config_file.py 上溯三層即專案根目錄。
# 以套件位置推算而非 CWD，Claude Code 從任何目錄啟動都找得到參數檔。
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"

# 參數檔允許的鍵；集中定義以便偵測拼錯的鍵名。
KNOWN_KEYS = frozenset(
    {
        "url",
        "api_key",
        "download_dir",
        "upload_dir",
        "max_attachment_mb",
    }
)

_ENV_PREFIX = "REDMINE_"

# tomllib 錯誤訊息中的位置片段，例如「(at line 3, column 5)」。
_POSITION_PATTERN = re.compile(r"\(at line \d+, column \d+\)|\(at end of document\)")

logger = logging.getLogger(__name__)


def _position_only(exc: tomllib.TOMLDecodeError) -> str:
    """僅取出 TOML 解析錯誤中的位置資訊。

    tomllib 的訊息可能夾帶出錯處的內容，若出錯的那一行正是 api_key 就會外洩金鑰，
    因此只保留位置片段；無法辨識時回傳通用說明。

    參數:
        exc: tomllib 拋出的解析錯誤。
    回傳:
        僅含位置資訊的字串。
    """
    match = _POSITION_PATTERN.search(str(exc))
    return match.group(0) if match else "無法解析 TOML 內容"


def resolve_config_path(env: Mapping[str, str] | None = None) -> Path:
    """決定要讀取的參數檔路徑。

    參數:
        env: 設定來源；預設讀取 os.environ，測試可注入假字典。
    回傳:
        REDMINE_CONFIG 指定的路徑，未設定時為 DEFAULT_CONFIG_PATH。
    """
    source = os.environ if env is None else env
    raw = (source.get("REDMINE_CONFIG") or "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_CONFIG_PATH


def load_config_file(path: Path) -> dict[str, str]:
    """讀取參數檔並攤平為環境變數形式的字典。

    參數:
        path: 參數檔路徑；檔案不存在時回傳空字典，不視為錯誤。
    回傳:
        鍵為 REDMINE_* 大寫形式、值皆為字串的字典。
    例外:
        ConfigError: TOML 語法錯誤，或某鍵的值不是單一純量。
    """
    if not path.is_file():
        return {}

    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"參數檔格式錯誤（{path}）：{_position_only(exc)}") from exc

    result: dict[str, str] = {}
    for key, value in raw.items():
        if key not in KNOWN_KEYS:
            # 只提鍵名不提值：拼錯的鍵有可能是 api_kye，其值就是真金鑰。
            logger.warning("參數檔中有無法識別的設定鍵 %r，已忽略（請確認是否拼錯）", key)
            continue
        if isinstance(value, dict | list):
            raise ConfigError(f"參數檔的 {key} 只接受單一值，不支援表格或陣列")
        if isinstance(value, bool):
            raise ConfigError(f"參數檔的 {key} 不接受布林值")
        result[_ENV_PREFIX + key.upper()] = str(value)
    return result
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_config_file.py -q`
Expected: PASS（13 passed）

- [ ] **Step 5: 確認既有測試未被破壞、型別與風格全綠**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過；`tests/test_config.py` 的 13 個測試仍全綠且檔案未被修改

- [ ] **Step 6: Commit**（先確認編號，見 Global Constraints）

```bash
git add src/redmine_mcp/config_file.py tests/test_config_file.py
git commit -m "feat: 新增 config.toml 讀取模組，攤平為環境變數形式的設定字典"
```

---

### Task 3: 參數檔與環境變數的合併

**Files:**
- Modify: `src/redmine_mcp/config_file.py`（在 `load_config_file` 之後追加 `build_env`）
- Test: `tests/test_config_file.py`（追加測試）

**Interfaces:**
- Consumes: Task 2 的 `resolve_config_path()`、`load_config_file()`、`KNOWN_KEYS`、`_ENV_PREFIX`
- Produces: `build_env(env: Mapping[str, str] | None = None, path: Path | None = None) -> dict[str, str]` — Task 4 的 `__main__.py` 會呼叫它

- [ ] **Step 1: 寫下失敗的測試**

在 `tests/test_config_file.py` 末尾追加（同時把 `build_env` 加進檔案頂端的 import）：

```python
def test_環境變數覆寫參數檔的同名值(tmp_path: Path):
    path = _write(
        tmp_path / "config.toml",
        'url = "https://來自檔案.example.com"\napi_key = "檔案金鑰"\n',
    )
    merged = build_env({"REDMINE_URL": "https://來自環境變數.example.com"}, path=path)
    assert merged["REDMINE_URL"] == "https://來自環境變數.example.com"
    # 環境變數沒提供的鍵仍沿用參數檔的值
    assert merged["REDMINE_API_KEY"] == "檔案金鑰"


def test_空字串的環境變數不覆寫參數檔(tmp_path: Path):
    path = _write(tmp_path / "config.toml", 'url = "https://來自檔案.example.com"\n')
    merged = build_env({"REDMINE_URL": "   "}, path=path)
    assert merged["REDMINE_URL"] == "https://來自檔案.example.com"


def test_參數檔不存在時純以環境變數組成(tmp_path: Path):
    merged = build_env(
        {"REDMINE_URL": "https://a.example.com", "REDMINE_API_KEY": "k"},
        path=tmp_path / "不存在.toml",
    )
    assert merged == {"REDMINE_URL": "https://a.example.com", "REDMINE_API_KEY": "k"}


def test_不相關的環境變數不會被帶入(tmp_path: Path):
    merged = build_env({"PATH": "/usr/bin", "REDMINE_URL": "https://a.example.com"}, path=tmp_path / "無.toml")
    assert merged == {"REDMINE_URL": "https://a.example.com"}


def test_未指定路徑時依環境變數解析參數檔位置(tmp_path: Path):
    path = _write(tmp_path / "自訂.toml", 'api_key = "來自自訂路徑"\n')
    merged = build_env({"REDMINE_CONFIG": str(path)})
    assert merged["REDMINE_API_KEY"] == "來自自訂路徑"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_config_file.py -q`
Expected: FAIL，錯誤為 `ImportError: cannot import name 'build_env'`

- [ ] **Step 3: 寫最小實作**

在 `src/redmine_mcp/config_file.py` 末尾追加：

```python
def build_env(
    env: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> dict[str, str]:
    """合併參數檔與環境變數，產出給 load_settings() 使用的設定來源。

    參數檔為底，環境變數覆寫同名鍵——日常只維護 config.toml，需要臨時切換
    站台或帳號時不必改檔。空白值視為未提供，不會覆寫參數檔既有的值。

    參數:
        env: 環境變數來源；預設讀取 os.environ，測試可注入假字典。
        path: 參數檔路徑；預設依 resolve_config_path() 決定。
    回傳:
        鍵為 REDMINE_* 大寫形式、值皆為字串的合併結果。
    例外:
        ConfigError: 參數檔語法錯誤，或某鍵的值不是單一純量。
    """
    source = os.environ if env is None else env
    config_path = resolve_config_path(source) if path is None else path

    merged = load_config_file(config_path)
    for key in KNOWN_KEYS:
        env_key = _ENV_PREFIX + key.upper()
        value = (source.get(env_key) or "").strip()
        if value:
            merged[env_key] = value
    return merged
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_config_file.py -q`
Expected: PASS（18 passed）

- [ ] **Step 5: 全量驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 6: Commit**（先確認編號，見 Global Constraints）

```bash
git add src/redmine_mcp/config_file.py tests/test_config_file.py
git commit -m "feat: 參數檔與環境變數合併，環境變數可覆寫同名設定"
```

---

### Task 4: 接進進入點並改寫缺漏設定的引導訊息

**Files:**
- Modify: `src/redmine_mcp/__main__.py:20-28`
- Modify: `src/redmine_mcp/config.py:46`、`src/redmine_mcp/config.py:54`（僅訊息文字）
- Test: `tests/test_config_file.py`（追加訊息斷言）

**Interfaces:**
- Consumes: Task 3 的 `build_env()`、Task 2 的 `resolve_config_path()`
- Produces: 無新介面；`main()` 行為改為由參數檔取得設定

- [ ] **Step 1: 寫下失敗的測試**

在 `tests/test_config_file.py` 末尾追加。注意這裡驗的是 `load_settings` 的訊息要引導到參數檔，且既有 `tests/test_config.py` 仍要求訊息含環境變數名，兩者必須同時成立：

```python
def test_缺漏設定的訊息同時提到參數檔與環境變數名():
    from redmine_mcp.config import load_settings

    with pytest.raises(ConfigError) as exc:
        load_settings({})
    message = str(exc.value)
    assert "REDMINE_URL" in message
    assert "config.toml" in message


def test_缺漏金鑰的訊息提到參數檔且不含金鑰值():
    from redmine_mcp.config import load_settings

    with pytest.raises(ConfigError) as exc:
        load_settings({"REDMINE_URL": "https://a.example.com"})
    message = str(exc.value)
    assert "REDMINE_API_KEY" in message
    assert "config.toml" in message
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_config_file.py -q -k 訊息`
Expected: FAIL，`assert "config.toml" in message` 失敗（現有訊息只提環境變數）

- [ ] **Step 3: 改寫 config.py 的兩則缺漏訊息**

`src/redmine_mcp/config.py:46`，把

```python
        raise ConfigError("缺少必填環境變數 REDMINE_URL，請設定 Redmine 站台根位址")
```

改為

```python
        raise ConfigError(
            "缺少必填設定 url（環境變數 REDMINE_URL）；"
            "請複製 config.example.toml 為 config.toml 並填入 Redmine 站台根位址"
        )
```

`src/redmine_mcp/config.py:54`，把

```python
        raise ConfigError("缺少必填環境變數 REDMINE_API_KEY，請於 Redmine 個人設定頁取得")
```

改為

```python
        raise ConfigError(
            "缺少必填設定 api_key（環境變數 REDMINE_API_KEY）；"
            "請複製 config.example.toml 為 config.toml 並填入於 Redmine 個人設定頁取得的金鑰"
        )
```

兩則訊息都保留 `REDMINE_URL`／`REDMINE_API_KEY` 字樣，`tests/test_config.py` 的 `test_缺少必填變數時拋出_ConfigError` 才會繼續通過。

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_config_file.py tests/test_config.py -q`
Expected: PASS（既有 13 + 新增 20 全綠）

- [ ] **Step 5: 改寫 __main__.py 讓設定來自參數檔**

把 `src/redmine_mcp/__main__.py` 的 import 區塊補上新模組：

```python
from redmine_mcp.config import ConfigError, load_settings
from redmine_mcp.config_file import build_env, resolve_config_path
```

再把 `main()` 中的第 20-24 行（`try: settings = load_settings()` 那段）改為：

```python
    # 先印出實際使用的參數檔路徑：排錯時第一步就是確認讀到的是哪個檔。
    config_path = resolve_config_path()
    if config_path.is_file():
        logging.info("讀取參數檔：%s", config_path)
    else:
        logging.info("未找到參數檔 %s，改由環境變數提供設定", config_path)

    try:
        settings = load_settings(build_env(path=config_path))
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
```

`build_env()` 也可能拋 `ConfigError`（參數檔語法錯誤），因此必須放在 `try` 內。

- [ ] **Step 6: 以實際參數檔手動驗證啟動**

```bash
cp config.example.toml config.toml
# 用編輯器把 config.toml 的 url 與 api_key 換成真實值後執行：
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' | uv run redmine-mcp
```

Expected: stderr 出現 `INFO 讀取參數檔：...config.toml` 與 `INFO Redmine MCP server 啟動，站台：...`，stdout 回傳含 `"serverInfo":{"name":"redmine"` 的 JSON。**確認 log 中沒有出現金鑰值。**

- [ ] **Step 7: 驗證參數檔缺漏時的錯誤訊息**

```bash
# 寫到系統暫存目錄，不污染專案；Windows 上 $TEMP 由 Git Bash 對應到使用者暫存區
printf 'url = "https://a.example.com"\n' > "$TEMP/僅有網址.toml"
REDMINE_CONFIG="$TEMP/僅有網址.toml" uv run redmine-mcp < /dev/null; echo "exit=$?"
rm "$TEMP/僅有網址.toml"
```

Expected: stderr 顯示 `設定錯誤：缺少必填設定 api_key（環境變數 REDMINE_API_KEY）；請複製 config.example.toml ...`，`exit=1`

- [ ] **Step 8: 全量驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 9: Commit**（先確認編號，見 Global Constraints）

```bash
git add src/redmine_mcp/__main__.py src/redmine_mcp/config.py tests/test_config_file.py
git commit -m "feat: 設定改由 config.toml 提供，啟動時記錄實際使用的參數檔路徑"
```

---

### Task 5: 文件更新

**Files:**
- Modify: `SETUP.md`（第 2 節、第 3 節、第 5 節排錯表、第 6 節）
- Modify: `README.md:19`、`README.md:30`、`README.md:39` 一帶的設定說明

**Interfaces:**
- Consumes: Task 1 的 `config.example.toml` 鍵名、Task 4 的錯誤訊息文字
- Produces: 無程式介面

- [ ] **Step 1: 改寫 SETUP.md 第 2 節**

把標題「## 2. 環境變數」改為「## 2. 參數檔設定」，表格的「變數」欄改為參數檔鍵名（`url`、`api_key`、`download_dir`、`upload_dir`、`max_attachment_mb`），並在表格前加入：

```markdown
設定放在專案根目錄的 `config.toml`。複製範本後填入實際值：

```powershell
cd C:\Users\kenny\Desktop\MCP\redmine-mcp
Copy-Item config.example.toml config.toml
# 用編輯器填入 url 與 api_key
```

`config.toml` 已列入 `.gitignore`，不會進版控。若同名的環境變數（`REDMINE_URL`、`REDMINE_API_KEY`、`REDMINE_DOWNLOAD_DIR`、`REDMINE_UPLOAD_DIR`、`REDMINE_MAX_ATTACHMENT_MB`）有值，會覆寫參數檔中的對應設定，可用於臨時切換站台或帳號。另可用 `REDMINE_CONFIG` 指定其他路徑的參數檔。
```

表格後保留既有的 `upload_dir` 安全性說明段落。

- [ ] **Step 2: 改寫 SETUP.md 第 3 節註冊步驟**

刪除方式 A 中「先把金鑰放進使用者環境變數」的 `SetEnvironmentVariable` 前置步驟與後面「設完環境變數必須完整重啟」的引言區塊，改為：

```markdown
### 方式 A：CLI（建議）

設定都在 `config.toml`，註冊指令不需要帶任何 `--env`：

```powershell
claude mcp add redmine --scope user `
  -- uv --directory C:\Users\kenny\Desktop\MCP\redmine-mcp run redmine-mcp
```

Claude Code 會把自身的環境變數一併傳給 MCP 子行程，`uv` 因此拿得到 `PATH` 與 `LOCALAPPDATA` 才找得到 Python 與快取；設定本身則由參數檔提供，不再依賴行程繼承環境變數。
```

方式 B 的 JSON 範例同步移除 `env` 區塊：

```json
"mcpServers": {
  "redmine": {
    "command": "uv",
    "args": ["--directory", "C:/Users/kenny/Desktop/MCP/redmine-mcp", "run", "redmine-mcp"]
  }
}
```

- [ ] **Step 3: 更新 SETUP.md 第 5 節排錯表**

把「缺少必填環境變數 REDMINE_API_KEY」該列改為：

```markdown
| 「缺少必填設定 api_key」 | `config.toml` 不存在或沒填 `api_key`。複製 `config.example.toml` 為 `config.toml` 並填入。啟動 log 的「讀取參數檔：...」會顯示實際讀到哪個檔，若顯示「未找到參數檔」則是路徑不對 |
```

並新增一列：

```markdown
| 改了 `config.toml` 沒生效 | MCP server 只在啟動時讀設定。在 `/mcp` 重新連線 redmine 即可，不需要完整重啟 VSCode |
```

- [ ] **Step 4: 改寫 SETUP.md 第 6 節安全注意事項**

把「**API key 只放使用者環境變數，不寫進任何設定檔**」該項替換為：

```markdown
- **API key 放在 `config.toml`，該檔已列入 `.gitignore`，絕不可提交**。輪換金鑰時改這個檔並重新連線 MCP server 即可
- **本專案位於 `Desktop` 底下，若已啟用 OneDrive 資料夾備份，`config.toml` 會被同步上雲。** 建議收緊檔案權限，只讓本人可讀：
  ```powershell
  icacls config.toml /inheritance:r /grant:r "$env:USERNAME:(R)"
  ```
- 不要用 `--scope project`（`.mcp.json` 會進版控）
```

刪除原本關於 `setx` 與 `.claude.json` 明文金鑰的兩項（已不適用），保留 untrusted content、附件驗證、傳輸層三項不動。

- [ ] **Step 5: 更新 README.md 設定段落**

把設定表格的欄位名由環境變數改為參數檔鍵名，並把 `SetEnvironmentVariable` 的範例（約 `README.md:30`）換成 `Copy-Item config.example.toml config.toml`；刪除 `README.md:39` 那段「Claude Code 會把環境變數傳給子行程，因此 `REDMINE_API_KEY` 不必出現在 `env` 區塊」的說明，改為一句：設定一律放 `config.toml`，同名環境變數可覆寫。

- [ ] **Step 6: 逐項核對文件與實作一致**

```bash
grep -n "SetEnvironmentVariable\|REDMINE_API_KEY" README.md SETUP.md
```

Expected: 不再出現 `SetEnvironmentVariable`；`REDMINE_API_KEY` 只出現在「同名環境變數可覆寫」的說明中，不再作為必要設定步驟。

- [ ] **Step 7: 全量驗證**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: 全部通過

- [ ] **Step 8: Commit**（先確認編號，見 Global Constraints）

```bash
git add README.md SETUP.md
git commit -m "docs: 設定說明改以 config.toml 為主並補上金鑰保護建議"
```

---

## 完工驗收

- [ ] `uv run pytest -q`、`uv run ruff check .`、`uv run mypy` 三者全綠
- [ ] `tests/test_config.py` 未被修改（`git diff --stat tests/test_config.py` 無輸出）
- [ ] `git status --porcelain config.toml` 無輸出（實際設定檔未進版控）
- [ ] 填好 `config.toml` 後，`/mcp reconnect redmine` 即可連上，**不需要完整重啟 VSCode**
- [ ] `get_current_user` 能回傳自己的 login 與 user id
- [ ] 啟動 log 與所有錯誤訊息均未出現金鑰值
