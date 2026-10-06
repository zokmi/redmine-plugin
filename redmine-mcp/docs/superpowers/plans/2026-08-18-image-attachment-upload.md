# 圖片必須上傳為附件 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 server 指示詞要求把開單者提供的圖片上傳為附件，並為 `upload_attachment` 新增只收圖像的 `content_base64` 入口。

**Architecture:** 在既有 `upload_attachment` 工具上加一條並行輸入路徑：`file_path`（現行、受 `upload_dir` 目錄限制）與 `content_base64`（新、不落地磁碟）互斥，兩條路匯流到同一段 `post_binary` 上傳。新入口的把關手段是「圖像型別白名單 + magic bytes 驗證 + 依偵測結果改寫副檔名」，全部實作成模組級純函式以便單測。

**Tech Stack:** Python 3.11、MCP SDK 2.0、httpx（測試用 `MockTransport`）、pytest（`asyncio_mode = "auto"`）、ruff、mypy、uv。

**Spec:** `docs/superpowers/specs/2026-08-18-image-attachment-upload-design.md`

## Global Constraints

- 專案語言慣例：所有新增函式、欄位、參數都要有**繁體中文**說明（docstring 或 `Field(description=...)`）。
- ruff `line-length = 100`；`target-version = "py311"`。
- 測試函式名沿用專案慣例：**中文敘述式命名**，例如 `async def test_upload_attachment_空檔案被拒絕(...)`。
- 測試指令：`uv run pytest -q`；風格檢查：`uv run ruff check .`；型別檢查：`uv run mypy src`。
- 工具內丟出的 `ValueError` 會被 MCP SDK 包成 `is_error` 結果；測試透過 `tests/conftest.py` 的 `call_tool` 還原成 `ToolCallError`，用 `pytest.raises` 斷言。但 `sites.resolve_for_write()` 拋的錯會直接從 `mcp.call_tool()` 往外拋（既有慣例，見 `pyproject.toml` 的 B017 豁免）。
- `tests/conftest.py` 的 `settings` fixture 設定 `max_attachment_bytes=1024`，大小上限測試直接沿用這個值。
- **不得退化的邊界**：`tests/test_upload_tools.py` 既有的 `file_path` 路徑測試一個字都不能改，且必須全部繼續通過。
- 所有改動集中在 `src/redmine_mcp/tools/attachments.py`、`src/redmine_mcp/server.py`、`src/redmine_mcp/guides/bug_report/report.md` 與對應測試；不做無關重構。

---

### Task 1: `detect_image_type` 圖像型別偵測

**Files:**
- Modify: `src/redmine_mcp/tools/attachments.py`（在 `guess_content_type` 之後新增）
- Test: `tests/test_upload_tools.py`

**Interfaces:**
- Consumes: 無（本任務為起點）
- Produces: `detect_image_type(data: bytes) -> tuple[str, str] | None`，回傳 `(MIME, 正規副檔名)`，非白名單圖像回 `None`。Task 2、Task 3 依賴此簽章。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_upload_tools.py` 檔尾新增。檔案開頭已有 `PNG_BYTES` 常數可直接使用，另外新增三個型別的最小樣本常數：

```python
# JPEG／GIF／WebP 的識別碼樣本：只需前綴足以被 magic bytes 判定，不必是可解碼的完整圖檔。
JPEG_BYTES = bytes.fromhex("ffd8ffe000104a464946")
GIF_BYTES = b"GIF89a" + b"\x01\x00\x01\x00"
WEBP_BYTES = b"RIFF" + b"\x1a\x00\x00\x00" + b"WEBP" + b"VP8 "


def test_detect_image_type_認得四種圖像型別():
    from redmine_mcp.tools.attachments import detect_image_type

    assert detect_image_type(PNG_BYTES) == ("image/png", ".png")
    assert detect_image_type(JPEG_BYTES) == ("image/jpeg", ".jpg")
    assert detect_image_type(GIF_BYTES) == ("image/gif", ".gif")
    assert detect_image_type(b"GIF87a" + b"\x01\x00") == ("image/gif", ".gif")
    assert detect_image_type(WEBP_BYTES) == ("image/webp", ".webp")


