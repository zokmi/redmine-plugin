"""Write immutable, content-free evidence inside the release lifecycle only."""
import json
from pathlib import Path
import os
import re
import shutil


def write_lifecycle_run(run_root, analysis_result, localdb_validation=None,
                        execution_artifact=None, operator_contract=None):
    """Persist deterministic metadata, rejecting operator paths and replacement."""
    requested = Path(run_root)
    if ".." in requested.parts:
        raise ValueError("Traversal is not permitted in lifecycle paths")
    root = requested.absolute()
    if (root.parent.name != "runs" or root.parent.parent.name != ".release-docs"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", root.name)):
        raise ValueError("Run must be inside lifecycle .release-docs/runs/<run-id>")
    if any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)):
        raise ValueError("Linked lifecycle paths are not permitted")
    lifecycle_metadata = {
            "schema_version": 1, "run_id": root.name,
            "retention": {"failed_days": 7, "cleanup_after_success": True},
            "cleanup_status": "pending_success", "review_status": "pending",
            "analysis_status": "blocked" if analysis_result.blocked else "ready",
        }
    if localdb_validation is not None:
        if not isinstance(localdb_validation, dict) or localdb_validation.get("schema_version") != 1:
            raise ValueError("Invalid LocalDB validation evidence")
        lifecycle_metadata["localdb_validation"] = localdb_validation
    if execution_artifact is not None:
        lifecycle_metadata["execution_artifact"] = execution_artifact
    if operator_contract is not None:
        lifecycle_metadata["operator_contract"] = operator_contract
    documents = {
        "lifecycle_exclusion_manifest.json": {"schema_version": 1, "exclusions": analysis_result.exclusions},
        "source_unit_metadata.json": {
            "schema_version": 1, "source_scope": analysis_result.source_scope,
            "baseline": analysis_result.baseline, "sources": analysis_result.sources,
            "units": [{k: v for k, v in u.items() if k != "sql"} for u in analysis_result.units],
            "findings": analysis_result.findings,
        },
        "lifecycle_metadata.json": lifecycle_metadata,
    }
    encoded = {name: (json.dumps(document, sort_keys=True, ensure_ascii=True,
                                indent=2) + "\n").encode("utf-8")
               for name, document in documents.items()}
    # Preflight every existing file before writing anything; no mixed generations.
    for name, data in encoded.items():
        destination = root / name
        if destination.is_symlink() or (destination.exists() and os.stat(destination).st_nlink != 1):
            raise ValueError("Linked lifecycle files are not permitted")
        if destination.exists() and destination.read_bytes() != data:
            raise ValueError("Lifecycle evidence is immutable; create a new run")
    root.mkdir(parents=True, exist_ok=True)
    ignore = root.parent.parent / ".gitignore"
    existing_ignore = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if "runs/" not in {line.strip() for line in existing_ignore.splitlines()}:
        suffix = "" if not existing_ignore or existing_ignore.endswith("\n") else "\n"
        ignore.write_text(existing_ignore + suffix + "runs/\n", encoding="utf-8")
    for name, data in encoded.items():
        destination = root / name
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(data)
    return root / "lifecycle_exclusion_manifest.json"


def cleanup_lifecycle_run(run_root, *, outcome):
    """Apply retention policy to one lifecycle run after review/deployment.

    Successful runs remove only owned temporary material; immutable manifests,
    review records and execution evidence remain available for traceability.
    Failed runs remain available for the configured seven-day retention job.
    """
    requested = Path(run_root)
    if ".." in requested.parts:
        raise ValueError("Traversal is not permitted in lifecycle paths")
    root = requested.absolute()
    if (root.parent.name != "runs" or root.parent.parent.name != ".release-docs"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", root.name)):
        raise ValueError("Run must be inside lifecycle .release-docs/runs/<run-id>")
    if any(p.is_symlink() or (p.exists() and p.is_junction()) for p in (root, *root.parents)):
        raise ValueError("Linked lifecycle paths are not permitted")
    if outcome != "success":
        return False
    if not root.exists():
        return False
    removed = False
    for name in ("temporary", ".tmp"):
        child = root / name
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            removed = True
    return removed


def sweep_expired_lifecycle_runs(lifecycle_root, *, now=None, retention_days=7):
    """Remove only expired failed/temporary lifecycle runs under ``runs``."""
    base = Path(lifecycle_root).absolute()
    runs = base / "runs"
    if base.name != ".release-docs" or runs.is_symlink() or not runs.is_dir():
        raise ValueError("Lifecycle root must contain a regular runs directory")
    if any(p.is_symlink() or (p.exists() and p.is_junction()) for p in (base, *base.parents)):
        raise ValueError("Linked lifecycle paths are not permitted")
    cutoff = (now.timestamp() if now is not None else __import__("time").time()) - retention_days * 86400
    removed = []
    for child in runs.iterdir():
        if not child.is_dir() or child.is_symlink() or child.stat().st_mtime > cutoff:
            continue
        # Permanent manifests and review records are never swept. Only an
        # explicitly owned temporary directory can expire.
        temporary = child / "temporary"
        if temporary.is_dir() and not temporary.is_symlink():
            shutil.rmtree(temporary)
            removed.append(child.name)
    return removed
