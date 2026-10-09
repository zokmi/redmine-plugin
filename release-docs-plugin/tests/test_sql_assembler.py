"""Static contract checks and byte-preserving artifact assembly; no DB is used."""
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills/release-docs/scripts"
sys.path.insert(0, str(SCRIPTS))


def api():
    assert (SCRIPTS / "assemble_deployment_sql.py").exists(), "SQL assembler is missing"
    return importlib.import_module("assemble_deployment_sql")


def unit(unit_id, phase, sql, dependencies=()):
    return {"unit_id": unit_id, "phase": phase, "complete": True,
            "depends_on": list(dependencies), "source_path": "sql/中文 ' " + unit_id + ".sql",
            "source_revision": "a" * 40, "source_hash": "b" * 64, "source_line": 2,
            "sql_hash": hashlib.sha256(sql.encode("utf-8")).hexdigest(), "sql": sql}


def assembled(tmp_path, mode=1, units=None):
    module = api()
    if units is None:
        units = [unit("schema", "SCHEMA", "CREATE TABLE dbo.NewTable (Id int);\r\n"),
                 unit("data", "DATA", "INSERT INTO dbo.NewTable VALUES (1);", ("schema",))]
    output = tmp_path / "01_部署SQL.sql"
    artifact = module.assemble_deployment_sql(units, output, mode)
    return module, artifact, output.read_bytes().decode("utf-8")


def codes(findings):
    return {finding["code"] for finding in findings}


@pytest.mark.parametrize("mode", [0, 1])
def test_unified_artifact_preserves_source_and_records_precise_mapping(tmp_path, mode):
    units = [unit("schema", "SCHEMA", "CREATE TABLE dbo.NewTable (Id int);\r\n"),
             unit("repair", "REPAIR", "ALTER TABLE dbo.NewTable ADD Code int;", ("schema",)),
             unit("data", "DATA", "INSERT INTO dbo.NewTable VALUES (1, 2);", ("repair",)),
             unit("validate", "VALIDATION", "SELECT COUNT(*) FROM dbo.NewTable;", ("data",))]
    module, artifact, text = assembled(tmp_path, mode, units)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["01_部署SQL.sql"]
    assert artifact.path == tmp_path / "01_部署SQL.sql"
    assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert artifact.transaction_mode == mode
    assert not module.validate_sql_contract(text)
    assert [m["unit_id"] for m in artifact.unit_mapping] == ["schema", "repair", "data", "validate"]
    positions = []
    for source, mapping in zip(units, artifact.unit_mapping):
        positions.append(text.index(source["sql"]))
        assert mapping["source_line"] == 2
        assert mapping["source_revision"] == "a" * 40
        assert mapping["source_path"] == source["source_path"]
        assert mapping["depends_on"] == source["depends_on"]
        start = mapping["artifact_start_line"] - 1
        lines = text.splitlines(keepends=True)
        assert "".join(lines[start:start + len(source["sql"].splitlines())])[:len(source["sql"])] == source["sql"]
    assert positions == sorted(positions)
    for phase in ("SCHEMA", "REPAIR", "DATA", "VALIDATION"):
        assert "-- PHASE: " + phase in text
    assert "IF @ValidateOnly = 1" in text and "ELSE IF @ValidateOnly = 0" in text
    assert "DECLARE @ValidateOnly bit = " + str(mode) in text


def test_same_inputs_produce_identical_bytes_and_release_id(tmp_path):
    _, first, _ = assembled(tmp_path / "first")
    _, second, _ = assembled(tmp_path / "second")
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.sha256 == second.sha256
    assert first.release_id == second.release_id


def test_explicit_release_and_injected_failure_have_structured_original_error_context(tmp_path):
    failed = unit("failure", "DATA", "THROW 51000, N'fixture failure', 1;")
    module, artifact, text = assembled(tmp_path, {"validate_only": 1, "release_id": "release-42"}, [failed])
    assert artifact.release_id == "release-42"
    assert "SET @UnitId = N'failure';" in text
    assert "SET @SourceLine = 2;" in text
    catch = text.split("BEGIN CATCH", 1)[1]
    assert catch.index("ROLLBACK TRANSACTION") < catch.index("SELECT") < catch.index("THROW;")
    for field in ("ReleaseId", "DatabaseName", "ServerName", "ValidateOnly", "Phase", "UnitId",
                  "SourcePath", "SourceRevision", "SourceLine", "ErrorNumber", "ErrorSeverity",
                  "ErrorState", "ErrorProcedure", "ErrorLine", "ErrorMessage", "XactState",
                  "TranCount", "TransactionAction"):
        assert " AS " + field in catch
    assert not module.validate_sql_contract(text)


