"""The HARD worktree constraints, as violation codes. No I/O."""
import re

from config import ConfigError, validate_prefix


def check(prefix, child, worktrees, max_concurrent, inside_worktree):
    """Return sorted violation codes; empty means preflight passes."""
    violations = []
    try:
        validate_prefix(prefix)
    except ConfigError:
        return ["prefix-invalid"]

    owned = [w for w in worktrees if re.fullmatch(re.escape(prefix) + r"-\d+", w)]
    if f"{prefix}-{child}" in owned:
        violations.append("worktree-exists")
    if len(owned) >= max_concurrent:
        violations.append("concurrency-cap")
    if inside_worktree:
        violations.append("nested-worktree")
    return sorted(violations)
