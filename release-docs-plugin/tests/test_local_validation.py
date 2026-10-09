"""Contract tests for the opt-in isolated LocalDB validation runner."""
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills/release-docs/scripts/run_local_validation.py"
spec = importlib.util.spec_from_file_location("run_local_validation", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_missing_runner_returns_not_run_with_all_rounds(tmp_path):
    artifact = tmp_path / "01_部署SQL.sql"
    artifact.write_text("SELECT 1;\n", encoding="utf-8")
    result = module.run_local_validation(artifact, server="(localdb)\\MSSQLLocalDB", database="release_test")
    assert result["status"] == "not_run"
    assert result["reason"]
    assert result["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert set(result["rounds"]) == {"validate_only", "commit", "rerun", "injected_failure"}
    assert all(r["status"] == "not_run" for r in result["rounds"].values())


def test_production_server_is_rejected_before_executor(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("SELECT 1;", encoding="utf-8")
    calls = []
    try:
        module.run_local_validation(artifact, server="prod-sql", database="release", executor=lambda **kw: calls.append(kw))
    except ValueError as exc:
        assert "LocalDB" in str(exc)
    else:
        raise AssertionError("production server must be rejected")
    assert calls == []


def test_four_rounds_use_fresh_sessions_and_expected_transaction_contract(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("-- artifact\n", encoding="utf-8")
    calls = []

    def executor(**kwargs):
        calls.append(kwargs)
        if kwargs["round_name"] == "injected_failure":
            return {"exit_code": 0, "status": "passed", "checks": [
                "expected THROW observed", "rollback confirmed", "later units did not execute"
            ], "error_output_summary": "Expected SQL error 51000; rollback and stop confirmed"}
        return {"exit_code": 0, "status": "passed", "checks": [
            "fresh baseline", "new session", "transaction checks passed"
        ], "error_output_summary": ""}

    result = module.run_local_validation(
        artifact, server="(localdb)\\MSSQLLocalDB", database="release_test",
        baseline_source="fixtures/old-schema.sql", fixture_source="fixtures/test-data.sql",
        executor=executor, command=["sqlcmd", "-S", "(localdb)\\MSSQLLocalDB"],
    )
    assert result["status"] == "passed"
    assert len(calls) == 4
    assert [c["round_name"] for c in calls] == ["validate_only", "commit", "rerun", "injected_failure"]
    assert all(c["fresh_session"] is True for c in calls)
    assert calls[0]["validate_only"] is True and calls[1]["validate_only"] is False
    assert calls[3]["inject_failure"] is True
    assert len({c["baseline_source"] for c in calls}) == 4
    assert all(r["provider_version"] and r["tool_version"] for r in result["rounds"].values())


def test_executor_failure_is_structured_and_never_claims_pass(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("SELECT 1;", encoding="utf-8")

    def executor(**kwargs):
        return {"status": "failed", "exit_code": 1, "checks": ["rollback attempted"],
                "error_output_summary": "SQL ERROR_NUMBER=50001 phase=DATA"}

    result = module.run_local_validation(artifact, server="(localdb)\\MSSQLLocalDB", database="test", executor=executor)
    assert result["status"] == "failed"
    assert result["rounds"]["validate_only"]["exit_code"] == 1
    assert "SQL ERROR_NUMBER" in result["rounds"]["validate_only"]["error_output_summary"]


def test_evidence_can_be_written_inside_lifecycle_only(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("SELECT 1;", encoding="utf-8")
    lifecycle = tmp_path / ".release-docs" / "runs" / "run-1"
    lifecycle.mkdir(parents=True)
    result = module.run_local_validation(artifact, server="(localdb)\\MSSQLLocalDB", database="test")
    path = module.write_localdb_evidence(lifecycle, result)
    assert path == lifecycle / "localdb_validation.json"
    assert path.exists()
    assert "not_run" in path.read_text(encoding="utf-8")
    try:
        module.write_localdb_evidence(tmp_path / "operator", result)
    except ValueError as exc:
        assert "lifecycle" in str(exc)
    else:
        raise AssertionError("operator evidence path must be rejected")
