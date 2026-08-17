import json
import subprocess

import pytest

import gh
from config import (
    ConfigError,
    parse_epic_config,
    resolve_gates,
    resolve_project,
    validate_prefix,
)
from preflight import check
from schedule import runnable, became_ready_at, merge_queue, halt_reason, park_signature


def test_run_json_parses_stdout(monkeypatch):
    def fake_check_output(args, text, cwd, stderr):
        assert args[0] == "gh"
        assert stderr == subprocess.PIPE
        return json.dumps({"number": 101})

    monkeypatch.setattr(gh.subprocess, "check_output", fake_check_output)
    assert gh.run_json(["pr", "view", "101"]) == {"number": 101}


def test_run_json_raises_gherror_on_failure(monkeypatch):
    def fake_check_output(args, text, cwd, stderr):
        assert stderr == subprocess.PIPE
        raise subprocess.CalledProcessError(1, args, stderr="not found")

    monkeypatch.setattr(gh.subprocess, "check_output", fake_check_output)
    with pytest.raises(gh.GhError) as excinfo:
        gh.run_json(["pr", "view", "999"])
    assert excinfo.value.returncode == 1
    assert excinfo.value.stderr == "not found"


def test_graphql_unwraps_data(monkeypatch):
    monkeypatch.setattr(
        gh, "run_json", lambda args, cwd=None: {"data": {"repository": {"id": "R_1"}}}
    )
    assert gh.graphql("query {}") == {"repository": {"id": "R_1"}}


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


def test_check_ignores_worktrees_with_shared_prefix_segments():
    # Regression: dark-mode-v2-3 should not count toward dark-mode's cap
    # even though "dark-mode-v2-3".startswith("dark-mode-") is True
    trees = ["dark-mode-v2-3", "dark-mode-v2-4"]
    assert check("dark-mode", 12, trees, 1, False) == []


def test_check_ignores_unrelated_worktrees():
    # Non-owned worktrees do not count toward concurrency cap
    trees = ["other-epic-1"]
    assert check("dark-mode", 12, trees, 1, False) == []


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


def test_became_ready_at_all_na_gates_falls_back_to_opened_at():
    child_ = child(3, 0, pr={
        "number": 101,
        "state": "OPEN",
        "gates": {"claude-review": "na", "copilot": "na"},
        "gate_cleared_at": {},
        "opened_at": "2026-08-17T08:00:00Z"
    })
    assert became_ready_at(child_) == "2026-08-17T08:00:00Z"


def test_became_ready_at_empty_gates_dict_falls_back_to_opened_at():
    child_ = child(3, 0, pr={
        "number": 101,
        "state": "OPEN",
        "gates": {},
        "gate_cleared_at": {},
        "opened_at": "2026-08-17T08:00:00Z"
    })
    assert became_ready_at(child_) == "2026-08-17T08:00:00Z"


def test_became_ready_at_prefers_max_stamp_over_opened_at():
    child_ = child(3, 0, pr={
        "number": 101,
        "state": "OPEN",
        "gates": {"ci": "clean"},
        "gate_cleared_at": {"ci": "2026-08-17T10:00:00Z"},
        "opened_at": "2026-08-17T08:00:00Z"
    })
    assert became_ready_at(child_) == "2026-08-17T10:00:00Z"


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_became_ready_at_excludes_non_open_pr_state(state):
    child_ = child(3, 0, pr={
        "number": 101,
        "state": state,
        "gates": {"ci": "clean"},
        "gate_cleared_at": {"ci": "2026-08-17T10:00:00Z"},
    })
    assert became_ready_at(child_) is None


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
