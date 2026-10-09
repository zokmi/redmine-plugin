"""Hash bounded release evidence without loading Git source/configuration values.

Git supplies blob IDs and hashes for declared SQL sources. Repository secret
files and configuration contents are never opened or included in the record.
Fingerprints are evidence identity, not proof of SQL correctness.
"""
from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

OPERATOR_FILES = ("00_上線指引.md", "01_部署SQL.sql", "02_參數異動.md")
LIFECYCLE_FILES = ("lifecycle_exclusion_manifest.json", "source_unit_metadata.json", "lifecycle_metadata.json")


@dataclass
class FingerprintRecord:
    sha256: str
    components: dict
    schema_version: int = 1


def plain_path(value):
    path = Path(value)
    if any(part in ("..", ".") or part.endswith((".", " ")) or ":" in part
           for part in path.parts if part != path.anchor):
        raise ValueError("Unsafe review path")
    path = path.absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ValueError("Linked review paths are forbidden")
        if item.exists():
            info = item.stat()
            if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                raise ValueError("Linked review paths are forbidden")
            if item.is_file() and info.st_nlink != 1:
                raise ValueError("Hardlinked review files are forbidden")
    return path


def lifecycle_root(value):
    root = plain_path(value)
    if (root.parent.name != "runs" or root.parent.parent.name != ".release-docs"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", root.name)):
        raise ValueError("Review evidence must be inside lifecycle .release-docs/runs/<run-id>")
    return root


def read_json(path):
    path = plain_path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Missing or invalid lifecycle evidence") from None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Invalid lifecycle evidence schema")
    return value


def file_hash(path):
    path = plain_path(path)
    if not path.is_file():
        raise ValueError("Covered release file is missing")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execution_path(run, metadata):
    artifact = metadata.get("execution_artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
        return None
    path = Path(artifact["path"])
    if not path.is_absolute():
        path = run / path
    path = plain_path(path)
    if path.name != "01_部署SQL.sql":
        raise ValueError("Execution artifact must use the unified SQL filename")
    return path


def _git(repo, *arguments):
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Source Git evidence is unavailable")
    return result.stdout


def _source_path(value):
    if not isinstance(value, str) or "\\" in value or ":" in value or "\0" in value:
        raise ValueError("Invalid declared source path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Invalid declared source path")
    # Only declared authoritative DB source files are hashed from the worktree.
    # Other committed code/config changes are represented by Git tree object IDs.
    if path.suffix.lower() not in (".sql", ".sqlproj", ".dacpac", ".cs"):
        raise ValueError("Source content hashing is restricted to database sources")
    return path.as_posix()


def review_fingerprint(repo, output_dir, run_root, source_scope) -> FingerprintRecord:
    repo, output, run = plain_path(repo), plain_path(output_dir), lifecycle_root(run_root)
    metadata = read_json(run / "lifecycle_metadata.json")
    sources = read_json(run / "source_unit_metadata.json")
    read_json(run / "lifecycle_exclusion_manifest.json")
    components = {}
    for name in OPERATOR_FILES:
        path = plain_path(output / name)
        components["output/" + name] = file_hash(path) if path.exists() else "missing"
    for name in LIFECYCLE_FILES:
        components["lifecycle/" + name] = file_hash(run / name)
    artifact_path = execution_path(run, metadata)
    components["execution_artifact"] = file_hash(artifact_path) if artifact_path else "missing"
    baseline = sources.get("baseline", {})
    if isinstance(baseline, dict) and isinstance(baseline.get("source_path"), str):
        path = Path(baseline["source_path"])
        if not path.is_absolute():
            path = repo / path
        if path.suffix.lower() not in (".sql", ".dacpac"):
            raise ValueError("Baseline content hashing is restricted to database schema")
        components["source/baseline"] = file_hash(path)
    if not isinstance(source_scope, dict):
        raise ValueError("Source scope must declare pinned revisions")
    for key in ("base_sha", "target_sha"):
        revision = source_scope.get(key)
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision):
            raise ValueError("Source scope requires pinned revisions")
        sha = _git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode("ascii").strip()
        # Tree IDs cover every committed source without reading secret values.
        components["source/" + key] = sha
        components["source/" + key + "/tree"] = _git(repo, "rev-parse", sha + "^{tree}").decode("ascii").strip()
    if any(not isinstance(sources.get(key, []), list) for key in ("sources", "units")):
        raise ValueError("Invalid declared source metadata")
    descriptors = sources.get("sources", []) + sources.get("units", [])
    declared = sorted({_source_path(item["source_path"]) for item in descriptors
                       if isinstance(item, dict) and "source_path" in item})
    for index, name in enumerate(declared):
        path = plain_path(repo / name)
        components["source/worktree/" + str(index)] = (
            _git(repo, "hash-object", "--no-filters", "--", str(path)).decode("ascii").strip()
            if path.is_file() else "missing")
    encoded = json.dumps(components, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return FingerprintRecord(hashlib.sha256(encoded).hexdigest(), components)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "output-dir", "run-root", "base", "target"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        record = review_fingerprint(args.repo, args.output_dir, args.run_root,
                                    {"base_sha": args.base, "target_sha": args.target})
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(asdict(record), ensure_ascii=True, sort_keys=True))
