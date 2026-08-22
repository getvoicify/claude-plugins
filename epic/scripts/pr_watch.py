"""Responsive PR/workflow monitoring. Pure core; only main() sleeps."""
import argparse
import hashlib
import json
import time

import gh

_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_SETTLED_STATES = {"SUCCESS", "FAILURE", "NONE", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


def snapshot(pr, threads):
    """Current state of everything worth waiting on, keyed on head SHA."""
    snap = {"head": pr.get("headRefOid")}

    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        snap["checks"] = "NONE"
    else:
        # Mixed check types: CheckRun (status/conclusion) and StatusContext (state/context)
        has_pending = False
        has_failure = False
        for c in rollup:
            # StatusContext has 'state'; CheckRun has 'status'
            if "state" in c:
                # Legacy StatusContext
                state = c.get("state")
                if state in {"PENDING", "EXPECTED"}:
                    has_pending = True
                elif state in {"FAILURE", "ERROR"}:
                    has_failure = True
                elif state == "SUCCESS":
                    # SUCCESS in StatusContext counts as passing (like NEUTRAL, SKIPPED in CheckRun)
                    pass  # Don't set has_pending or has_failure
                else:
                    # Unrecognized state (including None): treat as pending (fail-closed)
                    has_pending = True
            else:
                # Modern CheckRun
                if c.get("status") != "COMPLETED":
                    has_pending = True
                elif c.get("conclusion") not in _PASSING:
                    has_failure = True

        if has_pending:
            snap["checks"] = "PENDING"
        elif has_failure:
            snap["checks"] = "FAILURE"
        else:
            snap["checks"] = "SUCCESS"

    for review in sorted(
        pr.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""
    ):
        # Skip COMMENTED reviews; they don't override verdicts
        if review.get("state") == "COMMENTED":
            continue

        # Author can be a dict with "login" or a plain string
        author = review.get("author")
        if isinstance(author, dict):
            login = author.get("login")
        else:
            login = author

        if login:
            snap[login] = review["state"]

    snap["threads_unresolved"] = sum(
        1 for t in threads or [] if not t.get("isResolved")
    )
    return snap


FACETS = ("head", "checks", "reviews", "threads", "comments")


def fingerprint(pr, threads):
    """Per-facet digests of everything worth waking on.

    Deliberately records EVERY review, COMMENTED included: CodeRabbit and
    Copilot post COMMENTED reviews with inline threads and never a formal
    verdict, so dropping them is what made the old watcher blind to the
    reviews it was most often waiting for.
    """
    return {
        "head": pr.get("headRefOid"),
        "checks": _digest(
            (c.get("name") or c.get("context"),
             c.get("status") or c.get("state"),
             c.get("conclusion"))
            for c in pr.get("statusCheckRollup") or []
        ),
        "reviews": _digest(
            (_login(r.get("author")), r.get("state"), r.get("submittedAt"))
            for r in pr.get("reviews") or []
        ),
        "threads": _digest(
            (t.get("id"), bool(t.get("isResolved"))) for t in threads or []
        ),
        "comments": len(pr.get("comments") or []),
    }


def changed_facets(prev, curr):
    """Facet names that moved, in FACETS order. Arming is never activity."""
    if not prev:
        return []
    return [f for f in FACETS if prev.get(f) != curr.get(f)]


def _digest(rows):
    """Order-independent digest of a set of tuples."""
    ordered = sorted(rows, key=lambda row: [str(v) for v in row])
    payload = json.dumps(ordered, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _login(author):
    return author.get("login") if isinstance(author, dict) else author


def diff_event(prev, curr, awaited):
    """First meaningful change, or None. A head change always wins."""
    if prev.get("head") != curr.get("head"):
        return {"event": "head-changed", "state": curr.get("head"),
                "head": curr.get("head")}
    for key in awaited:
        if prev.get(key) != curr.get(key):
            return {"event": key, "state": curr.get(key), "head": curr.get("head")}
    return None


def settled_event(curr, awaited):
    """First awaited key already in a terminal state, or None.

    Returns event with "initial": True if found.
    """
    for key in awaited:
        val = curr.get(key)
        if key == "threads_unresolved":
            # Only 0 unresolved threads is settled
            if val == 0:
                return {"event": key, "state": val, "head": curr.get("head"),
                        "initial": True}
        elif val in _SETTLED_STATES:
            return {"event": key, "state": val, "head": curr.get("head"),
                    "initial": True}
    return None


def backoff(elapsed):
    """Poll fast early, then widen. Seconds."""
    if elapsed < 60:
        return 15
    if elapsed < 300:
        return 30
    return 60


_PR_FIELDS = "headRefOid,statusCheckRollup,reviews,mergeStateStatus"

_THREADS_QUERY = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}
"""


def _fetch(repo, pr_number):
    owner, name = repo.split("/")
    pr = gh.run_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", _PR_FIELDS]
    )
    data = gh.graphql(_THREADS_QUERY, owner=owner, name=name, pr=pr_number)
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return pr, threads


def main(argv=None):
    parser = argparse.ArgumentParser(description="Block until a PR state changes.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--await", dest="awaited", required=True,
                        help="comma-separated snapshot keys")
    parser.add_argument("--deadline", type=int, default=3600)
    args = parser.parse_args(argv)

    awaited = [k.strip() for k in args.awaited.split(",") if k.strip()]
    started = time.monotonic()
    previous = snapshot(*_fetch(args.repo, args.pr))

    # Check if any awaited gate is already settled (final state)
    event = settled_event(previous, awaited)
    if event:
        event["waited_s"] = 0
        print(json.dumps(event))
        return 0

    consecutive_errors = 0
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= args.deadline:
            print(json.dumps({"event": "deadline", "waited_s": round(elapsed),
                              "awaited": awaited, "snapshot": previous}))
            return 1
        time.sleep(backoff(elapsed))
        try:
            current = snapshot(*_fetch(args.repo, args.pr))
            consecutive_errors = 0  # Reset error counter on success
        except gh.GhError as err:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                print(json.dumps({"event": "error", "detail": err.stderr or str(err),
                                  "consecutive": consecutive_errors}))
                return 1
            # Continue polling after transient error
            continue

        event = diff_event(previous, current, awaited)
        if event:
            event["waited_s"] = round(time.monotonic() - started)
            print(json.dumps(event))
            return 0
        previous = current


if __name__ == "__main__":
    raise SystemExit(main())
