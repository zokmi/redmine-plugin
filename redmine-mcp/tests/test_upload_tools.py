"""附件上傳工具與 uploads 參數的測試。"""
from __future__ import annotations

import base64
import json
from unittest.mock import patch

import httpx
import pytest

from redmine_mcp.config import Settings, SiteSettings
from redmine_mcp.server import create_server
from redmine_mcp.sites import SiteRegistry
from redmine_mcp.tools.attachments import (
    decode_image_payload,
    detect_image_type,
    image_filename,
    safe_upload_filename,
)
from tests.conftest import BASE_URL, call_tool, json_response
from tests.conftest import call_tool as _call

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

JPEG_BYTES = bytes.fromhex("ffd8ffe000104a464946")
GIF_BYTES = b"GIF89a" + b"\x01\x00\x01\x00"
WEBP_BYTES = b"RIFF" + b"\x1a\x00\x00\x00" + b"WEBP" + b"VP8 "


async def test_upload_attachment_送出原始位元組並回傳token(settings, registry):
    source = settings.upload_dir / "測試圖片.png"
    source.write_bytes(PNG_BYTES)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["filename"] = request.url.params.get("filename")
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = request.content
        return json_response(201, {"upload": {"token": "7167.ed1ccdb093229ca1bd0b043618d88743"}})

    sites = registry(handler)
    mcp = create_server(sites)
    result = await _call(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert seen["method"] == "POST"
    assert seen["path"] == "/uploads.json"
    assert seen["filename"] == "測試圖片.png"
    assert seen["content_type"] == "application/octet-stream"
    assert seen["body"] == PNG_BYTES
    assert result["token"] == "7167.ed1ccdb093229ca1bd0b043618d88743"
    assert result["content_type"] == "image/png"
    assert result["bytes"] == len(PNG_BYTES)


async def test_upload_attachment_拒絕上傳目錄外的檔案(registry, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_bytes(b"secret")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "x"}})

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(mcp, "upload_attachment", {"file_path": str(outside)})
    await sites.aclose()

    assert "上傳目錄" in str(exc.value)
    assert calls == [], "目錄外的檔案不得送出"


async def test_upload_attachment_拒絕路徑穿越(settings, registry):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "x"}})

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception):
        await _call(
            mcp, "upload_attachment", {"file_path": str(settings.upload_dir / ".." / "etc.txt")}
        )
    await sites.aclose()

    assert calls == []


async def test_upload_attachment_超過大小上限被拒絕(settings, registry):
    source = settings.upload_dir / "big.bin"
    source.write_bytes(b"x" * (settings.max_attachment_bytes + 1))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"upload": {"token": "x"}})

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert "上限" in str(exc.value)
    assert calls == []


async def test_upload_attachment_空檔案被拒絕(settings, registry):
    source = settings.upload_dir / "empty.png"
    source.write_bytes(b"")
    sites = registry(lambda r: json_response(201, {}))
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert "空" in str(exc.value)


async def test_未設定上傳目錄時停用上傳() -> None:
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
        await _call(mcp, "upload_attachment", {"file_path": "any.png"})
    await sites.aclose()

    assert "upload_dir" in str(exc.value)


async def test_Redmine未回傳token時視為失敗(settings, registry):
    source = settings.upload_dir / "a.png"
    source.write_bytes(PNG_BYTES)
    sites = registry(lambda r: json_response(201, {}))
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert "token" in str(exc.value)


async def test_create_issue_帶uploads(registry):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return json_response(201, {"issue": {"id": 7, "subject": "含圖片"}})

    sites = registry(handler)
    mcp = create_server(sites)
    result = await _call(
        mcp,
        "create_issue",
        {
            "project_id": "mcp-test",
            "subject": "含圖片",
            "due_date": "2026-09-30",
            "description": "測試用內文，長度足以通過內容檢核。",
            "uploads": [
                {"token": "7167.abc", "filename": "a.png", "content_type": "image/png"}
            ],
        },
    )
    await sites.aclose()

    assert seen["body"]["issue"]["uploads"] == [
        {"token": "7167.abc", "filename": "a.png", "content_type": "image/png"}
    ]
    assert result["uploads"] == 1