def test_detect_image_type_非圖像內容回None():
    from redmine_mcp.tools.attachments import detect_image_type

    assert detect_image_type(b"BEGIN RSA PRIVATE KEY") is None
    assert detect_image_type(b"%PDF-1.7\n") is None
    # SVG 是文字格式且可內嵌 script，刻意不列入白名單。
    assert detect_image_type(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") is None


def test_detect_image_type_內容過短不拋例外():
    from redmine_mcp.tools.attachments import detect_image_type

    assert detect_image_type(b"") is None
    assert detect_image_type(b"RIF") is None
    # 前 4 byte 是 RIFF 但長度不足以讀到第 8-11 byte 的型別標記。
    assert detect_image_type(b"RIFF" + b"\x00\x00\x00\x00") is None
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_upload_tools.py -k detect_image_type -q`

Expected: FAIL，`ImportError: cannot import name 'detect_image_type'`

- [ ] **Step 3: 寫最小實作**

在 `src/redmine_mcp/tools/attachments.py` 的 `guess_content_type` 之後新增：

```python
#: 允許以 base64 上傳的圖像型別：magic bytes 前綴 → (MIME, 正規副檔名)。
_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    """以 magic bytes 判定圖像型別，回傳 (MIME, 正規副檔名)；非白名單圖像回 None。

    只認 PNG／JPEG／GIF／WebP。刻意不收 SVG：它是文字格式、可內嵌 script，
    附件在瀏覽器中被開啟時有 XSS 風險，且沒有可驗證的 magic bytes。
    """
    for prefix, mime, ext in _IMAGE_SIGNATURES:
        if data.startswith(prefix):
            return mime, ext
    # WebP 的識別碼分成兩段：前 4 byte 是 RIFF 容器標頭，真正的型別寫在第 8-11 byte。
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_upload_tools.py -k detect_image_type -q`

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/tools/attachments.py tests/test_upload_tools.py
git commit -m "feat(redmine-mcp): 新增圖像型別 magic bytes 偵測"
```

---

### Task 2: `image_filename` 副檔名對齊

**Files:**
- Modify: `src/redmine_mcp/tools/attachments.py`（在 `detect_image_type` 之後新增）
- Test: `tests/test_upload_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `detect_image_type`（概念相依：本函式吃它回傳的副檔名字串）；既有的 `safe_upload_filename(candidate: str) -> str`
- Produces: `image_filename(candidate: str | None, ext: str) -> str`。Task 4 依賴此簽章。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_upload_tools.py` 檔尾新增：

```python
def test_image_filename_依偵測結果改寫副檔名():
    from redmine_mcp.tools.attachments import image_filename

    # Redmine 依副檔名決定 content_type，內容是 PNG 卻叫 .jpg 會顯示不出來。
    assert image_filename("螢幕截圖.jpg", ".png") == "螢幕截圖.png"
    assert image_filename("a.b.gif", ".png") == "a.b.png"
    # 沒有副檔名時直接補上。
    assert image_filename("圖", ".png") == "圖.png"


def test_image_filename_未給檔名時用預設名():
    from redmine_mcp.tools.attachments import image_filename

    assert image_filename(None, ".png") == "attachment.png"
    assert image_filename("", ".webp") == "attachment.webp"


def test_image_filename_保留同型別的等價副檔名():
    from redmine_mcp.tools.attachments import image_filename

    # .jpeg 與 .jpg 同型別，沒有改寫的必要，保留呼叫方的寫法。
    assert image_filename("照片.jpeg", ".jpg") == "照片.jpeg"
    assert image_filename("照片.JPG", ".jpg") == "照片.JPG"
    assert image_filename("圖.png", ".png") == "圖.png"


def test_image_filename_淨化不安全檔名():
    from redmine_mcp.tools.attachments import image_filename

    # 沿用 safe_upload_filename：只取 basename、移除控制字元與冒號。
    assert image_filename("../../etc/passwd", ".png") == "passwd.png"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_upload_tools.py -k image_filename -q`

Expected: FAIL，`ImportError: cannot import name 'image_filename'`

- [ ] **Step 3: 寫最小實作**

在 `src/redmine_mcp/tools/attachments.py` 的 `detect_image_type` 之後新增：

```python
#: 同型別的等價副檔名；命中時保留呼叫方給的寫法，不改寫成正規副檔名。
_EQUIVALENT_EXTENSIONS: dict[str, frozenset[str]] = {".jpg": frozenset({".jpeg"})}


def image_filename(candidate: str | None, ext: str) -> str:
    """把上傳檔名對齊到偵測出的圖像型別副檔名。

    Redmine 依副檔名決定附件的 content_type，副檔名與實際內容不符時附件會顯示不出來，
    因此一律以偵測結果改寫（例如內容是 PNG 但檔名寫 .jpg → 改成 .png）。
    同型別的等價副檔名（.jpeg 對 JPEG）予以保留，避免無謂改動呼叫方的寫法。

    參數:
        candidate: 呼叫方提供的檔名；未提供或淨化後為空時用 attachment。
        ext: detect_image_type 回傳的正規副檔名，含前導點。
    """
    name = safe_upload_filename(candidate or "attachment")
    stem, _, suffix = name.rpartition(".")
    if not stem:
        stem, suffix = name, ""
    current = f".{suffix.lower()}" if suffix else ""
    if current == ext or current in _EQUIVALENT_EXTENSIONS.get(ext, frozenset()):
        return name
    return f"{stem}{ext}"
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_upload_tools.py -k image_filename -q`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/tools/attachments.py tests/test_upload_tools.py
git commit -m "feat(redmine-mcp): 新增圖片上傳檔名的副檔名對齊"
```

---

### Task 3: `decode_image_payload` base64 解碼與把關

**Files:**
- Modify: `src/redmine_mcp/tools/attachments.py`（新增 import 與函式）
- Test: `tests/test_upload_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `detect_image_type`
- Produces: `decode_image_payload(content_base64: str, max_bytes: int) -> tuple[bytes, str, str]`，回傳 `(bytes, MIME, 正規副檔名)`；任何不合格的輸入都拋 `ValueError`。Task 4 依賴此簽章。

設計文件只列了兩個純函式；把解碼與把關也抽成純函式是本計畫的具體化決定，理由是工具層可保持精簡，且拒絕情境全部能用純函式單測，不必每個都跑一次 MCP 工具。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_upload_tools.py` 檔尾新增（`PNG_BYTES` 沿用檔案開頭既有常數）：

```python
def test_decode_image_payload_解出圖像內容與型別():
    import base64

    from redmine_mcp.tools.attachments import decode_image_payload

    payload = base64.b64encode(PNG_BYTES).decode()
    assert decode_image_payload(payload, 1024) == (PNG_BYTES, "image/png", ".png")


def test_decode_image_payload_剝除data_uri前綴與換行():
    import base64

    from redmine_mcp.tools.attachments import decode_image_payload

    encoded = base64.b64encode(PNG_BYTES).decode()
    # 模型常直接把整個 data URI 送來，也常在長字串中插入換行。
    with_prefix = "data:image/png;base64," + encoded[:20] + "\n" + encoded[20:]
    data, mime, ext = decode_image_payload(with_prefix, 1024)
    assert data == PNG_BYTES
    assert (mime, ext) == ("image/png", ".png")


def test_decode_image_payload_非法base64給明確錯誤():
    from redmine_mcp.tools.attachments import decode_image_payload

    with pytest.raises(ValueError) as exc:
        decode_image_payload("這不是base64!!!", 1024)

    assert "base64" in str(exc.value)


def test_decode_image_payload_拒絕非圖像內容():
    import base64

    from redmine_mcp.tools.attachments import decode_image_payload

    payload = base64.b64encode(b"BEGIN RSA PRIVATE KEY").decode()
    with pytest.raises(ValueError) as exc:
        decode_image_payload(payload, 1024)

    assert "PNG" in str(exc.value)


def test_decode_image_payload_拒絕空內容():
    from redmine_mcp.tools.attachments import decode_image_payload

    with pytest.raises(ValueError):
        decode_image_payload("", 1024)
    with pytest.raises(ValueError):
        decode_image_payload("data:image/png;base64,", 1024)


def test_decode_image_payload_超過上限被拒絕():
    import base64

    from redmine_mcp.tools.attachments import decode_image_payload

    oversized = base64.b64encode(PNG_BYTES + b"\x00" * 2048).decode()
    with pytest.raises(ValueError) as exc:
        decode_image_payload(oversized, 1024)

    assert "上限" in str(exc.value)


def test_decode_image_payload_超大payload在解碼前就早退():
    from unittest.mock import patch

    from redmine_mcp.tools.attachments import decode_image_payload

    # 早期拒絕的意義在於不為了拒絕而多複製一份記憶體；只驗訊息無法擋下
    # 「先解碼再檢查」這種變異，因此直接斷言 b64decode 沒有被呼叫。
    huge = "A" * 100_000
    with patch("redmine_mcp.tools.attachments.base64.b64decode") as decode:
        with pytest.raises(ValueError):
            decode_image_payload(huge, 1024)

    decode.assert_not_called()
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_upload_tools.py -k decode_image_payload -q`

Expected: FAIL，`ImportError: cannot import name 'decode_image_payload'`

- [ ] **Step 3: 寫最小實作**

先在 `src/redmine_mcp/tools/attachments.py` 的 import 區補上（維持 isort 排序，`base64`、`binascii` 在 `mimetypes` 之前）：

```python
import base64
import binascii
```

再於 `image_filename` 之後新增：

```python
#: base64 內容可能帶的 data URI 前綴，例如 "data:image/png;base64,"。
_DATA_URI_PREFIX = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)

