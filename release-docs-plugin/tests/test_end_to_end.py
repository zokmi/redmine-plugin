"""Fixture-only end-to-end contract for the transactional release workflow."""
import hashlib
import json
import subprocess
from pathlib import Path
import sys

PLUGIN = Path(__file__).parents[1]
SCRIPTS = PLUGIN / "skills/release-docs/scripts"
REVIEW = PLUGIN / "skills/release-docs-review/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REVIEW))


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_collection_to_review_preserves_allowlist_and_localdb_evidence(tmp_path):
    from analyze_release_units import analyze_release_units
    from assemble_deployment_sql import assemble_deployment_sql
    from lifecycle_store import write_lifecycle_run
    from render_release_documents import render_release_documents
    from run_local_validation import run_local_validation
    from validate_release_output import validate_release_output

    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    sql = "IF OBJECT_ID(N'dbo.Customer', N'U') IS NULL\n    CREATE TABLE dbo.Customer (Id int);\n"
    (repo / "customer.sql").write_text(sql, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    revision = _git(repo, "rev-parse", "HEAD")
    baseline = tmp_path / "old-schema.sql"
    fixture = tmp_path / "test-data.sql"
    baseline.write_text("CREATE TABLE dbo.Old (Id int);\n", encoding="utf-8")
    fixture.write_text("INSERT INTO dbo.Old VALUES (1);\n", encoding="utf-8")
    evidence = {"base_sha": revision, "target_sha": revision,
                "committed_changes": [{"path": "customer.sql", "status": "A"}],
                "execution_units": [{"unit_id": "customer", "phase": "SCHEMA", "complete": True,
                                      "source_path": "customer.sql", "objects": ["dbo.Customer"],
                                      "issues": [], "depends_on": []}]}
    analysis = analyze_release_units(repo, evidence, baseline, "")
    assert not analysis.blocked
    analysis.structure_changes = [{"unit_id": "customer", "kind": "table", "name": "dbo.Customer",
                                   "description": "建立客戶表", "impact": "提供資料儲存"}]
    sql_path = tmp_path / "01_\u90e8\u7f72SQL.sql"
    artifact = assemble_deployment_sql(analysis.units, sql_path, {"validate_only": 1, "release_id": "e2e"})

    def executor(**kwargs):
        if kwargs["round_name"] == "injected_failure":
            return {"status": "passed", "exit_code": 0,
                    "checks": ["expected error", "rollback", "THROW", "later units did not execute"],
                    "error_output_summary": "Expected error; rollback; THROW; later units did not execute"}
        return {"status": "passed", "exit_code": 0,
                "checks": ["fresh baseline", "fresh session", "checks passed"],
                "error_output_summary": ""}

    localdb = run_local_validation(
        artifact.path, server="(localdb)\\MSSQLLocalDB", database="e2e_release",
        baseline_source=str(baseline), fixture_source=str(fixture),
        adapter_provenance="fixture-disposable-adapter/1",
        provider_version="fixture-sqlcmd/1", tool_version="fixture-mssql/1",
        command=["fixture-adapter", "--fresh-session"], executor=executor)
    assert localdb["status"] == "passed"
    run = tmp_path / ".release-docs/runs/e2e"
    manifest = write_lifecycle_run(
        run, analysis, localdb_validation=localdb,
        operator_contract={"parameters_applicable": False},
        execution_artifact={"path": str(artifact.path), "sha256": artifact.sha256})
    output = tmp_path / "operator"
    render_release_documents(analysis, artifact, manifest, output)
    assert {p.name for p in output.iterdir()} == {"00_上線指引.md", "01_部署SQL.sql"}
    assert (output / "01_部署SQL.sql").read_bytes() == artifact.path.read_bytes()
    assert (run / "lifecycle_metadata.json").exists()
    findings = validate_release_output(output, run)
    assert not any(f["blocking"] for f in findings)
    # Lifecycle cleanup policy is represented internally; operator delivery stays clean.
    assert not any(p.name.startswith("localdb") for p in output.iterdir())