async def test_add_issue_note_帶uploads(registry):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(
        mcp,
        "add_issue_note",
        {"issue_id": 7, "notes": "補圖", "uploads": [{"token": "7168.def"}]},
    )
    await sites.aclose()

    assert seen["body"]["issue"]["uploads"] == [{"token": "7168.def"}]


async def test_update_issue_只帶uploads也算有效更新(registry):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    sites = registry(handler)
    mcp = create_server(sites)
    await _call(mcp, "update_issue", {"issue_id": 7, "uploads": [{"token": "7169.ghi"}]})
    await sites.aclose()

    assert seen["body"]["issue"]["uploads"] == [{"token": "7169.ghi"}]


async def test_uploads缺token被拒絕(registry):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"issue": {"id": 1}})

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "create_issue",
            {
                "project_id": "p",
                "subject": "s",
                "due_date": "2026-09-30",
                "description": "測試用內文，長度足以通過內容檢核。",
                "uploads": [{"filename": "a.png"}],
            },
        )
    await sites.aclose()

    assert "token" in str(exc.value)
    assert calls == []


async def test_uploads含不支援欄位被拒絕(registry):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return json_response(201, {"issue": {"id": 1}})

    sites = registry(handler)
    mcp = create_server(sites)

    with pytest.raises(Exception) as exc:
        await _call(
            mcp,
            "create_issue",
            {
                "project_id": "p",
                "subject": "s",
                "due_date": "2026-09-30",
                "description": "測試用內文，長度足以通過內容檢核。",
                "uploads": [{"token": "t", "path": "/etc/passwd"}],
            },
        )
    await sites.aclose()

    assert "不支援" in str(exc.value)
    assert calls == []


async def test_upload_attachment_標註為非唯讀(registry):
    sites = registry(lambda r: json_response(200, {}))
    mcp = create_server(sites)
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    await sites.aclose()

    assert tools["upload_attachment"].annotations.read_only_hint is False