#: base64 字串中的換行與空白；嚴格解碼不接受這些字元，需先移除。
_WHITESPACE = re.compile(r"\s+")


def decode_image_payload(content_base64: str, max_bytes: int) -> tuple[bytes, str, str]:
    """把 base64 圖片內容解成 (bytes, MIME, 正規副檔名)，並擋掉非圖像與過大的內容。

    模型常把整個 data URI 一起送來、也常在長字串中插入換行，因此先剝前綴、去空白再解碼。
    解碼前先用字串長度估算原始大小做早期拒絕，避免為了拒絕一個超大 payload
    而多複製一份記憶體。

    參數:
        content_base64: 圖片內容的 base64，可含 data URI 前綴。
        max_bytes: 單一附件大小上限（位元組）。
    """
    payload = _WHITESPACE.sub("", _DATA_URI_PREFIX.sub("", content_base64.strip()))
    if not payload:
        raise ValueError("content_base64 內容為空，拒絕上傳")
    # base64 每 4 個字元代表 3 個位元組；估算值只用於早退，正式比對在解碼之後。
    if len(payload) // 4 * 3 > max_bytes:
        raise ValueError(f"圖片內容超過上限 {max_bytes} 位元組，拒絕上傳")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"content_base64 不是合法的 base64 內容：{exc}") from None
    if not data:
        raise ValueError("圖片內容為空，拒絕上傳")
    if len(data) > max_bytes:
        raise ValueError(f"圖片大小 {len(data)} 位元組超過上限 {max_bytes} 位元組，拒絕上傳")
    detected = detect_image_type(data)
    if detected is None:
        raise ValueError(
            "content_base64 只接受 PNG／JPEG／GIF／WebP 圖片；"
            "其他型別請把檔案放進上傳目錄後改用 file_path"
        )
    mime, ext = detected
    return data, mime, ext
