"""Derive the complete unmet-merge-requirement set from GitHub state. No I/O."""

_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _req(code, detail, action):
    return {"code": code, "detail": detail, "action": action}


def requirements(pr, ruleset, threads):
    """Every reason GitHub will refuse to merge this PR, sorted by code."""
    reqs = []
    state = pr.get("mergeStateStatus")

    if pr.get("isDraft"):
        reqs.append(_req("draft", "PR is a draft", "mark-ready"))
    if state == "BEHIND":
        reqs.append(_req("behind-base", "branch is behind base", "update-branch"))
    if state == "DIRTY":
        reqs.append(_req("conflict", "merge conflict with base", "rebase-resolve"))

    seen = set()
    for check in pr.get("statusCheckRollup") or []:
        name = check.get("name")
        seen.add(name)
        if check.get("status") != "COMPLETED":
            reqs.append(
                _req(f"check-pending:{name}", "check in progress", "wait")
            )
        elif check.get("conclusion") not in _PASSING:
            reqs.append(
                _req(f"check-failing:{name}", "check failed", "ci-fix-loop")
            )

    for name in sorted((ruleset or {}).get("required_status_checks") or []):
        if name not in seen:
            reqs.append(
                _req(f"check-missing:{name}", "required check never started",
                     "diagnose")
            )

    for thread in threads or []:
        if not thread.get("isResolved"):
            reqs.append(
                _req(
                    f"thread-unresolved:{thread['id']}",
                    f"unresolved thread on {thread.get('path')}",
                    "resolve-thread",
                )
            )

    decision = pr.get("reviewDecision")
    if decision == "CHANGES_REQUESTED":
        reqs.append(
            _req("changes-requested", "a reviewer requested changes", "fix-loop")
        )
    elif decision == "REVIEW_REQUIRED" and (ruleset or {}).get(
        "required_approving_review_count"
    ):
        reqs.append(
            _req("approval-missing", "an approving review is required",
                 "park-waiting-on-human")
        )

    return sorted(reqs, key=lambda r: r["code"])


def is_clean(reqs):
    """The HARD exit condition: nothing left for GitHub to block on."""
    return not reqs
