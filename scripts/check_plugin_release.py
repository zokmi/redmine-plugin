"""Validate release identity, versions and bundled plugin resources."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


def check(tag: str, root: Path) -> None:
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    version = tag[1:]
    for path in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        manifest = json.loads((root / path).read_text(encoding="utf-8"))
        if manifest["version"] != version:
            raise ValueError(f"{path}: expected version {version}")
        if manifest["name"] != "redmine":
            raise ValueError(f"{path}: expected plugin name redmine")
        if "mcpServers" in manifest:
            config = json.loads((root / manifest["mcpServers"]).read_text(encoding="utf-8"))
            if "redmine" not in config["mcpServers"]:
                raise ValueError(f"{path}: missing Redmine MCP server")
    package = tomllib.loads((root / "redmine-mcp/pyproject.toml").read_text(encoding="utf-8"))
    if package["project"]["version"] != version:
        raise ValueError("redmine-mcp/pyproject.toml: version differs from tag")
    for name in (
        "redmine-issue-writing", "issue-code-consistency-check",
        "release-change-items", "redmine-setup",
    ):
        if not (root / "skills" / name / "SKILL.md").is_file():
            raise ValueError(f"Missing skill: {name}")
    print(f"Release {tag}: plugin versions and resources verified")


if __name__ == "__main__":
    check(sys.argv[1], Path(__file__).resolve().parents[1])