```

- [ ] **Step 4: 執行測試確認通過**

Run: `uv run pytest tests/test_upload_tools.py -k decode_image_payload -q`

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/tools/attachments.py tests/test_upload_tools.py
git commit -m "feat(redmine-mcp): 新增 base64 圖片解碼與型別把關"
```

---

### Task 4: `upload_attachment` 接上 `content_base64`

**Files:**
- Modify: `src/redmine_mcp/tools/attachments.py:238-300`（`upload_attachment` 的工具描述、參數與函式主體）
- Test: `tests/test_upload_tools.py`

**Interfaces:**
- Consumes: Task 2 的 `image_filename`、Task 3 的 `decode_image_payload`；既有的 `safe_source`、`safe_upload_filename`、`guess_content_type`
- Produces: `upload_attachment` 工具新增 `content_base64` 參數；回傳欄位不變（`site`／`token`／`filename`／`content_type`／`bytes`）

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_upload_tools.py` 檔尾新增：

```python
async def test_upload_attachment_以base64上傳圖片(settings, registry):
    import base64

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["filename"] = request.url.params.get("filename")
        seen["body"] = request.content
        return json_response(201, {"upload": {"token": "tok-base64"}})

    sites = registry(handler)
    mcp = create_server(sites)
    result = await _call(
        mcp,
        "upload_attachment",
        {
            "content_base64": base64.b64encode(PNG_BYTES).decode(),
            "filename": "螢幕截圖.jpg",
        },
    )
    await sites.aclose()

    # 內容是 PNG，副檔名被改寫成 .png，content_type 取偵測結果而非猜測值。
    assert seen["filename"] == "螢幕截圖.png"
    assert seen["body"] == PNG_BYTES
    assert result["token"] == "tok-base64"
    assert result["filename"] == "螢幕截圖.png"
    assert result["content_type"] == "image/png"
    assert result["bytes"] == len(PNG_BYTES)


