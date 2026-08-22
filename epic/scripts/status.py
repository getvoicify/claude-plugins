"""Drift detection, sweep planning and the completion predicate. The pure
functions below do no I/O; `main()` is the thin impure shell and is
read-only — it never mutates anything (`--sweep` execution belongs to the
driver, not this script)."""
import argparse
import datetime
import json
import sys

import gh
import watch_state


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


def watch_report(children, cursors, now_iso):
    """Live PR watches and how long each has been quiet.

    A long silence is reported here and NEVER parked — the run stays alive
    while an operator gets told which gate has gone quiet and for how long.
    """
    out = []
    for child in sorted(children or [], key=lambda c: c["number"]):
        pr = child.get("pr") or {}
        if pr.get("state") != "OPEN":
            continue
        cursor = (cursors or {}).get(child["number"])
        if not cursor or not cursor.get("last_activity_at"):
            continue
        out.append({
            "child": child["number"],
            "pr": pr.get("number"),
            "quiet_s": watch_state.elapsed_s(cursor["last_activity_at"], now_iso),
            "last_activity": list(cursor.get("last_changed") or []),
        })
    return out


def _now():
    """ISO-8601 UTC stamp. A monkeypatch point for the tests."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_cursors(children):
    """Watch cursors for every child with an open PR, keyed by child number."""
    cursors = {}
    for child in children or []:
        pr = child.get("pr") or {}
        if pr.get("state") == "OPEN" and pr.get("number"):
            cursors[child["number"]] = watch_state.load(child["repo"], pr["number"])
    return cursors


_ISSUE_QUERY = """
query($owner:String!,$name:String!,$epic:Int!){
  repository(owner:$owner,name:$name){
    issue(number:$epic){
      state
      projectItems(first:5){
        nodes{ status: fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}} }
      }
      subIssues(first:100){
        nodes{
          number state
          repository{ nameWithOwner }
          projectItems(first:5){
            nodes{ status: fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}} }
          }
        }
      }
    }
  }
}
"""

_PR_MAP_QUERY = """
query($owner:String!,$name:String!){
  repository(owner:$owner,name:$name){
    pullRequests(first:100, orderBy:{field:UPDATED_AT, direction:DESC}){
      nodes{ number state headRefName closingIssuesReferences(first:100){nodes{number}} }
    }
  }
}
"""


def _project_status(project_items):
    for node in (project_items or {}).get("nodes") or []:
        value = node.get("status")
        if value and value.get("name"):
            return value["name"]
    return None


def _fetch_pr_map(repo):
    owner, name = repo.split("/")
    data = gh.graphql(_PR_MAP_QUERY, owner=owner, name=name)
    prs = data["repository"]["pullRequests"]["nodes"]
    mapping = {}
    for pr in prs:
        for closes in (pr.get("closingIssuesReferences") or {}).get("nodes") or []:
            mapping.setdefault(closes["number"], pr)
    return mapping


def _fetch_pr_maps(repos):
    """PR maps keyed by repo — one `_fetch_pr_map` call per DISTINCT child
    repo, mirroring `schedule.py`. A child homed in a repo other than the
    epic's own would otherwise never show a PR here either, re-entering the
    wave forever from `status`'s point of view too."""
    return {r: _fetch_pr_map(r) for r in sorted(set(repos))}


def _fetch(repo, epic):
    owner, name = repo.split("/")
    data = gh.graphql(_ISSUE_QUERY, owner=owner, name=name, epic=epic)
    issue = data["repository"]["issue"]
    epic_dict = {"state": issue["state"], "status": _project_status(issue.get("projectItems"))}

    children = []
    for node in issue["subIssues"]["nodes"]:
        # Children may live in a different repo than the epic. Fall back to
        # the epic's own repo when the query omits it (older fixtures/tests)
        # so single-repo epics are unaffected.
        child_repo = ((node.get("repository") or {}).get("nameWithOwner")) or repo
        children.append({
            "number": node["number"],
            "state": node["state"],
            "repo": child_repo,
            "status": _project_status(node.get("projectItems")),
        })

    pr_maps = _fetch_pr_maps(c["repo"] for c in children)
    for child in children:
        pr = (pr_maps.get(child["repo"]) or {}).get(child["number"])
        if pr:
            child["pr"] = {"state": pr["state"], "number": pr["number"]}
            child["branch"] = pr.get("headRefName")
        else:
            child["pr"] = None
            child["branch"] = None

    return children, epic_dict


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report epic status, drift, and sweep plan.")
    parser.add_argument("--epic", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sweep-plan", action="store_true",
                         help="include what --sweep would change (read-only; never mutates)")
    args = parser.parse_args(argv)

    try:
        children, epic = _fetch(args.repo, args.epic)
    except gh.GhError as err:
        message = err.stderr or str(err)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    result = {
        "complete": epic_complete(children),
        "drift": drift(children, epic),
        "sweep_plan": sweep_plan(children) if args.sweep_plan else [],
        "watches": watch_report(children, _load_cursors(children), _now()),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