@pytest.mark.parametrize("sql, code", [
    ("INSERT INTO dbo.T VALUES(1); COMMIT TRANSACTION;", "unit_transaction_control"),
    ("BEGIN TRAN; SELECT 1;", "unit_transaction_control"),
    ("ROLLBACK TRAN;", "unit_transaction_control"),
    ("SAVE TRANSACTION partial;", "unit_transaction_control"),
    ("BEGIN TRY SELECT 1 / 0; END TRY BEGIN CATCH PRINT N'continue'; END CATCH; SELECT 2;", "silent_catch"),
    ("RAISERROR(N'failure', 16, 1);", "raiserror_without_throw"),
    ("ALTER DATABASE CurrentDb SET SINGLE_USER WITH ROLLBACK IMMEDIATE;", "non_transactional_sql"),
    ("CREATE DATABASE OtherDb;", "non_transactional_sql"),
    ("BACKUP DATABASE CurrentDb TO DISK = 'somewhere';", "non_transactional_sql"),
    ("SELECT 1;\nGO\nSELECT 2;", "batch_separator"),
    ("RETURN; SELECT 1;", "early_exit"),
    ("SET XACT_ABORT OFF;", "xact_abort_disabled"),
    ("EXEC(N'COMMIT TRANSACTION');", "opaque_execution"),
    ("EXEC sys.sp_executesql N'SELECT 1';", "opaque_execution"),
    ("USE OtherDb;", "database_switch"),
    ("SET @ValidateOnly = 0;", "reserved_context_mutation"),
])
def test_unsafe_unit_is_blocked_before_writing(tmp_path, sql, code):
    module = api()
    destination = tmp_path / "absent/01_部署SQL.sql"
    with pytest.raises(module.SQLContractError) as error:
        module.assemble_deployment_sql([unit("unsafe", "DATA", sql)], destination, 1)
    assert code in codes(error.value.findings)
    assert all(f["blocking"] for f in error.value.findings)
    assert all(f["unit_id"] == "unsafe" for f in error.value.findings)
    assert not destination.parent.exists()


def test_comments_and_literal_keywords_do_not_fake_or_break_contract(tmp_path):
    sql = "SELECT N'COMMIT TRANSACTION; GO; ALTER DATABASE x; ''escaped''';\n/* BEGIN TRAN; /* inner */ COMMIT; */\n-- RAISERROR(1, 1, 1);\nSELECT [COMMIT], \"ROLLBACK\" FROM dbo.T;"
    module, _, text = assembled(tmp_path, units=[unit("text", "DATA", sql)])
    assert not module.validate_sql_contract(text)
    assert module.validate_sql_contract("-- SET XACT_ABORT ON; BEGIN TRY BEGIN TRANSACTION; THROW;\nSELECT N'BEGIN CATCH';")


@pytest.mark.parametrize("before, after, code", [
    ("SET XACT_ABORT ON;", "SET XACT_ABORT OFF;", "missing_xact_abort"),
    ("BEGIN TRY", "BEGIN", "missing_try_catch"),
    ("BEGIN TRANSACTION;", "SELECT 1;", "transaction_count"),
    ("IF @ValidateOnly = 1", "IF @ValidateOnly = 0", "validate_only_rollback"),
    ("ELSE IF @ValidateOnly = 0", "ELSE IF @ValidateOnly = 1", "deploy_commit"),
    ("COMMIT TRANSACTION;", "SELECT 1;", "transaction_count"),
    ("THROW;", "PRINT N'continue';", "silent_catch"),
    (" AS ErrorNumber", " AS Missing", "missing_error_context"),
    (" AS SourceLine", " AS Missing", "missing_error_context"),
    (" AS TransactionAction", " AS Missing", "missing_error_context"),
])
def test_contract_rejects_broken_wrapper(tmp_path, before, after, code):
    module, _, text = assembled(tmp_path)
    assert code in codes(module.validate_sql_contract(text.replace(before, after)))


def test_extra_commit_cannot_hide_between_units_or_outside_wrapper(tmp_path):
    module, _, text = assembled(tmp_path)
    for altered in (text + "\nCOMMIT;", text.replace("-- PHASE: DATA", "COMMIT;\n-- PHASE: DATA")):
        assert "transaction_count" in codes(module.validate_sql_contract(altered))


