# Task 3 implementation report

Implemented only the three requested Task 3 deliverables, plus this task report.

## Interfaces and behavior

- `assemble_deployment_sql(units, output_path, transaction_mode)` consumes Task 2's ordered complete unit dictionaries from memory. It verifies required provenance, exact SQL-text SHA-256, unique IDs, phase order and already-satisfied dependencies. It does not reread arbitrary source paths, infer ORM SQL or reorder authoritative descriptors.
- `transaction_mode` accepts integer `0` or `1`, or a dictionary with `validate_only` set to `0`/`1` and an optional safe `release_id`. Without an explicit ID, the assembler derives a deterministic ID from unit provenance. Invalid metadata is rejected without echoing supplied values.
- The sole accepted operator filename is `01_部署SQL.sql`. The UTF-8 artifact always contains `SCHEMA`, `REPAIR`, `DATA` and `VALIDATION` sections. Source SQL strings, including CRLF and missing final newlines, are preserved exactly within the surrounding wrapper; only separating wrapper text is appended. No `02_資料SQL.sql` is generated.
- `ArtifactRecord` is a dataclass exposing `path`, `sha256`, `release_id`, `transaction_mode`, `unit_mapping` and `findings`. Mappings contain unit/phase, source path/revision/hash/line range, SQL hash, dependencies and exact artifact line range. Fixed allowlists prevent unrelated credential fields from entering comments or error context.
- `validate_sql_contract(sql_text)` returns Task 2-compatible finding dictionaries (`code`, `blocking`, plus applicable metadata). `SQLContractError`, a `ValueError` subclass, carries blocking findings; unsafe unit findings also identify unit and source location. All unit and assembled-contract checks run before creating the output directory or writing SQL.

## Transaction and error contract

- The wrapper enables `SET XACT_ABORT ON`, rejects an inherited transaction without rolling it back, then opens one release transaction inside `TRY`. Validation-only execution rolls back; deployment execution commits after every unit and validation section.
- Before each unit, the wrapper assigns phase, unit ID, source path/revision and source starting line. The error path captures SQL Server's original `ERROR_NUMBER`, `ERROR_SEVERITY`, `ERROR_STATE`, `ERROR_PROCEDURE`, `ERROR_LINE`, `ERROR_MESSAGE`, `XACT_STATE` and `@@TRANCOUNT` before rollback.
- The `CATCH` rolls back when a transaction exists, selects structured release/database/server/mode/unit/source/error/state/action fields and next-action guidance, then performs an unconditional bare `THROW` to retain the original error and stop execution. `COMMIT NOT EXECUTED` is the initial failure action; successful rollback updates it to `ROLLBACK`.
- Diagnostic `ErrorMessage` masks messages containing password/token/secret/connection-string keywords. Original source SQL and the engine's rethrown error are retained; this heuristic is not a universal secrets scrubber and does not modify SQL semantics.
- Conservative lexical checks mask strings, quoted identifiers, line comments and nested block comments. Unterminated lexemes block. ASCII-only keyword casing preserves character offsets even with Unicode identifiers that expand under Python Unicode uppercasing.
- Blocking hazards include independent `BEGIN`/`SAVE`/`COMMIT`/`ROLLBACK`, disabled XACT_ABORT, GO, sqlcmd directives, database switches, early exits, reserved wrapper-variable access, opaque EXEC/procedure execution, module definitions requiring their own batch, unsafe database/fulltext/maintenance operations, RAISERROR without an immediate THROW and handlers without a final standalone rethrow. Transaction counts and scope, mode branches, error rollback, required error fields/functions and rollback action assignment are checked in the assembled wrapper.

## TDD and verification

1. Wrote 44 initial tests first. Focused RED: **44 failed**, all with the expected missing-assembler assertion.
2. Initial implementation: **26 passed, 18 failed**, exposing an overly broad CATCH conditional check and mismatched keyword casing in reserved-variable detection. After those implementation fixes: **44 passed**.
3. Added 13 lexical/transaction-placement regressions. RED: **8 failed, 49 passed**. Quoted EXEC targets, multi-variable context assignments, batch-only views, index reorganize, transaction statements outside TRY and misleading transaction-action assignments failed as expected. After fixes: **57 passed**.
4. Added Unicode-offset coverage. A single expansion was insufficient to expose the defect; the strengthened double-expansion fixture produced **1 failed, 57 passed** with a misplaced original-literal lookup. Replaced expanding Unicode uppercasing with offset-preserving ASCII translation.
5. Final `pytest release-docs-plugin/tests -q --tb=short --basetemp C:/Users/kenny/.codex/visualizations/2026/10/09/01a12037-23bb-7990-bf9c-e9a883477d76/task3-final`: **82 passed, 1 skipped** in 54.79 seconds, including all **58 SQL assembler cases**. Exit code **0**. The skip is the existing sandbox hardlink fixture. `git diff --check` passed before staging; a staged whitespace check also runs before the commit.
6. Root `pytest -q --tb=short` with a permitted `--basetemp` stopped with **1 collection error**: `_pytest.pathlib.ImportPathMismatchError` for `tests.conftest` in `redmine-issue-skills/tests` versus `redmine-mcp/tests`. This pre-existing repository collection conflict was also documented by Task 2; no unrelated files were modified.
7. Some installed pytest invocations print a `pyreadline3` Console destructor `WinError 0xc0000008` after exit. It does not change successful test exit codes. The existing hardlink fixture explicitly skips when the Windows sandbox denies hardlink creation.

