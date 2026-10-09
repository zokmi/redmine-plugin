"""Operator delivery boundary and evidence-based document projection."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills/release-docs/scripts"
sys.path.insert(0, str(SCRIPTS))


def api():
    assert (SCRIPTS / "render_release_documents.py").exists(), "Document renderer is missing"
    return importlib.import_module("render_release_documents")


def inputs(tmp_path):
    from analyze_release_units import AnalysisResult
    from assemble_deployment_sql import assemble_deployment_sql
    from lifecycle_store import write_lifecycle_run

    sql = "CREATE TABLE dbo.Customer (Id int);\r\n"
    unit = {"unit_id": "customer", "phase": "SCHEMA", "complete": True,
            "depends_on": [], "issues": ["#4000"], "objects": ["dbo.Customer"],
            "source_path": "sql/customer.sql", "source_revision": "a" * 40,
            "source_hash": "b" * 64, "source_line": 2,
            "sql_hash": hashlib.sha256(sql.encode()).hexdigest(), "sql": sql}
    exclusion = {"exclusion_id": "exclude-5005", "issues": ["#5005"],
                 "unit_ids": ["core"], "objects": ["dbo.Core"],
                 "reason": "核心 DB 本次不上線", "operator_action": "保留 dbo.Core 既有物件",
                 "dependency_impact": ["internal-graph-node"],
                 "reinstatement_conditions": ["完整單位重新授權與審查"],
                 "evidence": [{"source_hash": "c" * 64}]}
    analysis = AnalysisResult(units=[unit], exclusions=[exclusion],
                              source_scope={"base_sha": "d" * 40, "target_sha": "a" * 40},
                              baseline={"source_path": "baseline/old.sql", "source_hash": "e" * 64})
    analysis.structure_changes = [
        {"kind": "table", "name": "dbo.Customer", "description": "建立客戶主檔", "impact": "提供客戶查詢"},
        {"kind": "column", "name": "dbo.Customer.Id", "description": "建立識別欄位", "impact": "識別客戶"},
        {"kind": "index", "name": "IX_Customer", "description": "加速查詢", "impact": "增加寫入成本"},
        {"kind": "fk", "name": "FK_Customer", "description": "維持參照完整性", "impact": "檢查既有資料"},
        {"kind": "constraint", "name": "CK_Customer", "description": "限制識別值", "impact": "阻擋無效資料"},
        {"kind": "extended_property", "name": "dbo.Customer.MS_Description", "description": "補充欄位說明", "impact": "更新中繼資料"},
    ]
    manifest = write_lifecycle_run(tmp_path / ".release-docs/runs/run-1", analysis)
    artifact = assemble_deployment_sql(analysis.units, tmp_path / "assembly/01_部署SQL.sql", {"validate_only": 1, "release_id": "release-42"})
    return analysis, artifact, manifest


def render(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    output = tmp_path / "operator"
    inventory = module.render_release_documents(analysis, artifact, manifest, output)
    return analysis, artifact, manifest, output, inventory


def test_output_has_only_operator_files_and_preserves_sql_bytes(tmp_path):
    _, artifact, _, output, inventory = render(tmp_path)
    assert {p.name for p in output.iterdir()} == {"00_上線指引.md", "01_部署SQL.sql"}
    assert (output / "01_部署SQL.sql").read_bytes() == artifact.path.read_bytes()
    assert inventory.output_dir == output.resolve()
    assert set(inventory.files) == {"00_上線指引.md", "01_部署SQL.sql"}
    assert inventory.files["01_部署SQL.sql"] == artifact.sha256


def test_guide_projects_structure_and_lifecycle_scope_without_internal_evidence(tmp_path):
    _, _, _, output, _ = render(tmp_path)
    text = (output / "00_上線指引.md").read_text(encoding="utf-8")
    for expected in ("release-42", "dddddddddddd", "aaaaaaaaaaaa", "old.sql", "dbo.Customer",
                     "建立客戶主檔", "建立識別欄位", "加速查詢", "維持參照完整性",
                     "限制識別值", "補充欄位說明", "#5005", "核心 DB 本次不上線",
                     "保留 dbo.Core 既有物件", "完整單位重新授權與審查"):
        assert expected in text
    for internal in ("c" * 64, "e" * 64, "internal-graph-node", "lifecycle_exclusion_manifest.json", "depends_on"):
        assert internal not in text


def test_missing_structure_description_is_pending_instead_of_invented(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    del analysis.structure_changes
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "operator")
    text = (tmp_path / "operator/00_上線指引.md").read_text(encoding="utf-8")
    assert "dbo.Customer" in text and "結構異動說明待確認" in text
    assert "建立客戶主檔" not in text


def test_exclusions_come_from_manifest_not_in_memory_analysis(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    analysis.exclusions = []
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "operator")
    assert "#5005" in (tmp_path / "operator/00_上線指引.md").read_text(encoding="utf-8")


def test_guide_has_safe_execution_error_reporting_and_unexecuted_localdb_status(tmp_path):
    _, _, _, output, _ = render(tmp_path)
    text = (output / "00_上線指引.md").read_text(encoding="utf-8")
    for token in ("備份", "唯讀 preflight", "ValidateOnly=1", "ROLLBACK", "ValidateOnly=0",
                  "新 connection/session", "COMMIT", "THROW", "停止", "不跳過", "LocalDB：未執行",
                  "ReleaseId", "DatabaseName", "ServerName", "Phase", "UnitId", "SourcePath",
                  "SourceRevision", "SourceLine", "ErrorNumber", "ErrorSeverity", "ErrorState",
                  "ErrorProcedure", "ErrorLine", "ErrorMessage", "XactState", "TranCount", "TransactionAction",
                  "constraint trust", "index 定義", "重跑"):
        assert token in text


def test_parameter_document_is_optional_and_masks_secret_values(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    analysis.parameter_changes = [
        {"environment": "Production", "service": "CustomerAPI", "key": "Auth:ApiToken",
         "value": "SUPER_SECRET", "format_example": "SUPER_SECRET", "apply": "由機密儲存更新",
         "reload": "重新啟動服務", "validation": "登入檢查"},
        {"environment": "Production", "service": "CustomerAPI", "key": "Cache:TTL",
         "value": "120", "format_example": "整數，例如 120", "apply": "設定檔更新",
         "reload": "重新載入", "validation": "檢查快取期限"},
        {"environment": "Production", "service": "CustomerAPI", "key": "Service:Url",
         "format_example": "https://user:URL_PASSWORD@example.test/?token=QUERY_SECRET",
         "apply": "設定檔更新", "reload": "重新載入", "validation": "健康檢查"},
    ]
    inventory = module.render_release_documents(analysis, artifact, manifest, tmp_path / "operator")
    text = (tmp_path / "operator/02_參數異動.md").read_text(encoding="utf-8")
    for expected in ("Production", "CustomerAPI", "Auth:ApiToken", "Cache:TTL", "整數，例如 120",
                     "由機密儲存更新", "重新啟動服務", "登入檢查", "[REDACTED]"):
        assert expected in text
    for secret in ("SUPER_SECRET", "URL_PASSWORD", "QUERY_SECRET"):
        assert secret not in text
    assert set(inventory.files) == {"00_上線指引.md", "01_部署SQL.sql", "02_參數異動.md"}


@pytest.mark.parametrize("name", ["lifecycle_exclusion_manifest.json", "source_unit_metadata.json", "surprise.txt", "02_資料SQL.sql"])
def test_undeclared_output_is_rejected_before_any_write(tmp_path, name):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    output = tmp_path / "operator"
    output.mkdir()
    (output / name).write_text("sentinel")
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, output)
    assert list(output.iterdir()) == [output / name]
    assert (output / name).read_text() == "sentinel"


@pytest.mark.parametrize("suffix", ["docs/release-artifacts", "docs/RELEASE-ARTIFACTS/new", "operator/../escaped", ".release-docs/runs/operator"])
def test_unsafe_output_path_is_rejected_without_creating_it(tmp_path, suffix):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, tmp_path / suffix)
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "docs").exists()


@pytest.mark.parametrize("damage", ["missing_manifest", "wrong_manifest_name", "invalid_manifest", "changed_sql", "blocked", "excluded_mapping"])
def test_invalid_evidence_cannot_produce_operator_files(tmp_path, damage):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    if damage == "missing_manifest":
        manifest.unlink()
    elif damage == "wrong_manifest_name":
        manifest = manifest.parent / "source_unit_metadata.json"
    elif damage == "invalid_manifest":
        manifest.write_text("{}")
    elif damage == "changed_sql":
        artifact.path.write_bytes(b"tampered SQL")
    elif damage == "blocked":
        analysis.findings.append({"code": "unresolved_exclusion", "blocking": True})
    else:
        artifact.unit_mapping[0]["unit_id"] = "core"
    output = tmp_path / "operator"
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, output)
    assert not output.exists()


def test_existing_sql_in_output_can_be_preserved_and_documents_are_deterministic(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    output = artifact.path.parent
    first = module.render_release_documents(analysis, artifact, manifest, output)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    second = module.render_release_documents(analysis, artifact, manifest, output)
    assert first.files == second.files
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


def test_stale_optional_document_is_rejected_instead_of_delivered(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    output = tmp_path / "operator"
    output.mkdir()
    (output / "02_參數異動.md").write_text("stale")
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, output)
    assert {p.name for p in output.iterdir()} == {"02_參數異動.md"}


@pytest.mark.parametrize("surface", ["output_file", "artifact", "manifest"])
def test_hardlinks_are_rejected_without_touching_link_target(tmp_path, surface):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    output = tmp_path / "operator"
    output.mkdir()
    target = tmp_path / "target"
    if surface == "output_file":
        target.write_bytes(b"sentinel")
        link = output / "00_上線指引.md"
    elif surface == "artifact":
        target.write_bytes(artifact.path.read_bytes())
        artifact.path.unlink()
        link = artifact.path
    else:
        target.write_bytes(manifest.read_bytes())
        manifest.unlink()
        link = manifest
    original = target.read_bytes()
    try:
        os.link(target, link)
    except OSError as error:
        pytest.skip("Sandbox cannot create hardlinks: " + str(error.winerror))
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, output)
    assert target.read_bytes() == original


def test_output_directory_link_is_rejected(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "operator"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip("Sandbox cannot create symlinks: " + str(error.winerror))
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, link)
    assert not list(target.iterdir())


def test_localdb_pass_requires_complete_evidence_and_remains_separate_from_sql_review(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    metadata_path = manifest.parent / "lifecycle_metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["localdb_validation"] = {"status": "passed"}
    metadata_path.write_text(json.dumps(metadata))
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "operator")
    text = (tmp_path / "operator/00_上線指引.md").read_text(encoding="utf-8")
    assert "LocalDB：待確認" in text
    assert "SQL 內容審核：待確認" in text


def test_conflicting_existing_document_blocks_all_writes(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    output = tmp_path / "operator"
    output.mkdir()
    guide = output / "00_上線指引.md"
    guide.write_text("unrelated existing guide")
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, output)
    assert list(output.iterdir()) == [guide]
    assert guide.read_text() == "unrelated existing guide"


def test_secret_original_values_are_redacted_from_parameter_instructions(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    analysis.parameter_changes = [{"environment": "Production", "service": "CustomerAPI",
                                   "key": "Auth:Secret", "old_value": "OLD_SECRET", "new_value": "NEW_SECRET",
                                   "format_example": "NEW_SECRET", "apply": "將 OLD_SECRET 改成 NEW_SECRET",
                                   "reload": "reload using NEW_SECRET", "validation": "check NEW_SECRET"}]
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "operator")
    text = (tmp_path / "operator/02_參數異動.md").read_text(encoding="utf-8")
    assert "OLD_SECRET" not in text and "NEW_SECRET" not in text
    assert "將 [REDACTED] 改成 [REDACTED]" in text


def test_localdb_status_accepts_only_complete_current_artifact_evidence(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    path = manifest.parent / "lifecycle_metadata.json"
    metadata = json.loads(path.read_text())
    evidence = {"status": "passed", "exit_code": 0, "server": "(localdb)\\test",
                "database": "disposable", "provider_version": "SQL Server test",
                "tool_version": "runner test", "baseline_source": "old.sql", "fixture_source": "fixture.sql",
                "command": "isolated runner", "checks": ["all checks passed"]}
    metadata["localdb_validation"] = {"status": "passed", "artifact_sha256": artifact.sha256,
                                     "rounds": {name: evidence for name in ("validate_only", "commit", "rerun", "injected_failure")}}
    path.write_text(json.dumps(metadata))
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "complete")
    assert "LocalDB：通過" in (tmp_path / "complete/00_上線指引.md").read_text(encoding="utf-8")
    metadata["localdb_validation"]["artifact_sha256"] = "f" * 64
    path.write_text(json.dumps(metadata))
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "stale")
    assert "LocalDB：待確認" in (tmp_path / "stale/00_上線指引.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ["01_部署SQL.sql", "lifecycle_exclusion_manifest.json"])
def test_linked_input_files_are_rejected(tmp_path, filename):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    path = artifact.path if filename.endswith(".sql") else manifest
    target = tmp_path / "target"
    target.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip("Sandbox cannot create symlinks: " + str(error.winerror))
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, tmp_path / "operator")
    assert not (tmp_path / "operator").exists()


def test_excluded_units_do_not_reappear_in_structure_change_scope(tmp_path):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    excluded = dict(analysis.units[0], unit_id="core", objects=["dbo.Core"],
                    structure_changes=[{"kind": "table", "name": "dbo.Core", "description": "建立排除核心表", "impact": "排除影響"}])
    analysis.units.append(excluded)
    analysis.structure_changes.append({"kind": "column", "unit_id": "core", "name": "dbo.Core.Id",
                                       "description": "新增排除欄位", "impact": "排除影響"})
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "explicit")
    text = (tmp_path / "explicit/00_上線指引.md").read_text(encoding="utf-8")
    assert "新增排除欄位" not in text
    del analysis.structure_changes
    module.render_release_documents(analysis, artifact, manifest, tmp_path / "unit-descriptions")
    text = (tmp_path / "unit-descriptions/00_上線指引.md").read_text(encoding="utf-8")
    assert "建立排除核心表" not in text


@pytest.mark.parametrize("suffix", ["operator/.. /escaped", "docs./release-artifacts", "operator/folder. "])
def test_windows_ambiguous_segments_cannot_escape_path_checks(tmp_path, suffix):
    module = api()
    analysis, artifact, manifest = inputs(tmp_path)
    with pytest.raises(ValueError):
        module.render_release_documents(analysis, artifact, manifest, tmp_path / suffix)
    assert not (tmp_path / "operator").exists()
