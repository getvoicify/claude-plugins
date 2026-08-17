"""Responsive PR/workflow monitoring. Pure core; only main() sleeps."""
import argparse
import json
import time

import gh

_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def snapshot(pr, threads):
    """Current state of everything worth waiting on, keyed on head SHA."""
    snap = {"head": pr.get("headRefOid")}

    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        snap["checks"] = "NONE"
    elif any(c.get("status") != "COMPLETED" for c in rollup):
        snap["checks"] = "PENDING"
    elif all(c.get("conclusion") in _PASSING for c in rollup):
        snap["checks"] = "SUCCESS"
    else:
        snap["checks"] = "FAILURE"

    for review in sorted(
        pr.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""
    ):
        snap[review["author"]] = review["state"]

    snap["threads_unresolved"] = sum(
        1 for t in threads or [] if not t.get("isResolved")
    )
    return snap


def diff_event(prev, curr, awaited):
    """First meaningful change, or None. A head change always wins."""
    if prev.get("head") != curr.get("head"):
        return {"event": "head-changed", "state": curr.get("head"),
                "head": curr.get("head")}
    for key in awaited:
        if prev.get(key) != curr.get(key):
            return {"event": key, "state": curr.get(key), "head": curr.get("head")}
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

    while True:
        elapsed = time.monotonic() - started
        if elapsed >= args.deadline:
            print(json.dumps({"event": "deadline", "waited_s": round(elapsed),
                              "awaited": awaited}))
            return 1
        time.sleep(backoff(elapsed))
        current = snapshot(*_fetch(args.repo, args.pr))
        event = diff_event(previous, current, awaited)
        if event:
            event["waited_s"] = round(time.monotonic() - started)
            print(json.dumps(event))
            return 0
        previous = current


if __name__ == "__main__":
    raise SystemExit(main())
