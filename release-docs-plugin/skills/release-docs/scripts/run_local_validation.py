"""Opt-in, disposable LocalDB validation and lifecycle evidence writer.

The runner deliberately accepts an executor instead of opening a database itself.
Production deployment is therefore impossible through this module; integrations
must supply a disposable LocalDB executor and report structured checks.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import argparse
import sys
from typing import Any, Callable

ROUNDS = ("validate_only", "commit", "rerun", "injected_failure")
_LOCALDB = re.compile(r"^\(localdb\)(?:\\[A-Za-z0-9_.-]+)?$", re.IGNORECASE)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base_round(name: str, *, server: str, database: str, baseline: str,
                fixture: str, baseline_sha256: str, fixture_sha256: str,
                command: str | list[str], provider: str, tool: str) -> dict[str, Any]:
    return {
        "status": "not_run", "exit_code": None, "server": server,
        "database": database, "provider_version": provider,
        "tool_version": tool, "baseline_source": baseline,
        "baseline_sha256": baseline_sha256, "baseline_usage": "",
        "fixture_source": fixture, "fixture_sha256": fixture_sha256,
        "fixture_usage": "", "command": command,
        "checks": [], "error_output_summary": "",
        "round": name,
    }


def run_local_validation(
    artifact_path: str | os.PathLike[str], *,
    server: str,
    database: str,
    baseline_source: str = "",
    fixture_source: str = "",
    command: str | list[str] = "",
    provider_version: str = "unknown",
    tool_version: str = "release-docs-localdb-runner/1",
    adapter_provenance: str = "",
    executor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run four isolated checks or return explicit ``not_run`` evidence.

    ``executor`` is an injected disposable LocalDB adapter. Every invocation is
    marked as a fresh session and gets a distinct baseline token. The adapter
    must report checks and an error summary; it never receives production
    credentials or a production server because the server is checked here.
    """
    artifact = Path(artifact_path).resolve()
    if not artifact.is_file() or artifact.is_symlink():
        raise ValueError("Deployment artifact must be a regular file")
    if not isinstance(server, str) or not _LOCALDB.fullmatch(server):
        raise ValueError("LocalDB validation requires a (localdb) server; production is forbidden")
    if not isinstance(database, str) or not database.strip():
        raise ValueError("Disposable LocalDB database is required")
    digest = _digest(artifact)
    baseline = Path(baseline_source).resolve() if baseline_source else None
    fixture = Path(fixture_source).resolve() if fixture_source else None
    baseline_sha256 = _digest(baseline) if baseline and baseline.is_file() and not baseline.is_symlink() else ""
    fixture_sha256 = _digest(fixture) if fixture and fixture.is_file() and not fixture.is_symlink() else ""
    rounds = {
        name: _base_round(name, server=server, database=database,
                           baseline=baseline_source, fixture=fixture_source,
                           baseline_sha256=baseline_sha256, fixture_sha256=fixture_sha256,
                           command=command, provider=provider_version,
                           tool=tool_version)
        for name in ROUNDS
    }
    result: dict[str, Any] = {
        "schema_version": 1, "status": "not_run", "reason": "",
        "artifact_sha256": digest, "server": server, "database": database,
        "provider_version": provider_version, "tool_version": tool_version,
        "adapter_provenance": adapter_provenance,
        "rounds": rounds,
    }
    if executor is None:
        result["reason"] = "未提供隔離 LocalDB runner；未執行資料庫驗證"
        return result
    if not callable(executor):
        raise ValueError("executor must be callable")
    if not adapter_provenance.strip():
        raise ValueError("Actual LocalDB validation requires adapter provenance")
    if not provider_version.strip() or provider_version == "unknown":
        raise ValueError("Actual LocalDB validation requires provider version")
    if not tool_version.strip() or tool_version == "unknown":
        raise ValueError("Actual LocalDB validation requires tool version")
    if not baseline_sha256 or not fixture_sha256:
        raise ValueError("baseline_source and fixture_source must be existing regular files")

    failures: list[str] = []
    sql = artifact.read_text(encoding="utf-8")
    for index, name in enumerate(ROUNDS):
        evidence = rounds[name]
        # A fresh baseline/session is an explicit contract, not an implied retry.
        evidence["baseline_usage"] = f"fresh disposable baseline/session #{index + 1}"
        evidence["fixture_usage"] = "loaded into disposable LocalDB"
        if name == "rerun":
            # Convergence must exercise the committed database from the prior
            # round in a fresh session; resetting its baseline would hide rerun
            # defects instead of detecting them.
            fresh_baseline_source = f"{baseline_source}#committed-from=commit#session={index + 1}"
        else:
            fresh_baseline_source = f"{baseline_source}#fresh={index + 1}"
        try:
            raw = executor(
                sql=sql, artifact_path=str(artifact), round_name=name,
                validate_only=(name == "validate_only"),
                inject_failure=(name == "injected_failure"),
                fresh_session=True, baseline_source=fresh_baseline_source,
                fixture_source=fixture_source, server=server, database=database,
            )
            if not isinstance(raw, dict):
                raise ValueError("executor result must be an object")
            for key in ("status", "exit_code", "checks", "error_output_summary"):
                if key in raw:
                    evidence[key] = raw[key]
            if "provider_version" in raw:
                evidence["provider_version"] = raw["provider_version"]
            if "tool_version" in raw:
                evidence["tool_version"] = raw["tool_version"]
            if "command" in raw:
                evidence["command"] = raw["command"]
            if evidence.get("provider_version") in (None, "", "unknown") or evidence.get("tool_version") in (None, "", "unknown"):
                failures.append(f"{name}:version_evidence")
            if evidence["status"] != "passed" or evidence["exit_code"] != 0:
                failures.append(name)
            if not isinstance(evidence["checks"], (list, dict)) or not evidence["checks"]:
                failures.append(f"{name}:checks")
            if not isinstance(evidence["error_output_summary"], str):
                failures.append(f"{name}:error_output_summary")
            if name == "injected_failure":
                semantic = " ".join(map(str, [evidence["checks"], evidence["error_output_summary"]])).lower()
                required = ("expected" in semantic and "error" in semantic,
                            "rollback" in semantic,
                            "throw" in semantic,
                            ("later" in semantic and ("not execute" in semantic or "did not execute" in semantic or "stopped" in semantic)))
                if not all(required):
                    failures.append(f"{name}:rollback_throw_stop_contract")
        except Exception as exc:  # evidence must explain adapter failures
            evidence.update(status="failed", exit_code=1, checks=["runner exception"],
                            error_output_summary=f"{type(exc).__name__}: {exc}")
            failures.append(name)
    result["status"] = "failed" if failures else "passed"
    if failures:
        result["reason"] = "；".join(failures)
    else:
        result["reason"] = "四輪隔離 LocalDB 驗證完成"
    return result


