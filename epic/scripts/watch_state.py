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
    cursor.update({k: data[k] for k in DEFAULT_CURSOR if k in data})
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
    """Whole seconds between two ISO-8601 stamps; 0 when `then` is unset."""
    if not then_iso:
        return 0
    return int((_parse(now_iso) - _parse(then_iso)).total_seconds())


def _default():
    cursor = dict(DEFAULT_CURSOR)
    cursor["last_changed"] = list(DEFAULT_CURSOR["last_changed"])
    return cursor


def _parse(stamp):
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
