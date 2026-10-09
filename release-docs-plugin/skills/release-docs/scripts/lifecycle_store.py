"""Write immutable, content-free evidence inside the release lifecycle only."""
import json
from pathlib import Path
import os
import re


def write_lifecycle_run(run_root, analysis_result):
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
    documents = {
        "lifecycle_exclusion_manifest.json": {"schema_version": 1, "exclusions": analysis_result.exclusions},
        "source_unit_metadata.json": {
            "schema_version": 1, "source_scope": analysis_result.source_scope,
            "baseline": analysis_result.baseline, "sources": analysis_result.sources,
            "units": [{k: v for k, v in u.items() if k != "sql"} for u in analysis_result.units],
            "findings": analysis_result.findings,
        },
        "lifecycle_metadata.json": {
            "schema_version": 1, "run_id": root.name,
            "retention": {"failed_days": 7, "cleanup_after_success": True},
            "cleanup_status": "pending_success", "review_status": "pending",
            "analysis_status": "blocked" if analysis_result.blocked else "ready",
        },
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
    for name, data in encoded.items():
        destination = root / name
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(data)
    return root / "lifecycle_exclusion_manifest.json"
