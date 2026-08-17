"""Derive the complete unmet-merge-requirement set from GitHub state. The pure
`requirements()`/`is_clean()` functions do no I/O; `main()` is the thin
impure shell."""
import argparse
import json
import sys

import gh

_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_MERGEABLE_STATES = {"CLEAN", "UNSTABLE", "HAS_HOOKS"}


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

    required_checks = (ruleset or {}).get("required_status_checks") or []
    seen = set()
    for check in pr.get("statusCheckRollup") or []:
        # Normalize entry: CheckRun or StatusContext
        name = check.get("name") or check.get("context")
        seen.add(name)

        # Determine if this check should gate (only if required, or no required list)
        should_gate = not required_checks or name in required_checks

        if should_gate:
            # Handle StatusContext (has state) or CheckRun (has status)
            if "state" in check:
                # StatusContext: state = SUCCESS|PENDING|EXPECTED|FAILURE|ERROR
                state_val = check.get("state")
                if state_val == "SUCCESS":
                    pass  # Passing, no requirement
                elif state_val in {"PENDING", "EXPECTED"}:
                    reqs.append(
                        _req(f"check-pending:{name}", "check in progress", "wait")
                    )
                else:  # FAILURE or ERROR
                    reqs.append(
                        _req(f"check-failing:{name}", "check failed", "ci-fix-loop")
                    )
            else:
                # CheckRun: status = COMPLETED|IN_PROGRESS, conclusion = SUCCESS|FAILURE|...
                if check.get("status") != "COMPLETED":
                    reqs.append(
                        _req(f"check-pending:{name}", "check in progress", "wait")
                    )
                elif check.get("conclusion") not in _PASSING:
                    reqs.append(
                        _req(f"check-failing:{name}", "check failed", "ci-fix-loop")
                    )

    for name in sorted(required_checks):
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
    elif decision == "REVIEW_REQUIRED":
        reqs.append(
            _req("approval-missing", "an approving review is required",
                 "park-waiting-on-human")
        )

    # Catch-all: if state is blocking and we derived no requirements, report it
    if state not in _MERGEABLE_STATES and not reqs:
        reqs.append(
            _req(
                f"blocked-unexplained:{state}",
                "GitHub reports the PR unmergeable for a reason the driver could not derive",
                "diagnose",
            )
        )

    return sorted(reqs, key=lambda r: r["code"])


def is_clean(reqs):
    """The HARD exit condition: nothing left for GitHub to block on."""
    return not reqs


_PR_FIELDS = "mergeStateStatus,isDraft,statusCheckRollup,reviewDecision"

_THREADS_QUERY = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}
"""


def _build_ruleset(rules):
    """Collapse the live `rules/branches/<branch>` REST array into the shape
    `requirements()` expects: {"required_status_checks": [<name>, ...]}."""
    checks = []
    for rule in rules or []:
        if rule.get("type") != "required_status_checks":
            continue
        for entry in (rule.get("parameters") or {}).get("required_status_checks") or []:
            name = entry.get("context")
            if name:
                checks.append(name)
    return {"required_status_checks": sorted(set(checks))}


def _fetch(repo, pr_number):
    owner, name = repo.split("/")
    pr = gh.run_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", _PR_FIELDS]
    )
    try:
        rules = gh.run_json(["api", f"repos/{repo}/rules/branches/main"])
    except gh.GhError as err:
        if "404" in (err.stderr or ""):
            rules = []
        else:
            raise
    ruleset = _build_ruleset(rules)
    data = gh.graphql(_THREADS_QUERY, owner=owner, name=name, pr=pr_number)
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return pr, ruleset, threads


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ask GitHub what is unmet for a PR to merge.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    args = parser.parse_args(argv)

    try:
        pr, ruleset, threads = _fetch(args.repo, args.pr)
    except gh.GhError as err:
        message = err.stderr or str(err)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    reqs = requirements(pr, ruleset, threads)
    clean = is_clean(reqs)
    print(json.dumps({"requirements": reqs, "clean": clean}))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