async def test_upload_attachment_base64未給檔名時自動命名(settings, registry):
    import base64

    sites = registry(lambda r: json_response(201, {"upload": {"token": "t"}}))
    mcp = create_server(sites)
    result = await _call(
        mcp, "upload_attachment", {"content_base64": base64.b64encode(PNG_BYTES).decode()}
    )
    await sites.aclose()

    assert result["filename"] == "attachment.png"


async def test_upload_attachment_兩種輸入都給被拒絕(settings, registry):
    import base64

    source = settings.upload_dir / "圖.png"
    source.write_bytes(PNG_BYTES)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "t"}})

    sites = registry(handler)
    mcp = create_server(sites)
    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "upload_attachment",
            {
                "file_path": str(source),
                "content_base64": base64.b64encode(PNG_BYTES).decode(),
            },
        )
    await sites.aclose()

    assert "只能給一個" in str(exc.value)
    assert calls == []


async def test_upload_attachment_兩種輸入都沒給被拒絕(settings, registry):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "t"}})

    sites = registry(handler)
    mcp = create_server(sites)
    with pytest.raises(Exception) as exc:
        await _call(mcp, "upload_attachment", {})
    await sites.aclose()

    assert "content_base64" in str(exc.value)
    assert calls == []
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_upload_tools.py -k "base64 or 都給 or 都沒給" -q`

Expected: FAIL，第一個測試會因 `content_base64` 不是有效參數而報錯

- [ ] **Step 3: 寫最小實作**

把 `upload_attachment` 的工具描述改成：

```python
    @mcp.tool(
        annotations=UPLOAD,
        description=(
            "把圖片或檔案上傳到 Redmine，取得 upload token。"
            "token 要再帶進 create_issue／update_issue／add_issue_note 的 uploads 參數，"
            "才會真正掛到 issue 上。"
            "檔案已在磁碟上時用 file_path，只允許該站台參數檔中 upload_dir"
            "（未設定則沿用 download_dir）目錄內的檔案；"
            "只有圖片內容而沒有合法路徑時用 content_base64，僅接受 PNG／JPEG／GIF／WebP。"
        ),
    )
