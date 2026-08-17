"""Two-layer epic config resolution. No I/O."""
import re

try:
    import yaml
except ImportError:  # pragma: no cover - install path
    raise

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
