"""Wave selection and merge-queue ordering. `runnable()`/`merge_queue()`/
`halt_reason()` do no I/O; `main()` is the thin impure shell."""

import argparse
import hashlib
import json
import re
import sys

import gh
import mergeability

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _priority_rank(child):
    return _PRIORITY_RANK.get(child.get("priority"), 99)


def runnable(children, max_parallel, in_flight):
    """Children eligible to START driving now, in drive order.

    Excludes a child already being driven (Status "In Progress", no PR yet)
    from its own eligible set — the caller counts exactly that set as
    `in_flight`. Keeping the two sets disjoint is what stops an in-flight
    child from both consuming a capacity slot AND re-appearing in the wave,
    which would starve an eligible sibling despite free capacity.
    """
    closed = {c["number"] for c in children if c["state"] == "CLOSED"}
    eligible = [
        c
        for c in children
        if c["state"] == "OPEN"
        and not c["parked"]
        and c.get("pr") is None
        and c.get("status") != "In Progress"
        and all(b in closed for b in c["blocked_by"])
    ]
    eligible.sort(key=lambda c: (c["position"], _priority_rank(c), c["number"]))
    capacity = max(0, max_parallel - in_flight)
    return [c["number"] for c in eligible[:capacity]]


_CLEAN_STATES = {"clean", "na"}


def became_ready_at(child):
    """Latest gate-clearing timestamp, or None if any gate is not clean."""
    pr = child.get("pr")
    if not pr or pr.get("state") != "OPEN":
        return None
    gates = pr.get("gates") or {}
    if any(s not in _CLEAN_STATES for s in gates.values()):
        return None
    stamps = [t for t in (pr.get("gate_cleared_at") or {}).values() if t]
    return max(stamps) if stamps else pr.get("opened_at")


def merge_queue(children):
    """Merge-ready children in FIFO readiness order; ties break on position."""
    ready = []
    for child in children:
        stamp = became_ready_at(child)
        if stamp is not None:
            ready.append((stamp, child["position"], child["number"]))
    ready.sort()
    return [number for _, _, number in ready]


def park_signature(gate, reason):
    """Stable 12-char digest of (gate, normalized reason)."""
    normalized = re.sub(r"\s+", " ", (reason or "").strip().lower())
    payload = f"{gate}|{normalized}".encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def halt_reason(children, parks, threshold=3):
    """Why the run must halt, or None to keep going."""
    counts = {}
    for park in parks or []:
        if park.get("waiting_on_human"):
            continue
        sig = park_signature(park.get("gate"), park.get("reason"))
        counts[sig] = counts.get(sig, 0) + 1
    for sig in sorted(counts):
        if counts[sig] >= threshold:
            return f"systemic:{sig}"

    if all(c["state"] == "CLOSED" for c in children or []):
        return None
    if runnable(children, max_parallel=len(children) or 1, in_flight=0):
        return None
    # Live work that runnable() deliberately excludes from its own eligible
    # set is NOT a halt signal — two forms: a child with an open PR (past
    # the point runnable() will start it), and a child that is In Progress
    # with no PR yet (already being driven, between dispatch and PR-open).
    # Both are children runnable() will never re-offer, so without this
    # escape a perfectly healthy epic mid-dispatch reads as "no runnable
    # work" / "transitive-block" and the run halts on top of live work.
    if any(
        c["state"] == "OPEN"
        and (
            (c.get("pr") or {}).get("state") == "OPEN"
            or c.get("status") == "In Progress"
        )
        for c in children
    ):
        return None

    blocked_open = [
        c for c in children if c["state"] == "OPEN" and not c["parked"]
    ]
    return "transitive-block" if blocked_open else "no-runnable-work"


