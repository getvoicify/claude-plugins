# Durable PR Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `pr_watch.py`'s blocking `--await`/deadline wait with a non-sleeping tick that uses jittered exponential backoff, persists a disposable cursor, and never gives up on a slow reviewer.

**Architecture:** `pr_watch.py` stops sleeping. Each invocation is one tick: load cursor, fetch the PR, compare a fingerprint covering **all** review activity, then either report activity (exit 0) or report the delay until the next tick (exit 1). A new `watch_state.py` owns the cursor file. The driver schedules ticks with its own harness scheduler, so the script stays harness-agnostic.

**Tech Stack:** Python 3 standard library only (`argparse`, `json`, `hashlib`, `pathlib`, `datetime`, `random`, `re`), plus the existing `gh.py` shell-out layer. Tests are pytest, all in `tests/test_epic_scripts.py`.

**Spec:** `docs/durable-pr-watch-design.md`

## Global Constraints

- **TDD is mandatory.** Every task writes the failing test first, runs it to see it fail for the right reason, then writes the minimum implementation. No exceptions.
- **No new dependencies.** Standard library plus `gh.py` only. `pyyaml` is already a dependency but is not needed here.
- **`gh.py` stays the only module that shells out.** Cursor file I/O lives in `watch_state.py`; `pr_watch.py` itself never touches `subprocess`.
- **Exit-code convention (from `README.md:95`):** `0` success, `1` definite-negative / not-yet-satisfied, `2` hard error.
- **Pure core, thin shell.** Every module in `epic/scripts/` keeps its pure functions I/O-free and confines side effects to `main()`. Follow that pattern.
- **Backoff defaults:** `WATCH_FLOOR_S = 15`, `WATCH_MULT = 1.8`, `WATCH_CEIL_S = 900`, jitter `±20%`, `_MAX_ERRORS = 8`.
- **Run the whole suite before every commit:** `python3 -m pytest tests/ -q`. A task is not done until it is green.
- **Commit messages carry no AI attribution.** No `Co-Authored-By: Claude` trailer, no generated-with footer.

---

## File Structure

| File | Responsibility |
|---|---|
| `epic/scripts/watch_state.py` | **New.** Cursor path resolution, load/save/clear, and ISO-timestamp elapsed maths. The only filesystem state the watch keeps. |
| `epic/scripts/pr_watch.py` | **Rewritten.** Pure `fingerprint()`, `changed_facets()`, `backoff_delay()`, `error_backoff()`; `main()` is one tick. |
| `epic/scripts/status.py` | **Modified.** Adds `watch_report()` and surfaces live watches in the CLI payload. |
| `tests/test_epic_scripts.py` | **Modified.** The obsolete pr_watch block is deleted; new tests are appended at the end of the file. |
| `epic/skills/epic/SKILL.md` | **Modified.** Arm-and-yield protocol, new tunables, deadline-park circuit breaker removed. |
| `epic/skills/epic/references/github-graphql.md` | **Modified.** The `--await` vocabulary section is replaced. |
| `README.md` | **Modified.** The `pr_watch.py` script-table row. |

---

### Task 1: Watch cursor persistence

**Files:**
- Create: `epic/scripts/watch_state.py`
- Test: `tests/test_epic_scripts.py` (append at end of file)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DEFAULT_CURSOR` — dict with keys `fingerprint` (dict|None), `step` (int), `errors` (int), `last_activity_at` (str|None), `last_changed` (list[str]).
  - `cursor_path(repo: str, pr: int, override: str|None = None) -> pathlib.Path`
  - `load(repo: str, pr: int, override: str|None = None) -> dict`
  - `save(repo: str, pr: int, cursor: dict, override: str|None = None) -> None`
  - `clear(repo: str, pr: int, override: str|None = None) -> bool`
  - `elapsed_s(then_iso: str|None, now_iso: str) -> int`

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_epic_scripts.py`:

```python
# --- watch_state.py: the watch cursor -------------------------------------
# The cursor is a pure optimisation: losing it costs one wasted fast-tier
# poll and nothing else. Every "bad file" path below must therefore fall
# back to DEFAULT_CURSOR rather than raise, or a corrupt cursor would take
# down a live epic run.

import watch_state


def test_cursor_path_is_namespaced_by_repo_and_pr(tmp_path):
    path = watch_state.cursor_path("getvoicify/claude-plugins", 12, str(tmp_path))
    assert path == tmp_path / "getvoicify__claude-plugins__12.json"


def test_load_returns_defaults_when_no_cursor_exists(tmp_path):
    assert watch_state.load("o/n", 1, str(tmp_path)) == watch_state.DEFAULT_CURSOR


def test_load_returns_a_copy_not_the_shared_default(tmp_path):
    """Mutating one load must not poison the next one."""
    first = watch_state.load("o/n", 1, str(tmp_path))
    first["step"] = 99
    assert watch_state.load("o/n", 1, str(tmp_path))["step"] == 0


def test_save_then_load_round_trips(tmp_path):
    cursor = {
        "fingerprint": {"head": "abc", "checks": "d1"},
        "step": 3,
        "errors": 0,
        "last_activity_at": "2026-08-22T10:00:00+00:00",
        "last_changed": ["checks"],
    }
    watch_state.save("o/n", 7, cursor, str(tmp_path))
    assert watch_state.load("o/n", 7, str(tmp_path)) == cursor


def test_save_creates_missing_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "deeper"
    watch_state.save("o/n", 7, dict(watch_state.DEFAULT_CURSOR), str(nested))
    assert (nested / "o__n__7.json").exists()


def test_load_falls_back_to_defaults_on_corrupt_json(tmp_path):
    (tmp_path / "o__n__7.json").write_text("{not json at all")
    assert watch_state.load("o/n", 7, str(tmp_path)) == watch_state.DEFAULT_CURSOR


def test_load_falls_back_to_defaults_when_file_is_not_a_mapping(tmp_path):
    (tmp_path / "o__n__7.json").write_text('["a", "list"]')
    assert watch_state.load("o/n", 7, str(tmp_path)) == watch_state.DEFAULT_CURSOR


def test_load_drops_unknown_keys_and_fills_missing_ones(tmp_path):
    (tmp_path / "o__n__7.json").write_text('{"step": 4, "bogus": true}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["step"] == 4
    assert loaded["fingerprint"] is None
    assert "bogus" not in loaded


def test_clear_removes_the_cursor_and_reports_it(tmp_path):
    watch_state.save("o/n", 7, dict(watch_state.DEFAULT_CURSOR), str(tmp_path))
    assert watch_state.clear("o/n", 7, str(tmp_path)) is True
    assert not (tmp_path / "o__n__7.json").exists()


def test_clear_is_a_noop_when_no_cursor_exists(tmp_path):
    assert watch_state.clear("o/n", 7, str(tmp_path)) is False


def test_state_dir_honours_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("EPIC_WATCH_DIR", str(tmp_path / "from-env"))
    assert watch_state.cursor_path("o/n", 1).parent == tmp_path / "from-env"


def test_explicit_override_beats_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("EPIC_WATCH_DIR", str(tmp_path / "from-env"))
    path = watch_state.cursor_path("o/n", 1, str(tmp_path / "explicit"))
    assert path.parent == tmp_path / "explicit"


def test_elapsed_s_measures_the_gap_between_two_iso_stamps():
    assert watch_state.elapsed_s(
        "2026-08-22T10:00:00+00:00", "2026-08-22T10:47:00+00:00"
    ) == 2820


def test_elapsed_s_accepts_githubs_trailing_z():
    assert watch_state.elapsed_s(
        "2026-08-22T10:00:00Z", "2026-08-22T10:00:30Z"
    ) == 30


def test_elapsed_s_returns_zero_when_there_is_no_prior_stamp():
    assert watch_state.elapsed_s(None, "2026-08-22T10:00:00Z") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "cursor or watch_state or elapsed_s" -v`
