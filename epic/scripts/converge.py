"""Finding fingerprints and convergence verdicts. No I/O."""
import hashlib
import re

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