@pytest.mark.parametrize("change", ["hash", "missing_metadata", "incomplete", "dependency", "phase", "duplicate"])
def test_invalid_descriptors_cannot_produce_an_operator_artifact(tmp_path, change):
    module = api()
    units = [unit("schema", "SCHEMA", "SELECT 1;"), unit("data", "DATA", "SELECT 2;", ("schema",))]
    if change == "hash":
        units[0]["sql"] = "SELECT 999;"
    elif change == "missing_metadata":
        del units[0]["source_revision"]
    elif change == "incomplete":
        units[0]["complete"] = False
    elif change == "dependency":
        units[1]["depends_on"] = ["absent"]
    elif change == "phase":
        units.reverse()
    else:
        units[1]["unit_id"] = "schema"
    with pytest.raises(ValueError):
        module.assemble_deployment_sql(units, tmp_path / "01_部署SQL.sql", 1)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", [2, "commit", None, {"validate_only": 1, "release_id": "password=SECRET"}])
def test_invalid_or_sensitive_mode_does_not_leak_or_write(tmp_path, mode):
    module = api()
    with pytest.raises(ValueError) as error:
        module.assemble_deployment_sql([], tmp_path / "01_部署SQL.sql", mode)
    assert "SECRET" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_only_fixed_operator_sql_filename_is_accepted(tmp_path):
    module = api()
    with pytest.raises(ValueError):
        module.assemble_deployment_sql([], tmp_path / "02_資料SQL.sql", 1)
    assert not list(tmp_path.iterdir())


def test_unrelated_metadata_is_not_exposed_in_generated_context(tmp_path):
    source = unit("one", "VALIDATION", "SELECT 1;")
    source["password"] = "SECRET_NOT_IN_SQL"
    source["token"] = "SECRET_NOT_IN_SQL"
    _, artifact, text = assembled(tmp_path, units=[source])
    assert "SECRET_NOT_IN_SQL" not in text
    assert "SECRET_NOT_IN_SQL" not in json.dumps(artifact.unit_mapping)


@pytest.mark.parametrize("sql, code", [
    ("RAISERROR(N'failure', 16, 1)", "raiserror_without_throw"),
    ("EXEC [sys].[sp_executesql] N'COMMIT TRANSACTION';", "opaque_execution"),
    ("DECLARE @x int, @ValidateOnly bit; SELECT 1;", "reserved_context_mutation"),
    ("SELECT @x = 1, @ValidateOnly = 0;", "reserved_context_mutation"),
    ("CREATE VIEW dbo.V AS SELECT 1 AS Id;", "batch_only_sql"),
    ("ALTER INDEX IX_T ON dbo.T REORGANIZE;", "non_transactional_sql"),
    ("SELECT 1; /* unterminated", "malformed_sql_lexeme"),
    ("SELECT 'unterminated", "malformed_sql_lexeme"),
    ("BEGIN TRY SELECT 1/0; END TRY BEGIN CATCH IF 1=0 THROW; END CATCH;", "silent_catch"),
])
def test_additional_lexical_hazards_cannot_bypass_unit_checks(tmp_path, sql, code):
    module = api()
    output = tmp_path / "01_部署SQL.sql"
    with pytest.raises(module.SQLContractError) as error:
        module.assemble_deployment_sql([unit("hazard", "DATA", sql)], output, 1)
    assert code in codes(error.value.findings)
    assert not output.exists()


@pytest.mark.parametrize("mutation, code", [
    ("outside_commit", "transaction_scope"),
    ("outside_begin", "transaction_scope"),
    ("wrong_action", "missing_transaction_action"),
    ("conditional_rethrow", "silent_catch"),
])
def test_present_keywords_do_not_validate_transaction_outside_outer_scope(tmp_path, mutation, code):
    module, _, text = assembled(tmp_path)
    if mutation == "outside_commit":
        text = text.replace("COMMIT TRANSACTION;", "SELECT 1;") + "\nIF @ValidateOnly = 0 BEGIN COMMIT TRANSACTION; END;"
    elif mutation == "outside_begin":
        text = "BEGIN TRANSACTION;\n" + text.replace("    BEGIN TRANSACTION;", "    SELECT 1;")
    elif mutation == "wrong_action":
        text = text.replace("N'ROLLBACK'", "N'COMMIT'")
    else:
        text = text.replace("    THROW;", "    IF 1 = 0 THROW;")
    assert code in codes(module.validate_sql_contract(text))


def test_unquoted_unicode_identifiers_keep_lexical_source_offsets(tmp_path):
    module, _, text = assembled(tmp_path, units=[unit("unicode", "VALIDATION", "SELECT StraßeStraße FROM dbo.中文;")])
    assert not module.validate_sql_contract(text)


