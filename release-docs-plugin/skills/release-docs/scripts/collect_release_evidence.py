#!/usr/bin/env python3
"""Collect Git path/status evidence without reading or emitting file contents."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


class EvidenceError(Exception):
    """A safe, operator-readable collection failure."""


def git(repo, *args):
    # No shell, patches, external diff drivers, or Git stderr in output.
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=False,
        )
    except OSError as error:
        raise EvidenceError("Git executable is unavailable") from error
    if result.returncode:
        raise EvidenceError("Git metadata collection failed")
    return result.stdout


def resolve_revision(repo, revision):
    try:
        return git(repo, "rev-parse", "--verify", "--end-of-options",
                   revision + "^{commit}").decode("ascii").strip()
    except EvidenceError as error:
        raise EvidenceError("Unknown or invalid commit revision") from error


def parse_changes(raw):
    """Parse NUL-delimited --name-status, including both rename/copy paths."""
    tokens = raw.decode("utf-8", errors="surrogateescape").split("\0")
    records = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        index += 1
        record = {"status": status[0], "path": tokens[index]}
        index += 1
        if status[0] in "RC":
            record["old_path"] = record["path"]
            record["path"] = tokens[index]
            record["similarity"] = int(status[1:])
            index += 1
        records.append(record)
    return sorted(records, key=lambda record: (record["path"], record["status"]))


def collect_release_evidence(repo, base, target, diff_mode="direct"):
    if diff_mode not in ("direct", "merge-base"):
        raise EvidenceError("Unsupported diff mode")
    try:
        root = git(repo, "rev-parse", "--show-toplevel").decode("utf-8").rstrip("\r\n")
    except EvidenceError as error:
        raise EvidenceError("Path is not an accessible Git repository") from error
    base_sha = resolve_revision(root, base)
    target_sha = resolve_revision(root, target)
    diff_base_sha = base_sha
    if diff_mode == "merge-base":
        try:
            diff_base_sha = git(root, "merge-base", base_sha, target_sha).decode("ascii").strip()
        except EvidenceError as error:
            raise EvidenceError("Revisions have no available merge base") from error
    diff_options = ("--no-ext-diff", "--no-textconv", "--name-status", "-z", "--find-renames")
    committed = parse_changes(git(root, "diff", *diff_options, diff_base_sha, target_sha, "--"))
    staged = parse_changes(git(root, "diff", *diff_options, "--cached", "--"))
    unstaged = parse_changes(git(root, "diff", *diff_options, "--"))
    untracked_paths = git(root, "ls-files", "--others", "--exclude-standard", "-z").decode(
        "utf-8", errors="surrogateescape",
    ).split("\0")
    return {
        "schema_version": 1,
        "repo_root": str(Path(root).resolve()),
        "base_sha": base_sha,
        "target_sha": target_sha,
        "diff_base_sha": diff_base_sha,
        "diff_mode": diff_mode,
        "committed_changes": committed,
        "working_tree_changes": {
            "staged": staged,
            "unstaged": unstaged,
            "untracked": [{"status": "?", "path": path} for path in sorted(untracked_paths) if path],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--diff-mode", choices=("direct", "merge-base"), required=True)
    args = parser.parse_args()
    try:
        data = collect_release_evidence(args.repo, args.base, args.target, args.diff_mode)
    except EvidenceError as error:
        print(f"release evidence: {error}", file=sys.stderr)
        return 1
    # ASCII JSON escapes are lossless for Unicode and portable across console encodings.
    print(json.dumps(data, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
