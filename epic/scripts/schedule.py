"""Wave selection and merge-queue ordering. No I/O."""

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _priority_rank(child):
    return _PRIORITY_RANK.get(child.get("priority"), 99)


def runnable(children, max_parallel, in_flight):
    """Children eligible to START driving now, in drive order."""
    closed = {c["number"] for c in children if c["state"] == "CLOSED"}
    eligible = [
        c
        for c in children
        if c["state"] == "OPEN"
        and not c["parked"]
        and c.get("pr") is None
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
    if not gates or any(s not in _CLEAN_STATES for s in gates.values()):
        return None
    stamps = [t for t in (pr.get("gate_cleared_at") or {}).values() if t]
    return max(stamps) if stamps else None


def merge_queue(children):
    """Merge-ready children in FIFO readiness order; ties break on position."""
    ready = []
    for child in children:
        stamp = became_ready_at(child)
        if stamp is not None:
            ready.append((stamp, child["position"], child["number"]))
    ready.sort()
    return [number for _, _, number in ready]
