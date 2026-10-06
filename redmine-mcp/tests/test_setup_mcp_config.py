"""~/.gemini/settings.json 這類 MCP 設定檔的改寫邏輯。

這是本次唯一會動到使用者既有檔案的邏輯，因此與檔案 IO 分離成純函式，
逐個邊界情境驗證，而不是靠手動跑一次安裝。
"""
from __future__ import annotations

import json

from redmine_mcp.setup.mcp_config import (
    remove_mcp_server,
    upsert_mcp_server,
    讀出設定,
)


def test_加入時其餘設定原封不動():
    原文 = json.dumps(
        {
            "ide": {"hasSeenNudge": True},
            "mcpServers": {"serena": {"command": "uv", "args": ["run", "serena"]}},
        },
        indent=2,
    )
    新文 = upsert_mcp_server(原文, "redmine", "redmine-mcp")
    assert 新文 is not None
    資料 = json.loads(新文)
    assert 資料["mcpServers"]["redmine"] == {"command": "redmine-mcp"}
    assert 資料["mcpServers"]["serena"] == {"command": "uv", "args": ["run", "serena"]}
    assert 資料["ide"] == {"hasSeenNudge": True}


def test_原本沒有_mcpservers_也能加():
    新文 = upsert_mcp_server('{"ide": {}}', "redmine", "redmine-mcp")
    assert 新文 is not None
    assert json.loads(新文)["mcpServers"]["redmine"]["command"] == "redmine-mcp"


def test_空字串當成空設定():
    新文 = upsert_mcp_server("", "redmine", "redmine-mcp")
    assert 新文 is not None
    assert json.loads(新文) == {"mcpServers": {"redmine": {"command": "redmine-mcp"}}}


def test_同名會被覆蓋成新的啟動指令():
    原文 = json.dumps({"mcpServers": {"redmine": {"command": "舊的"}}})
    新文 = upsert_mcp_server(原文, "redmine", "redmine-mcp")
    assert 新文 is not None
    assert json.loads(新文)["mcpServers"]["redmine"] == {"command": "redmine-mcp"}


def test_壞掉的_json_回_none():
    # 含註解的 JSONC 也落在這裡：解析不了就不該猜，一律不碰檔案。
    assert upsert_mcp_server("{ 這不是 json", "redmine", "redmine-mcp") is None
    assert upsert_mcp_server('{"a": 1} // 註解', "redmine", "redmine-mcp") is None


def test_頂層或_mcpservers_不是物件時回_none():
    assert upsert_mcp_server("[1, 2, 3]", "redmine", "redmine-mcp") is None
    assert upsert_mcp_server('{"mcpServers": []}', "redmine", "redmine-mcp") is None


def test_bom_會被剝掉且不寫回():
    原文 = "﻿" + json.dumps({"mcpServers": {}})
    新文 = upsert_mcp_server(原文, "redmine", "redmine-mcp")
    assert 新文 is not None
    assert not 新文.startswith("﻿")


def test_輸出是兩格縮排且以換行結尾():
    # 與 Gemini CLI 自己寫出來的格式一致，diff 才不會整份翻掉。
    新文 = upsert_mcp_server('{"mcpServers": {}}', "redmine", "redmine-mcp")
    assert 新文 is not None
    assert '\n  "mcpServers"' in 新文
    assert 新文.endswith("\n")


def test_中文不會被跳脫成_unicode_逃脫序列():
    原文 = json.dumps({"mcpServers": {}, "說明": "測試"}, ensure_ascii=False)
    新文 = upsert_mcp_server(原文, "redmine", "redmine-mcp")
    assert 新文 is not None
    assert "測試" in 新文


def test_移除只刪那一鍵():
    原文 = json.dumps(
        {"ide": {}, "mcpServers": {"redmine": {"command": "x"}, "serena": {"command": "y"}}}
    )
    新文 = remove_mcp_server(原文, "redmine")
    assert 新文 is not None
    資料 = json.loads(新文)
    assert "redmine" not in 資料["mcpServers"]
    assert 資料["mcpServers"]["serena"] == {"command": "y"}
    assert "ide" in 資料


def test_移除不存在的鍵不算失敗():
    原文 = json.dumps({"mcpServers": {}})
    新文 = remove_mcp_server(原文, "redmine")
    assert 新文 is not None
    assert json.loads(新文)["mcpServers"] == {}


def test_移除壞掉的_json_回_none():
    assert remove_mcp_server("{ 壞", "redmine") is None


def test_讀出設定回傳完整值而不只挑_command():
    原文 = json.dumps(
        {"mcpServers": {"redmine": {"command": "舊的", "args": ["a"], "trust": True}}}
    )
    assert 讀出設定(原文, "redmine") == {"command": "舊的", "args": ["a"], "trust": True}


def test_讀出設定認得遠端型_url_設定():
    # {"url": ...} 這種遠端型 entry 沒有 command 欄位，但仍然是「已經有這一筆」，
    # 不可被誤判成「本來就沒有」。
    原文 = json.dumps({"mcpServers": {"redmine": {"url": "https://internal/mcp"}}})
    assert 讀出設定(原文, "redmine") == {"url": "https://internal/mcp"}


def test_讀出設定認得字串型設定():
    原文 = json.dumps({"mcpServers": {"redmine": "舊的字串設定"}})
    assert 讀出設定(原文, "redmine") == "舊的字串設定"


def test_讀出設定的兩種_none():
    原文 = json.dumps({"mcpServers": {"redmine": {"command": "舊的"}}})
    assert 讀出設定(原文, "不存在") is None
    assert 讀出設定("{ 壞", "redmine") is None
