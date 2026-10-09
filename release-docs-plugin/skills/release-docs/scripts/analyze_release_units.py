"""Derive complete release units from authoritative, revision-pinned sources.

SQL files declare one atomic unit in a first-line ``-- release-unit: {JSON}``.
Alternatively evidence.execution_units supplies the same descriptor plus
source_path and optional covers paths for migration/project sources. No ORM or
Database Project SQL is invented. SQL stays in memory, outside lifecycle JSON.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

PHASES = ("SCHEMA", "REPAIR", "DATA", "VALIDATION")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*\Z")
OBJECT = re.compile(r"[A-Za-z_][A-Za-z0-9_.\[\]]*\Z")
ISSUE = re.compile(r"#[0-9]+\Z")


@dataclass
class AnalysisResult:
    units: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    exclusions: list = field(default_factory=list)
    baseline: dict = field(default_factory=dict)
    source_scope: dict = field(default_factory=dict)

    @property
    def blocked(self):
        return any(f["blocking"] for f in self.findings)


def _finding(result, code, path=None, unit_id=None):
    finding = {"code": code, "blocking": True}
    if path is not None:
        finding["source_path"] = path
    if unit_id is not None:
        finding["unit_id"] = unit_id
    result.findings.append(finding)


def _safe_path(path):
    if not isinstance(path, str) or "\\" in path or ":" in path or "\0" in path:
        raise ValueError("Source path must be a relative repository path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        raise ValueError("Source path must be a relative repository path")
    return parsed.as_posix()


def _read_revision(repo, revision, path):
    # Validate revision independently; never substitute HEAD or working-tree text.
    resolved = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify",
                               "--end-of-options", revision + "^{commit}"],
                              capture_output=True, check=False)
    if resolved.returncode:
        raise ValueError("Unavailable source revision")
    sha = resolved.stdout.decode("ascii").strip()
    read = subprocess.run(["git", "-C", str(repo), "show", sha + ":" + path],
                          capture_output=True, check=False)
    if read.returncode:
        raise ValueError("Unavailable source artifact")
    return sha, read.stdout


def _kind(path):
    lower = path.lower()
    if lower.endswith(".sql"):
        return "sql"
    if lower.endswith(".sqlproj"):
        return "database_project"
    if lower.endswith(".dacpac"):
        return "dacpac"
    if "migration" in lower or "modelsnapshot" in lower:
        return "migration"
    return None


def _strings(descriptor, key, pattern):
    values = descriptor.get(key, [])
    if not isinstance(values, list) or any(not isinstance(v, str) or not pattern.fullmatch(v) for v in values):
        raise ValueError("Invalid unit metadata")
    return sorted(set(values))


def _unit(descriptor, metadata, sql, source_line):
    unit_id = descriptor.get("unit_id")
    phase = descriptor.get("phase", "").upper()
    if not isinstance(unit_id, str) or not IDENTIFIER.fullmatch(unit_id) or phase not in PHASES:
        raise ValueError("Invalid unit declaration")
    if descriptor.get("complete") is not True or not sql.strip():
        raise ValueError("Incomplete unit")
    return {"unit_id": unit_id, "phase": phase, "complete": True,
            "depends_on": _strings(descriptor, "depends_on", IDENTIFIER),
            "issues": _strings(descriptor, "issues", ISSUE),
            "objects": _strings(descriptor, "objects", OBJECT),
            "source_path": metadata["source_path"],
            "source_revision": metadata["source_revision"],
            "source_hash": metadata["source_hash"], "source_line": source_line,
            "covered_sources": sorted(_safe_path(p) for p in descriptor.get("covers", [])),
            "sql_hash": hashlib.sha256(sql.encode("utf-8")).hexdigest(), "sql": sql}


def _order(result):
    remaining = {u["unit_id"]: u for u in result.units}
    if len(remaining) != len(result.units):
        _finding(result, "duplicate_unit_id")
        return
    for unit in result.units:
        for dependency in unit["depends_on"]:
            if dependency not in remaining:
                _finding(result, "unresolved_dependency", unit_id=unit["unit_id"])
            elif PHASES.index(remaining[dependency]["phase"]) > PHASES.index(unit["phase"]):
                _finding(result, "phase_dependency_conflict", unit_id=unit["unit_id"])
    ordered = []
    done = set()
    while remaining:
        ready = sorted((u for u in remaining.values() if set(u["depends_on"]) <= done),
                       key=lambda u: (PHASES.index(u["phase"]), u["unit_id"]))
        if not ready:
            _finding(result, "dependency_cycle_or_missing_unit")
            ordered.extend(sorted(remaining.values(), key=lambda u: (PHASES.index(u["phase"]), u["unit_id"])))
            break
        unit = ready[0]
        ordered.append(unit)
        done.add(unit["unit_id"])
        del remaining[unit["unit_id"]]
    result.units = ordered


def _exclude(result, intent):
    if not intent:
        return
    # Only issue references are retained; freeform intent is never persisted.
    requested = set(re.findall(r"#[0-9]+", str(intent)))
    selected = [u for u in result.units if set(u["issues"]) & requested]
    if not requested or not selected or requested - {i for u in selected for i in u["issues"]}:
        _finding(result, "unresolved_exclusion")
        return
    if any(set(u["issues"]) - requested for u in selected):
        _finding(result, "partial_exclusion")
        return
    affected = {u["unit_id"] for u in selected}
    impacted = set(affected)
    while True:
        expanded = impacted | {u["unit_id"] for u in result.units if set(u["depends_on"]) & impacted}
        if expanded == impacted:
            break
        impacted = expanded
    impact = [u["unit_id"] for u in result.units if u["unit_id"] in impacted - affected]
    if impact:
        _finding(result, "excluded_dependency")
    result.exclusions.append({
        "exclusion_id": "exclude-" + "-".join(i[1:] for i in sorted(requested)),
        "issues": sorted(requested), "unit_ids": [u["unit_id"] for u in selected],
        "units": [{k: v for k, v in u.items() if k != "sql"} for u in selected],
        "objects": sorted({obj for u in selected for obj in u["objects"]}),
        "reason": "Requested issue scope excluded from this release",
        "operator_action": "Preserve existing objects; do not execute excluded units",
        "dependency_impact": impact,
        "reinstatement_conditions": ["Authorize complete unit scope", "Resolve dependent units and repeat artifact review"],
        "authorization_status": "requested", "review_status": "pending",
        "evidence": [{k: u[k] for k in ("unit_id", "source_path", "source_revision", "source_hash")} for u in selected],
    })


def analyze_release_units(repo, evidence, baseline_schema, exclusion_intent):
    """Analyze revision-pinned candidates; any unresolved boundary blocks output."""
    result = AnalysisResult()
    baseline = Path(baseline_schema)
    if baseline.is_file():
        result.baseline = {"source_path": str(baseline.resolve()),
                           "source_hash": hashlib.sha256(baseline.read_bytes()).hexdigest()}
    else:
        _finding(result, "missing_baseline_schema")
    result.source_scope = {k: evidence[k] for k in ("base_sha", "target_sha") if k in evidence}
    explicit = {}
    covered = set()
    for descriptor in evidence.get("execution_units", []):
        path = _safe_path(descriptor["source_path"])
        if path in explicit:
            _finding(result, "partial_execution_artifact", path)
        explicit[path] = descriptor
        covered.update(_safe_path(p) for p in descriptor.get("covers", []))
    candidates = { _safe_path(c["path"]): c for c in evidence.get("committed_changes", []) if _kind(c["path"]) }
    for path in explicit:
        candidates.setdefault(path, {"path": path, "status": "A"})
    for path, change in sorted(candidates.items()):
        kind = _kind(path)
        revision = evidence.get("base_sha") if change["status"] == "D" else evidence.get("target_sha")
        try:
            sha, raw = _read_revision(repo, revision, path)
        except (ValueError, TypeError):
            _finding(result, "missing_authoritative_sql", path)
            continue
        metadata = {"source_path": path, "source_revision": sha,
                    "source_hash": hashlib.sha256(raw).hexdigest(), "kind": kind,
                    "change_status": change["status"]}
        result.sources.append(metadata)
        if change["status"] == "D":
            _finding(result, "deleted_source_requires_execution_artifact", path)
            continue
        if kind != "sql":
            if path not in covered:
                _finding(result, "missing_authoritative_sql", path)
            continue
        try:
            text = raw.decode("utf-8-sig")
            descriptor = explicit.get(path)
            if descriptor is None:
                first, separator, sql = text.partition("\n")
                if not first.startswith("-- release-unit: ") or not separator:
                    raise ValueError("Missing complete unit declaration")
                descriptor = json.loads(first[len("-- release-unit: "):])
                source_line = 2
            else:
                sql = text
                source_line = 1
            if "-- release-unit: " in sql:
                raise ValueError("Partial source artifact")
            result.units.append(_unit(descriptor, metadata, sql, source_line))
        except (ValueError, TypeError, AttributeError):
            _finding(result, "missing_authoritative_sql", path)
    _order(result)
    _exclude(result, exclusion_intent)
    return result
