"""封裝相關的測試：版本號單一來源，以及文件與程式碼的一致性。"""
from __future__ import annotations

import re
from pathlib import Path

import redmine_mcp
from redmine_mcp.client import USER_AGENT
from redmine_mcp.server import SERVER_VERSION


def test_版本號取自套件_metadata():
    # 開發環境以 editable 模式安裝，metadata 讀得到；讀不到代表 __init__ 的
    # 後備分支被觸發，那在 CI 中應視為環境有問題而非通過。
    assert redmine_mcp.__version__ != "0+unknown"


def test_三處版本宣告皆來自同一來源():
    # pyproject 的 version 改動後，server 宣告版本與 User-Agent 必須自動跟著變，
    # 不能靠人工同步三個字面值。
    assert SERVER_VERSION == redmine_mcp.__version__
    assert USER_AGENT == f"redmine-mcp/{redmine_mcp.__version__}"


def test_README_的工具數與實際註冊數一致():
    """工具數寫死在文件裡，靠人工 review 一定會再過期（實際 17 個時三份文件都還寫 16）。"""
    src = Path(__file__).resolve().parents[1] / "src" / "redmine_mcp"
    實際 = sum(
        f.read_text(encoding="utf-8").count("@mcp.tool") for f in src.rglob("*.py")
    )
    assert 實際 > 0, "抓不到任何 @mcp.tool，這條測試的抓法失效了"

    for readme in (
        Path(__file__).resolve().parents[1] / "README.md",
        Path(__file__).resolve().parents[2] / "README.md",  # repo 根；單獨散佈時不存在
    ):
        if not readme.exists():
            continue
        數字 = re.findall(r"(\d+)\s*個工具", readme.read_text(encoding="utf-8"))
        # 只檢 redmine-mcp 自己的那句；根 README 另有 llm-wiki-mcp 的「3 個工具」。
        assert str(實際) in 數字, f"{readme.name} 的工具數已過期，實際 {實際} 個：{數字}"


def test_SETUP_只提供_plugin_站台設定流程():
    setup = Path(__file__).resolve().parents[1] / "SETUP.md"
    text = setup.read_text(encoding="utf-8")
    assert "redmine-setup" in text
    assert "已安裝 plugin" in text
    assert "不會執行 `claude mcp add`、`codex mcp add`" in text
    assert "逐步檢查" not in text
