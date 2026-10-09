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


def test_localdb_server_must_be_anchored_and_named_instance(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("SELECT 1;", encoding="utf-8")
    for server in ("prod(localdb)\\MSSQLLocalDB", "prefix(localdb)\\MSSQLLocalDB", "(localdb)\\MSSQLLocalDB extra"):
        try:
            module.run_local_validation(artifact, server=server, database="test", executor=lambda **kw: {})
        except ValueError as exc:
            assert "LocalDB" in str(exc)
        else:
            raise AssertionError(server)


def test_four_rounds_use_fresh_sessions_and_expected_transaction_contract(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("-- artifact\n", encoding="utf-8")
    baseline = tmp_path / "old-schema.sql"
    fixture = tmp_path / "test-data.sql"
    baseline.write_text("CREATE TABLE old_table (id int);", encoding="utf-8")
    fixture.write_text("INSERT INTO old_table VALUES (1);", encoding="utf-8")
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
        baseline_source=str(baseline), fixture_source=str(fixture),
        executor=executor, command=["sqlcmd", "-S", "(localdb)\\MSSQLLocalDB"],
        adapter_provenance="fake-disposable-adapter/1",
        provider_version="sqlcmd-localdb/1", tool_version="mssql/1",
    )
    assert result["status"] == "passed"
    assert len(calls) == 4
    assert [c["round_name"] for c in calls] == ["validate_only", "commit", "rerun", "injected_failure"]
    assert all(c["fresh_session"] is True for c in calls)
    assert calls[0]["validate_only"] is True and calls[1]["validate_only"] is False
    assert calls[3]["inject_failure"] is True
    assert len({c["baseline_source"] for c in calls if c["round_name"] != "rerun"}) == 3
    assert "committed-from=commit" in calls[2]["baseline_source"]
    assert all(r["provider_version"] and r["tool_version"] for r in result["rounds"].values())
    assert all(r["baseline_sha256"] and r["fixture_sha256"] for r in result["rounds"].values())


def test_executor_failure_is_structured_and_never_claims_pass(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("SELECT 1;", encoding="utf-8")
    baseline = tmp_path / "old.sql"
    fixture = tmp_path / "data.sql"
    baseline.write_text("old", encoding="utf-8")
    fixture.write_text("data", encoding="utf-8")

    def executor(**kwargs):
        return {"status": "failed", "exit_code": 1, "checks": ["rollback attempted"],
                "error_output_summary": "SQL ERROR_NUMBER=50001 phase=DATA"}

    result = module.run_local_validation(artifact, server="(localdb)\\MSSQLLocalDB", database="test",
                                         baseline_source=str(baseline), fixture_source=str(fixture),
                                         adapter_provenance="fake/1", provider_version="sqlcmd/1",
                                         tool_version="mssql/1", executor=executor)
    assert result["status"] == "failed"
    assert result["rounds"]["validate_only"]["exit_code"] == 1
    assert "SQL ERROR_NUMBER" in result["rounds"]["validate_only"]["error_output_summary"]


def test_executor_requires_real_fixture_paths_and_adapter_provenance(tmp_path):
    artifact = tmp_path / "01.sql"
    artifact.write_text("SELECT 1;", encoding="utf-8")
    for kwargs in ({"adapter_provenance": ""}, {"adapter_provenance": "fake/1", "baseline_source": "missing", "fixture_source": "missing"}):
        try:
            module.run_local_validation(artifact, server="(localdb)\\MSSQLLocalDB", database="test",
                                        executor=lambda **kw: {}, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid adapter or fixture must block a pass")


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


def test_success_cleanup_removes_only_temporary_material(tmp_path):
    lifecycle_spec = importlib.util.spec_from_file_location(
        "lifecycle_store_cleanup", ROOT / "skills/release-docs/scripts/lifecycle_store.py")
    lifecycle = importlib.util.module_from_spec(lifecycle_spec)
    lifecycle_spec.loader.exec_module(lifecycle)
    run = tmp_path / ".release-docs" / "runs" / "run-cleanup"
    (run / "temporary").mkdir(parents=True)
    (run / "temporary" / "sqlcmd.log").write_text("trace", encoding="utf-8")
    (run / "lifecycle_metadata.json").write_text("audit", encoding="utf-8")
    assert lifecycle.finalize_lifecycle_run(
        run, review_status="success", delivery_status="success") is True
    assert (run / "lifecycle_metadata.json").exists()
    assert not (run / "temporary").exists()
