"""The HARD worktree constraints, as violation codes. No I/O."""
from config import ConfigError, validate_prefix


def check(prefix, child, worktrees, max_concurrent, inside_worktree):
    """Return sorted violation codes; empty means preflight passes."""
    violations = []
    try:
        validate_prefix(prefix)
    except ConfigError:
        return ["prefix-invalid"]

    owned = [w for w in worktrees if w.startswith(f"{prefix}-")]
    if f"{prefix}-{child}" in owned:
        violations.append("worktree-exists")
    if len(owned) >= max_concurrent:
        violations.append("concurrency-cap")
    if inside_worktree:
        violations.append("nested-worktree")
    return sorted(violations)
