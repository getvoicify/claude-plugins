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


# Task 7: mergeability tests

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


# Defect fixes from review


def test_blocked_state_with_no_other_requirements_is_reported():
    """Finding 1: BLOCKED state without derived requirements returns blocked-unexplained."""
    pr = {"mergeStateStatus": "BLOCKED", "isDraft": False, "statusCheckRollup": []}
    reqs = requirements(pr, {}, [])
    assert codes(reqs) == ["blocked-unexplained:BLOCKED"]
    assert is_clean(reqs) is False


def test_unstable_mergeable_state_not_blocked_unexplained():
    """Finding 1: UNSTABLE is mergeable, should not emit blocked-unexplained."""
    pr = {
        "mergeStateStatus": "UNSTABLE",
        "isDraft": False,
        "statusCheckRollup": [
            {"name": "non-required", "conclusion": "FAILURE", "status": "COMPLETED"}
        ],
    }
    reqs = requirements(pr, {}, [])
    # UNSTABLE is mergeable; no blocked-unexplained should be emitted
    assert "blocked-unexplained" not in codes(reqs)


def test_approval_missing_reported_without_ruleset_contents():
    """Finding 2: REVIEW_REQUIRED emits approval-missing independent of ruleset."""
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [],
        "reviewDecision": "REVIEW_REQUIRED",
    }
    reqs = requirements(pr, {}, [])
    assert "approval-missing" in codes(reqs)
    approval = [r for r in reqs if r["code"] == "approval-missing"][0]
    assert approval["action"] == "park-waiting-on-human"


def test_statuscontext_passing_check():
    """Finding 3: StatusContext with state SUCCESS is handled."""
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [
            {"context": "commit-status", "state": "SUCCESS"}
        ],
    }
    ruleset = {"required_status_checks": ["commit-status"]}
    reqs = requirements(pr, ruleset, [])
    # Passing status context should not emit any check requirement
    assert "commit-status" not in codes(reqs)
    # And it should not emit check-missing when it's required
    assert "check-missing:commit-status" not in codes(reqs)


def test_statuscontext_failing_check():
    """Finding 3: StatusContext with state FAILURE is handled as check-failing."""
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [
            {"context": "commit-status", "state": "FAILURE"}
        ],
    }
    reqs = requirements(pr, {}, [])
    assert codes(reqs) == ["check-failing:commit-status"]


def test_statuscontext_pending_check():
    """Finding 3: StatusContext with state PENDING is handled as check-pending."""
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [
            {"context": "commit-status", "state": "PENDING"}
        ],
    }
    reqs = requirements(pr, {}, [])
    assert codes(reqs) == ["check-pending:commit-status"]


def test_non_required_check_does_not_gate_when_required_checks_specified():
    """Finding 4: Non-required checks do not gate merge when ruleset declares required checks."""
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [
            {"name": "non-required", "conclusion": "FAILURE", "status": "COMPLETED"}
        ],
    }
    ruleset = {"required_status_checks": ["required-check"]}
    reqs = requirements(pr, ruleset, [])
    # Non-required check should not emit requirement
    assert "check-failing:non-required" not in codes(reqs)
    # Should only report missing required check
    assert codes(reqs) == ["check-missing:required-check"]


def test_all_checks_gate_when_required_checks_empty():
    """Finding 4: All checks gate merge when ruleset has no required checks (fail-closed)."""
    pr = {
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "statusCheckRollup": [
            {"name": "any-check", "conclusion": "FAILURE", "status": "COMPLETED"}
        ],
    }
    ruleset = {}
    reqs = requirements(pr, ruleset, [])
    # When no required checks are declared, all checks gate (fail-closed)
    assert codes(reqs) == ["check-failing:any-check"]


# Task 8: convergence and stall detection

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


# Task 9: pin verification

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


def test_classify_stale_when_symbol_is_substring_suffix():
    """Symbol 'refresh' is not verified when source contains only 'refreshToken'."""
    claim = parse_claims(PIN)[0]
    assert classify(claim, "fun refreshToken(): Result<Token> {}") == "stale"


def test_classify_stale_when_symbol_is_substring_prefix():
    """Symbol 'refresh' is not verified when source contains only '_refresh'."""
    claim = parse_claims(PIN)[0]
    assert classify(claim, "fun _refresh(): Result<Token> {}") == "stale"


def test_classify_verified_with_word_boundaries():
    """Symbol 'refresh' is verified when it appears as complete word."""
    claim = parse_claims(PIN)[0]
    assert classify(claim, "fun refresh(): Result<Token> {}") == "verified"


def test_classify_with_regex_metacharacter_symbol():
    """Symbols containing regex metacharacters match literally via re.escape()."""
    # Test with dot (.) which is a regex wildcard but should match literally
    claim = {"kind": "verified", "path": "file.py", "ref": "main",
             "symbol": "foo.bar", "text": "setup"}
    # Dot should match literally, not as a wildcard
    assert classify(claim, "self.foo.bar = 42") == "verified"
    # "fooXbar" should not match even though . matches any char in regex
    assert classify(claim, "self.fooXbar = 42") == "stale"


def test_parse_claims_empty_string():
    """parse_claims('') returns empty list without raising."""
    assert parse_claims("") == []


def test_parse_claims_none():
    """parse_claims(None) returns empty list without raising."""
    assert parse_claims(None) == []


# Task 10: pr_watch — responsive PR monitoring

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
