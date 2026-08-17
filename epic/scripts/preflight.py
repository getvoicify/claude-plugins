"""The HARD worktree constraints, as violation codes. The `check()` function
itself does no I/O; `main()` is the thin impure shell that gathers git facts
via `gh.run_git`/`gh.run_json` — all shelling out stays confined to gh.py."""
import argparse
import json
import os
import re
import sys

import gh
from config import ConfigError, validate_prefix


def check(prefix, child, worktrees, max_concurrent, inside_worktree, invalid_start=False):
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
    if invalid_start:
        violations.append("invalid-start")
    return sorted(violations)


def _parse_worktree_list(porcelain_text):
    """Parse `git worktree list --porcelain` output into entry dicts."""
    entries = []
    entry = None
    for line in (porcelain_text or "").splitlines():
        if line.startswith("worktree "):
            if entry is not None:
                entries.append(entry)
            entry = {"path": line[len("worktree "):], "branch": None, "detached": False}
        elif entry is None:
            continue
        elif line.startswith("branch "):
            entry["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line.strip() == "detached":
            entry["detached"] = True
    if entry is not None:
        entries.append(entry)
    return entries


def _fetch(prefix):
    """Git facts a preflight check needs: sibling worktree names, whether cwd
    is itself inside a linked worktree, and whether cwd's branch state is an
    invalid start (detached HEAD or the repo's default branch)."""
    porcelain = gh.run_git(["worktree", "list", "--porcelain"])
    entries = _parse_worktree_list(porcelain)
    names = [os.path.basename(e["path"].rstrip("/")) for e in entries]

    cwd = os.path.realpath(os.getcwd())
    main_entry = entries[0] if entries else None
    current = None
    for e in entries:
        p = os.path.realpath(e["path"])
        if cwd == p or cwd.startswith(p + os.sep):
            current = e
            break
    inside_worktree = bool(current and main_entry and current is not main_entry)

    current_branch = (current or {}).get("branch")
    detached = (current or {}).get("detached", False)
    default_branch = None
    try:
        info = gh.run_json(["repo", "view", "--json", "defaultBranchRef"])
        default_branch = (info.get("defaultBranchRef") or {}).get("name")
    except gh.GhError:
        default_branch = None
    invalid_start = bool(detached or (current_branch and current_branch == default_branch))

    return names, inside_worktree, invalid_start


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the HARD worktree constraints.")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--child", type=int, required=True)
    parser.add_argument("--max-concurrent", type=int, required=True)
    args = parser.parse_args(argv)

    try:
        names, inside_worktree, invalid_start = _fetch(args.prefix)
    except gh.GhError as err:
        message = err.stderr or str(err)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    violations = check(
        args.prefix, args.child, names, args.max_concurrent, inside_worktree, invalid_start
    )
    print(json.dumps({"violations": violations}))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
