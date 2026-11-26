"""檢查相容入口的連結，以及其他歷史文件與維護來源的同步狀態。"""
from pathlib import Path


def test_historical_guides_match_canonical_source():
    legacy = Path(__file__).resolve().parents[1]
    canonical = legacy.parent / "skills" / "redmine-issue-writing"
    note = Path("references/bdd-verification-note.md")

    assert (canonical / note).is_file()
    assert not (legacy / note).exists()

    canonical_link = b"](references/bdd-verification-note.md"
    legacy_link = b"](../skills/redmine-issue-writing/references/bdd-verification-note.md"
    canonical_skill = (canonical / "SKILL.md").read_bytes()
    assert canonical_link in canonical_skill
    assert (legacy / "SKILL.md").read_bytes() == canonical_skill.replace(canonical_link, legacy_link)

    for source in (canonical / "references").rglob("*.md"):
        relative = source.relative_to(canonical)
        if relative != note:
            target = legacy / relative
            assert target.read_bytes() == source.read_bytes(), str(target)
