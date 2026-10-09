"""Real lifecycle, SQL, operator and Git evidence boundaries for independent review."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).parents[1]
sys.path.insert(0, str(PLUGIN / "skills/release-docs/scripts"))
REVIEW = PLUGIN / "skills/release-docs-review/scripts"
sys.path.insert(0, str(REVIEW))


def api(name="validate_release_output"):
    assert (REVIEW / (name + ".py")).exists(), "Lifecycle review tool is missing"
    return importlib.import_module(name)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout.decode().strip()


@pytest.fixture
def release(tmp_path):
    from analyze_release_units import AnalysisResult
    from assemble_deployment_sql import assemble_deployment_sql
    from lifecycle_store import write_lifecycle_run
    from render_release_documents import render_release_documents

    repo = tmp_path / "中文 source"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    sql = "IF OBJECT_ID(N'dbo.Customer', N'U') IS NULL\nBEGIN\nCREATE TABLE dbo.Customer (Id int);\nEND;\n"
    (repo / "customer.sql").write_text(sql, encoding="utf-8")
    (repo / ".env").write_text("PASSWORD=NEVER_READ_SECRET", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    revision = git(repo, "rev-parse", "HEAD")
    scope = {"base_sha": revision, "target_sha": revision}
    unit = {"unit_id": "customer", "phase": "SCHEMA", "complete": True,
            "source_path": "customer.sql", "source_revision": revision,
            "source_hash": hashlib.sha256(sql.encode()).hexdigest(),
            "sql_hash": hashlib.sha256(sql.encode()).hexdigest(), "source_line": 1,
            "objects": ["dbo.Customer"], "issues": [], "depends_on": [], "sql": sql}
    exclusion = {"exclusion_id": "exclude-5005", "issues": ["#5005"], "unit_ids": ["core"],
                 "objects": ["dbo.Core"], "reason": "本次排除核心 DB",
                 "operator_action": "保留 dbo.Core 既有物件", "dependency_impact": [],
                 "reinstatement_conditions": ["完整單位重新授權與審查"]}
    analysis = AnalysisResult(units=[unit], exclusions=[exclusion], sources=[{k: unit[k] for k in
                              ("source_path", "source_revision", "source_hash")}], source_scope=scope)
    run = tmp_path / ".release-docs/runs/review-1"
    manifest = write_lifecycle_run(run, analysis)
    artifact = assemble_deployment_sql(analysis.units, run / "assembly/01_部署SQL.sql", 1)
    output = tmp_path / "operator"
    render_release_documents(analysis, artifact, manifest, output)
    metadata = json.loads((run / "lifecycle_metadata.json").read_text())
    metadata.update({"operator_contract": {"parameters_applicable": False},
                     "execution_artifact": {"path": "assembly/01_部署SQL.sql", "sha256": artifact.sha256}})
    write_json(run / "lifecycle_metadata.json", metadata)
    return repo, output, run, scope


def findings(release):
    return api().validate_release_output(release[1], release[2])


def codes(release):
    return {item["code"] for item in findings(release)}


def status(release, code):
    return next(item["status"] for item in findings(release) if item["code"] == code)


def update_metadata(release, **fields):
    path = release[2] / "lifecycle_metadata.json"
    metadata = json.loads(path.read_text())
    metadata.update(fields)
    write_json(path, metadata)


def replace_sql(release, sql):
    """Keep artifact evidence current so defects exercise SQL review, not drift."""
    _, output, run, _ = release
    (output / "01_部署SQL.sql").write_bytes(sql.encode("utf-8"))
    (run / "assembly/01_部署SQL.sql").write_bytes(sql.encode("utf-8"))
    update_metadata(release, execution_artifact={"path": "assembly/01_部署SQL.sql",
                    "sha256": hashlib.sha256(sql.encode()).hexdigest()})


def test_valid_operator_contract_has_separate_static_and_deployment_statuses(release):
    assert status(release, "sql_content_status") == "通過"
    assert status(release, "deployment_validation_status") == "待確認"
    assert not any(f["blocking"] for f in findings(release))


@pytest.mark.parametrize("name", ["02_資料SQL.sql", "03_例外排除.json", "04_參數異動.md",
                                    "lifecycle_exclusion_manifest.json", "source_unit_metadata.json", "unknown.txt", "nested"])
def test_undeclared_operator_files_and_directories_block_review(release, name):
    path = release[1] / name
    path.mkdir() if name == "nested" else path.write_text("PASSWORD=NEVER_EMIT_SECRET")
    assert "undeclared_output" in codes(release)
    assert status(release, "sql_content_status") == "未通過"
    assert "NEVER_EMIT_SECRET" not in json.dumps(findings(release))


@pytest.mark.parametrize("name", ["00_上線指引.md", "01_部署SQL.sql"])
def test_required_operator_documents_cannot_be_omitted(release, name):
    (release[1] / name).unlink()
    assert "missing_operator_file" in codes(release)


def test_parameter_document_follows_declared_applicability(release):
    update_metadata(release, operator_contract={"parameters_applicable": True})
    assert "missing_parameter_document" in codes(release)
    (release[1] / "02_參數異動.md").write_text("設定鍵與遮罩格式")
    assert "missing_parameter_document" not in codes(release)
    update_metadata(release, operator_contract={"parameters_applicable": False})
    assert "unexpected_parameter_document" in codes(release)


def test_missing_parameter_declaration_with_present_document_is_pending(release):
    update_metadata(release, operator_contract={})
    (release[1] / "02_參數異動.md").write_text("設定鍵與遮罩格式")
    assert "parameter_applicability_unknown" in codes(release)
    assert status(release, "sql_content_status") == "待確認"


@pytest.mark.parametrize("name", ["lifecycle_exclusion_manifest.json", "source_unit_metadata.json", "lifecycle_metadata.json"])
def test_missing_lifecycle_evidence_never_assumes_no_exclusions(release, name):
    (release[2] / name).unlink()
    assert "missing_lifecycle_evidence" in codes(release)
    assert status(release, "sql_content_status") != "通過"


@pytest.mark.parametrize("damage", ["excluded_unit", "excluded_object", "quoted_excluded_object", "missing_unit", "changed_mapping", "guide", "extra_guide"])
def test_lifecycle_sql_guide_exclusion_mismatch_blocks_review(release, damage):
    output = release[1]
    sql = (output / "01_部署SQL.sql").read_text(encoding="utf-8")
    if damage == "excluded_unit":
        sql = sql.replace('"unit_id": "customer"', '"unit_id": "core"')
    elif damage == "excluded_object":
        sql = sql.replace("CREATE TABLE dbo.Customer", "CREATE TABLE [dbo].[Core]")
    elif damage == "quoted_excluded_object":
        sql = sql.replace("CREATE TABLE dbo.Customer", 'CREATE TABLE "dbo"."Core"')
    elif damage == "missing_unit":
        sql = sql.replace("-- UNIT:", "-- LOST:")
    elif damage == "changed_mapping":
        sql = sql.replace('"source_line": 1', '"source_line": 2')
    else:
        path = output / "00_上線指引.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("#5005", "#9999") if damage == "guide" else text.replace("## 本次排除摘要", "## 本次排除摘要\n\n- 排除名稱：#9999；原因：新增範圍")
        path.write_text(text, encoding="utf-8")
    if damage not in ("guide", "extra_guide"):
        replace_sql(release, sql)
    if damage in ("excluded_object", "quoted_excluded_object"):
        assert "excluded_object_in_sql" in codes(release)
    assert any(f["blocking"] for f in findings(release))
    assert status(release, "sql_content_status") == "未通過"


@pytest.mark.parametrize("defect, expected", [("commit", "transaction_count"), ("throw", "missing_throw"),
                                              ("unguarded", "rerun_risk"), ("duplicate_column", "duplicate_column"),
                                              ("partial_exclusion", "excluded_dependency")])
def test_deliberate_sql_and_dependency_defects_are_blocking(release, defect, expected):
    sql = (release[1] / "01_部署SQL.sql").read_text(encoding="utf-8")
    if defect == "commit":
        sql = sql.replace("CREATE TABLE dbo.Customer", "COMMIT; CREATE TABLE dbo.Customer")
    elif defect == "throw":
        sql = sql.replace("THROW;", "PRINT N'failure';")
    elif defect == "unguarded":
        sql = sql.replace("IF OBJECT_ID(N'dbo.Customer', N'U') IS NULL", "")
    elif defect == "duplicate_column":
        sql = sql.replace("CREATE TABLE dbo.Customer (Id int);", "CREATE TABLE dbo.Customer (Id int);\nALTER TABLE dbo.Customer ADD Code int;\nALTER TABLE dbo.Customer ADD Code int;")
    else:
        path = release[2] / "source_unit_metadata.json"
        metadata = json.loads(path.read_text())
        metadata["units"][0]["depends_on"] = ["core"]
        write_json(path, metadata)
    replace_sql(release, sql)
    assert expected in codes(release)
    assert status(release, "sql_content_status") == "未通過"


def localdb_evidence(release):
    evidence = {"status": "passed", "exit_code": 0, "server": "(localdb)\\release-test",
                "database": "disposable", "provider_version": "test provider", "tool_version": "test runner",
                "baseline_source": "old.sql", "fixture_source": "fixture.sql", "command": ["runner", "--isolated"],
                "error_output_summary": "", "checks": ["checks passed"]}
    rounds = {name: dict(evidence) for name in ("validate_only", "commit", "rerun", "injected_failure")}
    rounds["injected_failure"]["error_output_summary"] = "Expected failure rollback and stop confirmed"
    return {"status": "passed", "artifact_sha256": hashlib.sha256((release[1] / "01_部署SQL.sql").read_bytes()).hexdigest(), "rounds": rounds}


@pytest.mark.parametrize("damage", [None, "missing_round", "missing_field", "wrong_hash", "bad_exit", "error_summary", "empty_checks"])
def test_deployment_pass_requires_structured_current_artifact_evidence(release, damage):
    evidence = localdb_evidence(release)
    if damage == "missing_round":
        del evidence["rounds"]["rerun"]
    elif damage == "missing_field":
        del evidence["rounds"]["commit"]["provider_version"]
    elif damage == "wrong_hash":
        evidence["artifact_sha256"] = "0" * 64
    elif damage == "bad_exit":
        evidence["rounds"]["commit"]["exit_code"] = 2
    elif damage == "error_summary":
        evidence["rounds"]["injected_failure"]["error_output_summary"] = ""
    elif damage == "empty_checks":
        evidence["rounds"]["validate_only"]["checks"] = []
    update_metadata(release, localdb_validation=evidence)
    assert status(release, "deployment_validation_status") == ("通過" if damage is None else "待確認")
    assert status(release, "sql_content_status") == "通過"


def test_guide_claiming_localdb_pass_without_evidence_is_rejected(release):
    path = release[1] / "00_上線指引.md"
    path.write_text(path.read_text(encoding="utf-8").replace("LocalDB：未執行", "LocalDB：通過"), encoding="utf-8")
    assert "unsupported_deployment_pass_claim" in codes(release)
    assert status(release, "deployment_validation_status") == "待確認"


def test_deployment_failure_does_not_prove_sql_defect_without_evidence(release):
    update_metadata(release, localdb_validation={"status": "failed", "sql_defect": False})
    assert status(release, "deployment_validation_status") == "未通過"
    assert status(release, "sql_content_status") == "通過"
    update_metadata(release, localdb_validation={"status": "failed", "sql_defect": True})
    assert status(release, "sql_content_status") == "未通過"


@pytest.mark.parametrize("surface", ["guide", "sql", "parameters", "manifest", "source", "source_revision", "execution", "metadata"])
def test_fingerprint_changes_for_every_covered_source_or_output(release, surface):
    module = api("review_fingerprint")
    repo, output, run, scope = release
    original = module.review_fingerprint(repo, output, run, scope)
    assert original.sha256 == module.review_fingerprint(repo, output, run, scope).sha256
    paths = {"guide": output / "00_上線指引.md", "sql": output / "01_部署SQL.sql",
             "manifest": run / "lifecycle_exclusion_manifest.json", "source": repo / "customer.sql",
             "execution": run / "assembly/01_部署SQL.sql", "metadata": run / "source_unit_metadata.json"}
    if surface == "parameters":
        (output / "02_參數異動.md").write_text("遮罩參數")
    elif surface == "source_revision":
        (repo / "customer.sql").write_text("SELECT 2;")
        git(repo, "add", "customer.sql")
        git(repo, "commit", "-qm", "source change")
        scope = {**scope, "target_sha": git(repo, "rev-parse", "HEAD")}
    else:
        with paths[surface].open("ab") as stream:
            stream.write(b"\n ")
    changed = module.review_fingerprint(repo, output, run, scope)
    assert original.sha256 != changed.sha256
    assert "NEVER_READ_SECRET" not in repr(changed)


def test_fingerprint_ignores_unrelated_secret_worktree_values(release):
    module = api("review_fingerprint")
    first = module.review_fingerprint(*release)
    (release[0] / ".env").write_text("PASSWORD=OTHER_SECRET")
    second = module.review_fingerprint(*release)
    assert first.sha256 == second.sha256
    assert "OTHER_SECRET" not in repr(second)


def test_execution_drift_is_detected_independently_from_sql_contract(release):
    with (release[1] / "01_部署SQL.sql").open("ab") as stream:
        stream.write(b"\n-- drift")
    assert "execution_artifact_mismatch" in codes(release)


@pytest.mark.parametrize("surface", ["output", "lifecycle"])
def test_linked_review_files_do_not_read_external_contents(release, tmp_path, surface):
    target = tmp_path / "external"
    target.write_text("TOKEN=NEVER_EMIT_SECRET")
    path = release[1] / "00_上線指引.md" if surface == "output" else release[2] / "lifecycle_exclusion_manifest.json"
    path.unlink()
    try:
        os.link(target, path)
    except OSError as error:
        pytest.skip("Sandbox cannot create hardlinks: " + str(error.winerror))
    assert "unsafe_review_path" in codes(release)
    with pytest.raises(ValueError, match="[Ll]ink|[Hh]ard"):
        api("review_fingerprint").review_fingerprint(*release)


def test_review_report_stays_inside_lifecycle_and_omits_untrusted_values(release):
    module = api()
    record = api("review_fingerprint").review_fingerprint(*release)
    report = module.write_review_report(release[2], findings(release), record)
    assert report.parent == release[2]
    text = report.read_text(encoding="utf-8")
    assert "SQL 內容審核：通過" in text
    assert "部署驗證：待確認" in text
    assert record.sha256 in text
    assert {p.name for p in release[1].iterdir()} == {"00_上線指引.md", "01_部署SQL.sql"}
    untrusted = [{"code": "TOKEN=NEVER_EMIT_SECRET", "blocking": True, "status": "未通過"}]
    module.write_review_report(release[2], untrusted, record)
    assert "NEVER_EMIT_SECRET" not in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("damage", ["malformed_unit", "invalid_dependency", "wrong_phase", "false_guard", "altered_context"])
def test_review_rejects_unproven_unit_scope_and_guards(release, damage):
    path = release[2] / "source_unit_metadata.json"
    evidence = json.loads(path.read_text())
    sql = (release[1] / "01_部署SQL.sql").read_text(encoding="utf-8")
    if damage == "malformed_unit":
        evidence["units"][0]["depends_on"] = [None]
        write_json(path, evidence)
    elif damage == "invalid_dependency":
        evidence["units"][0]["complete"] = False
        write_json(path, evidence)
    elif damage == "wrong_phase":
        block = sql[sql.index("-- UNIT:"):sql.index("-- END UNIT: customer") + len("-- END UNIT: customer")]
        sql = sql.replace(block, "").replace("-- PHASE: VALIDATION", block + "\n-- PHASE: VALIDATION")
        replace_sql(release, sql)
    elif damage == "false_guard":
        sql = sql.replace("IF OBJECT_ID(N'dbo.Customer', N'U') IS NULL", "IF 1=1")
        source_sql = (release[0] / "customer.sql").read_text(encoding="utf-8").replace("IF OBJECT_ID(N'dbo.Customer', N'U') IS NULL", "IF 1=1")
        old_hash = evidence["units"][0]["sql_hash"]
        new_hash = hashlib.sha256(source_sql.encode()).hexdigest()
        evidence["units"][0]["sql_hash"] = new_hash
        write_json(path, evidence)
        sql = sql.replace(old_hash, new_hash)
        replace_sql(release, sql)
    else:
        sql = sql.replace("SET @UnitId = N'customer';", "SET @UnitId = N'other';")
        replace_sql(release, sql)
    result = findings(release)
    expected = {"malformed_unit": "invalid_unit_evidence", "invalid_dependency": "invalid_unit_evidence",
                "wrong_phase": "unit_phase_mismatch", "false_guard": "rerun_risk", "altered_context": "unit_context_mismatch"}
    assert expected[damage] in {f["code"] for f in result}
    assert any(f["blocking"] for f in result)
    assert status(release, "sql_content_status") == "未通過"


def test_fingerprint_covers_old_schema_source_changes(release):
    module = api("review_fingerprint")
    baseline = release[0] / "old.sql"
    baseline.write_text("CREATE TABLE dbo.Old (Id int);", encoding="utf-8")
    path = release[2] / "source_unit_metadata.json"
    evidence = json.loads(path.read_text())
    evidence["baseline"] = {"source_path": str(baseline), "source_hash": hashlib.sha256(baseline.read_bytes()).hexdigest()}
    write_json(path, evidence)
    first = module.review_fingerprint(*release)
    baseline.write_text("CREATE TABLE dbo.Old (Id bigint);", encoding="utf-8")
    assert first.sha256 != module.review_fingerprint(*release).sha256


@pytest.mark.parametrize("name", ["lifecycle_exclusion_manifest.json", "source_unit_metadata.json", "lifecycle_metadata.json"])
def test_invalid_json_is_pending_without_dumping_raw_secret(release, name):
    (release[2] / name).write_text("PASSWORD=RAW_SECRET", encoding="utf-8")
    result = findings(release)
    assert next(f["status"] for f in result if f["code"] == "sql_content_status") == "待確認"
    assert "RAW_SECRET" not in repr(result)


def test_fingerprint_rejects_source_path_escape_without_reading_target(release):
    path = release[2] / "source_unit_metadata.json"
    evidence = json.loads(path.read_text())
    evidence["sources"][0]["source_path"] = "../external.sql"
    write_json(path, evidence)
    with pytest.raises(ValueError, match="source path"):
        api("review_fingerprint").review_fingerprint(*release)


def test_source_and_parameter_applicability_conflict_is_blocking(release):
    path = release[2] / "source_unit_metadata.json"
    evidence = json.loads(path.read_text())
    evidence["parameter_changes"] = [{"key": "Cache:TTL"}]
    write_json(path, evidence)
    assert "conflicting_parameter_applicability" in codes(release)


def test_review_report_does_not_invalidate_its_own_fingerprint(release):
    module = api("review_fingerprint")
    first = module.review_fingerprint(*release)
    api().write_review_report(release[2], findings(release), first)
    assert first.sha256 == module.review_fingerprint(*release).sha256


def test_report_does_not_echo_a_secret_disguised_as_a_finding_code(release):
    module = api()
    fingerprint = api("review_fingerprint").review_fingerprint(*release)
    report = module.write_review_report(release[2], [{"code": "password_is_supersecret", "status": "未通過", "blocking": True}], fingerprint)
    assert "password_is_supersecret" not in report.read_text(encoding="utf-8")


def test_empty_excluded_unit_scope_cannot_pass(release):
    path = release[2] / "lifecycle_exclusion_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["exclusions"][0]["unit_ids"] = []
    write_json(path, manifest)
    assert "invalid_lifecycle_exclusions" in codes(release)


def test_sql_outside_declared_units_is_rejected_even_when_artifact_hash_matches(release):
    sql = (release[1] / "01_部署SQL.sql").read_text(encoding="utf-8")
    sql = sql.replace("-- PHASE: DATA", "DELETE FROM dbo.Other;\n-- PHASE: DATA")
    replace_sql(release, sql)
    assert "execution_artifact_mismatch" not in codes(release)
    assert "unmapped_sql" in codes(release)


def test_malformed_unit_mapping_returns_finding_instead_of_crashing(release):
    sql = (release[1] / "01_部署SQL.sql").read_text(encoding="utf-8")
    sql = sql.replace('"depends_on": []', '"depends_on": [[]]')
    replace_sql(release, sql)
    assert "invalid_unit_mapping" in codes(release)
