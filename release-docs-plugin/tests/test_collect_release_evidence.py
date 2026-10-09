"""CLI integration tests: all fixtures use real, isolated Git repositories."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "skills/release-docs/scripts/collect_release_evidence.py"


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def write(repo, path, content):
    destination = repo / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def commit(repo):
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "中文 repository"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "core.autocrlf", "false")
    for name in ("修改 檔.txt", "刪除.txt", "原始 檔.txt", "工作區.txt"):
        write(root, name, (name + " baseline\n") * 20)
    commit(root)
    return root


def collect(repo, base, target, mode="direct"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--base=" + base,
         "--target=" + target, "--diff-mode", mode], capture_output=True,
    )


def evidence(repo, base, target, mode="direct"):
    result = collect(repo, base, target, mode)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return json.loads(result.stdout)


def test_collects_nested_repo_unicode_paths_and_rename_delete_metadata(repo):
    base = git(repo, "rev-parse", "HEAD")
    write(repo, "新增 資料.sql", "SELECT 'NEVER_EMIT_SQL_SECRET';\n")
    write(repo, "修改 檔.txt", "changed\n")
    (repo / "刪除.txt").unlink()
    git(repo, "mv", "原始 檔.txt", "重新命名 檔.txt")
    target = commit(repo)
    nested = repo / "nested space/中文"
    nested.mkdir(parents=True)
    data = evidence(nested, base, target)
    assert Path(data["repo_root"]).resolve() == repo.resolve()
    assert data["schema_version"] == 1
    assert data["base_sha"] == base
    assert data["target_sha"] == target
    assert data["diff_mode"] == "direct"
    assert data["committed_changes"] == [
        {"status": "M", "path": "修改 檔.txt"},
        {"status": "D", "path": "刪除.txt"},
        {"status": "A", "path": "新增 資料.sql"},
        {"status": "R", "path": "重新命名 檔.txt", "old_path": "原始 檔.txt", "similarity": 100},
    ]
    assert "NEVER_EMIT_SQL_SECRET" not in json.dumps(data)


def test_uses_requested_old_target_and_separates_working_tree(repo):
    base = git(repo, "rev-parse", "HEAD")
    write(repo, "old target.txt", "old\n")
    target = commit(repo)
    write(repo, "new head.txt", "new\n")
    commit(repo)
    write(repo, "工作區.txt", "staged-secret\n")
    git(repo, "add", "工作區.txt")
    write(repo, "工作區.txt", "unstaged-secret\n")
    write(repo, "未追蹤 file.txt", "untracked-secret\n")
    data = evidence(repo, base, target)
    assert data["committed_changes"] == [{"status": "A", "path": "old target.txt"}]
    assert data["working_tree_changes"] == {
        "staged": [{"status": "M", "path": "工作區.txt"}],
        "unstaged": [{"status": "M", "path": "工作區.txt"}],
        "untracked": [{"status": "?", "path": "未追蹤 file.txt"}],
    }
    assert "secret" not in json.dumps(data)
    assert collect(repo, base, target).stdout == collect(repo, base, target).stdout


def test_merge_base_diff_excludes_base_only_branch_changes(repo):
    ancestor = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "base-branch")
    write(repo, "base only.txt", "base\n")
    base = commit(repo)
    git(repo, "checkout", "-qb", "target-branch", ancestor)
    write(repo, "target only.txt", "target\n")
    target = commit(repo)
    assert evidence(repo, base, target)["committed_changes"] == [
        {"status": "D", "path": "base only.txt"},
        {"status": "A", "path": "target only.txt"},
    ]
    merged = evidence(repo, base, target, "merge-base")
    assert merged["base_sha"] == base
    assert merged["diff_base_sha"] == ancestor
    assert merged["committed_changes"] == [{"status": "A", "path": "target only.txt"}]


@pytest.mark.parametrize("revision", ["unknown-revision", "--help", "HEAD; echo injected"])
def test_unknown_or_option_like_revision_is_rejected(repo, revision):
    result = collect(repo, revision, "HEAD")
    assert result.returncode != 0
    assert not result.stdout
    assert b"revision" in result.stderr.lower()


def test_non_repository_is_rejected(tmp_path):
    # tmp_path may itself live beneath the workspace repository.
    result = collect(tmp_path / "not-an-existing-repository", "HEAD", "HEAD")
    assert result.returncode != 0
    assert not result.stdout
    assert b"repository" in result.stderr.lower()
