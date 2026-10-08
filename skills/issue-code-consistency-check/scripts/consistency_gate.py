"""Select a consistency skill and enforce the Redmine write gate.

The BDD closeout flow can call these pure functions with the current issue,
repository, available skill metadata, and evidence status. No filesystem or
Redmine mutation is performed here.
"""
from __future__ import annotations

from typing import Any


def select_skill(context: dict[str, Any]) -> dict[str, Any]:
    """Choose the best available read-only consistency skill."""
    if not context.get("needsCodeConsistency", False):
        return {"status": "UNAVAILABLE", "skill": None}

    issue_system = str(context.get("issueSystem", "")).lower()
    ranked: list[tuple[int, str]] = []
    for candidate in context.get("availableSkills", []):
        name = str(candidate.get("name", ""))
        description = str(candidate.get("description", "")).lower()
        haystack = f"{name.lower()} {description}"
        score = 0
        if "consistency" in haystack or "一致" in haystack:
            score += 40
        if "issue" in haystack and ("git" in haystack or "程式" in haystack or "code" in haystack):
            score += 30
        if issue_system == "redmine" and name == "issue-code-consistency-check":
            score += 100
        if score:
            ranked.append((score, name))

    if not ranked:
        return {"status": "UNAVAILABLE", "skill": None}
    _, selected = max(ranked, key=lambda item: (item[0], item[1]))
    return {"status": "SELECTED", "skill": selected}


def can_update_redmine(evidence: dict[str, Any]) -> bool:
    """Return whether every required closeout gate is satisfied."""
    bdd = evidence.get("bdd", {})
    screenshots = evidence.get("screenshots", {})
    return (
        evidence.get("consistency") == "PASS"
        and bdd.get("failed", 0) == 0
        and bdd.get("blocked", 0) == 0
        and bdd.get("notRun", 0) == 0
        and screenshots.get("allMasked") is True
        and screenshots.get("attachmentsVerified") is True
    )