Expected: collection error — `ModuleNotFoundError: No module named 'watch_state'`.

- [ ] **Step 3: Write the implementation**

Create `epic/scripts/watch_state.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "cursor or watch_state or elapsed_s" -v`
Expected: PASS (15 tests).

Then run the whole suite: `python3 -m pytest tests/ -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/watch_state.py tests/test_epic_scripts.py
git commit -m "feat(epic): add watch cursor persistence

A disposable per-PR cursor holding the last fingerprint, backoff step,
error count, and last-activity stamp. Every unreadable-file path falls
back to defaults so a corrupt cursor can never take down a run."
```

---

### Task 2: PR activity fingerprint

**Files:**
- Modify: `epic/scripts/pr_watch.py`
- Test: `tests/test_epic_scripts.py` (append at end of file)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `FACETS = ("head", "checks", "reviews", "threads", "comments")`
  - `fingerprint(pr: dict, threads: list) -> dict` — keys are `FACETS`; `head` is the raw SHA, `checks`/`reviews`/`threads` are 16-char digests, `comments` is an int count.
  - `changed_facets(prev: dict|None, curr: dict) -> list[str]` — facet names that moved, in `FACETS` order. Returns `[]` when `prev` is falsy.

**Note:** `snapshot()`, `diff_event()` and `settled_event()` stay in place for now — they are deleted in Task 4. Add the new functions alongside them so the suite stays green.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_epic_scripts.py`:

```python
# --- pr_watch.py: the activity fingerprint --------------------------------
# The watcher is a dumb change-detector; mergeability.py remains the sole
# authority on what is actionable. So the fingerprint must move on ANY
# observable PR activity — most importantly a COMMENTED review, which is
# what CodeRabbit and Copilot actually post and which the old snapshot()
# deliberately dropped.

from pr_watch import FACETS, changed_facets, fingerprint


def _pr(**over):
    base = {
        "headRefOid": "a1b2c3",
        "state": "OPEN",
        "statusCheckRollup": [{"name": "ci", "status": "IN_PROGRESS", "conclusion": None}],
        "reviews": [],
        "comments": [],
    }
    base.update(over)
    return base


def test_fingerprint_is_stable_for_identical_input():
    assert fingerprint(_pr(), []) == fingerprint(_pr(), [])


def test_fingerprint_covers_every_facet():
    assert set(fingerprint(_pr(), [])) == set(FACETS)


def test_fingerprint_ignores_rollup_ordering():
    a = _pr(statusCheckRollup=[{"name": "ci"}, {"name": "lint"}])
    b = _pr(statusCheckRollup=[{"name": "lint"}, {"name": "ci"}])
    assert fingerprint(a, [])["checks"] == fingerprint(b, [])["checks"]


def test_commented_review_moves_the_fingerprint():
    """REGRESSION: the CodeRabbit stall.

    The old snapshot() skipped COMMENTED reviews, so `--await coderabbitai`
    waited out the full deadline and parked the child even though the
    review had landed. CodeRabbit and Copilot post COMMENTED, never
    APPROVED/CHANGES_REQUESTED, so this is the common case, not an edge one.
    """
    before = fingerprint(_pr(), [])
    after = fingerprint(
        _pr(reviews=[{
            "author": {"login": "coderabbitai"},
            "state": "COMMENTED",
            "submittedAt": "2026-08-22T10:00:00Z",
        }]),
        [],
    )
    assert changed_facets(before, after) == ["reviews"]


def test_head_change_is_detected():
    assert changed_facets(fingerprint(_pr(), []),
                          fingerprint(_pr(headRefOid="zzz"), [])) == ["head"]


def test_check_conclusion_change_is_detected():
    before = fingerprint(_pr(), [])
    after = fingerprint(
        _pr(statusCheckRollup=[{"name": "ci", "status": "COMPLETED",
                                "conclusion": "SUCCESS"}]),
        [],
    )
    assert changed_facets(before, after) == ["checks"]


def test_legacy_status_context_shape_is_fingerprinted():
    """StatusContext entries carry `context`/`state`, not `name`/`status`."""
    before = fingerprint(_pr(statusCheckRollup=[{"context": "cov", "state": "PENDING"}]), [])
    after = fingerprint(_pr(statusCheckRollup=[{"context": "cov", "state": "SUCCESS"}]), [])
    assert changed_facets(before, after) == ["checks"]


def test_new_thread_and_thread_resolution_both_move_threads():
    empty = fingerprint(_pr(), [])
    opened = fingerprint(_pr(), [{"id": "t1", "isResolved": False}])
    resolved = fingerprint(_pr(), [{"id": "t1", "isResolved": True}])
    assert changed_facets(empty, opened) == ["threads"]
    assert changed_facets(opened, resolved) == ["threads"]


def test_new_comment_moves_the_comments_facet():
    before = fingerprint(_pr(), [])
    after = fingerprint(_pr(comments=[{"id": "c1"}]), [])
    assert changed_facets(before, after) == ["comments"]


def test_author_may_be_a_plain_string():
    """Some gh payloads flatten author to a login string."""
    a = fingerprint(_pr(reviews=[{"author": "octocat", "state": "APPROVED",
                                  "submittedAt": "2026-08-22T10:00:00Z"}]), [])
    b = fingerprint(_pr(reviews=[{"author": {"login": "octocat"}, "state": "APPROVED",
                                  "submittedAt": "2026-08-22T10:00:00Z"}]), [])
    assert a["reviews"] == b["reviews"]