@pytest.mark.parametrize("sql, code", [
    ("SET IMPLICIT_TRANSACTIONS ON;", "implicit_transactions_enabled"),
    ("SET NOCOUNT, IMPLICIT_TRANSACTIONS ON;", "implicit_transactions_enabled"),
    ("SET NOEXEC ON;", "execution_disabled"),
    ("SET NOCOUNT, NOEXEC ON;", "execution_disabled"),
    ("SET PARSEONLY ON;", "execution_disabled"),
    ("SET SHOWPLAN_ALL ON;", "execution_disabled"),
    ("SET SHOWPLAN_TEXT ON;", "execution_disabled"),
    ("SET SHOWPLAN_XML ON;", "execution_disabled"),
    ("SET FMTONLY ON;", "execution_disabled"),
    ("SET XACT_ABORT, NOCOUNT OFF;", "xact_abort_disabled"),
    ("SET NOCOUNT, XACT_ABORT OFF;", "xact_abort_disabled"),
    ("CREATE INDEX IX_T ON dbo.T(Id) WITH (ONLINE = ON, RESUMABLE = ON);", "non_transactional_sql"),
    ("ALTER INDEX IX_T ON dbo.T REBUILD WITH (ONLINE = ON, RESUMABLE = ON);", "non_transactional_sql"),
    ("ALTER INDEX IX_T ON dbo.T RESUME;", "non_transactional_sql"),
    ("ALTER INDEX IX_T ON dbo.T PAUSE;", "non_transactional_sql"),
    ("ALTER INDEX IX_T ON dbo.T ABORT;", "non_transactional_sql"),
])
def test_session_options_and_resumable_indexes_block_before_output(tmp_path, sql, code):
    module = api()
    output = tmp_path / "absent/01_部署SQL.sql"
    with pytest.raises(module.SQLContractError) as error:
        module.assemble_deployment_sql([unit("hazard", "REPAIR", sql)], output, 0)
    assert code in codes(error.value.findings)
    assert all(f["unit_id"] == "hazard" for f in error.value.findings)
    assert not output.parent.exists()


@pytest.mark.parametrize("replacement, code", [
    ("IF 1 = 0 BEGIN TRANSACTION;", "unconditional_transaction_start"),
    ("IF 1 = 1 BEGIN BEGIN TRANSACTION; END;", "unconditional_transaction_start"),
    ("SELECT 1; BEGIN TRANSACTION;", "unconditional_transaction_start"),
])
def test_transaction_start_must_be_first_unconditional_try_statement(tmp_path, replacement, code):
    module, _, text = assembled(tmp_path)
    assert code in codes(module.validate_sql_contract(text.replace("BEGIN TRANSACTION;", replacement)))


def test_inherited_implicit_mode_is_normalized_before_transaction(tmp_path):
    module, _, text = assembled(tmp_path)
    # Missing normalization must be rejected even when all transaction keyword
    # counts and mode branches are still present.
    missing = text.replace("SET IMPLICIT_TRANSACTIONS OFF;", "")
    assert "missing_implicit_transactions_off" in codes(module.validate_sql_contract(missing))
    conditional = text.replace("SET IMPLICIT_TRANSACTIONS OFF;", "IF 1 = 0 SET IMPLICIT_TRANSACTIONS OFF;")
    assert "missing_implicit_transactions_off" in codes(module.validate_sql_contract(conditional))


def test_safe_multi_option_settings_and_masked_hazards_preserve_sql(tmp_path):
    sql = ("SET XACT_ABORT, NOCOUNT ON;\nSET IMPLICIT_TRANSACTIONS OFF;\n"
           "SET NOEXEC, SHOWPLAN_ALL OFF;\n"
           "CREATE INDEX IX_T ON dbo.T(Id) WITH (RESUMABLE = OFF);\n"
           "SELECT N'SET NOEXEC ON; SET XACT_ABORT, NOCOUNT OFF; RESUMABLE = ON';\n"
           "/* SET IMPLICIT_TRANSACTIONS ON; */\n")
    module, _, text = assembled(tmp_path, units=[unit("safe", "REPAIR", sql)])
    assert sql in text
    assert not module.validate_sql_contract(text)


@pytest.mark.parametrize("sql, code", [
    ("SET IMPLICIT_TRANSACTIONS ON;", "implicit_transactions_enabled"),
    ("SET NOEXEC ON;", "execution_disabled"),
    ("SET XACT_ABORT, NOCOUNT OFF;", "xact_abort_disabled"),
    ("CREATE INDEX IX_T ON dbo.T(Id) WITH (RESUMABLE = ON);", "non_transactional_sql"),
])
def test_public_validator_rejects_hazards_added_after_wrapper(tmp_path, sql, code):
    module, _, text = assembled(tmp_path)
    assert code in codes(module.validate_sql_contract(text + "\n" + sql))
