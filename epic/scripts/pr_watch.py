"""One invocation is one tick: report PR activity, or how long to wait for
the next tick. Nothing here ever sleeps."""
import argparse
import datetime
import hashlib
import json
import random
import re
import time

import gh
import watch_state

WATCH_FLOOR_S = 15
WATCH_MULT = 1.8
WATCH_CEIL_S = 900
_JITTER = 0.2
_MAX_ERRORS = 8

_RETRY_AFTER_RE = re.compile(r"retry[- ]after:\s*(\d{1,10})", re.I)
_RATELIMIT_RESET_RE = re.compile(r"x-ratelimit-reset:\s*(\d{1,10})", re.I)


def backoff_delay(step, jitter=0.0):
    """Seconds until the next tick.

    `jitter` is supplied by the caller (in [-1.0, 1.0]) rather than drawn
    here, so this stays pure and deterministic under test. main() passes
    random.uniform(-1, 1); the resulting spread desynchronises the parallel
    wave members that the old fixed staircase kept in lockstep.
    """
    step = max(0, step)
    try:
        base = WATCH_FLOOR_S * (WATCH_MULT ** step)
    except OverflowError:
        base = WATCH_CEIL_S
    base = min(base, WATCH_CEIL_S)
    return max(1, round(base * (1 + _JITTER * jitter)))


def error_backoff(errors, stderr, now_epoch=None, jitter=0.0):
    """Seconds to wait after a failed `gh` call.

    GitHub's own guidance wins when it gives any: an explicit `Retry-After`,
    then an `x-ratelimit-reset` epoch. Otherwise fall back to the same
    exponential ladder as a quiet tick.

    `jitter` is supplied by the caller (in [-1.0, 1.0]). For the ladder,
    it is passed through symmetrically. For GitHub-directed waits (Retry-After
    and x-ratelimit-reset), only positive jitter is applied to avoid retrying
    earlier than GitHub requested — map [-1, 1] to [0, +_JITTER] using max(0, jitter).
    """
    text = stderr or ""
    match = _RETRY_AFTER_RE.search(text)
    if match:
        base = min(int(match.group(1)), WATCH_CEIL_S)
        # Positive-only jitter: apply max(0, jitter) to spread herd above GitHub's floor
        return max(1, round(base * (1 + _JITTER * max(0, jitter))))
    match = _RATELIMIT_RESET_RE.search(text)
    if match and now_epoch is not None:
        base = min(int(match.group(1)) - int(now_epoch), WATCH_CEIL_S)
        if base >= 1:
            # Positive-only jitter: apply max(0, jitter) to spread herd above GitHub's floor
            return max(1, round(base * (1 + _JITTER * max(0, jitter))))
        return 1
    return backoff_delay(errors, jitter)


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
        "comments": _digest(
            (c.get("id"), c.get("body")) for c in pr.get("comments") or []
        ),
    }


def changed_facets(prev, curr):
    """Facet names that moved, in FACETS order. Arming is never activity."""
    if prev is None:
        return []
    return [f for f in FACETS if prev.get(f) != curr.get(f)]


def _digest(rows):
    """Order-independent digest of a set of tuples."""
    ordered = sorted(rows, key=lambda row: [str(v) for v in row])
    payload = json.dumps(ordered, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _login(author):
    return author.get("login") if isinstance(author, dict) else author


_PR_FIELDS = "headRefOid,state,statusCheckRollup,reviews,comments"

_THREADS_QUERY = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}
"""


def _now():
    """ISO-8601 UTC stamp. A monkeypatch point for the tests."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fetch(repo, pr_number):
    owner, name = repo.split("/")
    pr = gh.run_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", _PR_FIELDS]
    )
    data = gh.graphql(_THREADS_QUERY, owner=owner, name=name, pr=pr_number)
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return pr, threads


def _emit(payload, code):
    print(json.dumps(payload))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="One PR-watch tick: report activity, or the delay until the next tick."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--state-dir", dest="state_dir",
                        help="cursor directory (default: $EPIC_WATCH_DIR or ~/.cache/epic/watch)")
    parser.add_argument("--stop", action="store_true",
                        help="end this watch and delete its cursor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset-backoff", dest="reset", action="store_true",
                       help="force the backoff back to the floor (use after a push)")
    # --resume-backoff is deliberately inert: `args.resume` is never read.
    # Resuming from the stored step is already the default behaviour when
    # neither flag is passed; this flag exists only so a caller can state
    # that intent explicitly, for readability at the call site.
    group.add_argument("--resume-backoff", dest="resume", action="store_true",
                       help="continue from the stored step (the default; asserts intent, no-op)")
    args = parser.parse_args(argv)

    if args.stop:
        removed = watch_state.clear(args.repo, args.pr, args.state_dir)
        return _emit({"event": "stopped", "cursor_removed": removed}, 0)

    cursor = watch_state.load(args.repo, args.pr, args.state_dir)
    if args.reset:
        cursor["step"] = 0

    try:
        pr, threads = _fetch(args.repo, args.pr)
    except gh.GhError as err:
        detail = err.stderr or str(err)
        cursor["errors"] += 1
        watch_state.save(args.repo, args.pr, cursor, args.state_dir)
        if cursor["errors"] >= _MAX_ERRORS:
            return _emit({"event": "error", "detail": detail,
                          "consecutive": cursor["errors"]}, 2)
        return _emit({"event": "waiting",
                      "next_tick_in_s": error_backoff(cursor["errors"], detail,
                                                      int(time.time()),
                                                      random.uniform(-1, 1)),
                      "reason": "gh-error",
                      "consecutive_errors": cursor["errors"]}, 1)

    cursor["errors"] = 0

    state = pr.get("state")
    if state and state != "OPEN":
        watch_state.clear(args.repo, args.pr, args.state_dir)
        return _emit({"event": "pr-closed", "state": state,
                      "head": pr.get("headRefOid")}, 0)

    current = fingerprint(pr, threads)
    changed = changed_facets(cursor.get("fingerprint"), current)
    now = _now()

    if changed:
        # Only a real head change earns the floor again — comment noise on a
        # busy PR must not pin the watch at the fast tier.
        if "head" in changed:
            cursor["step"] = 0
        cursor.update(fingerprint=current, last_activity_at=now, last_changed=changed)
        watch_state.save(args.repo, args.pr, cursor, args.state_dir)
        return _emit({"event": "activity", "changed": changed,
                      "head": current["head"]}, 0)

    arming = cursor.get("fingerprint") is None
    if arming:
        cursor.update(fingerprint=current, last_activity_at=now)
    elif not args.reset:
        # --reset-backoff holds this tick at the floor it just set; every
        # other quiet tick widens the gap by one step.
        cursor["step"] += 1

    delay = backoff_delay(cursor["step"], random.uniform(-1, 1))
    watch_state.save(args.repo, args.pr, cursor, args.state_dir)
    payload = {"event": "waiting", "next_tick_in_s": delay,
               "quiet_s": watch_state.elapsed_s(cursor["last_activity_at"], now)}
    if arming:
        payload["armed"] = True
    return _emit(payload, 1)


if __name__ == "__main__":
    raise SystemExit(main())