def test_changed_facets_reports_multiple_moves_in_facet_order():
    before = fingerprint(_pr(), [])
    after = fingerprint(_pr(headRefOid="zzz", comments=[{"id": "c1"}]), [])
    assert changed_facets(before, after) == ["head", "comments"]


def test_changed_facets_is_empty_when_nothing_moved():
    assert changed_facets(fingerprint(_pr(), []), fingerprint(_pr(), [])) == []


def test_changed_facets_is_empty_when_there_is_no_previous_fingerprint():
    """Arming a watch is not activity."""
    assert changed_facets(None, fingerprint(_pr(), [])) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "fingerprint or changed_facets" -v`
Expected: FAIL with `ImportError: cannot import name 'FACETS' from 'pr_watch'`.

- [ ] **Step 3: Write the implementation**

Add to `epic/scripts/pr_watch.py` — new imports at the top, then the functions below the existing `snapshot()`:

```python
import hashlib

FACETS = ("head", "checks", "reviews", "threads", "comments")


def fingerprint(pr, threads):
    """Per-facet digests of everything worth waking on.

    Deliberately records EVERY review, COMMENTED included: CodeRabbit and
    Copilot post COMMENTED reviews with inline threads and never a formal
    verdict, so dropping them is what made the old watcher blind to the
    reviews it was most often waiting for.
    """
    return {
        "head": pr.get("headRefOid"),
        "checks": _digest(
            (c.get("name") or c.get("context"),
             c.get("status") or c.get("state"),
             c.get("conclusion"))
            for c in pr.get("statusCheckRollup") or []
        ),
        "reviews": _digest(
            (_login(r.get("author")), r.get("state"), r.get("submittedAt"))
            for r in pr.get("reviews") or []
        ),
        "threads": _digest(
            (t.get("id"), bool(t.get("isResolved"))) for t in threads or []
        ),
        "comments": len(pr.get("comments") or []),
    }


def changed_facets(prev, curr):
    """Facet names that moved, in FACETS order. Arming is never activity."""
    if not prev:
        return []
    return [f for f in FACETS if prev.get(f) != curr.get(f)]


