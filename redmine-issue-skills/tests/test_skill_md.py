"""SKILL.md 與 references/ 的一致性，以及 frontmatter 的合法性。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import GUIDE_PATHS, REFERENCES

#: skill 的路由指示檔。
SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"

#: 落點的四組字面值。與 server 指示詞用同一組寫法，讓兩邊可互相比對；
#: 省略 stage 的 change／feature 沒有 "+" 形式，改由路徑測試涵蓋。
LANDING_PAIRS = ("bug+report", "bug+diagnose", "change+assess", "feature+assess")


def frontmatter() -> dict[str, str]:
    """解析 SKILL.md 開頭的 YAML frontmatter。

    只支援 `key: value` 的單層扁平格式——skill 的 frontmatter 只有 name 與
    description 兩個純量欄位，不為此引入 YAML 相依。

    回傳:
        欄位名 → 值（已去除頭尾空白與包夾的引號）。
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md 開頭必須是 --- 包夾的 frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip("\"'")
    return fields


def test_frontmatter_有_name_與_description():
    # 這兩個欄位是 skill 被載入與被觸發的必要條件，缺任一個 skill 等於不存在。
    fields = frontmatter()

    assert fields.get("name") == "redmine-issue-writing"
    assert fields.get("description", "").strip(), "description 不可為空"


def test_description_含口語觸發詞():
    # description 是觸發的唯一依據。使用者不會說「取得 issue 格式指南」，
    # 他會說「幫我開一張單」。這些詞缺了，skill 就不會被載入。
    description = frontmatter()["description"]

    for word in ("開單", "臭蟲", "調整", "新需求", "Redmine"):
        assert word in description, f"description 缺少觸發詞「{word}」"


def test_SKILL_MD_提到的每個路徑都存在():
    # 文字指路寫錯不會有 KeyError，只會讓模型讀不到檔案而默默瞎寫。
    text = SKILL_MD.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"references/([\w/]+\.md)", text))

    assert mentioned, "SKILL.md 沒有提到任何 references/ 路徑"
    for rel in sorted(mentioned):
        assert (REFERENCES / rel).is_file(), f"SKILL.md 指向不存在的檔案 references/{rel}"


def test_references_底下的每個檔案都被_SKILL_MD_提到():
    # 反方向：新增了骨架卻忘了在 SKILL.md 加一列，模型永遠不會讀到它。
    text = SKILL_MD.read_text(encoding="utf-8")

    for rel in sorted(GUIDE_PATHS.values()):
        assert f"references/{rel}" in text, f"SKILL.md 的落點對照表漏了 references/{rel}"


@pytest.mark.parametrize("pair", LANDING_PAIRS)
def test_落點以逐項列舉呈現(pair: str):
    # 通則要模型自行推論「什麼算後段」，bug+report 就被誤判過一次。六組一律窮舉。
    text = SKILL_MD.read_text(encoding="utf-8")

    assert pair in text, f"SKILL.md 的落點列舉缺少 {pair}"


def test_交付規則要求標題與內文分開():
    # Redmine 的「主題」與「概述」是兩個欄位。混在一塊會逼使用者自己切。
    text = SKILL_MD.read_text(encoding="utf-8")

    assert "主題" in text and "概述" in text
    assert "先印" in text, "SKILL.md 未寫明先印在對話裡的交付規則"


def test_明講沒有上傳能力():
    # 沒有 upload_attachment 可用。不講清楚，模型會自己寫 ![](檔名)，
    # 而那個檔名在 Redmine 上並不存在，圖片永遠是破的。
    text = SKILL_MD.read_text(encoding="utf-8")

    assert "附件區" in text, "SKILL.md 未交代截圖要由使用者自己拖進附件區"


def test_指出新功能開發要拆子單並指路到骨架():
    # 規則本體只寫在 feature.md 一處；SKILL.md 負責讓模型在判單別的當下就知道
    # 「這件事要拆單」，並知道去哪裡讀怎麼拆。少了這一行，拆單規則不會被讀到。
    text = SKILL_MD.read_text(encoding="utf-8")

    assert "子單" in text, "SKILL.md 未提到新功能開發要拆子單"
    for layer in ("UI", "API", "DB"):
        assert layer in text, f"SKILL.md 的拆單指路缺少 {layer}"
    assert "references/feature_request/feature.md" in text, "SKILL.md 未指路到拆單規則所在的骨架"


def test_落點交代新需求沒有註記落點():
    # 新需求的每一種內容都有概述可去：需求面回母單，資料表與結構異動進 DB 子單。
    # 只要落點表把 feature+assess 寫成「新增註記」，模型就會把反覆修正堆成一則
    # 又一則註記，而概述停在第一版——這件事漏了不會有任何東西報錯。
    text = SKILL_MD.read_text(encoding="utf-8")

    assert "它沒有註記落點" in text, "SKILL.md 未交代新需求沒有註記落點"
    assert "DB 子單的概述" in text, "SKILL.md 未指出資料表與關聯要寫進 DB 子單的概述"
