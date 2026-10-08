"""Historical guides must not drift from the maintained plugin skill."""
from pathlib import Path

def test_historical_guides_match_canonical_source():
    legacy = Path(__file__).resolve().parents[1]
    canonical = legacy.parent / "skills" / "redmine-issue-writing"
    files = [canonical / "SKILL.md", *sorted((canonical / "references").rglob("*.md"))]
    for source in files:
        target = legacy / source.relative_to(canonical)
        assert target.read_bytes() == source.read_bytes(), str(target)