def _digest(rows):
    """Order-independent digest of a set of tuples."""
    ordered = sorted(rows, key=lambda row: [str(v) for v in row])
    payload = json.dumps(ordered, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _login(author):
    return author.get("login") if isinstance(author, dict) else author
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "fingerprint or changed_facets" -v`
Expected: PASS (13 tests).

Then: `python3 -m pytest tests/ -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/pr_watch.py tests/test_epic_scripts.py
git commit -m "feat(epic): fingerprint any PR activity, COMMENTED reviews included

The watcher becomes a change-detector over head, checks, all reviews,
threads, and comment count. Recording COMMENTED reviews fixes the stall
where awaiting coderabbitai never fired because CodeRabbit never submits
a formal verdict."
```

---

### Task 3: Exponential jittered backoff

**Files:**
- Modify: `epic/scripts/pr_watch.py`
- Test: `tests/test_epic_scripts.py` (append at end of file)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `WATCH_FLOOR_S = 15`, `WATCH_MULT = 1.8`, `WATCH_CEIL_S = 900`
  - `backoff_delay(step: int, jitter: float = 0.0) -> int` — `jitter` is a caller-supplied value in `[-1.0, 1.0]`; the function stays pure and `main()` supplies `random.uniform(-1, 1)`.
  - `error_backoff(errors: int, stderr: str, now_epoch: int|None = None) -> int`

**Note:** the old staircase `backoff(elapsed)` stays until Task 4 deletes it. The new function is named `backoff_delay` so both can coexist while the suite stays green.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_epic_scripts.py`:

```python
# --- pr_watch.py: exponential jittered backoff ----------------------------
# Jitter is injected, not drawn, so the pure function stays deterministic
# under test and main() owns the randomness. Desynchronising parallel wave
# members is the whole point: the old 15/30/60 staircase had every child in
# the wave polling in lockstep.

from pr_watch import (
    WATCH_CEIL_S,
    WATCH_FLOOR_S,
    backoff_delay,
    error_backoff,
)


def test_backoff_delay_grows_exponentially_to_a_ceiling():
    assert [backoff_delay(s) for s in range(8)] == [15, 27, 49, 87, 157, 283, 510, 900]


def test_backoff_delay_stays_at_the_ceiling_forever():
    assert backoff_delay(50) == WATCH_CEIL_S
    assert backoff_delay(5000) == WATCH_CEIL_S


def test_backoff_delay_starts_at_the_floor():
    assert backoff_delay(0) == WATCH_FLOOR_S


def test_negative_steps_clamp_to_the_floor():
    assert backoff_delay(-3) == WATCH_FLOOR_S


def test_jitter_spans_plus_or_minus_twenty_percent():
    assert backoff_delay(7, -1.0) == 720
    assert backoff_delay(7, 1.0) == 1080
    assert backoff_delay(7, 0.0) == 900


def test_delay_is_never_below_one_second():
    assert backoff_delay(0, -1.0) >= 1


def test_error_backoff_honours_retry_after():
    stderr = "HTTP 403: You have exceeded a secondary rate limit\nRetry-After: 47"
    assert error_backoff(1, stderr) == 47


def test_error_backoff_caps_retry_after_at_the_ceiling():
    assert error_backoff(1, "Retry-After: 99999") == WATCH_CEIL_S


def test_error_backoff_honours_ratelimit_reset_as_an_absolute_epoch():
    stderr = "HTTP 403: rate limit exceeded\nx-ratelimit-reset: 1000300"
    assert error_backoff(1, stderr, now_epoch=1000000) == 300


def test_error_backoff_ignores_an_already_past_ratelimit_reset():
    stderr = "x-ratelimit-reset: 900"
    assert error_backoff(0, stderr, now_epoch=1000) == 1


def test_error_backoff_falls_back_to_the_exponential_ladder():
    assert error_backoff(3, "HTTP 502: Bad Gateway") == backoff_delay(3)


def test_error_backoff_tolerates_empty_stderr():
    assert error_backoff(2, "") == backoff_delay(2)
    assert error_backoff(2, None) == backoff_delay(2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "backoff_delay or error_backoff" -v`
Expected: FAIL with `ImportError: cannot import name 'WATCH_CEIL_S' from 'pr_watch'`.

- [ ] **Step 3: Write the implementation**

Add to `epic/scripts/pr_watch.py` (add `import re` at the top):

```python
WATCH_FLOOR_S = 15
WATCH_MULT = 1.8
WATCH_CEIL_S = 900
_JITTER = 0.2

_RETRY_AFTER_RE = re.compile(r"retry[- ]after:?\s*(\d+)", re.I)
_RATELIMIT_RESET_RE = re.compile(r"x-ratelimit-reset:?\s*(\d+)", re.I)


def backoff_delay(step, jitter=0.0):
    """Seconds until the next tick.

    `jitter` is supplied by the caller (in [-1.0, 1.0]) rather than drawn
    here, so this stays pure and deterministic under test. main() passes
    random.uniform(-1, 1); the resulting spread desynchronises the parallel
    wave members that the old fixed staircase kept in lockstep.
    """
    base = min(WATCH_FLOOR_S * (WATCH_MULT ** max(0, step)), WATCH_CEIL_S)
    return max(1, round(base * (1 + _JITTER * jitter)))


def error_backoff(errors, stderr, now_epoch=None):
    """Seconds to wait after a failed `gh` call.

    GitHub's own guidance wins when it gives any: an explicit `Retry-After`,
    then an `x-ratelimit-reset` epoch. Otherwise fall back to the same
    exponential ladder as a quiet tick.
    """
    text = stderr or ""
    match = _RETRY_AFTER_RE.search(text)
    if match:
        return max(1, min(int(match.group(1)), WATCH_CEIL_S))
    match = _RATELIMIT_RESET_RE.search(text)
    if match and now_epoch is not None:
        return max(1, min(int(match.group(1)) - int(now_epoch), WATCH_CEIL_S))
    return backoff_delay(errors)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "backoff_delay or error_backoff" -v`
Expected: PASS (12 tests).

Then: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/pr_watch.py tests/test_epic_scripts.py
git commit -m "feat(epic): exponential jittered backoff for the PR watch

15s to a 900s ceiling at x1.8, jittered +/-20% so parallel wave members
desynchronise. gh failures honour Retry-After and x-ratelimit-reset
before falling back to the same ladder."
```

---

### Task 4: The tick — `main()` rewrite

**Files:**
- Modify: `epic/scripts/pr_watch.py`
- Modify: `tests/test_epic_scripts.py` (delete the obsolete block; append new tests)

**Interfaces:**
- Consumes: `watch_state.load/save/clear/elapsed_s` (Task 1); `fingerprint`, `changed_facets` (Task 2); `backoff_delay` (Task 3).
- Produces: `main(argv=None) -> int` with the documented event shapes; `_now() -> str` (monkeypatch point returning an ISO-8601 UTC stamp).

**Deletions in this task:**
- From `epic/scripts/pr_watch.py`: `snapshot()`, `diff_event()`, `settled_event()`, `backoff()`, `_PASSING`, `_SETTLED_STATES`. **Keep `import time`** — Task 5 needs `time.time()` as the `x-ratelimit-reset` reference point. It is never used to sleep again.
- From `tests/test_epic_scripts.py`: everything from the line `# Task 10: pr_watch — responsive PR monitoring` through the line immediately before `# Task 11: status.py — drift, sweep plan, completion`. Match on those two comment markers, not on line numbers. The tests appended in Tasks 1–3 live at the end of the file and must not be touched.

- [ ] **Step 1: Delete the obsolete tests and write the failing new ones**

First delete the obsolete block described above. Then append to the end of `tests/test_epic_scripts.py`:

```python
# --- pr_watch.py: one invocation is one tick ------------------------------
# main() never sleeps. It loads the cursor, fetches once, and either
# reports activity (exit 0) or reports how long until the next tick
# (exit 1). That is what makes a wait survivable: no long-lived process to
# be killed, and a cursor cheap enough to lose.

import pr_watch as _pw


def _install_tick(monkeypatch, pr, threads, now="2026-08-22T10:00:00+00:00"):
    monkeypatch.setattr(_pw, "_fetch", lambda repo, number: (pr, threads))
    monkeypatch.setattr(_pw, "_now", lambda: now)
    monkeypatch.setattr(_pw.random, "uniform", lambda lo, hi: 0.0)


def test_first_tick_arms_the_watch_and_asks_for_another(tmp_path, monkeypatch, capsys):
    _install_tick(monkeypatch, _pr(), [])
    code = _pw.main(["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)])
    event = json.loads(capsys.readouterr().out)
    assert code == 1
    assert event["event"] == "waiting"
    assert event["armed"] is True
    assert event["next_tick_in_s"] == 15
    assert watch_state.load("o/n", 7, str(tmp_path))["fingerprint"] is not None


def test_quiet_tick_advances_the_backoff_step(tmp_path, monkeypatch, capsys):
    _install_tick(monkeypatch, _pr(), [])
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _pw.main(args)                      # arm at step 0
    capsys.readouterr()
    assert _pw.main(args) == 1
    event = json.loads(capsys.readouterr().out)
    assert event["next_tick_in_s"] == 27
    assert watch_state.load("o/n", 7, str(tmp_path))["step"] == 1


def test_quiet_tick_reports_how_long_the_pr_has_been_silent(tmp_path, monkeypatch, capsys):
    _install_tick(monkeypatch, _pr(), [], now="2026-08-22T10:00:00+00:00")
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _pw.main(args)
    capsys.readouterr()
    monkeypatch.setattr(_pw, "_now", lambda: "2026-08-22T10:47:00+00:00")
    _pw.main(args)
    assert json.loads(capsys.readouterr().out)["quiet_s"] == 2820


def test_a_commented_review_ends_the_wait(tmp_path, monkeypatch, capsys):
    """REGRESSION: the end-to-end form of the CodeRabbit stall."""
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    capsys.readouterr()
    _install_tick(monkeypatch, _pr(reviews=[{
        "author": {"login": "coderabbitai"}, "state": "COMMENTED",
        "submittedAt": "2026-08-22T10:05:00Z"}]), [])
    code = _pw.main(args)
    event = json.loads(capsys.readouterr().out)
    assert code == 0
    assert event["event"] == "activity"
    assert event["changed"] == ["reviews"]


def test_an_empty_rollup_reports_waiting_not_settled(tmp_path, monkeypatch, capsys):
    """REGRESSION: the instant false positive.

    The old settled_event() treated an empty check rollup ("NONE") and zero
    unresolved threads as terminal, so a watch armed seconds after PR open
    returned "settled" before CI had even registered.
    """
    _install_tick(monkeypatch, _pr(statusCheckRollup=[]), [])
    code = _pw.main(["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["event"] == "waiting"


def test_head_change_resets_the_backoff_to_the_floor(tmp_path, monkeypatch, capsys):
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    _pw.main(args)
    _pw.main(args)                      # step is now 2
    capsys.readouterr()
    _install_tick(monkeypatch, _pr(headRefOid="newsha"), [])
    _pw.main(args)
    capsys.readouterr()
    assert watch_state.load("o/n", 7, str(tmp_path))["step"] == 0


def test_comment_noise_does_not_reset_the_backoff(tmp_path, monkeypatch, capsys):
    """A chatty PR must not pin the watch at the fast tier."""
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    _pw.main(args)
    _pw.main(args)                      # step is now 2
    capsys.readouterr()
    _install_tick(monkeypatch, _pr(comments=[{"id": "c1"}]), [])
    _pw.main(args)
    capsys.readouterr()
    assert watch_state.load("o/n", 7, str(tmp_path))["step"] == 2


def test_reset_backoff_forces_the_floor(tmp_path, monkeypatch, capsys):
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    _pw.main(args)
    _pw.main(args)
    capsys.readouterr()
    _pw.main(args + ["--reset-backoff"])
    assert json.loads(capsys.readouterr().out)["next_tick_in_s"] == 15


def test_resume_backoff_continues_from_the_stored_step(tmp_path, monkeypatch, capsys):
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    _pw.main(args)                      # step is now 1
    capsys.readouterr()
    _pw.main(args + ["--resume-backoff"])
    assert json.loads(capsys.readouterr().out)["next_tick_in_s"] == 49


def test_activity_records_what_moved_on_the_cursor(tmp_path, monkeypatch, capsys):
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    capsys.readouterr()
    _install_tick(monkeypatch, _pr(statusCheckRollup=[
        {"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}]), [])
    _pw.main(args)
    capsys.readouterr()
    assert watch_state.load("o/n", 7, str(tmp_path))["last_changed"] == ["checks"]


def test_a_closed_pr_ends_the_watch_and_clears_the_cursor(tmp_path, monkeypatch, capsys):
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    capsys.readouterr()
    _install_tick(monkeypatch, _pr(state="MERGED"), [])
    code = _pw.main(args)
    event = json.loads(capsys.readouterr().out)
    assert code == 0
    assert event["event"] == "pr-closed"
    assert event["state"] == "MERGED"
    assert not watch_state.cursor_path("o/n", 7, str(tmp_path)).exists()


def test_the_tick_never_sleeps(tmp_path, monkeypatch, capsys):
    """The stall this whole rewrite exists to fix: a blocking wait cannot
    outlive its tool call, so main() must not block at all."""
    _install_tick(monkeypatch, _pr(), [])
    monkeypatch.setattr(
        _pw.time, "sleep",
        lambda *a: pytest.fail("main() must never sleep"),
        raising=False,
    )
    _pw.main(["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)])
```

Note: `test_the_tick_never_sleeps` keeps `raising=False` so it holds whether or not the module imports `time`. The assertion is simply that nothing blocks.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "tick or arms_the_watch or ends_the_wait or pr_closed or backoff_to_the_floor" -v`
Expected: FAIL — `main()` still parses `--await` and rejects `--state-dir` with `SystemExit: 2`.

- [ ] **Step 3: Write the implementation**

Replace everything from `_PR_FIELDS` to the end of `epic/scripts/pr_watch.py`, and delete `snapshot`, `diff_event`, `settled_event`, `backoff`, `_PASSING`, `_SETTLED_STATES`. Add `import datetime`, `import random`, and `import watch_state` at the top. The new module docstring is:

```python
"""One invocation is one tick: report PR activity, or how long to wait for
the next tick. Nothing here ever sleeps."""
```

```python
_MAX_ERRORS = 8

_PR_FIELDS = "headRefOid,state,statusCheckRollup,reviews,comments"

_THREADS_QUERY = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}
"""


def _now():
    """ISO-8601 UTC stamp. A monkeypatch point for the tests."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fetch(repo, pr_number):
    owner, name = repo.split("/")
    pr = gh.run_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", _PR_FIELDS]
    )
    data = gh.graphql(_THREADS_QUERY, owner=owner, name=name, pr=pr_number)
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return pr, threads


