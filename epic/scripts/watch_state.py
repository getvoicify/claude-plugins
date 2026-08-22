"""Watch-cursor persistence — the only filesystem state the PR watch keeps.

The cursor is a pure optimisation, never authoritative: it holds the last
fingerprint, the backoff step, the consecutive-error count, and when/what
last moved. A missing or unreadable cursor is not an error — it means
"start fresh", which costs one wasted fast-tier poll and nothing else.
That is what keeps the watch compatible with the skill's HARD
stateless-recovery invariant.
"""
import datetime
import json
import os
import pathlib

DEFAULT_CURSOR = {
    "fingerprint": None,
    "step": 0,
    "errors": 0,
    "last_activity_at": None,
    "last_changed": [],
}


def state_dir(override=None):
    """Directory holding cursors: explicit override, then env, then cache."""
    if override:
        return pathlib.Path(override)
    env = os.environ.get("EPIC_WATCH_DIR")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".cache" / "epic" / "watch"


def cursor_path(repo, pr, override=None):
    owner, name = repo.split("/")
    return state_dir(override) / f"{owner}__{name}__{pr}.json"


def load(repo, pr, override=None):
    """The stored cursor, or a fresh default for any unreadable file."""
    try:
        data = json.loads(cursor_path(repo, pr, override).read_text())
    except (OSError, ValueError):
        return _default()
    if not isinstance(data, dict):
        return _default()
    cursor = _default()
    for k in DEFAULT_CURSOR:
        if k not in data:
            continue
        if not _is_valid_type(k, data[k]):
            continue
        cursor[k] = data[k]
    return cursor


def save(repo, pr, cursor, override=None):
    """Write the cursor atomically so a killed tick cannot corrupt it."""
    path = cursor_path(repo, pr, override)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursor))
    tmp.replace(path)


def clear(repo, pr, override=None):
    """Remove the cursor. True if one existed."""
    try:
        cursor_path(repo, pr, override).unlink()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def elapsed_s(then_iso, now_iso):
    """Whole seconds between two ISO-8601 stamps.

    0 when `then` is unset, when either stamp is unparseable (malformed
    string, naive/aware mismatch, wrong type), or when the delta comes out
    negative (a `then` in the future, or a clock that moved backwards).
    Never raises — a corrupt cursor can never take down a live run, the
    same invariant this module's own docstring states for a missing or
    unreadable cursor file.
    """
    if not then_iso:
        return 0
    try:
        delta = (_parse(now_iso) - _parse(then_iso)).total_seconds()
    except (TypeError, ValueError, AttributeError):
        return 0
    return max(0, int(delta))


def _is_valid_type(key, value):
    """Check if a stored value has the correct type for its cursor field."""
    if key in ("fingerprint", "last_activity_at"):
        # These fields are nullable; None or their expected type is valid
        if value is None:
            return True
        if key == "fingerprint":
            return isinstance(value, dict)
        if key == "last_activity_at":
            return isinstance(value, str)
    elif key == "step" or key == "errors":
        # Integers, but reject bool (which is an int subclass in Python)
        return isinstance(value, int) and not isinstance(value, bool)
    elif key == "last_changed":
        return isinstance(value, list)
    return False


def _default():
    cursor = dict(DEFAULT_CURSOR)
    cursor["last_changed"] = list(DEFAULT_CURSOR["last_changed"])
    return cursor


def _parse(stamp):
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
