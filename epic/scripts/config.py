"""Two-layer epic config resolution. `main()` is the thin impure shell; the
parse/resolve functions above it stay pure."""
import argparse
import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - install path
    raise

import gh

_BLOCK = re.compile(r"```epic-config\s*\n(.*?)```", re.DOTALL)
_TASK_LIST = re.compile(r"^\s*-\s*\[[ xX]\]\s*#\d+", re.MULTILINE)
_PREFIX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ConfigError(Exception):
    pass


def parse_epic_config(body):
    """Strictly parse the fenced epic-config block from an epic issue body."""
    if _TASK_LIST.search(body or ""):
        raise ConfigError("legacy epic — run `/epic:migrate <epic#>` first.")
    match = _BLOCK.search(body or "")
    if not match:
        raise ConfigError("no epic-config block found in epic issue body")
    try:
        cfg = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ConfigError(f"malformed epic-config YAML: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ConfigError("epic-config block is not a mapping")
    return cfg


def resolve_project(epic_cfg, planning):
    """D4 order: epic-config.project -> planning.project -> error."""
    for source in (epic_cfg or {}, planning or {}):
        value = source.get("project")
        if value is not None:
            return int(value)
    raise ConfigError(
        "no project number: set epic-config.project or planning.project"
    )


def validate_prefix(prefix):
    if not _PREFIX.match(prefix or ""):
        raise ConfigError("invalid worktree_prefix (must be kebab-case)")


def resolve_gates(names, catalog):
    """Resolve epic-level gate names against ONE repo's catalog."""
    applicable, skipped = [], []
    for name in names or []:
        entry = (catalog or {}).get(name)
        if entry is None:
            skipped.append(name)
        else:
            applicable.append({"name": name, **entry})
    return applicable, skipped


_LAYER2_PATHS = (
    pathlib.Path(".agents/epic.yaml"),
    pathlib.Path(".claude/epic.yaml"),
)


def _load_layer2_yaml():
    """Load the checkout's per-repo epic.yaml: `.agents/` first, `.claude/` fallback."""
    for path in _LAYER2_PATHS:
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ConfigError(f"malformed epic.yaml at {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError(f"{path} is not a mapping")
            return data
    raise ConfigError(
        "no epic.yaml found (checked .agents/epic.yaml, .claude/epic.yaml)"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve the two-layer epic config.")
    parser.add_argument("--epic", type=int, required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)

    try:
        issue = gh.run_json(
            ["issue", "view", str(args.epic), "--repo", args.repo, "--json", "body"]
        )
        epic_cfg = parse_epic_config(issue.get("body"))
        if epic_cfg.get("epic") != args.epic:
            raise ConfigError(
                f"epic-config `epic: {epic_cfg.get('epic')!r}` does not match "
                f"invoked epic #{args.epic}"
            )
        validate_prefix(epic_cfg.get("worktree_prefix") or "")

        layer2 = _load_layer2_yaml()
        planning = layer2.get("planning") or {}
        project = resolve_project(epic_cfg, planning)
        catalog = layer2.get("gates") or {}
        applicable, skipped = resolve_gates(epic_cfg.get("gates"), catalog)
    except ConfigError as exc:
        message = str(exc)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2
    except gh.GhError as exc:
        message = exc.stderr or str(exc)
        print(json.dumps({"error": message}))
        sys.stderr.write(message + "\n")
        return 2

    result = {
        "epic": args.epic,
        "repo": args.repo,
        "project": project,
        "worktree_prefix": epic_cfg["worktree_prefix"],
        "spec": epic_cfg.get("spec"),
        "runbook": epic_cfg.get("runbook"),
        "docs_repo": epic_cfg.get("docs_repo"),
        "max_parallel": epic_cfg.get("max_parallel", 3),
        "gates": {"applicable": applicable, "skipped": sorted(skipped)},
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