def _emit(payload, code):
    print(json.dumps(payload))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="One PR-watch tick: report activity, or the delay until the next tick."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--state-dir", dest="state_dir",
                        help="cursor directory (default: $EPIC_WATCH_DIR or ~/.cache/epic/watch)")
    parser.add_argument("--stop", action="store_true",
                        help="end this watch and delete its cursor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reset-backoff", dest="reset", action="store_true",
                       help="force the backoff back to the floor (use after a push)")
    group.add_argument("--resume-backoff", dest="resume", action="store_true",
                       help="continue from the stored step (the default; asserts intent)")
    args = parser.parse_args(argv)

    if args.stop:
        removed = watch_state.clear(args.repo, args.pr, args.state_dir)
        return _emit({"event": "stopped", "cursor_removed": removed}, 0)

    cursor = watch_state.load(args.repo, args.pr, args.state_dir)
    if args.reset:
        cursor["step"] = 0

    try:
        pr, threads = _fetch(args.repo, args.pr)
    except gh.GhError as err:
        return _emit({"event": "error", "detail": err.stderr or str(err)}, 2)

    state = pr.get("state")
    if state and state != "OPEN":
        watch_state.clear(args.repo, args.pr, args.state_dir)
        return _emit({"event": "pr-closed", "state": state,
                      "head": pr.get("headRefOid")}, 0)

    current = fingerprint(pr, threads)
    changed = changed_facets(cursor.get("fingerprint"), current)
    now = _now()

    if changed:
        # Only a real head change earns the floor again — comment noise on a
        # busy PR must not pin the watch at the fast tier.
        if "head" in changed:
            cursor["step"] = 0
        cursor.update(fingerprint=current, last_activity_at=now, last_changed=changed)
        watch_state.save(args.repo, args.pr, cursor, args.state_dir)
        return _emit({"event": "activity", "changed": changed,
                      "head": current["head"]}, 0)

    arming = cursor.get("fingerprint") is None
    if arming:
        cursor.update(fingerprint=current, last_activity_at=now)
    elif not args.reset:
        # --reset-backoff holds this tick at the floor it just set; every
        # other quiet tick widens the gap by one step.
        cursor["step"] += 1

    delay = backoff_delay(cursor["step"], random.uniform(-1, 1))
    watch_state.save(args.repo, args.pr, cursor, args.state_dir)
    payload = {"event": "waiting", "next_tick_in_s": delay,
               "quiet_s": watch_state.elapsed_s(cursor["last_activity_at"], now)}
    if arming:
        payload["armed"] = True
    return _emit(payload, 1)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "tick or arms_the_watch or ends_the_wait or pr_closed or backoff_to_the_floor or noise or resume_backoff or reset_backoff or what_moved or silent" -v`
Expected: PASS (12 tests).

Then: `python3 -m pytest tests/ -q`
Expected: PASS. The obsolete `snapshot`/`diff_event`/`settled_event` tests are gone; nothing else references them.

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/pr_watch.py tests/test_epic_scripts.py
git commit -m "feat(epic)!: make pr_watch a tick instead of a blocking wait

main() no longer sleeps. Each invocation loads the cursor, fetches once,
and reports either activity (exit 0) or the delay until the next tick
(exit 1). Removes --await, the deadline, and settled_event, whose empty
rollup and zero-threads terminal states fired instantly on a fresh PR.

BREAKING: --await and --deadline are gone; --state-dir, --stop,
--reset-backoff and --resume-backoff take their place."
```

