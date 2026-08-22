# Parallel Epic Execution + Deterministic Driver Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the epic driver run N children in parallel behind a FIFO merge queue, and replace every fixed round budget and ad-hoc sleep with deterministic, unit-tested computation.

**Architecture:** Eight pure-logic modules plus one I/O boundary land in `epic/scripts/`, mirroring the existing `scripts/release/` split (`version.py` is pure, `release.py` shells out). Every module is a pure function of already-fetched `gh` JSON, so all logic is fixture-testable and byte-identical across runs. `epic/skills/epic/SKILL.md` is then rewritten to *call* those modules instead of restating their algorithms in prose.

**Tech Stack:** Python 3.x (stdlib only at runtime, plus `pyyaml`), `pytest` 9.1.1, `gh` CLI, GitHub GraphQL API.

**Spec:** [`docs/parallel-epic-design.md`](parallel-epic-design.md)

## Global Constraints

- **Pure/impure split is mandatory.** Only `epic/scripts/gh.py` may call `subprocess`. Every other module takes parsed data and returns data. A module that imports `subprocess` is a defect.
- **Determinism.** Identical inputs must produce byte-identical output. No `time.time()`, `random`, `set` iteration order, or dict ordering assumptions in any emitted value. Sort every collection before emitting.
- **Runtime deps:** Python 3 stdlib + `pyyaml` only. No new third-party runtime dependency. `pyyaml` is installed on `ImportError` with `pip install --break-system-packages --quiet pyyaml`.
- **Timing:** `time.monotonic()` only. No `time.sleep()` in pure logic; no shell `sleep` anywhere.
- **Tests:** `pytest tests/ -q` must pass. CI runs it on `python-version: "3.x"` (`.github/workflows/test.yml:25`).
- **Test imports:** bare module names (`from schedule import merge_queue`), enabled by the `sys.path` insert in `tests/conftest.py`.
- **Commits:** Conventional Commits — `scripts/release/version.py` parses them for the release bump. `feat:` = minor, `fix:`/`perf:` = patch. Anything else does not release.
- **No AI attribution** in any commit message or PR body. No `Co-Authored-By: Claude`, no "Generated with" footer.
- **Retired tunable names** must not survive anywhere under `epic/`: `PLAN_REVIEW_ROUNDS`, `PRE_PR_REVIEW_ROUNDS`, `CLAUDE_REVIEW_FIX_ROUNDS`, `CODERABBIT_FIX_ROUNDS`, `COPILOT_FIX_ROUNDS`, `CI_FIX_ROUNDS`, `CI_ESTIMATE`, `MAX_WAIT_CYCLES`, `MERGE_WAIT_CYCLES`, `CONSECUTIVE_PARK_HALT`, `GLOBAL_PARK_THRESHOLD`.
- **Skill prose must stay lint-clean** — `tests/test_skills_lint.py` enforces guarded capability tokens, the config-lookup-order sentence, resolvable reference links, and the D5 conditional-gate sentence.

---

## File Structure

**New — `epic/scripts/` (the deterministic core)**

| File | Responsibility |
|---|---|
| `gh.py` | The *only* module that shells out. Wraps `gh` invocation + JSON parsing. |
| `config.py` | Layer-1 `epic-config` parse, Layer-2 `epic.yaml` merge, project resolution, per-repo gate resolution. |
| `preflight.py` | The five HARD worktree constraints as violation codes. |
| `schedule.py` | Runnable wave, FIFO merge queue, park signatures, halt decision. |
| `mergeability.py` | Complete unmet-merge-requirement set from ruleset + PR state. |
| `converge.py` | Finding fingerprints; `progress`/`no_progress`/`converged`; stall detection. |
| `verify_pin.py` | Pin claim parsing and `verified`/`stale`/`unverifiable` classification. |
| `pr_watch.py` | Snapshot diffing + backoff schedule; CLI blocks until first awaited change. |
| `status.py` | Drift report, sweep plan, epic-completion predicate. |

**New — tests**

- `tests/fixtures/gh/*.json` — recorded `gh` responses.
- `tests/test_epic_scripts.py` — unit tests for all eight logic modules.

**Modified**

- `tests/conftest.py` — add `epic/scripts` to `sys.path`.
- `epic/skills/epic/SKILL.md` — the rewrite (Tasks 11–14).
- `epic/skills/epic/references/github-graphql.md` — queries the scripts issue.
- `epic/README.md` — `--serial`, `max_parallel`, runtime dependency.
- `tests/test_skills_lint.py` — retired-tunable and script-existence ratchets.

---

### Task 1: Test scaffolding and the `gh` I/O boundary

Establishes the import path and the single impure module everything else avoids.

**Files:**
- Create: `epic/scripts/gh.py`
- Modify: `tests/conftest.py:1-6`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `gh.run_json(args: list[str], cwd: str | None = None) -> dict | list` — runs `gh` and parses stdout as JSON. `gh.graphql(query: str, **variables) -> dict` — runs `gh api graphql` and returns the `data` payload. `gh.GhError` — raised on non-zero exit, carrying `.returncode` and `.stderr`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_epic_scripts.py
import json
import subprocess

import pytest

import gh


def test_run_json_parses_stdout(monkeypatch):
    def fake_check_output(args, text, cwd):
        assert args[0] == "gh"
        return json.dumps({"number": 101})

    monkeypatch.setattr(gh.subprocess, "check_output", fake_check_output)
    assert gh.run_json(["pr", "view", "101"]) == {"number": 101}


def test_run_json_raises_gherror_on_failure(monkeypatch):
    def fake_check_output(args, text, cwd):
        raise subprocess.CalledProcessError(1, args, stderr="not found")

    monkeypatch.setattr(gh.subprocess, "check_output", fake_check_output)
    with pytest.raises(gh.GhError) as excinfo:
        gh.run_json(["pr", "view", "999"])
    assert excinfo.value.returncode == 1