_CHILDREN_QUERY = """
query($owner:String!,$name:String!,$epic:Int!){
  repository(owner:$owner,name:$name){
    issue(number:$epic){
      subIssues(first:100){
        nodes{
          number state
          repository{ nameWithOwner }
          blockedBy(first:20){nodes{number}}
          projectItems(first:5){
            nodes{
              status: fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}
              priority: fieldValueByName(name:"Priority"){... on ProjectV2ItemFieldSingleSelectValue{name}}
            }
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

_PARK_TRAILER_RE = re.compile(r"epic-park:\s*(\{.*\})")
_FAILED_RE = re.compile(r"FAILED:\s*(.+)")


def _project_field(nodes, key):
    for node in nodes or []:
        value = node.get(key)
        if value and value.get("name"):
            return value["name"]
    return None


def _fetch_children(repo, epic):
    owner, name = repo.split("/")
    data = gh.graphql(_CHILDREN_QUERY, owner=owner, name=name, epic=epic)
    nodes = data["repository"]["issue"]["subIssues"]["nodes"]
    children = []
    for position, node in enumerate(nodes):
        items = (node.get("projectItems") or {}).get("nodes")
        status = _project_field(items, "status")
        priority = _project_field(items, "priority")
        # Children may live in a different repo than the epic (SKILL.md's
        # "Checkout resolution (cross-repo children)"). Fall back to the
        # epic's own repo when the query omits it (older fixtures/tests, or
        # a schema that hasn't backfilled it) so single-repo epics are
        # unaffected.
        child_repo = ((node.get("repository") or {}).get("nameWithOwner")) or repo
        children.append({
            "number": node["number"],
            "state": node["state"],
            "repo": child_repo,
            "position": position,
            "priority": priority,
            "blocked_by": [
                b["number"] for b in (node.get("blockedBy") or {}).get("nodes") or []
            ],
            "parked": status == "Parked",
            "status": status,
            "pr": None,
        })
    return children


def _fetch_pr_map(repo):
    """Most-recently-updated PR that closes each child issue number."""
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
    repo, so a cross-repo epic's children each see PRs opened in their own
    repo instead of only the epic's home repo (a child homed elsewhere would
    otherwise never show a PR and re-enter the wave forever)."""
    return {r: _fetch_pr_map(r) for r in sorted(set(repos))}


_REQUIRED_CHECK_FAIL_PREFIXES = ("check-failing:", "check-missing:", "blocked-unexplained:")


def _latest(stamps):
    values = [s for s in stamps if s]
    return max(values) if values else None


def _compute_gates(pr_detail, ruleset, threads, reviews):
    """Real prose-gate readiness, derived from the same signals
    `mergeability.py` uses for its merge/no-merge requirement set — reused
    directly via `mergeability.requirements()` rather than re-deriving the
    check/review/thread logic here.

    Folded to four named gates that map onto the requirement codes a drive
    subagent actually resolves BEFORE merge-queue entry:
      - "checks": required status checks (CI, and `claude-review` when it is
        one of them — see D3/SKILL.md).
      - "threads": unresolved review threads.
      - "review": formal review decision (CHANGES_REQUESTED / REVIEW_REQUIRED),
        aggregating every reviewer (human or bot — CodeRabbit, Copilot,
        Claude Review all submit formal reviews that fold into GitHub's own
        `reviewDecision`).
      - "draft": PR still marked draft.
    Structural, merge-phase-only concerns (`behind-base`, `conflict`) are
    deliberately excluded — those are resolved once, at merge-queue head-of-
    line admission, not while a child is still being driven in parallel.

    Returns (gates, gate_cleared_at). A gate absent from `gate_cleared_at`
    contributes no timestamp — `became_ready_at()` only needs ONE real stamp
    among all clean gates to skip the `opened_at` fallback.
    """
    reqs = mergeability.requirements(pr_detail, ruleset, threads)
    codes = {r["code"] for r in reqs}

    def has(prefix):
        return any(c.startswith(prefix) for c in codes)

    gates, cleared = {}, {}

    if has("check-pending:"):
        gates["checks"] = "pending"
    elif any(has(p) for p in _REQUIRED_CHECK_FAIL_PREFIXES):
        gates["checks"] = "red"
    else:
        gates["checks"] = "clean"
        stamp = _latest(c.get("completedAt") for c in pr_detail.get("statusCheckRollup") or [])
        if stamp:
            cleared["checks"] = stamp

    gates["threads"] = "pending" if has("thread-unresolved:") else "clean"

    if "changes-requested" in codes:
        gates["review"] = "red"
    elif "approval-missing" in codes:
        gates["review"] = "pending"
    else:
        gates["review"] = "clean"
        stamp = _latest(
            r.get("submittedAt") for r in reviews or [] if r.get("state") != "COMMENTED"
        )
        if stamp:
            cleared["review"] = stamp

    gates["draft"] = "red" if pr_detail.get("isDraft") else "clean"

    return gates, cleared