## Limits

These tests exercise deterministic file production and static contract rejection, including an injected THROW unit and its emitted context/rollback/rethrow ordering. No database was contacted, and no rollback, commit, retry convergence or provider compatibility is claimed to have executed. Static checks are not a SQL parser or deployment proof. Batch/procedure SQL is conservatively blocked until an explicit execution strategy can prove its transaction behavior. Source repair guards and definition checks must be supplied by authoritative repair units; the assembler does not invent guards or rewrite upstream SQL.

Commit message: `feat: assemble unified transactional deployment SQL`.

## P1/P2 transaction contract review fixes

- Read and reproduced all five findings from `task-3-review.md`. The root causes were missing session-option protections, single-option SET matching, transaction keyword counts without unconditional placement, and an incomplete unsafe index-operation check.
- Added 25 regression cases before the fixes. Focused RED using the repository venv Python: **24 failed, 59 passed**. The previously safe-setting preservation case passed; all newly asserted unsafe cases failed for the intended contract gaps.
- After rejecting an inherited transaction, the wrapper now unconditionally executes `SET IMPLICIT_TRANSACTIONS OFF` immediately before TRY. The validator requires that final standalone preflight statement. Missing or conditional normalization blocks, and units that enable implicit transactions block rather than being rewritten.
- The lexical checker recognizes the limited `SET option[, option] ON/OFF` grammar after masking comments/literals. It rejects combined or single XACT_ABORT OFF, IMPLICIT_TRANSACTIONS ON, and NOEXEC/PARSEONLY/SHOWPLAN_ALL/SHOWPLAN_TEXT/SHOWPLAN_XML/FMTONLY ON. Safe OFF settings and combined XACT_ABORT ON remain allowed.
- The transaction start must be the first unconditionally executed statement inside the release TRY. Conditional/nested starts and source statements before BEGIN TRANSACTION block even when all transaction keyword counts remain correct.
- CREATE/ALTER INDEX with RESUMABLE=ON and ALTER INDEX PAUSE/RESUME/ABORT now produce blocking non-transactional findings. Safe RESUMABLE=OFF source and keyword-like text in comments/literals remain preserved.
- Focused GREEN: `redmine-mcp/.venv/Scripts/python.exe -m pytest release-docs-plugin/tests/test_sql_assembler.py -q --tb=short` with the permitted `task3-review-fix-green` basetemp: **83 passed** in 1.19 seconds, exit code **0**. The repository venv avoids the system console destructor warning.
- The first full plugin run using the reviewer's repository venv (Python 3.11.13) produced **106 passed, 1 skipped, 1 failed** in 44.58 seconds. The existing Task 2 test `test_lifecycle_is_deterministic_immutable_and_contains_no_source_contents` fails because `pathlib.WindowsPath.is_junction` is unavailable in Python 3.11 (`AttributeError`). That API exists in the Python 3.13 runtime used for Task 2 and the original Task 3 full-suite runs. No unrelated lifecycle code was changed.
- Full-plugin GREEN on its previously validated Python 3.13.5 runtime: `pytest release-docs-plugin/tests -q --tb=short` with permitted `task3-review-fix-plugin313` basetemp: **107 passed, 1 skipped** in 43.94 seconds, exit code **0**. The skip is the existing sandbox hardlink fixture; the system pytest console destructor warning remains as previously documented.
- Fresh root-suite verification with the same venv still stops during collection with the existing `tests.conftest` `ImportPathMismatchError` between `redmine-issue-skills/tests` and `redmine-mcp/tests`: **1 collection error**, exit code **1**. No unrelated files were changed. No database was contacted and no runtime transaction claim is made.
- Fix commit message: `fix: harden deployment transaction contract`.