def write_localdb_evidence(run_root: str | os.PathLike[str], evidence: dict[str, Any]) -> Path:
    """Write runner evidence only below ``.release-docs/runs/<run-id>``."""
    root = Path(run_root).absolute()
    if ".." in root.parts or root.parent.name != "runs" or root.parent.parent.name != ".release-docs":
        raise ValueError("LocalDB evidence must be inside lifecycle .release-docs/runs/<run-id>")
    if any(p.is_symlink() or (p.exists() and p.is_junction()) for p in (root, *root.parents)):
        raise ValueError("Linked lifecycle paths are not permitted")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("Invalid LocalDB evidence")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "localdb_validation.json"
    data = (json.dumps(evidence, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError("LocalDB evidence is immutable; create a new run")
    if not destination.exists():
        with destination.open("xb") as stream:
            stream.write(data)
    return destination


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; without an adapter it records an explicit not_run result."""
    parser = argparse.ArgumentParser(description="Run isolated LocalDB release validation")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--server", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--baseline", default="")
    parser.add_argument("--fixture", default="")
    parser.add_argument("--run-root", type=Path, required=True,
                        help=".release-docs/runs/<run-id> lifecycle directory")
    args = parser.parse_args(argv)
    evidence = run_local_validation(args.artifact, server=args.server,
                                    database=args.database,
                                    baseline_source=args.baseline,
                                    fixture_source=args.fixture)
    path = write_localdb_evidence(args.run_root, evidence)
    print(json.dumps({"status": evidence["status"], "evidence": str(path)}, ensure_ascii=True))
    return 0 if evidence["status"] in ("passed", "not_run") else 1


if __name__ == "__main__":
    sys.exit(main())
