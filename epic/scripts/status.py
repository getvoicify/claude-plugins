"""Drift detection, sweep planning and the completion predicate. No I/O."""


def epic_complete(children):
    """True when EVERY sub-issue is closed (never trust subIssuesSummary)."""
    return all(c["state"] == "CLOSED" for c in children or [])


def drift(children, epic):
    """Project-field values that disagree with reality. Reality wins."""
    out = []
    for child in sorted(children or [], key=lambda c: c["number"]):
        if child["state"] == "CLOSED" and child.get("status") != "Done":
            out.append(
                {"target": f"child:{child['number']}", "field": "status",
                 "actual": child.get("status"), "expected": "Done"}
            )
    if (
        epic
        and epic.get("state") == "CLOSED"
        and epic_complete(children)
        and epic.get("status") != "Done"
    ):
        out.append(
            {"target": "epic", "field": "status", "actual": epic.get("status"),
             "expected": "Done"}
        )
    return out


def sweep_plan(children):
    """Worktrees safe to remove: the child's PR is MERGED."""
    return [
        {"child": c["number"], "action": "remove-worktree", "branch": c["branch"]}
        for c in sorted(children or [], key=lambda c: c["number"])
        if (c.get("pr") or {}).get("state") == "MERGED" and c.get("branch")
    ]
