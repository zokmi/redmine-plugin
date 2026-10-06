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
    # 這正是 SETUP.md 排錯表的頭號情況:url 少了 /redmine 會拿到 HTML。
    transport = _transport(200, text="<html>login page</html>")
    with pytest.raises(RedmineError) as exc:
        await verify_credentials("https://a", "key", transport)
    assert "子路徑" in str(exc.value)


async def test_金鑰錯誤且回應是合法_JSON_時不提示子路徑():
    # 對照 SETUP.md 排錯表:401/403 且回應是 JSON,代表真的是 API key 錯誤或
    # 無權限,不是網址少子路徑。若這裡誤加提示,會出現「先叫使用者查金鑰又叫他
    # 查網址」的自我矛盾訊息,而金鑰打錯正是這種情境最常見的原因。
    transport = _transport(401, {})
    with pytest.raises(RedmineAuthError) as exc:
        await verify_credentials("https://a/redmine", "bad", transport)
    assert "子路徑" not in str(exc.value)


async def test_回應缺少_user_欄位視為失敗():
    transport = _transport(200, {})
    with pytest.raises(RedmineError):
        await verify_credentials("https://a/redmine", "key", transport)


async def test_錯誤訊息不含金鑰():
    transport = _transport(401, {})
    with pytest.raises(RedmineError) as exc:
        await verify_credentials("https://a/redmine", "super-secret-key", transport)
    assert "super-secret-key" not in str(exc.value)