def test_graphql_unwraps_data(monkeypatch):
    monkeypatch.setattr(
        gh, "run_json", lambda args, cwd=None: {"data": {"repository": {"id": "R_1"}}}
    )
    assert gh.graphql("query {}") == {"repository": {"id": "R_1"}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'gh'`

- [ ] **Step 3: Add the import path**

```python
# tests/conftest.py
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts" / "release"))
sys.path.insert(0, str(_ROOT / "epic" / "scripts"))
```

- [ ] **Step 4: Write minimal implementation**

```python
# epic/scripts/gh.py
"""The only module in epic/scripts that performs I/O."""
import json
import subprocess


class GhError(RuntimeError):
    def __init__(self, returncode, stderr):
        super().__init__(stderr)
        self.returncode = returncode
        self.stderr = stderr


def run_json(args, cwd=None):
    """Run `gh <args>` and parse stdout as JSON."""
    try:
        out = subprocess.check_output(["gh", *args], text=True, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        raise GhError(exc.returncode, exc.stderr) from exc
    return json.loads(out)


def graphql(query, **variables):
    """Run a GraphQL query and return the unwrapped `data` payload."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in sorted(variables.items()):
        args += ["-F", f"{key}={value}"]
    return run_json(args)["data"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add epic/scripts/gh.py tests/conftest.py tests/test_epic_scripts.py
git commit -m "feat(epic): add gh I/O boundary and epic/scripts test path"
```

---

### Task 2: `config.py` — two-layer config resolution

**Files:**
- Create: `epic/scripts/config.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `parse_epic_config(body: str) -> dict` — strict YAML from the fenced ` ```epic-config ` block. Raises `ConfigError` if absent or malformed.
  - `resolve_project(epic_cfg: dict, planning: dict) -> int` — D4 order: `epic_cfg["project"]` → `planning["project"]` → raise `ConfigError`.
  - `resolve_gates(names: list[str], catalog: dict) -> tuple[list[dict], list[str]]` — returns `(applicable, skipped_names)`.
  - `validate_prefix(prefix: str) -> None` — raises `ConfigError` unless kebab-case.
  - `ConfigError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
from config import (
    ConfigError,
    parse_epic_config,
    resolve_gates,
    resolve_project,
    validate_prefix,
)

BODY = """Some prose.

```epic-config
epic: 42
repo: acme/planning
project: 7
docs_repo: acme/app
worktree_prefix: dark-mode
spec: docs/dark-mode.md
runbook: docs/dark-mode-runbook.md
```

More prose.
"""


def test_parse_epic_config_extracts_block():
    cfg = parse_epic_config(BODY)
    assert cfg["epic"] == 42
    assert cfg["worktree_prefix"] == "dark-mode"


def test_parse_epic_config_missing_block_raises():
    with pytest.raises(ConfigError):
        parse_epic_config("no config here")


def test_parse_epic_config_rejects_task_list_epic():
    with pytest.raises(ConfigError, match="legacy epic"):
        parse_epic_config("- [ ] #12\n- [ ] #13\n")


@pytest.mark.parametrize(
    "epic_cfg, planning, expected",
    [
        ({"project": 7}, {"project": 9}, 7),
        ({}, {"project": 9}, 9),
    ],
)
def test_resolve_project_order(epic_cfg, planning, expected):
    assert resolve_project(epic_cfg, planning) == expected


def test_resolve_project_missing_everywhere_raises():
    with pytest.raises(ConfigError):
        resolve_project({}, {})


@pytest.mark.parametrize("prefix", ["dark-mode", "epic", "a1-b2-c3"])
def test_validate_prefix_accepts_kebab(prefix):
    validate_prefix(prefix)


@pytest.mark.parametrize("prefix", ["Dark-Mode", "dark_mode", "-dark", "dark-", ""])
def test_validate_prefix_rejects_non_kebab(prefix):
    with pytest.raises(ConfigError, match="kebab-case"):
        validate_prefix(prefix)


def test_resolve_gates_skips_names_absent_from_this_catalog():
    catalog = {"screenshot": {"hook": "pre-review"}}
    applicable, skipped = resolve_gates(["screenshot", "migration"], catalog)
    assert [g["name"] for g in applicable] == ["screenshot"]
    assert skipped == ["migration"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/config.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/config.py tests/test_epic_scripts.py
git commit -m "feat(epic): add deterministic two-layer config resolution"
```

---

### Task 3: `preflight.py` — HARD worktree constraints

**Files:**
- Create: `epic/scripts/preflight.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: `config.validate_prefix`.
- Produces: `preflight.check(prefix: str, child: int, worktrees: list[str], max_concurrent: int, inside_worktree: bool) -> list[str]` — returns violation codes, empty list means pass. Codes: `prefix-invalid`, `worktree-exists`, `concurrency-cap`, `nested-worktree`.

- [ ] **Step 1: Write the failing test**

```python
from preflight import check


def test_check_passes_when_clean():
    assert check("dark-mode", 12, [], 3, False) == []


def test_check_flags_existing_worktree_for_same_child():
    assert check("dark-mode", 12, ["dark-mode-12"], 3, False) == ["worktree-exists"]


def test_check_flags_concurrency_cap():
    trees = ["dark-mode-1", "dark-mode-2", "dark-mode-3"]
    assert check("dark-mode", 12, trees, 3, False) == ["concurrency-cap"]


def test_check_allows_siblings_below_cap():
    assert check("dark-mode", 12, ["dark-mode-1", "dark-mode-2"], 3, False) == []


def test_check_flags_nesting():
    assert check("dark-mode", 12, [], 3, True) == ["nested-worktree"]


def test_check_flags_invalid_prefix():
    assert check("Dark_Mode", 12, [], 3, False) == ["prefix-invalid"]


def test_check_returns_all_violations_sorted():
    assert check("dark-mode", 1, ["dark-mode-1"], 1, True) == [
        "concurrency-cap",
        "nested-worktree",
        "worktree-exists",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'preflight'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/preflight.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/preflight.py tests/test_epic_scripts.py
git commit -m "feat(epic): add worktree preflight constraint checks"
```

---

### Task 4: `schedule.py` — runnable wave

**Files:**
- Create: `epic/scripts/schedule.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `schedule.runnable(children: list[dict], max_parallel: int, in_flight: int) -> list[int]`.

The normalized child dict used by every function in this module:

```python
{
    "number": 3,
    "state": "OPEN",            # OPEN | CLOSED
    "position": 0,              # sub-issue order
    "priority": "P1",           # P0 | P1 | P2 | None
    "blocked_by": [1, 2],
    "parked": False,
    "pr": None,                 # or the PR dict from Task 5
}
```

- [ ] **Step 1: Write the failing test**

```python
from schedule import runnable


def child(number, position, **kw):
    base = {
        "number": number,
        "state": "OPEN",
        "position": position,
        "priority": None,
        "blocked_by": [],
        "parked": False,
        "pr": None,
    }
    base.update(kw)
    return base


def test_runnable_orders_by_position():
    kids = [child(5, 1), child(3, 0)]
    assert runnable(kids, 3, 0) == [3, 5]


def test_runnable_priority_breaks_position_ties():
    kids = [child(5, 0, priority="P2"), child(3, 0, priority="P0")]
    assert runnable(kids, 3, 0) == [3, 5]


def test_runnable_excludes_blocked_children():
    kids = [child(3, 0), child(4, 1, blocked_by=[3])]
    assert runnable(kids, 3, 0) == [3]


def test_runnable_includes_child_whose_blocker_closed():
    kids = [child(3, 0, state="CLOSED"), child(4, 1, blocked_by=[3])]
    assert runnable(kids, 3, 0) == [4]


def test_runnable_excludes_parked_closed_and_pr_open():
    kids = [
        child(3, 0, parked=True),
        child(4, 1, state="CLOSED"),
        child(5, 2, pr={"number": 101, "state": "OPEN"}),
        child(6, 3),
    ]
    assert runnable(kids, 3, 0) == [6]


def test_runnable_respects_capacity():
    kids = [child(3, 0), child(4, 1), child(5, 2)]
    assert runnable(kids, 3, 2) == [3]
    assert runnable(kids, 3, 3) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'schedule'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/schedule.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/schedule.py tests/test_epic_scripts.py
git commit -m "feat(epic): add runnable-wave selection"
```

---

### Task 5: `schedule.py` — FIFO merge queue

**Files:**
- Modify: `epic/scripts/schedule.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: `schedule.runnable` (same module).
- Produces:
  - `schedule.became_ready_at(child: dict) -> str | None` — max ISO-8601 timestamp across cleared merge-gating gates; `None` if any gate is not clean.
  - `schedule.merge_queue(children: list[dict]) -> list[int]` — FIFO on `became_ready_at`, ties by `position`.

The PR dict shape:

```python
{
    "number": 101,
    "state": "OPEN",
    "gates": {"claude-review": "clean", "coderabbit": "clean", "copilot": "na"},
    "gate_cleared_at": {"claude-review": "2026-08-17T10:00:00Z",
                        "coderabbit": "2026-08-17T10:05:00Z"},
}
```

Gate states: `clean`, `na` (legitimately not applicable — `claude-review` absent from `required_checks`, or the Copilot 422 path), `pending`, `red`.

- [ ] **Step 1: Write the failing test**

```python
from schedule import became_ready_at, merge_queue


def pr(number, gates, cleared):
    return {"number": number, "state": "OPEN", "gates": gates,
            "gate_cleared_at": cleared}


def test_became_ready_at_is_latest_cleared_gate():
    child_ = child(3, 0, pr=pr(
        101,
        {"claude-review": "clean", "coderabbit": "clean"},
        {"claude-review": "2026-08-17T10:00:00Z",
         "coderabbit": "2026-08-17T10:05:00Z"},
    ))
    assert became_ready_at(child_) == "2026-08-17T10:05:00Z"


def test_became_ready_at_treats_na_as_clean():
    child_ = child(3, 0, pr=pr(
        101,
        {"claude-review": "na", "coderabbit": "clean"},
        {"coderabbit": "2026-08-17T10:05:00Z"},
    ))
    assert became_ready_at(child_) == "2026-08-17T10:05:00Z"


@pytest.mark.parametrize("state", ["pending", "red"])
def test_became_ready_at_is_none_when_any_gate_unclean(state):
    child_ = child(3, 0, pr=pr(
        101,
        {"claude-review": state, "coderabbit": "clean"},
        {"coderabbit": "2026-08-17T10:05:00Z"},
    ))
    assert became_ready_at(child_) is None


def test_became_ready_at_none_without_pr():
    assert became_ready_at(child(3, 0)) is None


def test_merge_queue_is_fifo_not_position_order():
    early = child(9, 5, pr=pr(109, {"ci": "clean"}, {"ci": "2026-08-17T09:00:00Z"}))
    late = child(2, 0, pr=pr(102, {"ci": "clean"}, {"ci": "2026-08-17T11:00:00Z"}))
    assert merge_queue([late, early]) == [9, 2]


def test_merge_queue_ties_break_on_position():
    same = "2026-08-17T09:00:00Z"
    a = child(9, 5, pr=pr(109, {"ci": "clean"}, {"ci": same}))
    b = child(2, 0, pr=pr(102, {"ci": "clean"}, {"ci": same}))
    assert merge_queue([a, b]) == [2, 9]


def test_merge_queue_excludes_unready_children():
    ready = child(2, 0, pr=pr(102, {"ci": "clean"}, {"ci": "2026-08-17T09:00:00Z"}))
    waiting = child(3, 1, pr=pr(103, {"ci": "pending"}, {}))
    assert merge_queue([ready, waiting]) == [2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ImportError: cannot import name 'became_ready_at'`

- [ ] **Step 3: Write minimal implementation**

Append to `epic/scripts/schedule.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/schedule.py tests/test_epic_scripts.py
git commit -m "feat(epic): add FIFO merge queue ordered on gate readiness"
```

---

### Task 6: `schedule.py` — park signatures and halt

**Files:**
- Modify: `epic/scripts/schedule.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: `schedule.runnable`.
- Produces:
  - `schedule.park_signature(gate: str, reason: str) -> str` — 12-char hex digest.
  - `schedule.halt_reason(children: list[dict], parks: list[dict], threshold: int = 3) -> str | None` — one of `systemic:<signature>`, `no-runnable-work`, `transitive-block`, or `None`.

A park dict: `{"child": 4, "code": "gate-stall", "gate": "claude-review", "reason": "...", "waiting_on_human": False}`.

- [ ] **Step 1: Write the failing test**

```python
from schedule import halt_reason, park_signature


def test_park_signature_is_stable_and_normalized():
    a = park_signature("claude-review", "Timed  out\nwaiting")
    b = park_signature("claude-review", "timed out waiting")
    assert a == b
    assert len(a) == 12


def test_park_signature_differs_by_gate():
    assert park_signature("ci", "boom") != park_signature("coderabbit", "boom")


def test_halt_on_three_matching_signatures():
    parks = [
        {"child": n, "gate": "ci", "reason": "runner offline",
         "waiting_on_human": False}
        for n in (3, 4, 5)
    ]
    kids = [child(6, 0)]
    assert halt_reason(kids, parks, 3).startswith("systemic:")


def test_no_halt_when_signatures_differ():
    parks = [
        {"child": 3, "gate": "ci", "reason": "a", "waiting_on_human": False},
        {"child": 4, "gate": "ci", "reason": "b", "waiting_on_human": False},
        {"child": 5, "gate": "ci", "reason": "c", "waiting_on_human": False},
    ]
    assert halt_reason([child(6, 0)], parks, 3) is None


def test_waiting_on_human_parks_never_trigger_systemic_halt():
    parks = [
        {"child": n, "gate": "approval-missing", "reason": "needs approval",
         "waiting_on_human": True}
        for n in (3, 4, 5)
    ]
    assert halt_reason([child(6, 0)], parks, 3) is None


def test_halt_when_nothing_runnable_and_epic_incomplete():
    kids = [child(3, 0, parked=True), child(4, 1, blocked_by=[3])]
    assert halt_reason(kids, [], 3) == "transitive-block"


def test_halt_no_runnable_work_without_blockers():
    kids = [child(3, 0, parked=True)]
    assert halt_reason(kids, [], 3) == "no-runnable-work"


def test_no_halt_while_work_remains():
    assert halt_reason([child(3, 0)], [], 3) is None


def test_no_halt_while_a_child_is_in_flight():
    # runnable() excludes children with an open PR, so an in-flight child must
    # be counted as work separately or the run halts on top of live work.
    kids = [child(3, 0, pr={"number": 101, "state": "OPEN"})]
    assert halt_reason(kids, [], 3) is None


def test_no_halt_when_epic_is_complete():
    assert halt_reason([child(3, 0, state="CLOSED")], [], 3) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ImportError: cannot import name 'halt_reason'`

- [ ] **Step 3: Write minimal implementation**

Append to `epic/scripts/schedule.py` (add `import hashlib` and `import re` at the top):

```python
def park_signature(gate, reason):
    """Stable 12-char digest of (gate, normalized reason)."""
    normalized = re.sub(r"\s+", " ", (reason or "").strip().lower())
    payload = f"{gate}|{normalized}".encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def halt_reason(children, parks, threshold=3):
    """Why the run must halt, or None to keep going."""
    counts = {}
    for park in parks or []:
        if park.get("waiting_on_human"):
            continue
        sig = park_signature(park.get("gate"), park.get("reason"))
        counts[sig] = counts.get(sig, 0) + 1
    for sig in sorted(counts):
        if counts[sig] >= threshold:
            return f"systemic:{sig}"

    if all(c["state"] == "CLOSED" for c in children or []):
        return None
    if runnable(children, max_parallel=len(children) or 1, in_flight=0):
        return None
    # A child with an open PR is live work that runnable() deliberately skips.
    if any(
        (c.get("pr") or {}).get("state") == "OPEN"
        for c in children
        if c["state"] == "OPEN"
    ):
        return None

    blocked_open = [
        c for c in children if c["state"] == "OPEN" and not c["parked"]
    ]
    return "transitive-block" if blocked_open else "no-runnable-work"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/schedule.py tests/test_epic_scripts.py
git commit -m "feat(epic): add park signatures and parallel-safe halt decision"
```

---

### Task 7: `mergeability.py` — the complete unmet-requirement set

Implements spec decision D12, including the HARD exit condition.

**Files:**
- Create: `epic/scripts/mergeability.py`
- Create: `tests/fixtures/gh/pr_behind.json`, `tests/fixtures/gh/pr_clean.json`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `mergeability.requirements(pr: dict, ruleset: dict, threads: list[dict]) -> list[dict]` — each `{"code": str, "detail": str, "action": str}`, sorted by `code`.
  - `mergeability.is_clean(reqs: list[dict]) -> bool`.

`pr` is the payload of `gh pr view --json mergeStateStatus,mergeable,reviewDecision,statusCheckRollup,isDraft`.

- [ ] **Step 1: Write the failing test**

```python
import json
import pathlib

from mergeability import is_clean, requirements

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "gh"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def codes(reqs):
    return [r["code"] for r in reqs]


def test_clean_pr_has_no_requirements():
    reqs = requirements(load("pr_clean.json"), {}, [])
    assert reqs == []
    assert is_clean(reqs) is True


def test_behind_base_is_reported_with_update_action():
    reqs = requirements(load("pr_behind.json"), {}, [])
    assert codes(reqs) == ["behind-base"]
    assert reqs[0]["action"] == "update-branch"
    assert is_clean(reqs) is False


def test_dirty_reports_conflict():
    pr = {"mergeStateStatus": "DIRTY", "statusCheckRollup": [], "isDraft": False}
    assert codes(requirements(pr, {}, [])) == ["conflict"]


def test_draft_is_reported():
    pr = {"mergeStateStatus": "BLOCKED", "statusCheckRollup": [], "isDraft": True}
    assert "draft" in codes(requirements(pr, {}, []))


def test_failing_and_pending_checks_are_named_individually():
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [
            {"name": "unit", "conclusion": "FAILURE", "status": "COMPLETED"},
            {"name": "lint", "conclusion": None, "status": "IN_PROGRESS"},
        ],
    }
    assert codes(requirements(pr, {}, [])) == [
        "check-failing:unit",
        "check-pending:lint",
    ]


def test_required_check_never_started_is_missing_not_pending():
    pr = {"mergeStateStatus": "BLOCKED", "isDraft": False, "statusCheckRollup": []}
    ruleset = {"required_status_checks": ["claude-review"]}
    assert codes(requirements(pr, ruleset, [])) == ["check-missing:claude-review"]


def test_unresolved_threads_are_reported_per_thread():
    threads = [
        {"id": "T_1", "isResolved": False, "isOutdated": False, "path": "a.py"},
        {"id": "T_2", "isResolved": True, "isOutdated": False, "path": "b.py"},
    ]
    pr = {"mergeStateStatus": "BLOCKED", "isDraft": False, "statusCheckRollup": []}
    assert codes(requirements(pr, {}, threads)) == ["thread-unresolved:T_1"]


def test_changes_requested_is_reported():
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [],
        "reviewDecision": "CHANGES_REQUESTED",
    }
    assert "changes-requested" in codes(requirements(pr, {}, []))


def test_missing_approval_is_reported_as_waiting_on_human():
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [],
        "reviewDecision": "REVIEW_REQUIRED",
    }
    reqs = requirements(pr, {"required_approving_review_count": 1}, [])
    approval = [r for r in reqs if r["code"] == "approval-missing"][0]
    assert approval["action"] == "park-waiting-on-human"
```

- [ ] **Step 2: Create the fixtures**

```json
// tests/fixtures/gh/pr_clean.json
{
  "mergeStateStatus": "CLEAN",
  "mergeable": "MERGEABLE",
  "reviewDecision": "APPROVED",
  "isDraft": false,
  "statusCheckRollup": [
    {"name": "unit", "conclusion": "SUCCESS", "status": "COMPLETED"}
  ]
}
```

```json
// tests/fixtures/gh/pr_behind.json
{
  "mergeStateStatus": "BEHIND",
  "mergeable": "MERGEABLE",
  "reviewDecision": "APPROVED",
  "isDraft": false,
  "statusCheckRollup": [
    {"name": "unit", "conclusion": "SUCCESS", "status": "COMPLETED"}
  ]
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mergeability'`

- [ ] **Step 4: Write minimal implementation**

```python
# epic/scripts/mergeability.py
"""Derive the complete unmet-merge-requirement set from GitHub state. No I/O."""

_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _req(code, detail, action):
    return {"code": code, "detail": detail, "action": action}


def requirements(pr, ruleset, threads):
    """Every reason GitHub will refuse to merge this PR, sorted by code."""
    reqs = []
    state = pr.get("mergeStateStatus")

    if pr.get("isDraft"):
        reqs.append(_req("draft", "PR is a draft", "mark-ready"))
    if state == "BEHIND":
        reqs.append(_req("behind-base", "branch is behind base", "update-branch"))
    if state == "DIRTY":
        reqs.append(_req("conflict", "merge conflict with base", "rebase-resolve"))

    seen = set()
    for check in pr.get("statusCheckRollup") or []:
        name = check.get("name")
        seen.add(name)
        if check.get("status") != "COMPLETED":
            reqs.append(
                _req(f"check-pending:{name}", "check in progress", "wait")
            )
        elif check.get("conclusion") not in _PASSING:
            reqs.append(
                _req(f"check-failing:{name}", "check failed", "ci-fix-loop")
            )

    for name in sorted((ruleset or {}).get("required_status_checks") or []):
        if name not in seen:
            reqs.append(
                _req(f"check-missing:{name}", "required check never started",
                     "diagnose")
            )

    for thread in threads or []:
        if not thread.get("isResolved"):
            reqs.append(
                _req(
                    f"thread-unresolved:{thread['id']}",
                    f"unresolved thread on {thread.get('path')}",
                    "resolve-thread",
                )
            )

    decision = pr.get("reviewDecision")
    if decision == "CHANGES_REQUESTED":
        reqs.append(
            _req("changes-requested", "a reviewer requested changes", "fix-loop")
        )
    elif decision == "REVIEW_REQUIRED" and (ruleset or {}).get(
        "required_approving_review_count"
    ):
        reqs.append(
            _req("approval-missing", "an approving review is required",
                 "park-waiting-on-human")
        )

    return sorted(reqs, key=lambda r: r["code"])


def is_clean(reqs):
    """The HARD exit condition: nothing left for GitHub to block on."""
    return not reqs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add epic/scripts/mergeability.py tests/fixtures/gh tests/test_epic_scripts.py
git commit -m "feat(epic): derive complete merge requirements from GitHub state"
```

---

### Task 8: `converge.py` — fingerprints and stall detection

Implements spec decisions D4 and D5.

**Files:**
- Create: `epic/scripts/converge.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `converge.fingerprint(finding: dict) -> str` — 12-char digest of `(file, category, normalized claim)`. **Excludes `anchor`.**
  - `converge.blocking_set(findings: list[dict]) -> set[str]`.
  - `converge.compare(prev: list[dict], curr: list[dict]) -> str` — `converged` | `progress` | `no_progress`.
  - `converge.is_stall(verdicts: list[str], stall_rounds: int = 2) -> bool`.

A finding: `{"file": str, "anchor": str, "category": str, "claim": str, "blocking": bool}`.

- [ ] **Step 1: Write the failing test**

```python
from converge import blocking_set, compare, fingerprint, is_stall


def finding(claim, blocking=True, file="a.py", category="bug", anchor="f()"):
    return {"file": file, "anchor": anchor, "category": category,
            "claim": claim, "blocking": blocking}


def test_fingerprint_ignores_anchor_movement():
    assert fingerprint(finding("x", anchor="f()")) == fingerprint(
        finding("x", anchor="g():42")
    )


def test_fingerprint_normalizes_whitespace_and_case():
    assert fingerprint(finding("Missing  Retry")) == fingerprint(
        finding("missing retry")
    )


def test_fingerprint_distinguishes_file_and_category():
    assert fingerprint(finding("x", file="a.py")) != fingerprint(
        finding("x", file="b.py")
    )
    assert fingerprint(finding("x", category="bug")) != fingerprint(
        finding("x", category="spec-gap")
    )


def test_blocking_set_excludes_residual_findings():
    findings = [finding("real"), finding("nit", blocking=False)]
    assert len(blocking_set(findings)) == 1


def test_compare_converged_when_no_blocking_remain():
    assert compare([finding("x")], [finding("nit", blocking=False)]) == "converged"


def test_compare_progress_when_findings_resolved():
    prev = [finding("x"), finding("y")]
    assert compare(prev, [finding("x")]) == "progress"


def test_compare_progress_when_new_evidence_appears():
    assert compare([finding("x")], [finding("x"), finding("z")]) == "progress"


def test_compare_no_progress_on_identical_blocking_set():
    assert compare([finding("x")], [finding("x")]) == "no_progress"


def test_is_stall_requires_two_consecutive_no_progress():
    assert is_stall(["no_progress"], 2) is False
    assert is_stall(["no_progress", "no_progress"], 2) is True
    assert is_stall(["no_progress", "progress", "no_progress"], 2) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'converge'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/converge.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/converge.py tests/test_epic_scripts.py
git commit -m "feat(epic): replace review round budgets with convergence detection"
```

---

### Task 9: `verify_pin.py` — mechanical pin-claim verification

Implements spec decision D6 — the strike at the faulty-pin root cause.

**Files:**
- Create: `epic/scripts/verify_pin.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `verify_pin.parse_claims(pin_text: str) -> list[dict]` — each `{"kind": "verified"|"assumption", "path": str|None, "ref": str|None, "symbol": str|None, "text": str}`.
  - `verify_pin.classify(claim: dict, source: str | None) -> str` — `verified` | `stale` | `unverifiable` | `assumption`. `source` is the file content at `ref`, or `None` when the path/ref did not resolve.

Claim syntax in the pin: `- verified: path@ref#symbol — free text` and `- assumption: free text`.

- [ ] **Step 1: Write the failing test**

```python
from verify_pin import classify, parse_claims

PIN = """
## Pin

- verified: src/auth/Auth.kt@origin/main#refresh — returns Result<Token>
- assumption: billing API accepts partial refunds
- some unrelated prose line
"""


def test_parse_claims_extracts_both_kinds():
    claims = parse_claims(PIN)
    assert [c["kind"] for c in claims] == ["verified", "assumption"]


def test_parse_claims_splits_path_ref_symbol():
    claim = parse_claims(PIN)[0]
    assert claim["path"] == "src/auth/Auth.kt"
    assert claim["ref"] == "origin/main"
    assert claim["symbol"] == "refresh"


def test_parse_claims_assumption_has_no_locator():
    claim = parse_claims(PIN)[1]
    assert claim["path"] is None
    assert "partial refunds" in claim["text"]


def test_classify_verified_when_symbol_present():
    claim = parse_claims(PIN)[0]
    assert classify(claim, "fun refresh(): Result<Token> {}") == "verified"


def test_classify_stale_when_symbol_absent():
    claim = parse_claims(PIN)[0]
    assert classify(claim, "fun renew(): Result<Token> {}") == "stale"


def test_classify_unverifiable_when_source_missing():
    claim = parse_claims(PIN)[0]
    assert classify(claim, None) == "unverifiable"


def test_classify_assumption_passes_through():
    claim = parse_claims(PIN)[1]
    assert classify(claim, None) == "assumption"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'verify_pin'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/verify_pin.py
"""Parse and mechanically re-check pin claims. No I/O."""
import re

_CLAIM = re.compile(
    r"^\s*-\s*(?P<kind>verified|assumption):\s*(?P<body>.+)$", re.MULTILINE
)
_LOCATOR = re.compile(r"^(?P<path>[^@\s]+)@(?P<ref>[^#\s]+)#(?P<symbol>\S+)")


def parse_claims(pin_text):
    """Every tagged claim in a pin, in document order."""
    claims = []
    for match in _CLAIM.finditer(pin_text or ""):
        body = match.group("body").strip()
        claim = {"kind": match.group("kind"), "path": None, "ref": None,
                 "symbol": None, "text": body}
        locator = _LOCATOR.match(body)
        if locator:
            claim.update(
                path=locator.group("path"),
                ref=locator.group("ref"),
                symbol=locator.group("symbol"),
            )
        claims.append(claim)
    return claims


def classify(claim, source):
    """verified | stale | unverifiable | assumption."""
    if claim["kind"] == "assumption":
        return "assumption"
    if not claim.get("symbol"):
        return "unverifiable"
    if source is None:
        return "unverifiable"
    return "verified" if claim["symbol"] in source else "stale"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/verify_pin.py tests/test_epic_scripts.py
git commit -m "feat(epic): add mechanical pin-claim verification"
```

---

### Task 10: `pr_watch.py` — responsive, per-OS-correct monitoring

Implements spec decision D7. The pure core is the snapshot diff and the backoff schedule; the CLI loop is the thin impure shell.

**Files:**
- Create: `epic/scripts/pr_watch.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: `gh.run_json` (CLI path only).
- Produces:
  - `pr_watch.snapshot(pr: dict, threads: list[dict]) -> dict` — `{gate_name: state}` plus `{"head": sha}`.
  - `pr_watch.diff_event(prev: dict, curr: dict, awaited: list[str]) -> dict | None` — the first awaited change, or `None`.
  - `pr_watch.backoff(elapsed: float) -> float` — poll interval in seconds.

- [ ] **Step 1: Write the failing test**

```python
from pr_watch import backoff, diff_event, snapshot


def test_snapshot_records_head_and_check_states():
    pr = {
        "headRefOid": "a1b2c3",
        "statusCheckRollup": [
            {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
        "reviews": [],
    }
    snap = snapshot(pr, [])
    assert snap["head"] == "a1b2c3"
    assert snap["checks"] == "SUCCESS"


def test_snapshot_checks_pending_when_any_incomplete():
    pr = {
        "headRefOid": "a1b2c3",
        "statusCheckRollup": [
            {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "lint", "status": "IN_PROGRESS", "conclusion": None},
        ],
        "reviews": [],
    }
    assert snapshot(pr, [])["checks"] == "PENDING"


def test_snapshot_records_latest_review_state_per_author():
    pr = {
        "headRefOid": "a1b2c3",
        "statusCheckRollup": [],
        "reviews": [
            {"author": "coderabbitai", "state": "COMMENTED",
             "submittedAt": "2026-08-17T09:00:00Z"},
            {"author": "coderabbitai", "state": "APPROVED",
             "submittedAt": "2026-08-17T10:00:00Z"},
        ],
    }
    assert snapshot(pr, [])["coderabbitai"] == "APPROVED"


def test_snapshot_counts_unresolved_threads():
    pr = {"headRefOid": "a1b2c3", "statusCheckRollup": [], "reviews": []}
    threads = [{"id": "T_1", "isResolved": False}, {"id": "T_2", "isResolved": True}]
    assert snapshot(pr, threads)["threads_unresolved"] == 1


def test_diff_event_returns_none_when_nothing_awaited_changed():
    prev = {"head": "a1", "checks": "PENDING", "coderabbitai": "COMMENTED"}
    curr = {"head": "a1", "checks": "PENDING", "coderabbitai": "APPROVED"}
    assert diff_event(prev, curr, ["checks"]) is None


def test_diff_event_reports_first_awaited_change():
    prev = {"head": "a1", "checks": "PENDING"}
    curr = {"head": "a1", "checks": "SUCCESS"}
    event = diff_event(prev, curr, ["checks"])
    assert event["event"] == "checks"
    assert event["state"] == "SUCCESS"
    assert event["head"] == "a1"


def test_diff_event_reports_head_change_even_when_not_awaited():
    prev = {"head": "a1", "checks": "PENDING"}
    curr = {"head": "b2", "checks": "PENDING"}
    assert diff_event(prev, curr, ["checks"])["event"] == "head-changed"


def test_backoff_starts_fast_and_widens_to_a_ceiling():
    assert backoff(0) == 15
    assert backoff(120) == 30
    assert backoff(600) == 60
    assert backoff(86400) == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pr_watch'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/pr_watch.py
"""Responsive PR/workflow monitoring. Pure core; only main() sleeps."""
import argparse
import json
import time

import gh

_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def snapshot(pr, threads):
    """Current state of everything worth waiting on, keyed on head SHA."""
    snap = {"head": pr.get("headRefOid")}

    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        snap["checks"] = "NONE"
    elif any(c.get("status") != "COMPLETED" for c in rollup):
        snap["checks"] = "PENDING"
    elif all(c.get("conclusion") in _PASSING for c in rollup):
        snap["checks"] = "SUCCESS"
    else:
        snap["checks"] = "FAILURE"

    for review in sorted(
        pr.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""
    ):
        snap[review["author"]] = review["state"]

    snap["threads_unresolved"] = sum(
        1 for t in threads or [] if not t.get("isResolved")
    )
    return snap


def diff_event(prev, curr, awaited):
    """First meaningful change, or None. A head change always wins."""
    if prev.get("head") != curr.get("head"):
        return {"event": "head-changed", "state": curr.get("head"),
                "head": curr.get("head")}
    for key in awaited:
        if prev.get(key) != curr.get(key):
            return {"event": key, "state": curr.get(key), "head": curr.get("head")}
    return None


def backoff(elapsed):
    """Poll fast early, then widen. Seconds."""
    if elapsed < 60:
        return 15
    if elapsed < 300:
        return 30
    return 60
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: all passed

> **Superseded.** The `--await`/`--deadline` CLI shell built in this step (and
> the blocking `main()` it produces) was replaced by the tick model in
> `docs/durable-pr-watch-design.md`. Kept for historical context only.

- [ ] **Step 5: Add the CLI shell**

Append to `epic/scripts/pr_watch.py`:

```python
_PR_FIELDS = "headRefOid,statusCheckRollup,reviews,mergeStateStatus"

_THREADS_QUERY = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}
"""


def _fetch(repo, pr_number):
    owner, name = repo.split("/")
    pr = gh.run_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", _PR_FIELDS]
    )
    data = gh.graphql(_THREADS_QUERY, owner=owner, name=name, pr=pr_number)
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return pr, threads


def main(argv=None):
    parser = argparse.ArgumentParser(description="Block until a PR state changes.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--await", dest="awaited", required=True,
                        help="comma-separated snapshot keys")
    parser.add_argument("--deadline", type=int, default=3600)
    args = parser.parse_args(argv)

    awaited = [k.strip() for k in args.awaited.split(",") if k.strip()]
    started = time.monotonic()
    previous = snapshot(*_fetch(args.repo, args.pr))

    while True:
        elapsed = time.monotonic() - started
        if elapsed >= args.deadline:
            print(json.dumps({"event": "deadline", "waited_s": round(elapsed),
                              "awaited": awaited}))
            return 1
        time.sleep(backoff(elapsed))
        current = snapshot(*_fetch(args.repo, args.pr))
        event = diff_event(previous, current, awaited)
        if event:
            event["waited_s"] = round(time.monotonic() - started)
            print(json.dumps(event))
            return 0
        previous = current


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/ -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add epic/scripts/pr_watch.py tests/test_epic_scripts.py
git commit -m "feat(epic): add responsive PR watcher with monotonic backoff"
```

---

### Task 11: `status.py` — drift, sweep plan, completion

**Files:**
- Create: `epic/scripts/status.py`
- Test: `tests/test_epic_scripts.py`

**Interfaces:**
- Consumes: the child dict from Task 4.
- Produces:
  - `status.epic_complete(children: list[dict]) -> bool` — every child `state == "CLOSED"`.
  - `status.drift(children: list[dict], epic: dict) -> list[dict]` — each `{"target": "child:3"|"epic", "field": "status", "actual": str, "expected": str}`.
  - `status.sweep_plan(children: list[dict]) -> list[dict]` — each `{"child": int, "action": "remove-worktree", "branch": str}`.

- [ ] **Step 1: Write the failing test**

```python
from status import drift, epic_complete, sweep_plan


def test_epic_complete_requires_every_child_closed():
    assert epic_complete([child(3, 0, state="CLOSED")]) is True
    assert epic_complete([child(3, 0, state="CLOSED"), child(4, 1)]) is False


def test_epic_complete_counts_closed_as_not_planned():
    kids = [child(3, 0, state="CLOSED"), child(4, 1, state="CLOSED")]
    assert epic_complete(kids) is True


def test_drift_flags_closed_child_not_marked_done():
    kid = child(3, 0, state="CLOSED")
    kid["status"] = "In Review"
    assert drift([kid], {"state": "OPEN", "status": "In Progress"}) == [
        {"target": "child:3", "field": "status", "actual": "In Review",
         "expected": "Done"}
    ]


def test_drift_flags_closed_complete_epic_not_done():
    kid = child(3, 0, state="CLOSED")
    kid["status"] = "Done"
    epic = {"state": "CLOSED", "status": "In Progress"}
    assert drift([kid], epic) == [
        {"target": "epic", "field": "status", "actual": "In Progress",
         "expected": "Done"}
    ]


def test_drift_does_not_touch_open_epic_status():
    kid = child(3, 0)
    kid["status"] = "In Progress"
    assert drift([kid], {"state": "OPEN", "status": "In Progress"}) == []


def test_sweep_plan_removes_worktrees_only_for_merged_prs():
    merged = child(3, 0, state="CLOSED",
                   pr={"number": 101, "state": "MERGED"}, branch="dark-mode-3")
    open_pr = child(4, 1, pr={"number": 102, "state": "OPEN"},
                    branch="dark-mode-4")
    assert sweep_plan([merged, open_pr]) == [
        {"child": 3, "action": "remove-worktree", "branch": "dark-mode-3"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_epic_scripts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'status'`

- [ ] **Step 3: Write minimal implementation**

```python
# epic/scripts/status.py
"""Drift detection, sweep planning and the completion predicate. No I/O."""


def epic_complete(children):
    """True when EVERY sub-issue is closed (never trust subIssuesSummary)."""
    return all(c["state"] == "CLOSED" for c in children or [])


def drift(children, epic):
    """Project-field values that disagree with reality. Reality wins."""
    out = []
    for child in sorted(children or [], key=lambda c: c["number"]):
        if child["state"] == "CLOSED" and child.get("status") != "Done":
            out.append(
                {"target": f"child:{child['number']}", "field": "status",
                 "actual": child.get("status"), "expected": "Done"}
            )
    if (
        epic
        and epic.get("state") == "CLOSED"
        and epic_complete(children)
        and epic.get("status") != "Done"
    ):
        out.append(
            {"target": "epic", "field": "status", "actual": epic.get("status"),
             "expected": "Done"}
        )
    return out


def sweep_plan(children):
    """Worktrees safe to remove: the child's PR is MERGED."""
    return [
        {"child": c["number"], "action": "remove-worktree", "branch": c["branch"]}
        for c in sorted(children or [], key=lambda c: c["number"])
        if (c.get("pr") or {}).get("state") == "MERGED" and c.get("branch")
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/status.py tests/test_epic_scripts.py
git commit -m "feat(epic): add drift detection and sweep planning"
```

---

### Task 12: Lint ratchets for the new surface

**Files:**
- Modify: `tests/test_skills_lint.py`
- Test: same file

**Interfaces:**
- Consumes: `epic/scripts/*.py` existing on disk.
- Produces: two new test functions guarding the rewrite in Tasks 13–16.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills_lint.py`:

```python
RETIRED_TUNABLES = [
    "PLAN_REVIEW_ROUNDS",
    "PRE_PR_REVIEW_ROUNDS",
    "CLAUDE_REVIEW_FIX_ROUNDS",
    "CODERABBIT_FIX_ROUNDS",
    "COPILOT_FIX_ROUNDS",
    "CI_FIX_ROUNDS",
    "CI_ESTIMATE",
    "MAX_WAIT_CYCLES",
    "MERGE_WAIT_CYCLES",
    "CONSECUTIVE_PARK_HALT",
    "GLOBAL_PARK_THRESHOLD",
]

EPIC_SCRIPTS = [
    "config.py",
    "converge.py",
    "gh.py",
    "mergeability.py",
    "pr_watch.py",
    "preflight.py",
    "schedule.py",
    "status.py",
    "verify_pin.py",
]


@pytest.mark.parametrize("tunable", RETIRED_TUNABLES)
def test_retired_tunables_absent_from_epic(tunable):
    hits = [
        path
        for path in (REPO_ROOT / "epic").rglob("*.md")
        if tunable in path.read_text()
    ]
    assert hits == [], f"{tunable} is retired but still appears in {hits}"


@pytest.mark.parametrize("script", EPIC_SCRIPTS)
def test_epic_script_exists(script):
    assert (REPO_ROOT / "epic" / "scripts" / script).is_file()


@pytest.mark.parametrize("script", EPIC_SCRIPTS)
def test_only_gh_module_shells_out(script):
    source = (REPO_ROOT / "epic" / "scripts" / script).read_text()
    if script == "gh.py":
        assert "subprocess" in source
    else:
        assert "subprocess" not in source, f"{script} must stay pure"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_skills_lint.py -q -k "retired_tunables or epic_script or shells_out"`
Expected: FAIL — `test_retired_tunables_absent_from_epic` fails for every tunable still in `SKILL.md`. `test_epic_script_exists` passes (Tasks 1–11 created them).

Note: if `REPO_ROOT` is not already defined in `tests/test_skills_lint.py`, reuse the existing module-level path constant rather than introducing a second one.

- [ ] **Step 3: Leave the failures red**

These ratchets are the acceptance criteria for Tasks 13–16 and must stay red until the prose rewrite lands. Do not weaken them.

- [ ] **Step 4: Commit**

```bash
git add tests/test_skills_lint.py
git commit -m "test(epic): add ratchets for retired tunables and script purity"
```

---

### Task 13: SKILL.md — frontmatter, arguments, and config/preflight call sites

**Files:**
- Modify: `epic/skills/epic/SKILL.md:1-4` (frontmatter), `:24-45` (Arguments), `:46-128` (config), `:129-141` (worktree constraints)

**Interfaces:**
- Consumes: `config.py`, `preflight.py`.
- Produces: prose contract for `run --serial` and `epic-config.max_parallel`.

- [ ] **Step 1: Run the lint to see the current state**

Run: `pytest tests/test_skills_lint.py -q`
Expected: the Task 12 ratchets fail; everything else passes.

- [ ] **Step 2: Update the frontmatter description**

```markdown
---
name: epic
description: Drive a GitHub epic (sub-issues + a project board) — one child per interactive invocation, N in parallel under `run`
---
```

- [ ] **Step 3: Add `--serial` to Arguments**

Replace the argument block and add the bullet:

```markdown
/epic <epic#> [status | next | run | <child#>] [--stop-at-pr] [--sweep] [--serial]
```

```markdown
- `--serial`: valid on `run` only. Drives one child at a time, as the pre-parallel
  driver did. On `status`/`next`/`<child#>` → STOP: "`--serial` applies to `run`
  only." Harnesses without subagent support take this path automatically.
```

- [ ] **Step 4: Replace the config-parsing prose with the script call**

Replace the Layer-1 parsing steps with a call, preserving the existing
config-lookup-order sentence verbatim (`tests/test_skills_lint.py::test_config_lookup_order_sentence_present` asserts it):

```markdown
2. Parse and resolve BOTH layers with `python epic/scripts/config.py --epic <epic#>
   --repo <owner/name>`. It emits one resolved-config JSON object and is the only
   supported parser — never regex-extract the block by hand. It enforces the
   `epic` / `<epic#>` match, the kebab-case `worktree_prefix`, the D4 project
   order (`epic-config.project` → `planning.project` → STOP), and per-child-repo
   gate resolution. A `ConfigError` on stdout is a STOP in every mode.
   `epic-config` also accepts optional `max_parallel` (int, default 3) — the
   global cap on children in flight across all involved repos.
```

- [ ] **Step 5: Replace the worktree constraints with the preflight call**

```markdown
## Worktree constraints (HARD — every drive, enforced in the child's repo checkout)

Run `python epic/scripts/preflight.py --prefix <worktree_prefix> --child <n>
--max-concurrent <worktrees.max_concurrent>` before any drive. An empty violation
list is the only pass. Any of `prefix-invalid`, `worktree-exists`,
`concurrency-cap`, `nested-worktree` → STOP, naming the code.

The constraints it enforces: one deterministic worktree per child at
`<worktrees.root>/${worktree_prefix}-<n>` on branch `${worktree_prefix}-<n>` from
latest `origin/main`, never reused for a different issue and never elsewhere;
never nested inside another worktree or started from `main`/detached HEAD;
per-issue uniqueness; and the per-repo concurrency cap. Auto-clean on merge ONLY
— never remove mid-flight, on failure, or at STOP.
```

- [ ] **Step 6: Verify the lint still passes for untouched rules**

Run: `pytest tests/test_skills_lint.py -q -k "not retired_tunables"`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add epic/skills/epic/SKILL.md
git commit -m "feat(epic): call config and preflight scripts from the driver skill"
```

---

### Task 14: SKILL.md — review loops become convergence, pins become verifiable

**Files:**
- Modify: `epic/skills/epic/SKILL.md:207-255` (steps 3–6 of the drive)

**Interfaces:**
- Consumes: `converge.py`, `verify_pin.py`.
- Produces: prose contract for the structured-finding JSON shape from Task 8 and the claim syntax from Task 9.

- [ ] **Step 1: Rewrite the step-1 pin section**

```markdown
3. **Step-1 pin (adversarial, iterative)**: dispatch a read-only adversarial
   reviewer subagent if your harness supports subagents (otherwise perform the
   review yourself inline, as a separate sequential pass) to attack the child's
   spec/runbook slice against current reality.

   Every load-bearing claim in the pin MUST carry a provenance tag:
   `- verified: <path>@<ref>#<symbol> — <claim>` or `- assumption: <claim>`.
   Run `python epic/scripts/verify_pin.py --pin <file>` to classify each one as
   `verified`, `stale`, `unverifiable` or `assumption`. A `stale` claim is a
   defect: amend the plan via the pin (merged docs are never edited) and re-run.
   An `unverifiable` load-bearing claim is never silently built upon —
   interactive: ASK; `run`: record it explicitly as an assumption in the pin
   comment AND the PR body. On a contract/API-defining or P0 child, an
   unverifiable load-bearing claim parks for human input instead.

   Reviewers return findings as JSON: `[{"file", "anchor", "category", "claim",
   "blocking"}]`. Feed consecutive rounds to `python epic/scripts/converge.py`,
   which returns `converged`, `progress` or `no_progress`. Loop while it returns
   `progress`. There is NO round budget. Two consecutive `no_progress` verdicts
   are a STALL — see the convergence contract below. Post the final pin —
   verified claims, every amendment, every assumption, AND any residual
   findings — as a child-issue comment before any code.
```

- [ ] **Step 2: Rewrite the pre-PR review section**

Replace the `PRE_PR_REVIEW_ROUNDS` sentence:

```markdown
6. **Pre-PR adversarial reviews**: run two read-only reviews framed as
   devil's-advocate critiques — as parallel subagents if supported, otherwise
   performed inline, sequentially (the reviews still happen, in-session) — a
   spec-compliance reviewer (does the diff FULLY satisfy the child's
   spec/runbook, no gaps?) and a quality reviewer (logic bugs, security, missing
   tests, repo-convention violations). Both return the structured-finding JSON
   above. Implementer fixes; re-run BOTH reviewers on the amended diff, feeding
   each round to `converge.py`. Loop while it returns `progress`; stop on
   `converged`. Residual (non-blocking) nits are recorded in the PR body, not
   loop fuel. There is NO round budget.
```

- [ ] **Step 3: Add the convergence contract as a new subsection**

Insert after step 6:

```markdown
### Convergence contract (replaces every review round budget)

A STALL is two consecutive `no_progress` verdicts from `converge.py`. A stall is
treated as a PIN FAULT until proven otherwise:

1. Re-run `verify_pin.py` over the pin; surface every `stale` claim.
2. Re-run the step-3 reviewer against the PLAN, not the diff, supplying the
   stalled findings as evidence.
3. Pin amends → post the amendment as a child comment, reset the convergence
   history, restart the loop. Bounded by `REPIN_ATTEMPTS` (1) — a second re-pin
   means the problem is not the pin.
4. Stall again after re-pin → interactive: ASK the operator with the surviving
   findings and the suspect claims (via `AskUserQuestion` if your harness
   supports structured questions; otherwise as numbered plain-text questions,
   waiting for the reply); `run`: park.

The only hard ceilings are resources: `CHILD_DRIVE_CEILING_S` (7200) from drive
start to merge-queue entry, and `PR_WATCH_DEADLINE_S` (3600) per wait. Time spent
waiting at the head of the merge queue is NOT charged to the child.
```

- [ ] **Step 4: Verify the retired-tunable ratchet advances**

Run: `pytest tests/test_skills_lint.py -q -k "retired_tunables"`
Expected: `PLAN_REVIEW_ROUNDS` and `PRE_PR_REVIEW_ROUNDS` now pass; the merge-phase tunables still fail (Task 15 removes them).

- [ ] **Step 5: Commit**

```bash
git add epic/skills/epic/SKILL.md
git commit -m "feat(epic): replace review round budgets with a convergence contract"
```

---

### Task 15: SKILL.md — merge phase becomes the FIFO queue plus mergeability

**Files:**
- Modify: `epic/skills/epic/SKILL.md:271-328` (merge phase)

**Interfaces:**
- Consumes: `mergeability.py`, `schedule.merge_queue`, `pr_watch.py`.

- [ ] **Step 1: Rewrite the merge phase**

```markdown
### 3. Merge phase (default) — or stop

**If `--stop-at-pr`:** report (child, worktree, branch, PR URL, gates, reviews —
including a **Claude review status** field = one of "pending" / "approved (green)"
/ "changes requested (red)" / "N/A (not required)" (the last when `claude-review`
is not in the repo's `merge.required_checks` — parallel to the Copilot
"N/A (not enabled)" state), CodeRabbit status, and a **Copilot status** field =
one of "not requested" / "requested, pending" / "clean" / "N/A (not enabled)")
and STOP. Worktree stays intact. Status stays In Review. No `--auto` was armed.

**Otherwise drive to merge, through the queue.** A child enters the merge phase
only at the head of the FIFO merge queue (`schedule.py`), and only one PR is ever
in the merge phase.

1. **Ask GitHub what is unmet** — `python epic/scripts/mergeability.py --repo
   <owner/name> --pr <n>` returns the COMPLETE requirement set from the live
   ruleset plus current PR state. This is the authority; the repo's `epic.yaml`
   `merge` block is a declaration of intent that the script cross-checks and
   reports drift against. Resolve by code:
   - `behind-base` → update the branch onto freshly fetched `origin/main`.
   - `conflict` → rebase and resolve. Budget `CONFLICT_ATTEMPTS`, which also
     bounds RECURRING `behind-base`: exceeding it means the base is moving faster
     than a CI cycle completes → park with exactly that diagnosis.
   - `check-failing:<name>` → diagnose from the run, dispatch the implementer if
     your harness supports subagents (otherwise make the fix inline), push.
   - `check-pending:<name>` → wait via `pr_watch.py`, never a fixed sleep.
   - `check-missing:<name>` → a required check never started; diagnose, do not
     wait forever.
   - `thread-unresolved:<id>` → drive to a terminal state: addressed (fix, push,
     then CALL `resolveReviewThread` — changing the code is not resolving the
     thread), rebutted (reply with rationale, then resolve — disagreeing with a
     reviewer is allowed, ignoring one is not), or outdated (`isOutdated` because
     the code moved; resolve with a reply naming the change).
   - `changes-requested` → address via implementer, push; each push dismisses
     stale approvals, so always wait for review of the LATEST head.
   - `approval-missing` → the driver must NEVER approve its own PR. Park
     waiting-on-human with the PR intact and unarmed; this park kind is reported
     distinctly and never counts toward the systemic-cause signature threshold.
   - `draft` → mark ready.
   - every `custom_gate` whose hook gates merge.

   A **re-armed review**: if the head-of-queue rebase resolved conflicts, the diff
   is no longer the one the pre-PR reviewers approved — re-run the pre-PR review
   loop on the rebased head before arming. A clean fast-forward does not re-arm it.

2. **Arm `gh pr merge <pr> --auto --<merge.method>`** — only once
   `mergeability.py` returns an EMPTY requirement set and the rebased head's
   checks are green. Arming earlier risks merging the instant CI goes green with
   a finding still open.

3. **HARD exit condition**: the merge phase does not exit successfully until
   `mergeability.py` returns empty (equivalently `mergeStateStatus == CLEAN`) or
   the PR is MERGED. There is no partial satisfaction. Every requirement the repo
   imposes must be resolved.

4. On MERGED (verify `state == MERGED`): confirm the child issue auto-closed
   (close it if not), Status = **Done**, sweep the worktree per
   `status.py --sweep-plan`. If the epic is now complete (every sub-issue
   `state == CLOSED`, none parked-open), apply the epic-completion rule.

5. Report; interactive modes then STOP (they drive ONE child only).

**HARD: before any STOP or park, if `--auto` was armed, run
`gh pr merge <pr> --disable-auto` and note the disarm in the report** — never
leave a STOPPED PR armed to auto-merge unresolved findings.
```

- [ ] **Step 2: Verify the ratchet advances further**

Run: `pytest tests/test_skills_lint.py -q -k "retired_tunables"`
Expected: only `CI_ESTIMATE`, `MAX_WAIT_CYCLES`, `MERGE_WAIT_CYCLES`, `CONSECUTIVE_PARK_HALT`, `GLOBAL_PARK_THRESHOLD` still fail (Task 16 removes them).

- [ ] **Step 3: Commit**

```bash
git add epic/skills/epic/SKILL.md
git commit -m "feat(epic): drive merges from GitHub-derived requirements via a FIFO queue"
```

---

### Task 16: SKILL.md — `run` becomes a parallel orchestrator

**Files:**
- Modify: `epic/skills/epic/SKILL.md:330-388` (`run` mode)

**Interfaces:**
- Consumes: `schedule.py`, all prior scripts.

- [ ] **Step 1: Rewrite the `run` mode section**

```markdown
## Mode: `run` (autonomous — epic to completion)

An orchestrator loop. Each cycle:

1. Recover ALL state from `gh` — stateless recovery is unchanged and HARD.
2. `python epic/scripts/schedule.py --epic <n>` → the runnable wave, the FIFO
   merge queue, and any halt reason.
3. Dispatch one **drive subagent per wave member, in parallel**, if your harness
   supports subagents; otherwise drive the wave sequentially in merge-queue order,
   in-session. `--serial` forces the sequential path.
4. Marshal the merge queue: admit `merge_queue[0]` ONLY, run the merge phase for
   it, then recompute.
5. Reschedule (via `ScheduleWakeup` if your harness supports scheduled wakeups;
   otherwise keep an in-session loop). No eligible unparked child left and the
   epic complete → apply the epic-completion rule, final report, TERMINATE.

**A drive subagent owns exactly one child**: worktree → context → pin →
implement → `pre-review` gates → pre-PR reviews → PR-open → Status = In Review →
prose-gate resolution, ending when every prose gate is clean. It NEVER rebases
for merge, NEVER arms `--auto`, NEVER merges, and never touches another child's
worktree or branch. Prose-gate resolution stays inside the parallel region
deliberately: bot-review latency is the largest cost in a child's lifecycle.

**Concurrency is capped twice**: globally by `epic-config.max_parallel`
(`MAX_PARALLEL`, default 3), and per repo by that repo's
`worktrees.max_concurrent`.

**Unattended invariants:** no interactive questions of any kind (on an
architectural fork: pick the most conservative defensible default — smallest
blast radius, reversible, matches existing repo patterns — record decision +
rationale in the PR body and a child comment; park only if no defensible default
exists. HARD EXCEPTION: for a contract/API-defining child or a P0 child, NEVER
auto-decide an architectural fork — park for human input instead). **Never STOP
or park with auto-merge armed** — run `gh pr merge <pr> --disable-auto` first and
record it.

Tunables (do not exceed): `MAX_PARALLEL=3`, `STALL_ROUNDS=2`, `REPIN_ATTEMPTS=1`,
`PR_WATCH_DEADLINE_S=3600`, `CHILD_DRIVE_CEILING_S=7200`, `CONFLICT_ATTEMPTS=2`,
`PARK_SIGNATURE_THRESHOLD=3`.

**Circuit breakers:**
- Convergence stall after re-pin / gate unfixable / a resource ceiling exceeded /
  a subagent BLOCKED with no defensible path (if your harness runs steps via
  subagents — otherwise an inline step stalled the same way) → **park**: if
  `--auto` is armed, FIRST run `gh pr merge <pr> --disable-auto`; comment
  `FAILED: <precise reason + evidence URLs>` on the child, with a machine-readable
  trailer `epic-park: {"code":…, "gate":…, "signature":…, "waiting_on_human":…}`;
  set Status = **Parked**; leave worktree + PR intact. **Siblings continue** —
  a park never stalls the wave or drains the queue.
- `approval-missing` parks are `waiting_on_human: true` and are excluded from the
  systemic-cause count: an epic correctly waiting on your approval is working.
- **Global halt** (no reschedule, full report) exactly when `schedule.py` returns
  a halt reason: `systemic:<signature>` (`PARK_SIGNATURE_THRESHOLD` parks sharing
  one signature — assume CI down, `main` broken or a ruleset change, and
  re-verify via `gh api repos/<owner>/<repo>/rules/branches/main`),
  `no-runnable-work`, or `transitive-block`.
```

- [ ] **Step 2: Run the full lint**

Run: `pytest tests/ -q`
Expected: all passed — every retired-tunable ratchet from Task 12 is now green.

- [ ] **Step 3: Commit**

```bash
git add epic/skills/epic/SKILL.md
git commit -m "feat(epic): run drives children in parallel behind a marshalled merge queue"
```

---

### Task 17: Reference queries and README

**Files:**
- Modify: `epic/skills/epic/references/github-graphql.md`
- Modify: `epic/README.md`

**Interfaces:**
- Consumes: all scripts.

- [ ] **Step 1: Add the queries the scripts issue**

Append a section to `references/github-graphql.md` documenting, with runnable
snippets: the `reviewThreads(first:100){nodes{id isResolved isOutdated path}}`
query used by `pr_watch.py` and `mergeability.py`; the
`gh pr view --json headRefOid,statusCheckRollup,reviews,mergeStateStatus,mergeable,reviewDecision,isDraft`
field set; and `gh api repos/<owner>/<repo>/rules/branches/main` as the ruleset
source of truth. State that `mergeability.py` is the only supported consumer of
these for gating decisions.

- [ ] **Step 2: Document the new surface in the README**

Add to `epic/README.md`: the `--serial` flag; `epic-config.max_parallel`; that
`epic/scripts/` requires Python 3 and `pyyaml` at runtime; and a one-line
description of each of the nine scripts.

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -q`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add epic/skills/epic/references/github-graphql.md epic/README.md
git commit -m "docs(epic): document parallel drive, merge queue and script surface"
```

---

## Self-Review Notes

**Spec coverage.** D1 → Tasks 1–11 (all nine modules, `gh.py` added as the I/O boundary the spec's "shell out to `gh`" implies). D2 → Task 16. D3 → Tasks 5, 15. D4 → Task 8. D5 → Tasks 8, 14. D6 → Tasks 9, 14. D7 → Task 10. D8 → Tasks 6, 16. D9 → Tasks 12, 14–16. D10 → Task 13. D11 → Task 12. D12 → Tasks 7, 15.

**Type consistency.** The child dict is defined once in Task 4 and reused verbatim in Tasks 5, 6 and 11; the `child()` test helper is defined in Task 4 and referenced by later tests in the same file. The PR dict is defined in Task 5 and reused in Task 11. The finding dict is defined in Task 8 and referenced in Task 14's prose. Gate states are `clean`/`na`/`pending`/`red` throughout.

**Deliberate ordering.** Task 12 lands the lint ratchets *before* the prose rewrite so Tasks 13–16 have an executable acceptance criterion, and each of those tasks turns a documented subset of the ratchets green.