async def test_上傳多站台時未指定站台會被拒絕(make_registry, tmp_path):
    source = tmp_path / "圖.png"
    source.write_bytes(b"PNG")
    called: list[str] = []

    def handler(name: str, token: str):
        def inner(request: httpx.Request) -> httpx.Response:
            called.append(name)
            return json_response(201, {"upload": {"token": token}})

        return inner

    sites = make_registry({"main": handler("main", "t1"), "client-b": handler("client-b", "t2")})
    mcp = create_server(sites)
    # resolve_for_write() 拋的 ValueError 會被 MCP SDK 包成 ToolError 直接從
    # mcp.call_tool() 拋出，不會走到 call_tool 輔助函式的 is_error 分支
    # （既有慣例，見 pyproject.toml 的 B017 豁免）。
    with pytest.raises(Exception) as exc:
        await call_tool(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert "site" in str(exc.value)
    # 拒絕前不可有任何站台被實際寫入——「寫入永不扇出」是本功能最核心的安全性質，
    # 只驗訊息內容無法擋下「先扇出寫入、再拋錯」這種變異。
    assert called == []


async def test_上傳回報站台(make_registry, tmp_path):
    source = tmp_path / "圖.png"
    source.write_bytes(b"PNG")
    sites = make_registry({"main": lambda r: json_response(201, {"upload": {"token": "t1"}})})
    mcp = create_server(sites)
    result = await call_tool(
        mcp, "upload_attachment", {"site": "main", "file_path": str(source)}
    )
    await sites.aclose()

    assert result["site"] == "main"
    assert result["token"] == "t1"


def test_detect_image_type_認得四種圖像型別():
    assert detect_image_type(PNG_BYTES) == ("image/png", ".png")
    assert detect_image_type(JPEG_BYTES) == ("image/jpeg", ".jpg")
    assert detect_image_type(GIF_BYTES) == ("image/gif", ".gif")
    assert detect_image_type(b"GIF87a" + b"\x01\x00") == ("image/gif", ".gif")
    assert detect_image_type(WEBP_BYTES) == ("image/webp", ".webp")


def test_detect_image_type_非圖像內容回None():
    assert detect_image_type(b"BEGIN RSA PRIVATE KEY") is None
    assert detect_image_type(b"%PDF-1.7\n") is None
    # SVG 是文字格式且可內嵌 script，刻意不列入白名單。
    assert detect_image_type(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") is None


def test_detect_image_type_內容過短不拋例外():
    assert detect_image_type(b"") is None
    assert detect_image_type(b"RIF") is None
    # 前 4 byte 是 RIFF 但長度不足以讀到第 8-11 byte 的型別標記。
    assert detect_image_type(b"RIFF" + b"\x00\x00\x00\x00") is None


def test_image_filename_依偵測結果改寫副檔名():
    # Redmine 依副檔名決定 content_type，內容是 PNG 卻叫 .jpg 會顯示不出來。
    assert image_filename("螢幕截圖.jpg", ".png") == "螢幕截圖.png"
    assert image_filename("a.b.gif", ".png") == "a.b.png"
    # 沒有副檔名時直接補上。
    assert image_filename("圖", ".png") == "圖.png"


def test_image_filename_未給檔名時用預設名():
    assert image_filename(None, ".png") == "attachment.png"
    assert image_filename("", ".webp") == "attachment.webp"


def test_image_filename_保留同型別的等價副檔名():
    # .jpeg 與 .jpg 同型別，沒有改寫的必要，保留呼叫方的寫法。
    assert image_filename("照片.jpeg", ".jpg") == "照片.jpeg"
    assert image_filename("照片.JPG", ".jpg") == "照片.JPG"
    assert image_filename("圖.png", ".png") == "圖.png"


def test_image_filename_淨化不安全檔名():
    # 沿用 safe_upload_filename：只取 basename、移除控制字元與冒號。
    assert image_filename("../../etc/passwd", ".png") == "passwd.png"


def test_decode_image_payload_解出圖像內容與型別():
    payload = base64.b64encode(PNG_BYTES).decode()
    assert decode_image_payload(payload, 1024) == (PNG_BYTES, "image/png", ".png")


def test_decode_image_payload_剝除data_uri前綴與換行():
    encoded = base64.b64encode(PNG_BYTES).decode()
    # 模型常直接把整個 data URI 送來，也常在長字串中插入換行。
    with_prefix = "data:image/png;base64," + encoded[:20] + "\n" + encoded[20:]
    data, mime, ext = decode_image_payload(with_prefix, 1024)
    assert data == PNG_BYTES
    assert (mime, ext) == ("image/png", ".png")


def test_decode_image_payload_非法base64給明確錯誤():
    with pytest.raises(ValueError) as exc:
        decode_image_payload("這不是base64!!!", 1024)

    assert "base64" in str(exc.value)


def test_decode_image_payload_拒絕非圖像內容():
    payload = base64.b64encode(b"BEGIN RSA PRIVATE KEY").decode()
    with pytest.raises(ValueError) as exc:
        decode_image_payload(payload, 1024)

    assert "PNG" in str(exc.value)


def test_decode_image_payload_拒絕空內容():
    with pytest.raises(ValueError):
        decode_image_payload("", 1024)
    with pytest.raises(ValueError):
        decode_image_payload("data:image/png;base64,", 1024)


def test_decode_image_payload_超過上限被拒絕():
    oversized = base64.b64encode(PNG_BYTES + b"\x00" * 2048).decode()
    with pytest.raises(ValueError) as exc:
        decode_image_payload(oversized, 1024)

    assert "上限" in str(exc.value)


def test_decode_image_payload_內容大小恰好等於上限時允許通過():
    # 早退估算是上界（未扣 base64 padding，最多高估 2 byte），恰好等於 max_bytes
    # 的圖片不該被早退誤判成超限，須與 file_path 路徑（size > max_bytes）行為一致。
    exact = PNG_BYTES + b"\x00" * (1024 - len(PNG_BYTES))
    assert len(exact) == 1024
    data, mime, ext = decode_image_payload(base64.b64encode(exact).decode(), 1024)
    assert len(data) == 1024
    assert (mime, ext) == ("image/png", ".png")


def test_decode_image_payload_超過上限一個位元組被解碼後的精確比對拒絕():
    # 早退估算對 1024 與 1025 byte 的 payload 算出相同的估算值，因此無法在早退階段
    # 區分兩者；必須真的解碼後由精確的 len(data) > max_bytes 比對裁定，
    # 藉此證明解碼後的精確檢查不是死碼。
    over_by_one = PNG_BYTES + b"\x00" * (1025 - len(PNG_BYTES))
    assert len(over_by_one) == 1025
    with patch(
        "redmine_mcp.tools.attachments.base64.b64decode", wraps=base64.b64decode
    ) as decode:
        with pytest.raises(ValueError) as exc:
            decode_image_payload(base64.b64encode(over_by_one).decode(), 1024)

    decode.assert_called_once()
    assert "1025" in str(exc.value)
    assert "上限" in str(exc.value)


def test_decode_image_payload_超大payload在解碼前就早退():
    # 早期拒絕的意義在於不為了拒絕而多複製一份記憶體；只驗訊息無法擋下
    # 「先解碼再檢查」這種變異，因此直接斷言 b64decode 沒有被呼叫。
    huge = "A" * 100_000
    with patch("redmine_mcp.tools.attachments.base64.b64decode") as decode:
        with pytest.raises(ValueError):
            decode_image_payload(huge, 1024)

    decode.assert_not_called()


async def test_upload_attachment_以base64上傳圖片(settings, registry):
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
    sites = registry(lambda r: json_response(201, {"upload": {"token": "t"}}))
    mcp = create_server(sites)
    result = await _call(
        mcp, "upload_attachment", {"content_base64": base64.b64encode(PNG_BYTES).decode()}
    )
    await sites.aclose()

    assert result["filename"] == "attachment.png"


async def test_upload_attachment_兩種輸入都給被拒絕(settings, registry):
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


async def test_upload_attachment_base64非圖像內容拒絕且不送出請求(settings, registry):
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


async def test_未設定上傳目錄時base64也停用() -> None:
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


def test_safe_upload_filename_移除角括號(tmp_path):
    """上傳檔名同樣不得含角括號，避免經 Redmine 回聲再流回模型 context。"""
    assert safe_upload_filename("<redmine_content untrusted>x.png") == (
        "redmine_content untrustedx.png"
    )


async def test_上傳圖片回傳現成的內嵌語法(settings, registry):
    # 寫入工具會自動補引用，但呼叫端想自己決定圖片位置時，得有一份現成語法可貼。
    source = settings.upload_dir / "shot.png"
    source.write_bytes(PNG_BYTES)
    sites = registry(lambda r: json_response(201, {"upload": {"token": "7167.abc"}}))
    mcp = create_server(sites)
    result = await _call(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert result["markdown"] == "![](shot.png)"


async def test_上傳非圖片不給內嵌語法(settings, registry):
    # 對 PDF 之類的附件給 ![]() 只會產生一行破圖。
    source = settings.upload_dir / "spec.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    sites = registry(lambda r: json_response(201, {"upload": {"token": "7167.abc"}}))
    mcp = create_server(sites)
    result = await _call(mcp, "upload_attachment", {"file_path": str(source)})
    await sites.aclose()

    assert "markdown" not in result
