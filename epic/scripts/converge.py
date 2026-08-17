"""Finding fingerprints and convergence verdicts. The pure functions below do
no I/O; `main()` reads two finding-array files and is the thin impure shell
(no `gh` calls — findings arrive as files, never inline JSON on argv)."""
import argparse
import hashlib
import json
import re
import sys

_CODE_SPAN = re.compile(r"`[^`]*`")


def _normalize(claim):
    stripped = _CODE_SPAN.sub(" ", claim or "")
    return re.sub(r"\s+", " ", stripped.strip().lower())


def fingerprint(finding):
    """Stable id for a finding. Anchor is excluded: a moved finding is the same."""
    payload = "|".join(
        [finding.get("file", ""), finding.get("category", ""),
         _normalize(finding.get("claim"))]
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def blocking_set(findings):
    return {fingerprint(f) for f in findings or [] if f.get("blocking")}


def compare(prev, curr):
    """Verdict for one round pair."""
    previous, current = blocking_set(prev), blocking_set(curr)
    if not current:
        return "converged"
    if current == previous:
        return "no_progress"
    return "progress"


def is_stall(verdicts, stall_rounds=2):
    """True once the tail holds `stall_rounds` consecutive no_progress verdicts."""
    tail = list(verdicts or [])[-stall_rounds:]
    return len(tail) == stall_rounds and all(v == "no_progress" for v in tail)


def _load_findings(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare two rounds of findings and report the convergence verdict."
    )
    parser.add_argument("--prev", required=True, help="path to the previous round's findings JSON")
    parser.add_argument("--curr", required=True, help="path to the current round's findings JSON")
    args = parser.parse_args(argv)

    try:
        prev = _load_findings(args.prev)
        curr = _load_findings(args.curr)
    except (OSError, json.JSONDecodeError) as exc:
        message = str(exc)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    verdict = compare(prev, curr)
    print(json.dumps({"verdict": verdict}))
    return 1 if verdict == "no_progress" else 0


if __name__ == "__main__":
    raise SystemExit(main())
