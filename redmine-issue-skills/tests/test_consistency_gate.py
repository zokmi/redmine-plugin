from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".."
    / "skills"
    / "issue-code-consistency-check"
    / "scripts"
    / "consistency_gate.py"
).resolve()
SPEC = importlib.util.spec_from_file_location("consistency_gate", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_redmine_issue_prefers_consistency_skill():
    result = MODULE.select_skill(
        {
            "issueSystem": "redmine",
            "needsCodeConsistency": True,
            "availableSkills": [
                {"name": "generic-review", "description": "review changes"},
                {
                    "name": "issue-code-consistency-check",
                    "description": "compare Redmine requirements with git changes",
                },
            ],
        }
    )

    assert result["status"] == "SELECTED"
    assert result["skill"] == "issue-code-consistency-check"


def test_no_matching_skill_returns_unavailable():
    result = MODULE.select_skill(
        {
            "issueSystem": "redmine",
            "needsCodeConsistency": True,
            "availableSkills": [{"name": "formatting", "description": "write notes"}],
        }
    )

    assert result == {"status": "UNAVAILABLE", "skill": None}


def test_only_complete_pass_evidence_can_write_issue():
    base = {
        "bdd": {"passed": 2, "failed": 0, "blocked": 0, "notRun": 0},
        "consistency": "PASS",
        "screenshots": {"allMasked": True, "attachmentsVerified": True},
    }

    assert MODULE.can_update_redmine(base) is True
    assert MODULE.can_update_redmine({**base, "consistency": "UNVERIFIED"}) is False
    assert MODULE.can_update_redmine({**base, "screenshots": {"allMasked": False, "attachmentsVerified": True}}) is False
