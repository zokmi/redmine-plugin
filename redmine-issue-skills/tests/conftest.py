"""測試共用：把 references/ 底下的六份骨架讀進來。"""
from __future__ import annotations

from pathlib import Path

#: references/ 的根目錄。測試檔在 tests/ 底下，往上一層就是本子專案的根。
REFERENCES = Path(__file__).resolve().parents[1] / "references"

#: (kind, stage) → 相對於 references/ 的路徑。
#: 這份對照表與 SKILL.md 的落點對照表是同一份事實的兩種寫法，兩者的一致性
#: 由 test_skill_md.py 雙向驗證（Task 2）。
GUIDE_PATHS: dict[tuple[str, str | None], str] = {
    ("bug", "report"): "bug_report/report.md",
    ("bug", "diagnose"): "bug_report/diagnose.md",
    ("change", None): "change_request/change.md",
    ("change", "assess"): "change_request/assess.md",
    ("feature", None): "feature_request/feature.md",
    ("feature", "assess"): "feature_request/assess.md",
}


def guide(kind: str, stage: str | None = None) -> str:
    """讀出指定單別／階段的骨架全文。

    參數:
        kind: 單別，bug／change／feature。
        stage: 撰寫階段。bug 必填（report／diagnose）；change 與 feature 省略時
            取概述骨架，給 assess 取評估模板（change 的落點是註記，feature 的落點
            是 DB 子單的概述）。
    回傳:
        骨架的 Markdown 全文。
    """
    return (REFERENCES / GUIDE_PATHS[(kind, stage)]).read_text(encoding="utf-8")
