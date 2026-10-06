"""精簡讀取不得隱藏截斷或改變完整讀取的內容。"""
import importlib.util
from pathlib import Path

from redmine_mcp.formatting import format_issue_detail
from redmine_mcp.server import create_server
from tests.conftest import call_tool, json_response


def test_summary_bounds_text_and_keeps_untrusted_boundary():
    raw = {"id": 1, "subject": "測試", "description": "<資料>" * 500,
           "journals": [{"id": 2, "notes": "留言" * 1000}]}
    result = format_issue_detail(raw, summary=True)
    assert result["summary"] is True
    assert result["description"]["truncated"] is True
    assert result["description"]["length"] == len(raw["description"])
    assert 'untrusted="true"' in result["description"]["preview"]
    assert "&lt;資料&gt;" in result["description"]["preview"]
    assert result["journals"][0]["notes"]["truncated"] is True
    assert "custom_fields" not in result
    full = format_issue_detail(raw)
    assert isinstance(full["description"], str)
    assert full["description"].count("&lt;資料&gt;") == 500
    assert isinstance(full["journals"][0]["notes"], str)


def test_journal_offset_reads_older_windows_without_overlap():
    raw = {"journals": [{"id": i} for i in range(6)]}
    latest = format_issue_detail(raw, journals_limit=2)
    older = format_issue_detail(raw, journals_limit=2, journals_offset=2)
    assert [j["id"] for j in latest["journals"]] == [4, 5]
    assert [j["id"] for j in older["journals"]] == [2, 3]
    assert older["journals_next_offset"] == 4
    end = format_issue_detail(raw, journals_limit=2, journals_offset=4)
    assert [j["id"] for j in end["journals"]] == [0, 1]
    assert "journals_next_offset" not in end


async def test_summary_and_journal_offset_are_exposed_by_tool(registry):
    sites = registry(lambda r: json_response(200, {"issue": {
        "id": 1, "description": "內容" * 1000,
        "journals": [{"id": i} for i in range(6)],
    }}))
    try:
        result = await call_tool(create_server(sites), "get_issue", {
            "issue_id": 1, "summary": True, "include": ["journals"],
            "journals_limit": 2, "journals_offset": 2,
        })
        issue = next(iter(result["sites"].values()))
        assert issue["summary"] is True
        assert issue["description"]["truncated"] is True
        assert [j["id"] for j in issue["journals"]] == [2, 3]
    finally:
        await sites.aclose()


def test_diff_budget_counts_duplicate_output_and_marks_omissions(monkeypatch):
    script = Path(__file__).resolve().parents[2] / "skills/issue-code-consistency-check/scripts/collect_issue_changes.py"
    spec = importlib.util.spec_from_file_location("collector", script)
    assert spec and spec.loader
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)

    def fake_diff(repo, commit, path, max_lines):
        return {"patch": "\n".join(["line"] * min(4, max_lines)),
                "truncated": max_lines < 4, "total_lines": 4}

    monkeypatch.setattr(collector, "get_diff", fake_diff)

    def commit():
        return {"full_hash": "abc", "hash": "abc", "files": [{"path": "a"}, {"path": "b"}, {"path": "c"}]}

    result = {"issues": {"1": {"commits": [commit()]}, "2": {"commits": [commit()]}}, "unlabeled": []}
    collector.add_bounded_diffs("repo", result, 4, 5)
    first = result["issues"]["1"]["commits"][0]["files"]
    assert first[1]["diff"]["truncated"] is True
    assert first[2]["diff"]["omitted"] == "total_budget"
    assert result["issues"]["2"]["commits"][0]["files"][0]["diff"]["omitted"] == "duplicate"
    assert result["diff_budget"]["emitted_lines"] == 5
    bounded = {"issues": {"1": {"commits": [commit()]}}, "unlabeled": []}
    collector.add_bounded_diffs("repo", bounded, 4, 20, total_chars=6)
    files = bounded["issues"]["1"]["commits"][0]["files"]
    assert len(files[0]["diff"]["patch"]) == 6
    assert files[0]["diff"]["truncated"] is True
    assert files[1]["diff"]["omitted"] == "total_budget"
    assert bounded["diff_budget"]["emitted_chars"] == 6