def _populate_prs(children, pr_maps):
    """Attach each child's PR, including its real gate readiness (`gates` +
    `gate_cleared_at`, computed via `mergeability.requirements()` on the
    child's OWN repo) and `opened_at` from a dedicated `gh pr view --json
    createdAt` call — the FIFO merge-queue fallback for gate-free/absent-gate
    children depends on this field being present.

    Gate/ruleset/thread lookups only run for an OPEN pr — `became_ready_at()`
    never considers a non-OPEN PR, so fetching them for closed/merged PRs
    would be pure waste.

    A failed lookup is NOT swallowed into a silently-None opened_at (that
    would re-strand the child from the merge queue, the exact bug this
    field exists to fix) — it propagates to main()'s GhError handler
    instead, which exits 2 with the standard error shape.
    """
    for child in children:
        repo = child["repo"]
        pr = (pr_maps.get(repo) or {}).get(child["number"])
        if not pr:
            continue
        extra = gh.run_json(
            ["pr", "view", str(pr["number"]), "--repo", repo, "--json", "createdAt,reviews"]
        )
        gates, cleared = {}, {}
        if pr["state"] == "OPEN":
            pr_detail, ruleset, threads = mergeability._fetch(repo, pr["number"])
            gates, cleared = _compute_gates(pr_detail, ruleset, threads, extra.get("reviews"))
        child["pr"] = {
            "state": pr["state"],
            "gates": gates,
            "gate_cleared_at": cleared,
            "opened_at": extra.get("createdAt"),
        }


def _fetch_parks(children):
    """Machine-readable `epic-park:` trailers on every parked child's issue,
    read from the CHILD's own repo (cross-repo children may be parked in a
    repo other than the epic's home repo)."""
    parks = []
    for child in children:
        if not child.get("parked"):
            continue
        data = gh.run_json(["issue", "view", str(child["number"]), "--repo", child["repo"],
                             "--json", "comments"])
        for comment in reversed(data.get("comments") or []):
            body = comment.get("body") or ""
            trailer_match = _PARK_TRAILER_RE.search(body)
            if not trailer_match:
                continue
            try:
                info = json.loads(trailer_match.group(1))
            except json.JSONDecodeError:
                info = {}
            reason_match = _FAILED_RE.search(body)
            parks.append({
                "gate": info.get("gate"),
                "reason": reason_match.group(1).strip() if reason_match else "",
                "waiting_on_human": bool(info.get("waiting_on_human")),
            })
            break
    return parks


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compute the runnable wave, the FIFO merge queue, and any halt reason."
    )
    parser.add_argument("--epic", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--max-parallel", type=int, default=3)
    args = parser.parse_args(argv)

    try:
        children = _fetch_children(args.repo, args.epic)
        pr_maps = _fetch_pr_maps(c["repo"] for c in children)
        _populate_prs(children, pr_maps)
        parks = _fetch_parks(children)
    except gh.GhError as err:
        message = err.stderr or str(err)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    in_flight = sum(
        1 for c in children
        if c["state"] == "OPEN" and not c["parked"] and c["pr"] is None
        and c.get("status") == "In Progress"
    )
    wave = runnable(children, args.max_parallel, in_flight)
    queue = merge_queue(children)
    halt = halt_reason(children, parks)
    print(json.dumps({"wave": wave, "merge_queue": queue, "halt": halt}))
    return 1 if halt else 0


if __name__ == "__main__":
    raise SystemExit(main())
