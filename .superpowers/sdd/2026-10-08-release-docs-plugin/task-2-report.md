# Task 2 implementation report

Implemented the four requested Task 2 files using Python standard library production code.

## Behavior

- `analyze_release_units(repo, evidence, baseline_schema, exclusion_intent)` returns an extensible `AnalysisResult` dataclass with units, source metadata, baseline hash, source scope, findings and exclusions. `blocked` reports blocking findings.
- SQL is read from the requested Git revision, including a resolved commit SHA and raw source SHA-256. Uncommitted worktree text never replaces the requested source. Discovery covers SQL, SQL projects, dacpac, migration and model snapshot path candidates.
- One SQL file is one complete atomic unit, with a first-line JSON declaration. An explicit `evidence.execution_units` descriptor can instead map an authoritative deployment artifact to project/migration sources through `covers`. Descriptors provide unit ID, phase, issue references, objects, dependencies and completeness. Arbitrary fields are ignored. This conservative contract avoids deriving SQL from ORM code or splitting unknown deployment batches.
- Phase/dependency ordering is deterministic. Missing artifacts, unresolved dependencies, cycles, duplicate IDs and phase conflicts block output. Deleted sources require an explicit deployment artifact rather than generating DROP instructions.
- `#5005` intent derives complete controlled units from explicit source issue references, with source evidence, reason, operator action, authorization/review state, reinstatement conditions and transitive dependent units. Mixed issue ownership blocks partial exclusion. Remaining dependent units produce a blocking finding rather than silently widening authorization.
- `write_lifecycle_run` writes the fixed `lifecycle_exclusion_manifest.json`, `source_unit_metadata.json` and `lifecycle_metadata.json` only under `.release-docs/runs/<run-id>/`. Same input is byte-identical; changed evidence cannot overwrite an existing run. Paths through links and existing linked files are rejected. SQL/file contents and freeform user intent are not persisted.
- Lifecycle policy records cleanup after successful delivery, seven-day retention for failure/interruption and a pending-success cleanup marker. Cleanup execution and LocalDB runner integration belong to subsequent workflow tasks; Task 2 does not run a database provider.

## TDD and verification

1. Wrote seven focused real-Git fixture tests before implementation. The first invocation hit the sandbox's inaccessible default pytest temp directory. Re-running with an explicit permitted `--basetemp` produced **7 expected failures**, all asserting the missing analyzer.
2. Initial implementation produced 6 passes and one source-hash mismatch caused by Git autocrlf in the fixture. Added `core.autocrlf=false` so the fixture comparison uses the exact committed source bytes.
3. Added deployment mapping/source-line, cycle and hardlink tests. The mapping/source-line test failed before its metadata support was added. The sandbox denied hardlink fixture creation; that test now reports an explicit skip on this environment.
4. `pytest release-docs-plugin/tests -q --basetemp C:/Users/kenny/.codex/visualizations/2026/10/09/01a12037-23bb-7990-bf9c-e9a883477d76/task2-tests`: **16 passed, 1 skipped**.
5. Root `pytest -q` with a permitted basetemp: **collection error** in `redmine-mcp/tests`, `ImportPathMismatchError` for the two existing `tests.conftest` modules in `redmine-issue-skills` and `redmine-mcp`. Retrying `--import-mode=importlib` also stopped during collection with duplicate plugin registration of those same conftest modules. No unrelated files were changed to address that existing repository-wide collection issue.
6. The installed pytest runtime emits a `pyreadline3` console destructor warning after exit on this Windows sandbox. It does not change the successful plugin-suite exit code. The default WindowsApps `python.exe` alias cannot execute here; verification used the installed `pytest.exe`.

## Scope and limitations

The analyzer validates provenance, explicit complete unit boundaries and graph consistency. It does not claim arbitrary SQL parsing, complete-definition SQL repair, transaction validation or deployment validation; those are subsequent plan tasks. Project and migration sources without an authoritative, explicitly mapped SQL unit block production. No actual cleanup or LocalDB execution is implemented in this task.

Only Task 2 production files, tests and this report are included in the requested commit. Existing README and design/plan work remains outside the Task 2 commit.
