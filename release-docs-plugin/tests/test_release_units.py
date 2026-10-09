"""Real Git/source fixtures for complete unit and lifecycle boundaries."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills/release-docs/scripts"
sys.path.insert(0, str(SCRIPTS))


def api():
    assert (SCRIPTS / "analyze_release_units.py").exists(), "unit analyzer is missing"
    assert (SCRIPTS / "lifecycle_store.py").exists(), "lifecycle store is missing"
    return importlib.import_module("analyze_release_units"), importlib.import_module("lifecycle_store")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout.decode().strip()


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "中文 source"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    baseline = root / "baseline.sql"
    baseline.write_text("CREATE TABLE dbo.Existing (Id int);", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    base = git(root, "rev-parse", "HEAD")
    specs = [
        ("table", "SCHEMA", [], ["#5005"], "CREATE TABLE dbo.Core (Id int);"),
        ("column", "REPAIR", ["table"], ["#5005"], "ALTER TABLE dbo.Core ADD Code int;"),
        ("index", "REPAIR", ["column"], ["#5005"], "CREATE INDEX IX_Code ON dbo.Core(Code);"),
        ("fk", "REPAIR", ["table"], ["#5005"], "ALTER TABLE dbo.Core ADD CONSTRAINT FK_Core FOREIGN KEY(Id) REFERENCES dbo.Existing(Id);"),
        ("data", "DATA", ["column"], [], "INSERT INTO dbo.Core VALUES (1, 2); -- NEVER_STORE_SECRET"),
        ("validate", "VALIDATION", ["data"], [], "SELECT COUNT(*) FROM dbo.Core;"),
    ]
    changes = []
    for unit, phase, deps, issues, sql in specs:
        path = "sql/" + unit + ".sql"
        (root / "sql").mkdir(exist_ok=True)
        declaration = {"unit_id": unit, "phase": phase, "depends_on": deps,
                       "issues": issues, "objects": ["dbo.Core"], "complete": True}
        (root / path).write_text("-- release-unit: " + json.dumps(declaration) + "\n" + sql,
                                encoding="utf-8")
        changes.append({"status": "A", "path": path})
    git(root, "add", ".")
    git(root, "commit", "-qm", "units")
    evidence = {"base_sha": base, "target_sha": git(root, "rev-parse", "HEAD"),
                "committed_changes": changes, "release_id": "fixture-release"}
    return root, evidence, baseline


def test_ordered_units_exclusion_contains_whole_units_and_transitive_impact(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    result = analyzer.analyze_release_units(root, evidence, baseline, "#5005")
    assert [u["unit_id"] for u in result.units] == ["table", "column", "fk", "index", "data", "validate"]
    exclusion = result.exclusions[0]
    assert exclusion["unit_ids"] == ["table", "column", "fk", "index"]
    assert exclusion["dependency_impact"] == ["data", "validate"]
    assert exclusion["reinstatement_conditions"]
    assert exclusion["review_status"] == "pending"
    assert "excluded_dependency" in {f["code"] for f in result.findings}
    assert result.units[0]["source_revision"] == evidence["target_sha"]
    assert result.units[0]["source_hash"] == hashlib.sha256((root / "sql/table.sql").read_bytes()).hexdigest()


def test_requested_revision_does_not_read_current_worktree(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    (root / "sql/table.sql").write_text("BAD WORKTREE SQL", encoding="utf-8")
    result = analyzer.analyze_release_units(root, evidence, baseline, None)
    assert "CREATE TABLE" in result.units[0]["sql"]
    assert "BAD WORKTREE" not in result.units[0]["sql"]


def test_missing_authoritative_sql_blocks_migration_inference(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    (root / "migration.cs").write_text("void Up() { AddColumn(); }", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "missing SQL")
    evidence["target_sha"] = git(root, "rev-parse", "HEAD")
    evidence["committed_changes"].append({"status": "A", "path": "migration.cs"})
    result = analyzer.analyze_release_units(root, evidence, baseline, None)
    assert any(f["code"] == "missing_authoritative_sql" and f["blocking"] for f in result.findings)


def test_partial_issue_ownership_blocks_exclusion_instead_of_splitting(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    path = root / "sql/table.sql"
    path.write_text(path.read_text().replace('["#5005"]', '["#5005", "#6006"]'), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "shared unit")
    evidence["target_sha"] = git(root, "rev-parse", "HEAD")
    result = analyzer.analyze_release_units(root, evidence, baseline, "#5005")
    assert any(f["code"] == "partial_exclusion" and f["blocking"] for f in result.findings)
    assert not result.exclusions


def test_lifecycle_is_deterministic_immutable_and_contains_no_source_contents(fixture, tmp_path):
    analyzer, store = api()
    root, evidence, baseline = fixture
    result = analyzer.analyze_release_units(root, evidence, baseline, "#5005")
    run = tmp_path / ".release-docs/runs/run-1"
    manifest = store.write_lifecycle_run(run, result)
    assert manifest == run / "lifecycle_exclusion_manifest.json"
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    store.write_lifecycle_run(run, result)
    assert before == {p.name: p.read_bytes() for p in run.iterdir()}
    text = b"".join(before.values()).decode()
    assert "NEVER_STORE_SECRET" not in text and "CREATE TABLE" not in text
    document = json.loads(manifest.read_text())
    assert document["exclusions"][0]["unit_ids"] == ["table", "column", "fk", "index"]
    policy = json.loads((run / "lifecycle_metadata.json").read_text())
    assert policy["retention"]["failed_days"] == 7
    assert policy["retention"]["cleanup_after_success"] is True
    assert policy["cleanup_status"] == "pending_success"
    result.exclusions.clear()
    with pytest.raises(ValueError, match="immutable"):
        store.write_lifecycle_run(run, result)


def test_lifecycle_rejects_operator_output_path_without_creating_files(fixture, tmp_path):
    analyzer, store = api()
    result = analyzer.analyze_release_units(*fixture, None)
    output = tmp_path / "release-output"
    with pytest.raises(ValueError, match="lifecycle"):
        store.write_lifecycle_run(output, result)
    assert not output.exists()


def test_unknown_exclusion_and_unresolved_dependency_block(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    path = root / "sql/data.sql"
    path.write_text(path.read_text().replace('["column"]', '["missing"]'), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "bad dependency")
    evidence["target_sha"] = git(root, "rev-parse", "HEAD")
    result = analyzer.analyze_release_units(root, evidence, baseline, "#9999")
    assert {"unresolved_dependency", "unresolved_exclusion"} <= {f["code"] for f in result.findings if f["blocking"]}


def test_explicit_deployment_artifact_covers_project_and_migration_without_inference(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    for path in ("db.sqlproj", "snapshot.dacpac", "migration.cs"):
        (root / path).write_bytes(b"SOURCE_SECRET_DO_NOT_EMIT")
        evidence["committed_changes"].append({"status": "A", "path": path})
    (root / "deployment.sql").write_text("SELECT 1; -- DEPLOYMENT_SECRET", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "deployment mapping")
    evidence["target_sha"] = git(root, "rev-parse", "HEAD")
    evidence["execution_units"] = [{"unit_id": "mapped", "phase": "VALIDATION", "complete": True,
                                    "source_path": "deployment.sql", "covers": ["db.sqlproj", "snapshot.dacpac", "migration.cs"],
                                    "token": "IGNORED_SECRET"}]
    result = analyzer.analyze_release_units(root, evidence, baseline, None)
    assert not result.blocked
    unit = next(u for u in result.units if u["unit_id"] == "mapped")
    assert unit["source_line"] == 1
    assert unit["covered_sources"] == ["db.sqlproj", "migration.cs", "snapshot.dacpac"]
    assert "IGNORED_SECRET" not in json.dumps(unit)
    assert {"database_project", "dacpac", "migration"} <= {s["kind"] for s in result.sources}


def test_dependency_cycle_blocks_instead_of_returning_ready_units(fixture):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    path = root / "sql/column.sql"
    path.write_text(path.read_text().replace('["table"]', '["index"]'), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "cycle")
    evidence["target_sha"] = git(root, "rev-parse", "HEAD")
    result = analyzer.analyze_release_units(root, evidence, baseline, None)
    assert result.blocked
    assert "dependency_cycle_or_missing_unit" in {f["code"] for f in result.findings}


def test_operator_directory_hardlink_cannot_be_overwritten_through_lifecycle(fixture, tmp_path):
    analyzer, store = api()
    result = analyzer.analyze_release_units(*fixture, None)
    run = tmp_path / ".release-docs/runs/hardlink"
    run.mkdir(parents=True)
    operator = tmp_path / "operator.json"
    operator.write_text("operator file", encoding="utf-8")
    try:
        (run / "lifecycle_exclusion_manifest.json").hardlink_to(operator)
    except PermissionError:
        pytest.skip("Current Windows sandbox disallows hardlink creation")
    with pytest.raises(ValueError, match="Linked"):
        store.write_lifecycle_run(run, result)
    assert operator.read_text() == "operator file"


@pytest.mark.parametrize("source", ["migration.cs", "db.sqlproj"])
def test_non_sql_execution_descriptor_cannot_cover_itself_and_be_ready(fixture, source):
    analyzer, _ = api()
    root, evidence, baseline = fixture
    (root / source).write_text("NOT EXECUTABLE SQL", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "self coverage")
    evidence["target_sha"] = git(root, "rev-parse", "HEAD")
    evidence["committed_changes"] = [{"status": "A", "path": source}]
    evidence["execution_units"] = [{"unit_id": "pretend", "phase": "SCHEMA", "complete": True,
                                    "source_path": source, "covers": [source]}]
    result = analyzer.analyze_release_units(root, evidence, baseline, None)
    assert result.blocked, "A non-SQL descriptor with zero executable units must block"
    assert not result.units
    assert "missing_authoritative_sql" in {f["code"] for f in result.findings}


@pytest.mark.parametrize("unsafe", ["..", "../escape", "nested/run", "nested\\run", "/absolute"])
def test_unsafe_run_id_is_rejected_before_any_lifecycle_write(fixture, tmp_path, unsafe):
    analyzer, store = api()
    result = analyzer.analyze_release_units(*fixture, None)
    root = tmp_path / ".release-docs/runs" / unsafe
    with pytest.raises(ValueError, match="lifecycle"):
        store.write_lifecycle_run(root, result)
    assert not list(tmp_path.rglob("lifecycle_exclusion_manifest.json"))


def test_traversal_before_lifecycle_root_is_rejected(fixture, tmp_path):
    analyzer, store = api()
    result = analyzer.analyze_release_units(*fixture, None)
    root = tmp_path / "nominal/../outside/.release-docs/runs/run-1"
    with pytest.raises(ValueError, match="lifecycle"):
        store.write_lifecycle_run(root, result)
    assert not (tmp_path / "outside").exists()