```

參數簽章改成（保留原有順序，`content_base64` 插在 `file_path` 之後）：

```python
    async def upload_attachment(
        file_path: Annotated[
            str | None,
            Field(
                description=(
                    "本機檔案路徑，必須位於允許的上傳目錄內；與 content_base64 二者其一必填。"
                )
            ),
        ] = None,
        content_base64: Annotated[
            str | None,
            Field(
                description=(
                    "圖片內容的 base64，可含 data URI 前綴；只接受 PNG／JPEG／GIF／WebP，"
                    "與 file_path 互斥。"
                )
            ),
        ] = None,
        site: Annotated[str | None, Field(description=WRITE_SITE_DESCRIPTION)] = None,
        filename: Annotated[
            str | None,
            Field(
                description=(
                    "上傳後顯示的檔名；未提供則使用原檔名，走 base64 時會依實際型別補副檔名。"
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        """上傳圖片或檔案並取得 upload token。

        參數:
            file_path: 本機檔案路徑，必須位於允許的上傳目錄內；與 content_base64 互斥。
            content_base64: 圖片內容的 base64，只接受 PNG／JPEG／GIF／WebP；與 file_path 互斥。
            site: 要寫入的站台代號；設定多個站台時必填。
            filename: 上傳後顯示的檔名；未提供則使用原檔名。
        """
```

函式主體從 `site_name, client = sites.resolve_for_write(site)` 之後全部改成：

```python
        upload_dir = client.settings.upload_dir
        if upload_dir is None:
            # 管理者不設上傳目錄就是在關閉上傳功能；base64 雖不落地磁碟也一併受此閘門
            # 管制，否則這個停用開關等於失效。
            raise ValueError(
                f"站台 {site_name} 未設定 upload_dir（或 download_dir），附件上傳功能已停用"
            )

        if file_path and content_base64:
            raise ValueError("file_path 與 content_base64 只能給一個")

        max_bytes = client.settings.max_attachment_bytes
        if content_base64:
            data, content_type, ext = decode_image_payload(content_base64, max_bytes)
            display_name = image_filename(filename, ext)
        elif file_path:
            source = safe_source(upload_dir, file_path)
            size = source.stat().st_size
            if size == 0:
                raise ValueError("檔案內容為空，拒絕上傳")
            if size > max_bytes:
                raise ValueError(f"檔案大小 {size} 位元組超過上限 {max_bytes} 位元組，拒絕上傳")
            display_name = safe_upload_filename(filename or source.name)
            content_type = guess_content_type(display_name)
            data = source.read_bytes()
        else:
            raise ValueError(
                "請給 file_path（上傳目錄內的檔案）或 content_base64（圖片內容）其中一個"
            )

        raw = await client.post_binary("/uploads.json", data, {"filename": display_name})
        token = (raw.get("upload") or {}).get("token")
        if not token:
            raise ValueError("Redmine 未回傳 upload token，請確認站台是否允許附件上傳")
        return {
            "site": site_name,
            "token": token,
            "filename": display_name,
            "content_type": content_type,
            "bytes": len(data),
        }
```

`else` 分支在邏輯上就是「兩者都沒給」的必填檢查，同時讓型別檢查器把 `file_path` 的 `str | None` 收斂成 `str`，因此不在前面另寫一次 neither 檢查。

- [ ] **Step 4: 執行完整測試與檢查確認通過**

Run: `uv run pytest tests/test_upload_tools.py -q`

Expected: 全部 passed（含既有 `file_path` 路徑測試一個都沒壞）

Run: `uv run ruff check . && uv run mypy src`

Expected: 兩者都沒有錯誤

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/tools/attachments.py tests/test_upload_tools.py
git commit -m "feat(redmine-mcp): upload_attachment 新增 content_base64 圖片入口"
```

---

### Task 5: base64 路徑的拒絕情境（工具層迴歸鎖）

**Files:**
- Test: `tests/test_upload_tools.py`

**Interfaces:**
- Consumes: Task 4 完成後的 `upload_attachment`
- Produces: 無新介面；本任務只補強行為的測試覆蓋

Task 3 已在純函式層覆蓋解碼與型別的拒絕情境。本任務驗證的是**工具層的性質**：拒絕發生在任何 HTTP 往返之前，而且停用開關與多站台保護對 base64 同樣有效——這幾件事純函式測不到。

- [ ] **Step 1: 寫測試**

在 `tests/test_upload_tools.py` 檔尾新增：

```python
async def test_upload_attachment_base64非圖像內容拒絕且不送出請求(settings, registry):
    import base64

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "t"}})

    sites = registry(handler)
    mcp = create_server(sites)
    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "upload_attachment",
            {"content_base64": base64.b64encode(b"BEGIN RSA PRIVATE KEY").decode()},
        )
    await sites.aclose()

    assert "PNG" in str(exc.value)
    # 拒絕必須發生在任何 HTTP 往返之前，否則機敏內容已經離開本機了。
    assert calls == []


async def test_upload_attachment_base64超過上限拒絕且不送出請求(settings, registry):
    import base64

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "t"}})

    sites = registry(handler)
    mcp = create_server(sites)
    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "upload_attachment",
            {"content_base64": base64.b64encode(PNG_BYTES + b"\x00" * 2048).decode()},
        )
    await sites.aclose()

    assert "上限" in str(exc.value)
    assert calls == []


async def test_base64上傳多站台時未指定站台會被拒絕(make_registry):
    import base64

    called: list[str] = []

    def handler(name: str):
        def inner(request: httpx.Request) -> httpx.Response:
            called.append(name)
            return json_response(201, {"upload": {"token": "t"}})

        return inner

    sites = make_registry({"main": handler("main"), "client-b": handler("client-b")})
    mcp = create_server(sites)
    with pytest.raises(Exception) as exc:
        await call_tool(
            mcp, "upload_attachment", {"content_base64": base64.b64encode(PNG_BYTES).decode()}
        )
    await sites.aclose()

    assert "site" in str(exc.value)
    # 「寫入永不扇出」：拒絕前不可有任何站台被實際寫入。
    assert called == []
```

再加一個「未設定上傳目錄時 base64 也停用」的測試（建構方式與既有的 `test_未設定上傳目錄時停用上傳` 相同，該測試約在第 122 行）：

```python
async def test_未設定上傳目錄時base64也停用() -> None:
    import base64

    site_settings = SiteSettings(
        name="default",
        url=BASE_URL,
        api_key="k",
        description=None,
        download_dir=None,
        upload_dir=None,
        max_attachment_bytes=1024,
    )
    sites = SiteRegistry(
        Settings(sites={"default": site_settings}),
        transports={"default": httpx.MockTransport(lambda r: json_response(200, {}))},
    )
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(
            mcp, "upload_attachment", {"content_base64": base64.b64encode(PNG_BYTES).decode()}
        )
    await sites.aclose()

    assert "upload_dir" in str(exc.value)
```

- [ ] **Step 2: 執行測試**

Run: `uv run pytest tests/test_upload_tools.py -q`

Expected: 這些測試應該直接 PASS。**這是刻意的**：本任務是行為的迴歸鎖，不是新功能。若有任何一個 FAIL，代表 Task 4 的實作有缺口——回頭修 Task 4 的實作，**不要**改測試預期。

- [ ] **Step 3: Commit**

```bash
git add tests/test_upload_tools.py
git commit -m "test(redmine-mcp): 補上 base64 上傳的拒絕情境覆蓋"
```

---

### Task 6: 指示詞與 guide 規則

**Files:**
- Modify: `src/redmine_mcp/server.py:14-33`（`_BASE_INSTRUCTIONS`）
- Modify: `src/redmine_mcp/guides/bug_report/report.md:61-63`（佐證章節）與 `:130`（檢核項）
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: Task 4 完成後的 `upload_attachment`（指示詞會提到 `file_path` 與 `content_base64` 兩個參數名）
- Produces: 無程式介面

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_server.py` 檔尾新增：

```python
async def test_指示詞要求把圖片上傳為附件(make_registry):
    sites = make_registry({"main": lambda r: httpx.Response(200)})
    mcp = create_server(sites)
    await sites.aclose()

    instructions = mcp.instructions or ""
    assert "upload_attachment" in instructions
    assert "uploads" in instructions
    assert "content_base64" in instructions
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `uv run pytest tests/test_server.py -k 圖片上傳為附件 -q`

Expected: FAIL，`assert "upload_attachment" in instructions`

- [ ] **Step 3: 寫最小實作**

在 `src/redmine_mcp/server.py` 的 `_BASE_INSTRUCTIONS` 中，接在「開單的人多半不是 IT……選填章節不問，沒有就整段拿掉。\n」那段之後插入：

```python
    "開單者提供的圖片（截圖、錯誤畫面）必須以 upload_attachment 上傳，"
    "取得 token 後帶入 create_issue／update_issue／add_issue_note 的 uploads 參數，"
    "不可只在文字裡描述畫面內容。圖片已在磁碟上時給 file_path（限 upload_dir 內）；"
    "只有圖片內容而無檔案時給 content_base64。\n"
```

再改 `src/redmine_mcp/guides/bug_report/report.md`。佐證章節（第 61-63 行）改成：

```markdown
## 佐證（選填）

<截圖、錯誤訊息原文、相關單號或訂單編號。截圖要用 upload_attachment 上傳成附件，這裡只寫文字說明>
```

第 130 行的檢核項改成：

```markdown
- [ ] 若有寫佐證，是截圖、錯誤訊息原文或相關單號；截圖已上傳為附件而非只用文字描述；沒有就整段拿掉
```

`change_request` 與 `feature_request` 的 guide 不動——它們的骨架沒有佐證章節，加規則只是雜訊。

- [ ] **Step 4: 執行完整測試與檢查確認通過**

Run: `uv run pytest -q`

Expected: 全部 passed（`test_guide_tools.py` 也要通過——它可能對 guide 內容有斷言）

Run: `uv run ruff check . && uv run mypy src`

Expected: 兩者都沒有錯誤

- [ ] **Step 5: Commit**

```bash
git add src/redmine_mcp/server.py src/redmine_mcp/guides/bug_report/report.md tests/test_server.py
git commit -m "feat(redmine-mcp): 指示詞要求把開單者提供的圖片上傳為附件"
```

---

## 完工驗證

- [ ] `uv run pytest -q` 全綠
- [ ] `uv run ruff check .` 無輸出
- [ ] `uv run mypy src` 無錯誤
- [ ] `git log --oneline` 應有 6 個新 commit（Task 1-6 各一）
- [ ] 人工確認：`tests/test_upload_tools.py` 中原有的 `file_path` 路徑測試內容未被修改（用 `git diff` 對照計畫開始前的 commit）
