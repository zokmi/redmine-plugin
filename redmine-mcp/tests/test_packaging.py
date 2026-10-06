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


#: 中文數字對照，供把 SETUP.md 裡的「七個步驟」換算成整數。安裝步驟不會多到
#: 需要一般化的中文數字剖析器。
_中文數字 = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def test_SETUP_的安裝步驟數與實際註冊數一致():
    """步驟數寫死在文件裡，理由同上一條：靠人工 review 一定會再過期。

    實際發生過——`InstallSkillsStep` 加進 `STEPS` 之後 SETUP.md 仍寫「六個步驟」。
    """
    from redmine_mcp.setup.installer import STEPS

    setup = Path(__file__).resolve().parents[1] / "SETUP.md"
    文字 = setup.read_text(encoding="utf-8")
    數字 = re.findall(r"逐步檢查([一二三四五六七八九十])個步驟", 文字)
    assert 數字, "抓不到 SETUP.md 的步驟數，這條測試的抓法失效了"
    for 中文 in 數字:
        assert _中文數字[中文] == len(STEPS), (
            f"SETUP.md 寫「{中文}個步驟」，實際 STEPS 有 {len(STEPS)} 個"
        )

    # 步驟數對得上還不夠：那句括號裡逐項列出每一步在做什麼。這裡只守住最近
    # 一次漏掉的那一步——括號裡用的是口語簡稱（「uv」而非 title 的「uv 可用」），
    # 逐字比對 step.title 只會變成假紅燈。
    assert "撰寫格式 skill" in 文字, "SETUP.md 的步驟列舉漏掉了撰寫格式 skill"