---

### Task 5: Survive `gh` failures without dropping the watch

**Files:**
- Modify: `epic/scripts/pr_watch.py:main`
- Test: `tests/test_epic_scripts.py` (append at end of file)

**Interfaces:**
- Consumes: `error_backoff` (Task 3), `_MAX_ERRORS` (Task 4).
- Produces: no new public names. `main()` gains the error ladder; the `waiting` event may now carry `"reason": "gh-error"` and `"consecutive_errors": int`.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_epic_scripts.py`:

```python
# --- pr_watch.py: a flaky gh must not end the watch -----------------------
# A transient 502 or a secondary rate limit is a reason to wait longer, not
# a reason to abandon a PR. Only a sustained outage (8 consecutive
# failures) is fatal.

def _failing_fetch(monkeypatch, stderr, now="2026-08-22T10:00:00+00:00"):
    def boom(repo, number):
        raise gh.GhError(1, stderr)
    monkeypatch.setattr(_pw, "_fetch", boom)
    monkeypatch.setattr(_pw, "_now", lambda: now)
    monkeypatch.setattr(_pw.random, "uniform", lambda lo, hi: 0.0)


def test_a_transient_gh_error_asks_for_another_tick(tmp_path, monkeypatch, capsys):
    _failing_fetch(monkeypatch, "HTTP 502: Bad Gateway")
    code = _pw.main(["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)])
    event = json.loads(capsys.readouterr().out)
    assert code == 1
    assert event["event"] == "waiting"
    assert event["reason"] == "gh-error"
    assert event["consecutive_errors"] == 1


def test_consecutive_errors_accumulate_across_ticks(tmp_path, monkeypatch, capsys):
    _failing_fetch(monkeypatch, "HTTP 502: Bad Gateway")
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _pw.main(args)
    _pw.main(args)
    capsys.readouterr()
    _pw.main(args)
    assert json.loads(capsys.readouterr().out)["consecutive_errors"] == 3


def test_a_secondary_rate_limit_waits_exactly_as_long_as_github_asks(
    tmp_path, monkeypatch, capsys
):
    _failing_fetch(monkeypatch, "HTTP 403: secondary rate limit\nRetry-After: 47")
    _pw.main(["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["next_tick_in_s"] == 47


def test_a_successful_tick_clears_the_error_count(tmp_path, monkeypatch, capsys):
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _failing_fetch(monkeypatch, "HTTP 502: Bad Gateway")
    _pw.main(args)
    _pw.main(args)
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    capsys.readouterr()
    assert watch_state.load("o/n", 7, str(tmp_path))["errors"] == 0


def test_a_sustained_outage_is_finally_fatal(tmp_path, monkeypatch, capsys):
    _failing_fetch(monkeypatch, "HTTP 502: Bad Gateway")
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    for _ in range(_pw._MAX_ERRORS - 1):
        assert _pw.main(args) == 1
    capsys.readouterr()
    code = _pw.main(args)
    event = json.loads(capsys.readouterr().out)
    assert code == 2
    assert event["event"] == "error"
    assert event["consecutive"] == _pw._MAX_ERRORS


def test_stop_deletes_the_cursor(tmp_path, monkeypatch, capsys):
    _install_tick(monkeypatch, _pr(), [])
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _pw.main(args)
    capsys.readouterr()
    code = _pw.main(args + ["--stop"])
    event = json.loads(capsys.readouterr().out)
    assert code == 0
    assert event["event"] == "stopped"
    assert event["cursor_removed"] is True
    assert not watch_state.cursor_path("o/n", 7, str(tmp_path)).exists()


def test_stop_on_an_unwatched_pr_is_harmless(tmp_path, capsys):
    code = _pw.main(["--repo", "o/n", "--pr", "9", "--state-dir", str(tmp_path), "--stop"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["cursor_removed"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "gh_error or rate_limit or outage or error_count or accumulate" -v`
Expected: FAIL — the first assertion trips on `code == 2`, because Task 4's `main()` exits `2` on the first `GhError`.

- [ ] **Step 3: Write the implementation**

In `epic/scripts/pr_watch.py`, replace the `except gh.GhError` block inside `main()` with:

```python
    try:
        pr, threads = _fetch(args.repo, args.pr)
    except gh.GhError as err:
        detail = err.stderr or str(err)
        cursor["errors"] += 1
        watch_state.save(args.repo, args.pr, cursor, args.state_dir)
        if cursor["errors"] >= _MAX_ERRORS:
            return _emit({"event": "error", "detail": detail,
                          "consecutive": cursor["errors"]}, 2)
        return _emit({"event": "waiting",
                      "next_tick_in_s": error_backoff(cursor["errors"], detail,
                                                      int(time.time())),
                      "reason": "gh-error",
                      "consecutive_errors": cursor["errors"]}, 1)

    cursor["errors"] = 0
```

`time` is already imported (Task 4 kept it); it is used here only for `time.time()` as the `x-ratelimit-reset` reference point, never for sleeping.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "gh_error or rate_limit or outage or error_count or accumulate or stop" -v`
Expected: PASS (7 tests).

Then: `python3 -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/pr_watch.py tests/test_epic_scripts.py
git commit -m "feat(epic): ride out gh failures instead of dropping the watch

A transient error asks for another tick on its own backoff ladder,
honouring Retry-After and x-ratelimit-reset. Only eight consecutive
failures are fatal, and one success clears the count."
```

---

### Task 6: Report silent watches in `status.py`

**Files:**
- Modify: `epic/scripts/status.py`
- Test: `tests/test_epic_scripts.py` (append at end of file)

**Interfaces:**
- Consumes: `watch_state.load`, `watch_state.elapsed_s` (Task 1).
- Produces: `watch_report(children: list, cursors: dict, now_iso: str) -> list[dict]` where each entry is `{"child": int, "pr": int, "quiet_s": int, "last_activity": list[str]}`. `main()`'s JSON payload gains a `"watches"` key.

**Also in this task:** `status.py`'s `_fetch` currently sets `child["pr"] = {"state": pr["state"]}`. Add `"number": pr["number"]` so the report can name the PR.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_epic_scripts.py`:

```python
# --- status.py: which watches have gone quiet -----------------------------
# A long silence is REPORTED, never parked. This is the replacement for the
# deadline-to-park circuit breaker: the run stays alive and the operator
# gets told which gate has gone quiet and for how long.

from status import watch_report

_NOW = "2026-08-22T11:00:00+00:00"


def test_watch_report_names_the_pr_and_how_long_it_has_been_quiet():
    children = [{"number": 12, "pr": {"state": "OPEN", "number": 34}}]
    cursors = {12: {"last_activity_at": "2026-08-22T10:13:00+00:00",
                    "last_changed": ["checks"]}}
    assert watch_report(children, cursors, _NOW) == [
        {"child": 12, "pr": 34, "quiet_s": 2820, "last_activity": ["checks"]}
    ]


def test_watch_report_skips_children_without_an_open_pr():
    children = [
        {"number": 1, "pr": None},
        {"number": 2, "pr": {"state": "MERGED", "number": 20}},
    ]
    cursors = {1: {"last_activity_at": _NOW, "last_changed": []},
               2: {"last_activity_at": _NOW, "last_changed": []}}
    assert watch_report(children, cursors, _NOW) == []


def test_watch_report_skips_children_with_no_cursor():
    children = [{"number": 12, "pr": {"state": "OPEN", "number": 34}}]
    assert watch_report(children, {}, _NOW) == []


def test_watch_report_skips_a_cursor_that_never_recorded_activity():
    children = [{"number": 12, "pr": {"state": "OPEN", "number": 34}}]
    cursors = {12: {"last_activity_at": None, "last_changed": []}}
    assert watch_report(children, cursors, _NOW) == []


def test_watch_report_is_ordered_by_child_number():
    children = [
        {"number": 9, "pr": {"state": "OPEN", "number": 90}},
        {"number": 3, "pr": {"state": "OPEN", "number": 30}},
    ]
    cursors = {
        9: {"last_activity_at": _NOW, "last_changed": []},
        3: {"last_activity_at": _NOW, "last_changed": []},
    }
    assert [w["child"] for w in watch_report(children, cursors, _NOW)] == [3, 9]


def test_status_cli_includes_the_watch_report(tmp_path, monkeypatch, capsys):
    import status

    children = [{"number": 12, "state": "OPEN", "repo": "o/n", "status": "In Review",
                 "pr": {"state": "OPEN", "number": 34}, "branch": "feat/12"}]
    monkeypatch.setattr(status, "_fetch",
                        lambda repo, epic: (children, {"state": "OPEN", "status": None}))
    monkeypatch.setattr(status, "_now", lambda: _NOW)
    monkeypatch.setenv("EPIC_WATCH_DIR", str(tmp_path))
    watch_state.save("o/n", 34, {
        "fingerprint": {"head": "a"}, "step": 4, "errors": 0,
        "last_activity_at": "2026-08-22T10:13:00+00:00", "last_changed": ["checks"],
    }, str(tmp_path))

    code = status.main(["--epic", "1", "--repo", "o/n"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["watches"] == [
        {"child": 12, "pr": 34, "quiet_s": 2820, "last_activity": ["checks"]}
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "watch_report or watches" -v`
Expected: FAIL with `ImportError: cannot import name 'watch_report' from 'status'`.

- [ ] **Step 3: Write the implementation**

In `epic/scripts/status.py`, add `import datetime` and `import watch_state` at the top, then add the pure function beside `sweep_plan`:

```python
def watch_report(children, cursors, now_iso):
    """Live PR watches and how long each has been quiet.

    A long silence is reported here and NEVER parked — the run stays alive
    while an operator gets told which gate has gone quiet and for how long.
    """
    out = []
    for child in sorted(children or [], key=lambda c: c["number"]):
        pr = child.get("pr") or {}
        if pr.get("state") != "OPEN":
            continue
        cursor = (cursors or {}).get(child["number"])
        if not cursor or not cursor.get("last_activity_at"):
            continue
        out.append({
            "child": child["number"],
            "pr": pr.get("number"),
            "quiet_s": watch_state.elapsed_s(cursor["last_activity_at"], now_iso),
            "last_activity": list(cursor.get("last_changed") or []),
        })
    return out


def _now():
    """ISO-8601 UTC stamp. A monkeypatch point for the tests."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_cursors(children):
    """Watch cursors for every child with an open PR, keyed by child number."""
    cursors = {}
    for child in children or []:
        pr = child.get("pr") or {}
        if pr.get("state") == "OPEN" and pr.get("number"):
            cursors[child["number"]] = watch_state.load(child["repo"], pr["number"])
    return cursors
```

In `_fetch`, change the PR assignment to carry the number:

```python
            child["pr"] = {"state": pr["state"], "number": pr["number"]}
```

In `main()`, add the key to the result:

```python
    result = {
        "complete": epic_complete(children),
        "drift": drift(children, epic),
        "sweep_plan": sweep_plan(children) if args.sweep_plan else [],
        "watches": watch_report(children, _load_cursors(children), _now()),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_epic_scripts.py -k "watch_report or watches" -v`
Expected: PASS (6 tests).

Then: `python3 -m pytest tests/ -q`
Expected: PASS. The existing `status.py` CLI tests assert on specific keys, so an added `watches` key does not break them — if any test asserts exact payload equality, update it to include `"watches": []`.

- [ ] **Step 5: Commit**

```bash
git add epic/scripts/status.py tests/test_epic_scripts.py
git commit -m "feat(epic): report quiet PR watches in status

Names the child, the PR, how long it has been silent, and which facet
last moved. This replaces the deadline-to-park breaker: a slow reviewer
is surfaced, never a reason to abandon the child."
```

---

### Task 7: Rewrite the wait protocol in the skill docs

**Files:**
- Modify: `epic/skills/epic/SKILL.md`
- Modify: `epic/skills/epic/references/github-graphql.md`
- Modify: `README.md`
- Test: `tests/test_skills_lint.py` (existing; must stay green)

**Interfaces:**
- Consumes: the CLI surface produced by Tasks 4–6.
- Produces: no code.

This task carries no TDD cycle of its own — it is documentation — but the repo's skill linter must stay green, and no doc may reference a flag that no longer exists.

- [ ] **Step 1: Prove no stale references survive**

Run: `grep -rn -- "--await\|PR_WATCH_DEADLINE_S\|settled_event\|snapshot()" epic/ README.md docs/`
Expected before edits: hits in `SKILL.md`, `references/github-graphql.md`, `README.md`.
Expected after Step 2: hits only in `docs/durable-pr-watch-design.md` and `docs/durable-pr-watch-plan.md`, which describe the old behaviour on purpose.

- [ ] **Step 2: Edit `SKILL.md`**

Replace the `check-pending:<name>` bullet (currently at `SKILL.md:355`) with:

```markdown
   - `check-pending:<name>` → arm the watch and YIELD, never sleep and never
     block: `python epic/scripts/pr_watch.py --repo <owner/name> --pr <n>
     --reset-backoff`. It returns immediately with either
     `{"event":"activity",...}` (exit 0 — re-run `mergeability.py` and act) or
     `{"event":"waiting","next_tick_in_s":N}` (exit 1 — schedule the next tick
     at N seconds, clamped to your scheduler's range). There is no `--await`
     key to choose and no deadline: the watch reports ANY PR activity and
     `mergeability.py` stays the sole authority on what is actionable.
```

Replace the drive-subagent prose-gate sentence (currently at `SKILL.md:452`, the paragraph beginning "Prose-gate resolution stays inside the parallel region deliberately") with:

```markdown
**Prose-gate resolution yields rather than waits.** Bot-review latency (CI,
CodeRabbit, Copilot, Claude Review) is the largest single cost in a child's
lifecycle, but a drive subagent cannot survive a scheduled wake — so a
subagent that has nothing left to do but wait arms the watch and RETURNS with
a `waiting` outcome naming the PR and its `next_tick_in_s`. The run loop owns
every tick and re-dispatches a fresh drive subagent when activity fires. The
child is left at Status = In Review with an open PR, which is exactly the
state the run loop re-derives from `gh` on the next wake. Nothing sits idle,
and a killed session loses at most one cursor.
```

In the run-loop step 5 (`SKILL.md:448`), replace ONLY the first sentence — "Reschedule (via `ScheduleWakeup` if your harness supports scheduled wakeups; otherwise keep an in-session loop)." — with the text below. Leave the rest of step 5 (the epic-completion condition, the empty-children-fetch warning, and the TERMINATE clause) exactly as it stands:

```markdown
5. Reschedule. The wake delay is the MINIMUM `next_tick_in_s` across all live
   watches, clamped to your scheduler's range (`ScheduleWakeup` has a 60s
   floor, so early ticks clamp up; an in-session loop honours them as-is).
   One scheduler for the whole epic, never one per child.
```

In the Tunables block (`SKILL.md:516` area), delete `PR_WATCH_DEADLINE_S=3600` and add `WATCH_FLOOR_S=15`, `WATCH_MULT=1.8`, `WATCH_CEIL_S=900`. Replace the sentence beginning "CI/CodeRabbit/Copilot waits are bounded by `PR_WATCH_DEADLINE_S`" with:

```markdown
CI/CodeRabbit/Copilot waits are never bounded by a deadline. A watch ends only
when the PR stops being OPEN, when `pr_watch.py --stop` is run, or when the run
terminates. A long silence is REPORTED by `status.py` (`watches[]`: child, PR,
`quiet_s`, and the facet that last moved) and is never a reason to park.
```

In the Circuit breakers list, delete the deadline-to-park clause. The remaining breakers (convergence stall, gate unfixable, resource ceiling, subagent BLOCKED, armed-but-refusing) are unchanged.

- [ ] **Step 3: Edit `references/github-graphql.md`**

Replace the whole `### pr_watch.py --await vocabulary` section (currently `references/github-graphql.md:396` through the end of its known-divergence paragraph) with:

```markdown
### Waiting is a tick, never a sleep — `pr_watch.py`

`pr_watch.py` has no `--await` keys and no deadline. One invocation is one
tick: it loads a cursor, fetches the PR once, and exits.

```bash
python3 epic/scripts/pr_watch.py --repo <owner>/<repo> --pr <pr#> --reset-backoff
```

| Exit | Event | What the driver does |
|---|---|---|
| `0` | `{"event":"activity","changed":["reviews"],"head":…}` | Re-run `mergeability.py`, act on what is unmet |
| `0` | `{"event":"pr-closed","state":"MERGED"}` | Stop watching |
| `1` | `{"event":"waiting","next_tick_in_s":283,"quiet_s":1240}` | Schedule the next tick |
| `2` | `{"event":"error","detail":…,"consecutive":8}` | Sustained `gh` outage — diagnose |

`changed` names the facets that moved: `head`, `checks`, `reviews`, `threads`,
`comments`. Every review counts, `COMMENTED` included — that is what makes a
CodeRabbit or Copilot review visible, since neither submits a formal verdict.

`next_tick_in_s` is a REQUEST, not a command: clamp it to your scheduler's
range. The backoff runs 15 → 27 → 49 → 87 → 157 → 283 → 510 → 900 seconds,
jittered ±20% so parallel wave members desynchronise. It returns to the floor
only on a real head change; `--reset-backoff` forces the floor after a push,
`--resume-backoff` asserts the default of continuing from the stored step.

The cursor lives in `$EPIC_WATCH_DIR` (default `~/.cache/epic/watch`) and is
disposable — losing it costs one wasted fast-tier poll. `--stop` deletes it and
ends the watch.

Because the script no longer judges gates, the old divergence between its
`checks` key and the ruleset's required checks is gone: `mergeability.py` is
the sole authority on what is required, and `pr_watch.py` only reports that
something moved.
```

- [ ] **Step 4: Edit the `README.md` script table**

Replace the `pr_watch.py` row (currently `README.md:105`) with:

```markdown
| `pr_watch.py` | One tick of a PR watch: reports any activity (head, checks, reviews, threads, comments) or how long until the next tick. Never sleeps, never expires | `python3 epic/scripts/pr_watch.py --repo <owner/name> --pr <pr#> [--reset-backoff\|--resume-backoff] [--state-dir <dir>] [--stop]` | `0` activity observed, PR closed, or watch stopped; `1` no activity yet — schedule the next tick at `next_tick_in_s`; `2` eight consecutive `gh` failures |
```

- [ ] **Step 5: Verify and commit**

Run: `python3 -m pytest tests/ -q`
Expected: PASS, including `tests/test_skills_lint.py`.

Run: `grep -rn -- "--await\|PR_WATCH_DEADLINE_S" epic/ README.md`
Expected: no output.

```bash
git add epic/skills/epic/SKILL.md epic/skills/epic/references/github-graphql.md README.md
git commit -m "docs(epic): document the tick protocol and the yield rule

Waits arm a watch and yield to the run loop instead of blocking inside a
drive subagent. Replaces the --await vocabulary and retires
PR_WATCH_DEADLINE_S along with the deadline-to-park breaker."
```

---

## Verification

After Task 7, confirm the whole change end to end:

```bash
python3 -m pytest tests/ -q
grep -rn -- "--await\|PR_WATCH_DEADLINE_S\|settled_event" epic/ README.md
grep -rn "time.sleep" epic/scripts/
```

Expected: the suite passes; both greps produce no output.
