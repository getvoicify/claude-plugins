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
    # No other requirement is present, so the ONLY thing standing between an
    # empty requirement set and a spurious "blocked-unexplained:UNSTABLE" is
    # `_MERGEABLE_STATES` including UNSTABLE (an earlier fixture also carried
    # an unrelated failing check, which made `reqs` non-empty for reasons
    # having nothing to do with the state-membership check under test —
    # `not reqs` was already False regardless of UNSTABLE's membership, so
    # no mutation of `_MERGEABLE_STATES` could ever have flipped this
    # fixture's result; that check has been dropped so the test actually
    # isolates the behaviour its docstring claims to guard).
    pr = {
        "mergeStateStatus": "UNSTABLE",
        "isDraft": False,
        "statusCheckRollup": [],
    }
    reqs = requirements(pr, {}, [])
    # A bare `"blocked-unexplained" not in codes(reqs)` can never fail: every
    # real code carries a `:<state>` suffix (e.g. "blocked-unexplained:BLOCKED"),
    # so exact list membership on the unsuffixed string is vacuous — this
    # checks for any code with that PREFIX instead.
    # MUTATION: remove "UNSTABLE" from `_MERGEABLE_STATES` in mergeability.py
    # and this assertion fails (reqs becomes ["blocked-unexplained:UNSTABLE"]),
    # where the original vacuous form would not have.
    assert not any(c.startswith("blocked-unexplained") for c in codes(reqs))
    assert reqs == []


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
    # Passing status context should not emit any check requirement. A bare
    # `"commit-status" not in codes(reqs)` can never fail: real codes are
    # always prefixed (`check-failing:commit-status`, `check-pending:...`),
    # so the exact string "commit-status" never appears in the list either
    # way — this checks for the substring instead, so a regression that
    # wrongly emits check-failing/check-pending for a passing check is
    # actually caught.
    # MUTATION: in `requirements()`, change the StatusContext SUCCESS branch
    # from `pass` to `reqs.append(_req(f"check-failing:{name}", ...))` and
    # this assertion fails, where the original vacuous form would not have.
    assert not any("commit-status" in c for c in codes(reqs))
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


def test_fingerprint_ignores_code_span_content():
    # GUARDS: `_CODE_SPAN.sub` strips backtick-delimited code spans before
    # normalizing a claim, so two findings differing only in the SYMBOL named
    # inside backticks (a rename, a different call site quoted verbatim)
    # still fingerprint identically — the point being made is the same claim
    # ("missing retry"), not the exact code snippet.
    # MUTATION: delete the `_CODE_SPAN.sub(" ", claim or "")` line in
    # `_normalize` (i.e. skip straight to using `claim or ""`) and this
    # assertion fails, since the two claims would then differ by the code
    # span text (`foo()` vs `bar()`).
    assert fingerprint(finding("Missing retry in `foo()`")) == fingerprint(
        finding("Missing retry in `bar()`")
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


# Task 11: status.py — drift, sweep plan, completion

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
    kid = child(3, 0, state="CLOSED")
    kid["status"] = "Done"
    assert drift([kid], {"state": "OPEN", "status": "In Progress"}) == []


def test_drift_does_not_flag_closed_epic_with_open_children():
    kid = child(3, 0)  # OPEN — epic is closed early, an anomaly
    assert drift([kid], {"state": "CLOSED", "status": "In Progress"}) == []


def test_sweep_plan_removes_worktrees_only_for_merged_prs():
    merged = child(3, 0, state="CLOSED",
                   pr={"number": 101, "state": "MERGED"}, branch="dark-mode-3")
    open_pr = child(4, 1, pr={"number": 102, "state": "OPEN"},
                    branch="dark-mode-4")
    assert sweep_plan([merged, open_pr]) == [
        {"child": 3, "action": "remove-worktree", "branch": "dark-mode-3"}
    ]


def test_sweep_plan_skips_merged_pr_without_branch():
    no_branch = child(3, 0, state="CLOSED",
                      pr={"number": 101, "state": "MERGED"})
    assert sweep_plan([no_branch]) == []


def test_epic_complete_empty_list_is_true():
    assert epic_complete([]) is True


# Task 18: CLI entry points for the deterministic core.
#
# Every module gets a thin main(argv=None) -> int shell: parse args, fetch via
# gh (monkeypatched below — never a real network/gh call), call the existing
# pure function, print one JSON object, return an exit code (0 success, 1
# definite-negative, 2 usage/config error). Also: preflight's new
# `invalid-start` violation, and schedule's opened_at wiring.

import gh as gh_module


# --- gh.py: new I/O primitives (git facts + repo resolution) ---------------

def test_run_git_returns_stdout_text(monkeypatch):
    def fake_check_output(args, text, cwd, stderr):
        assert args[0] == "git"
        return "worktree /repo\nbranch refs/heads/main\n"
    monkeypatch.setattr(gh_module.subprocess, "check_output", fake_check_output)
    assert "worktree /repo" in gh_module.run_git(["worktree", "list", "--porcelain"])


def test_run_git_raises_gherror_on_failure(monkeypatch):
    def fake_check_output(args, text, cwd, stderr):
        raise subprocess.CalledProcessError(128, args, stderr="not a git repository")
    monkeypatch.setattr(gh_module.subprocess, "check_output", fake_check_output)
    with pytest.raises(gh.GhError):
        gh_module.run_git(["status"])


@pytest.mark.parametrize(
    "url, expected",
    [
        ("git@github.com:acme/planning.git", "acme/planning"),
        ("https://github.com/acme/planning.git", "acme/planning"),
        ("https://github.com/acme/planning", "acme/planning"),
    ],
)
def test_resolve_repo_from_cwd_parses_origin_url(monkeypatch, url, expected):
    monkeypatch.setattr(gh_module, "run_git", lambda args, cwd=None: url + "\n")
    assert gh_module.resolve_repo_from_cwd() == expected


def test_resolve_repo_from_cwd_raises_on_unparseable_url(monkeypatch):
    # GUARDS: the regex-match / raise branch. WOULD FAIL if the raise were
    # deleted (an unparseable URL would then crash later with a confusing
    # AttributeError/TypeError instead of a clean GhError).
    monkeypatch.setattr(gh_module, "run_git", lambda args, cwd=None: "not-a-url\n")
    with pytest.raises(gh.GhError):
        gh_module.resolve_repo_from_cwd()


# --- preflight.py: invalid-start violation on check() -----------------------

def test_check_flags_invalid_start_when_true():
    # GUARDS: the `if invalid_start:` branch itself. WOULD FAIL if the branch
    # were deleted or the code string were misspelled.
    assert check("dark-mode", 12, [], 3, False, invalid_start=True) == ["invalid-start"]


def test_check_invalid_start_defaults_to_false():
    # GUARDS: the default value of the new parameter. WOULD FAIL if the
    # default were flipped to True (every existing 5-arg caller would then
    # spuriously fail).
    assert check("dark-mode", 12, [], 3, False) == []


def test_check_invalid_start_combines_and_sorts_with_other_violations():
    # GUARDS: sorted() still applied after adding the 5th code, and the code
    # combines rather than short-circuits. WOULD FAIL if sorted() were removed
    # (list would come back in append order: worktree-exists, concurrency-cap,
    # nested-worktree, invalid-start) or if invalid_start were ignored when
    # other violations exist.
    result = check("dark-mode", 1, ["dark-mode-1"], 1, True, invalid_start=True)
    assert result == [
        "concurrency-cap",
        "invalid-start",
        "nested-worktree",
        "worktree-exists",
    ]


# --- preflight.py: CLI --------------------------------------------------

def _porcelain(entries):
    """Build `git worktree list --porcelain` text from (path, branch, detached) triples."""
    lines = []
    for path, branch, detached in entries:
        lines.append(f"worktree {path}")
        if detached:
            lines.append("detached")
        else:
            lines.append(f"branch refs/heads/{branch}")
        lines.append("")
    return "\n".join(lines) + "\n"


def test_preflight_main_prints_empty_violations_and_exits_zero(monkeypatch):
    import os
    from preflight import main

    cwd = os.getcwd()
    porcelain = _porcelain([(cwd, "feature-xyz", False)])
    monkeypatch.setattr(gh_module, "run_git", lambda args, cwd=None: porcelain)
    monkeypatch.setattr(
        gh_module, "run_json", lambda args, cwd=None: {"defaultBranchRef": {"name": "main"}}
    )
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--prefix", "dark-mode", "--child", "12", "--max-concurrent", "3"])
    assert rc == 0
    assert json.loads(captured[0]) == {"violations": []}


def test_preflight_main_flags_invalid_start_when_cwd_is_on_default_branch(monkeypatch):
    # GUARDS: the wiring from git facts -> invalid_start -> check(). WOULD
    # FAIL if main() stopped passing invalid_start through to check(), or if
    # the default-branch comparison used the wrong field.
    import os
    from preflight import main

    cwd = os.getcwd()
    porcelain = _porcelain([(cwd, "main", False)])
    monkeypatch.setattr(gh_module, "run_git", lambda args, cwd=None: porcelain)
    monkeypatch.setattr(
        gh_module, "run_json", lambda args, cwd=None: {"defaultBranchRef": {"name": "main"}}
    )
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--prefix", "dark-mode", "--child", "12", "--max-concurrent", "3"])
    assert rc == 1
    assert json.loads(captured[0]) == {"violations": ["invalid-start"]}


def test_preflight_main_exits_two_on_gherror(monkeypatch):
    from preflight import main

    def raise_gherror(args, cwd=None):
        raise gh.GhError(128, "fatal: not a git repository")
    monkeypatch.setattr(gh_module, "run_git", raise_gherror)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--prefix", "dark-mode", "--child", "12", "--max-concurrent", "3"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# --- config.py: CLI ------------------------------------------------------

BODY_NO_PROJECT = """Some prose.

```epic-config
epic: 42
repo: acme/planning
docs_repo: acme/app
worktree_prefix: dark-mode
spec: docs/dark-mode.md
runbook: docs/dark-mode-runbook.md
```

More prose.
"""


def test_config_main_resolves_and_prints_config(monkeypatch, tmp_path):
    from config import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "epic.yaml").write_text("planning:\n  project: 9\n", encoding="utf-8")

    monkeypatch.setattr(gh_module, "run_json", lambda args, cwd=None: {"body": BODY})
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    result = json.loads(captured[0])
    assert result["epic"] == 42
    assert result["worktree_prefix"] == "dark-mode"
    # D4 order: epic-config.project (7) wins over planning.project (9).
    assert result["project"] == 7


def test_config_main_falls_back_to_planning_project(monkeypatch, tmp_path):
    # GUARDS: main() passing (epic_cfg, planning) to resolve_project in the
    # documented order. WOULD FAIL if main() hardcoded epic-config's project
    # or never read Layer 2's `planning.project` at all — the epic-config here
    # has NO project field, so only the Layer-2 fallback can supply 9.
    from config import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "epic.yaml").write_text("planning:\n  project: 9\n", encoding="utf-8")

    monkeypatch.setattr(gh_module, "run_json", lambda args, cwd=None: {"body": BODY_NO_PROJECT})
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    assert json.loads(captured[0])["project"] == 9


def test_config_main_exits_two_on_config_error(monkeypatch, tmp_path):
    from config import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "epic.yaml").write_text("planning:\n  project: 9\n", encoding="utf-8")

    monkeypatch.setattr(gh_module, "run_json", lambda args, cwd=None: {"body": "no config here"})
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


def test_config_main_exits_two_when_no_layer2_yaml(monkeypatch, tmp_path):
    # GUARDS: the missing-epic.yaml ConfigError path specifically (as opposed
    # to a generic crash). WOULD FAIL if main() let a bare FileNotFoundError
    # propagate instead of catching ConfigError and exiting 2 cleanly.
    from config import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gh_module, "run_json", lambda args, cwd=None: {"body": BODY})
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


def test_config_main_exits_two_on_gherror(monkeypatch, tmp_path):
    from config import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "epic.yaml").write_text("planning:\n  project: 9\n", encoding="utf-8")

    def raise_gherror(args, cwd=None):
        raise gh.GhError(1, "issue not found")
    monkeypatch.setattr(gh_module, "run_json", raise_gherror)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# --- mergeability.py: CLI -------------------------------------------------

def test_mergeability_main_reports_clean_and_exits_zero(monkeypatch):
    from mergeability import main

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            return {"mergeStateStatus": "CLEAN", "isDraft": False,
                     "statusCheckRollup": [], "reviewDecision": "APPROVED"}
        if args[0] == "api":
            return []  # empty ruleset
        raise AssertionError(f"unexpected run_json call: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    monkeypatch.setattr(
        gh_module, "graphql",
        lambda query, **kw: {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}},
    )
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--repo", "acme/app", "--pr", "7"])
    assert rc == 0
    assert json.loads(captured[0]) == {"requirements": [], "clean": True}


def test_mergeability_main_reports_unmet_and_exits_one(monkeypatch):
    from mergeability import main

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            return {"mergeStateStatus": "BEHIND", "isDraft": False,
                     "statusCheckRollup": [], "reviewDecision": "APPROVED"}
        if args[0] == "api":
            return []
        raise AssertionError(f"unexpected run_json call: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    monkeypatch.setattr(
        gh_module, "graphql",
        lambda query, **kw: {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}},
    )
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--repo", "acme/app", "--pr", "7"])
    assert rc == 1
    result = json.loads(captured[0])
    assert result["clean"] is False
    assert result["requirements"][0]["code"] == "behind-base"


def test_mergeability_main_treats_404_ruleset_as_empty(monkeypatch):
    # GUARDS: the 404-swallowing branch in _fetch. WOULD FAIL if the 404
    # GhError were re-raised instead of treated as an empty ruleset — the CLI
    # would exit 2 on every repo with no branch-protection ruleset configured
    # at all, which the brief explicitly says is not an error.
    from mergeability import main

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            return {"mergeStateStatus": "CLEAN", "isDraft": False,
                     "statusCheckRollup": [], "reviewDecision": "APPROVED"}
        if args[0] == "api":
            raise gh.GhError(1, "HTTP 404: Not Found")
        raise AssertionError(f"unexpected run_json call: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    monkeypatch.setattr(
        gh_module, "graphql",
        lambda query, **kw: {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}},
    )
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--repo", "acme/app", "--pr", "7"])
    assert rc == 0
    assert json.loads(captured[0]) == {"requirements": [], "clean": True}


def test_mergeability_main_exits_two_on_non_404_gherror(monkeypatch):
    from mergeability import main

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            raise gh.GhError(1, "HTTP 500: Internal Server Error")
        raise AssertionError(f"unexpected run_json call: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--repo", "acme/app", "--pr", "7"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# --- converge.py: CLI ------------------------------------------------------

def test_converge_main_reads_files_and_exits_zero_on_converged(tmp_path):
    from converge import main

    prev = tmp_path / "prev.json"
    curr = tmp_path / "curr.json"
    prev.write_text(json.dumps([{"file": "a.py", "category": "bug", "claim": "x", "blocking": True}]))
    curr.write_text(json.dumps([]))

    result = _run_and_capture(main, ["--prev", str(prev), "--curr", str(curr)])
    assert result.rc == 0
    assert json.loads(result.out) == {"verdict": "converged"}


def test_converge_main_exits_one_on_no_progress(tmp_path):
    from converge import main

    finding = [{"file": "a.py", "category": "bug", "claim": "x", "blocking": True}]
    prev = tmp_path / "prev.json"
    curr = tmp_path / "curr.json"
    prev.write_text(json.dumps(finding))
    curr.write_text(json.dumps(finding))

    result = _run_and_capture(main, ["--prev", str(prev), "--curr", str(curr)])
    assert result.rc == 1
    assert json.loads(result.out) == {"verdict": "no_progress"}


def test_converge_main_exits_two_on_missing_file(tmp_path):
    from converge import main

    curr = tmp_path / "curr.json"
    curr.write_text("[]")

    result = _run_and_capture(main, ["--prev", str(tmp_path / "missing.json"), "--curr", str(curr)])
    assert result.rc == 2
    assert "error" in json.loads(result.out)


class _CapturedRun:
    def __init__(self, rc, out):
        self.rc = rc
        self.out = out


def _run_and_capture(main_fn, argv):
    captured = []
    real_print = print
    import builtins
    builtins.print = lambda x: captured.append(x)
    try:
        rc = main_fn(argv)
    finally:
        builtins.print = real_print
    return _CapturedRun(rc, captured[0] if captured else "")


# --- verify_pin.py: CLI ----------------------------------------------------

PIN_TEXT = """Pin notes.

- verified: epic/scripts/gh.py@main#run_json — run_json shells to gh
- assumption: the API rate limit resets hourly
"""

PIN_TEXT_STALE = """Pin notes.

- verified: epic/scripts/gh.py@main#totally_missing_symbol — this symbol exists
"""


def test_verify_pin_main_classifies_claims_and_exits_zero(monkeypatch, tmp_path):
    from verify_pin import main

    pin_file = tmp_path / "pin.md"
    pin_file.write_text(PIN_TEXT, encoding="utf-8")

    def fake_run_json(args, cwd=None):
        assert args[0] == "api"
        import base64
        return {"content": base64.b64encode(b"def run_json(): pass\n").decode()}
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--pin", str(pin_file), "--repo", "acme/app"])
    assert rc == 0
    result = json.loads(captured[0])
    verdicts = {c["verdict"] for c in result["claims"]}
    assert verdicts == {"verified", "assumption"}


def test_verify_pin_main_exits_one_when_any_claim_stale(monkeypatch, tmp_path):
    from verify_pin import main

    pin_file = tmp_path / "pin.md"
    pin_file.write_text(PIN_TEXT_STALE, encoding="utf-8")

    def fake_run_json(args, cwd=None):
        import base64
        return {"content": base64.b64encode(b"def run_json(): pass\n").decode()}
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--pin", str(pin_file), "--repo", "acme/app"])
    assert rc == 1
    result = json.loads(captured[0])
    assert result["claims"][0]["verdict"] == "stale"


def test_verify_pin_main_unresolvable_path_is_unverifiable_not_error(monkeypatch, tmp_path):
    # GUARDS: the distinction the brief calls load-bearing — a content-fetch
    # 404 must classify as "unverifiable" and exit 0/1 normally, never surface
    # as a CLI error (exit 2). WOULD FAIL if a GhError from the content fetch
    # were allowed to propagate out of main() uncaught.
    from verify_pin import main

    pin_file = tmp_path / "pin.md"
    pin_file.write_text(PIN_TEXT, encoding="utf-8")

    def raise_404(args, cwd=None):
        raise gh.GhError(1, "HTTP 404: Not Found")
    monkeypatch.setattr(gh_module, "run_json", raise_404)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--pin", str(pin_file), "--repo", "acme/app"])
    assert rc == 0
    result = json.loads(captured[0])
    verdicts = {c["verdict"] for c in result["claims"]}
    assert verdicts == {"unverifiable", "assumption"}


def test_verify_pin_main_resolves_repo_from_cwd_when_not_given(monkeypatch, tmp_path):
    # GUARDS: the SKILL.md-literal invocation `verify_pin.py --pin <file>`
    # with NO --repo. WOULD FAIL if --repo were made required (argparse would
    # error out before this ever ran), which is exactly the failure mode the
    # brief warns the whole task is about.
    from verify_pin import main

    pin_file = tmp_path / "pin.md"
    pin_file.write_text(PIN_TEXT, encoding="utf-8")

    monkeypatch.setattr(gh_module, "resolve_repo_from_cwd", lambda cwd=None: "acme/app")

    def fake_run_json(args, cwd=None):
        import base64
        return {"content": base64.b64encode(b"def run_json(): pass\n").decode()}
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--pin", str(pin_file)])
    assert rc == 0


def test_verify_pin_main_exits_two_on_missing_pin_file(tmp_path):
    from verify_pin import main

    captured = []
    result = _run_and_capture(main, ["--pin", str(tmp_path / "missing.md"), "--repo", "acme/app"])
    assert result.rc == 2
    assert "error" in json.loads(result.out)


# --- schedule.py: CLI --------------------------------------------------

def test_schedule_main_prints_wave_queue_and_halt(monkeypatch):
    from schedule import main

    children_resp = {
        "repository": {"issue": {"subIssues": {"nodes": [
            {"number": 3, "state": "OPEN", "blockedBy": {"nodes": []},
             "projectItems": {"nodes": [{"status": {"name": "Todo"}, "priority": None}]}},
        ]}}}
    }
    pr_map_resp = {"repository": {"pullRequests": {"nodes": []}}}
    graphql_calls = iter([children_resp, pr_map_resp])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(graphql_calls))
    monkeypatch.setattr(gh_module, "run_json", lambda args, cwd=None: (_ for _ in ()).throw(
        AssertionError(f"unexpected run_json: {args}")))
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    result = json.loads(captured[0])
    assert result["wave"] == [3]
    assert result["merge_queue"] == []
    assert result["halt"] is None


def test_schedule_main_populates_opened_at_from_pr_view(monkeypatch):
    # GUARDS: the exact wiring the task calls out as "only half-effective
    # until the CLI supplies the field" — a child whose PR has no gates
    # recorded must still enter the merge queue via the opened_at fallback.
    # WOULD FAIL if main()/_populate_prs stopped calling `gh pr view --json
    # createdAt` (opened_at would stay None, became_ready_at would return
    # None for an otherwise-clean PR, and the child would silently vanish
    # from merge_queue instead of appearing).
    from schedule import main

    children_resp = {
        "repository": {"issue": {"subIssues": {"nodes": [
            {"number": 3, "state": "OPEN", "blockedBy": {"nodes": []},
             "projectItems": {"nodes": [{"status": {"name": "In Review"}, "priority": None}]}},
        ]}}}
    }
    pr_map_resp = {"repository": {"pullRequests": {"nodes": [
        {"number": 101, "state": "OPEN", "headRefName": "dark-mode-3",
         "closingIssuesReferences": {"nodes": [{"number": 3}]}},
    ]}}}
    threads_empty = {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}
    graphql_calls = iter([children_resp, pr_map_resp, threads_empty])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(graphql_calls))

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            # A genuinely gate-free PR: clean merge state, no checks yet
            # posted, no reviews yet posted — real `_compute_gates` derives
            # every gate as "clean" with NO cleared_at stamp (nothing to
            # date a "went clean" moment from), so `became_ready_at` must
            # still fall back to `opened_at`.
            return {
                "createdAt": "2026-01-01T00:00:00Z",
                "mergeStateStatus": "CLEAN",
                "isDraft": False,
                "statusCheckRollup": [],
                "reviewDecision": None,
                "reviews": [],
            }
        if args[:2] == ["issue", "view"]:
            return {"comments": []}
        if args[:1] == ["api"]:
            return []
        raise AssertionError(f"unexpected run_json: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    result = json.loads(captured[0])
    # A clean-but-never-cleared child (no stamps in gate_cleared_at) reaches
    # the merge queue only because opened_at was populated for the FIFO
    # fallback.
    assert result["merge_queue"] == [3]


# --- schedule.py: CRITICAL fix — the merge queue must be FIFO on gate
# readiness, not on PR-open time. Before this fix `_populate_prs` hardcoded
# `"gates": {}, "gate_cleared_at": {}` for every PR, so `became_ready_at`
# always fell through to `opened_at` regardless of real gate state — a
# gate-pending PR that opened first held the head slot ahead of a
# merge-ready sibling, AND (worse) schedule.py had no way to tell the
# orchestrator that a child's prose gates were still being resolved.

def test_schedule_main_excludes_pending_gate_pr_and_orders_by_readiness_not_open_time(monkeypatch):
    # Child 2's PR opened FIRST (2026-01-01) but has a check still
    # IN_PROGRESS. Child 9's PR opened SECOND (2026-01-02) and is fully
    # clean (checks green, review approved). A correct FIFO-ON-READINESS
    # queue admits ONLY child 9 — child 2 is excluded entirely (it is still
    # being driven by its own subagent), not merely sorted after.
    # MUTATION: revert `_populate_prs` to hardcode `gates={}` and
    # `cleared={}` for every PR (the pre-fix behaviour) — both children
    # would then look all-clean via the opened_at fallback, and
    # merge_queue would become [2, 9] (raw open-time order) instead of the
    # correct [9]. This is the exact concurrency-collision bug: PR 201
    # would be admitted to the merge phase while its own drive subagent is
    # still resolving its pending check.
    from schedule import main

    children_resp = {
        "repository": {"issue": {"subIssues": {"nodes": [
            {"number": 2, "state": "OPEN", "blockedBy": {"nodes": []},
             "projectItems": {"nodes": [{"status": {"name": "In Review"}, "priority": None}]}},
            {"number": 9, "state": "OPEN", "blockedBy": {"nodes": []},
             "projectItems": {"nodes": [{"status": {"name": "In Review"}, "priority": None}]}},
        ]}}}
    }
    pr_map_resp = {"repository": {"pullRequests": {"nodes": [
        {"number": 201, "state": "OPEN", "headRefName": "x-2",
         "closingIssuesReferences": {"nodes": [{"number": 2}]}},
        {"number": 209, "state": "OPEN", "headRefName": "x-9",
         "closingIssuesReferences": {"nodes": [{"number": 9}]}},
    ]}}}
    threads_empty = {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}
    graphql_calls = iter([children_resp, pr_map_resp, threads_empty, threads_empty])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(graphql_calls))

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            pr_number = args[2]
            if pr_number == "201":
                return {
                    "createdAt": "2026-01-01T00:00:00Z",
                    "mergeStateStatus": "BLOCKED",
                    "isDraft": False,
                    "statusCheckRollup": [
                        {"name": "unit", "status": "IN_PROGRESS", "conclusion": None},
                    ],
                    "reviewDecision": None,
                    "reviews": [],
                }
            return {
                "createdAt": "2026-01-02T00:00:00Z",
                "mergeStateStatus": "CLEAN",
                "isDraft": False,
                "statusCheckRollup": [
                    {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS",
                     "completedAt": "2026-01-02T01:00:00Z"},
                ],
                "reviewDecision": "APPROVED",
                "reviews": [
                    {"author": {"login": "reviewer"}, "state": "APPROVED",
                     "submittedAt": "2026-01-02T00:30:00Z"},
                ],
            }
        if args[:1] == ["api"]:
            return []
        raise AssertionError(f"unexpected run_json: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    result = json.loads(captured[0])
    assert result["merge_queue"] == [9]


def test_compute_gates_pending_check_is_pending_not_clean():
    # MUTATION: delete the `if has("check-pending:")` branch in
    # `_compute_gates` (falling through to the "clean" else-branch) and this
    # assertion fails.
    from schedule import _compute_gates

    pr_detail = {
        "mergeStateStatus": "BLOCKED", "isDraft": False,
        "statusCheckRollup": [{"name": "unit", "status": "IN_PROGRESS", "conclusion": None}],
        "reviewDecision": None,
    }
    gates, cleared = _compute_gates(pr_detail, {}, [], [])
    assert gates["checks"] == "pending"
    assert "checks" not in cleared


def test_compute_gates_failing_check_is_red():
    # MUTATION: change the `elif any(has(p) for p in _REQUIRED_CHECK_FAIL_PREFIXES)`
    # branch's result from "red" to "clean" and this assertion fails.
    from schedule import _compute_gates

    pr_detail = {
        "mergeStateStatus": "BLOCKED", "isDraft": False,
        "statusCheckRollup": [
            {"name": "unit", "status": "COMPLETED", "conclusion": "FAILURE"},
        ],
        "reviewDecision": None,
    }
    gates, _ = _compute_gates(pr_detail, {}, [], [])
    assert gates["checks"] == "red"


def test_compute_gates_clean_checks_capture_latest_completed_at():
    # MUTATION: change `_latest`'s `max(values)` to `min(values)` (or
    # `values[0]`) and this assertion fails — it would return the EARLIER
    # timestamp instead of the latest one, breaking D3's "latest
    # gate-clearing timestamp" FIFO ordering.
    from schedule import _compute_gates

    pr_detail = {
        "mergeStateStatus": "CLEAN", "isDraft": False,
        "statusCheckRollup": [
            {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS",
             "completedAt": "2026-01-01T00:00:00Z"},
            {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS",
             "completedAt": "2026-01-02T00:00:00Z"},
        ],
        "reviewDecision": None,
    }
    gates, cleared = _compute_gates(pr_detail, {}, [], [])
    assert gates["checks"] == "clean"
    assert cleared["checks"] == "2026-01-02T00:00:00Z"


def test_compute_gates_changes_requested_review_is_red():
    # MUTATION: swap the "red"/"pending" results between the
    # changes-requested and approval-missing branches and this assertion
    # fails.
    from schedule import _compute_gates

    pr_detail = {
        "mergeStateStatus": "BLOCKED", "isDraft": False,
        "statusCheckRollup": [], "reviewDecision": "CHANGES_REQUESTED",
    }
    gates, _ = _compute_gates(pr_detail, {}, [], [])
    assert gates["review"] == "red"


def test_compute_gates_draft_pr_is_red():
    # MUTATION: delete the `gates["draft"] = "red" if ... else "clean"` line
    # (dropping the "draft" key, or hardcoding "clean") and this assertion
    # fails.
    from schedule import _compute_gates

    pr_detail = {
        "mergeStateStatus": "CLEAN", "isDraft": True,
        "statusCheckRollup": [], "reviewDecision": None,
    }
    gates, _ = _compute_gates(pr_detail, {}, [], [])
    assert gates["draft"] == "red"


def test_compute_gates_unresolved_thread_is_pending():
    # MUTATION: invert the `has("thread-unresolved:")` ternary in
    # `_compute_gates` and this assertion fails.
    from schedule import _compute_gates

    pr_detail = {
        "mergeStateStatus": "BLOCKED", "isDraft": False,
        "statusCheckRollup": [], "reviewDecision": None,
    }
    threads = [{"id": "t1", "isResolved": False, "path": "a.py"}]
    gates, _ = _compute_gates(pr_detail, {}, threads, [])
    assert gates["threads"] == "pending"


def test_schedule_main_reports_halt_from_parks(monkeypatch):
    from schedule import main

    def make_child(n):
        return {"number": n, "state": "OPEN", "blockedBy": {"nodes": []},
                "projectItems": {"nodes": [{"status": {"name": "Parked"}, "priority": None}]}}

    children_resp = {
        "repository": {"issue": {"subIssues": {"nodes": [make_child(n) for n in (1, 2, 3)]}}}
    }
    pr_map_resp = {"repository": {"pullRequests": {"nodes": []}}}
    graphql_calls = iter([children_resp, pr_map_resp])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(graphql_calls))

    park_comment = {
        "body": (
            "FAILED: ci-fix-loop stuck on flaky test\n\n"
            'epic-park: {"code":"stall","gate":"ci-fix-loop","signature":"abc123",'
            '"waiting_on_human":false}'
        )
    }

    def fake_run_json(args, cwd=None):
        assert args[:2] == ["issue", "view"]
        return {"comments": [park_comment]}
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 1
    result = json.loads(captured[0])
    assert result["halt"] is not None
    assert result["halt"].startswith("systemic:")


def test_schedule_main_finds_pr_and_park_for_child_homed_in_a_different_repo(monkeypatch):
    # GUARDS item 5: a child living in a different repo than the epic's own
    # (SKILL.md: "Children may live in a different repo than the cwd") must
    # get its PR-map lookup AND its parked-issue lookup run against ITS OWN
    # repo, not the epic's home repo. Without this, such a child never shows
    # a PR (so it stays eligible and re-enters the wave forever, per the
    # task's regression note) and a `epic-park:` trailer on its issue is
    # never read (so a systemic park pattern involving it is invisible).
    # MUTATION: revert `_fetch_pr_maps`/`_fetch_parks` to the single-repo
    # form (`_fetch_pr_map(args.repo)` / `_fetch_parks(args.repo, children)`)
    # and this test fails — the mock's own assertion on `owner`/`name`
    # catches the wrong-repo call directly, and even if it didn't, child 7's
    # PR would never be found (empty merge_queue) and its park never read.
    from schedule import main

    children_resp = {
        "repository": {"issue": {"subIssues": {"nodes": [
            {"number": 7, "state": "OPEN",
             "repository": {"nameWithOwner": "acme/worker"},
             "blockedBy": {"nodes": []},
             "projectItems": {"nodes": [{"status": {"name": "Parked"}, "priority": None}]}},
        ]}}}
    }
    pr_map_worker = {"repository": {"pullRequests": {"nodes": []}}}
    park_comment = {
        "body": (
            "FAILED: ci-fix-loop stuck on flaky test\n\n"
            'epic-park: {"code":"stall","gate":"ci-fix-loop","signature":"abc123",'
            '"waiting_on_human":false}'
        )
    }

    def fake_graphql(query, **kw):
        if "epic" in kw:
            assert (kw["owner"], kw["name"]) == ("acme", "planning")
            return children_resp
        assert (kw["owner"], kw["name"]) == ("acme", "worker"), kw
        return pr_map_worker
    monkeypatch.setattr(gh_module, "graphql", fake_graphql)

    def fake_run_json(args, cwd=None):
        assert args[:2] == ["issue", "view"]
        repo_index = args.index("--repo") + 1
        assert args[repo_index] == "acme/worker", args
        return {"comments": [park_comment]}
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    result = json.loads(captured[0])
    # Only ONE park (below the systemic-signature threshold of 3), so the
    # halt comes from the ordinary "no eligible unparked child" path, not
    # "systemic:<sig>" — this confirms the park WAS read (a lone OPEN+parked
    # child with nothing else runnable halts as "no-runnable-work"; if the
    # park comment had never been fetched at all, `parked` status alone
    # already drives this same halt, so the REAL proof the fetch ran against
    # the right repo is the mock's own assertion above, not this halt value).
    assert result["halt"] == "no-runnable-work"
    assert rc == 1


def test_schedule_main_exits_two_on_gherror(monkeypatch):
    from schedule import main

    def raise_gherror(query, **kw):
        raise gh.GhError(1, "epic not found")
    monkeypatch.setattr(gh_module, "graphql", raise_gherror)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# --- status.py: CLI ---------------------------------------------------

def test_status_main_reports_complete_and_drift(monkeypatch):
    from status import main

    data = {
        "repository": {"issue": {
            "state": "OPEN",
            "projectItems": {"nodes": [{"status": {"name": "In Progress"}}]},
            "subIssues": {"nodes": [
                {"number": 3, "state": "CLOSED",
                 "projectItems": {"nodes": [{"status": {"name": "In Review"}}]}},
            ]},
        }}
    }
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    # graphql is called twice by status.py's own design (issue+children, then
    # a separate PR-map query) — supply both via a queue instead of a single
    # fixed return value.
    responses = iter([data, {"repository": {"pullRequests": {"nodes": []}}}])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(responses))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    result = json.loads(captured[0])
    # The one child is CLOSED, so the epic is complete — but its Status field
    # still says "In Review" instead of "Done", which is exactly the drift
    # reality-vs-Project-field case drift() reports.
    assert result["complete"] is True
    assert result["drift"] == [
        {"target": "child:3", "field": "status", "actual": "In Review", "expected": "Done"}
    ]
    assert result["sweep_plan"] == []


def test_status_main_sweep_plan_only_when_flag_passed(monkeypatch):
    # GUARDS: --sweep-plan gating. WOULD FAIL if sweep_plan were always
    # computed and returned regardless of the flag, or never computed even
    # with the flag.
    from status import main

    data = {
        "repository": {"issue": {
            "state": "OPEN",
            "projectItems": {"nodes": []},
            "subIssues": {"nodes": [
                {"number": 3, "state": "CLOSED",
                 "projectItems": {"nodes": [{"status": {"name": "Done"}}]}},
            ]},
        }}
    }
    pr_map = {"repository": {"pullRequests": {"nodes": [
        {"number": 101, "state": "MERGED", "headRefName": "dark-mode-3",
         "closingIssuesReferences": {"nodes": [{"number": 3}]}},
    ]}}}

    responses_no_flag = iter([data, pr_map])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(responses_no_flag))
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))
    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 0
    assert json.loads(captured[0])["sweep_plan"] == []

    responses_with_flag = iter([data, pr_map])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(responses_with_flag))
    captured2 = []
    monkeypatch.setattr("builtins.print", lambda x: captured2.append(x))
    rc = main(["--epic", "42", "--repo", "acme/planning", "--sweep-plan"])
    assert rc == 0
    assert json.loads(captured2[0])["sweep_plan"] == [
        {"child": 3, "action": "remove-worktree", "branch": "dark-mode-3"}
    ]


def test_status_main_finds_pr_for_child_homed_in_a_different_repo(monkeypatch):
    # GUARDS item 5: a child living in a different repo than the epic's own
    # (SKILL.md: "Children may live in a different repo than the cwd") must
    # still get its PR/branch populated — from a PR-map query run against
    # the CHILD's repo, not silently against the epic's home repo only.
    # MUTATION: revert `_fetch` to build a single `_fetch_pr_map(repo)` (the
    # epic's own repo) instead of `_fetch_pr_maps` keyed per child repo, and
    # this test fails: child 7's PR lives in "acme/worker", which the epic's
    # own "acme/planning" PR-map query would never see, so `child["pr"]`
    # would stay None and `sweep_plan` would stay empty.
    from status import main

    data = {
        "repository": {"issue": {
            "state": "OPEN",
            "projectItems": {"nodes": []},
            "subIssues": {"nodes": [
                {"number": 7, "state": "CLOSED",
                 "repository": {"nameWithOwner": "acme/worker"},
                 "projectItems": {"nodes": [{"status": {"name": "Done"}}]}},
            ]},
        }}
    }
    pr_map_worker = {"repository": {"pullRequests": {"nodes": [
        {"number": 55, "state": "MERGED", "headRefName": "child-7",
         "closingIssuesReferences": {"nodes": [{"number": 7}]}},
    ]}}}

    def fake_graphql(query, **kw):
        if "epic" in kw:
            assert (kw["owner"], kw["name"]) == ("acme", "planning")
            return data
        # The PR-map query MUST run against the CHILD's own repo
        # (acme/worker) — asserting it here (rather than trusting whichever
        # response a repo-agnostic queue would hand back) is what makes this
        # test actually fail if `_fetch` reverts to querying only the
        # epic's home repo.
        assert (kw["owner"], kw["name"]) == ("acme", "worker"), kw
        return pr_map_worker
    monkeypatch.setattr(gh_module, "graphql", fake_graphql)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning", "--sweep-plan"])
    assert rc == 0
    result = json.loads(captured[0])
    assert result["sweep_plan"] == [
        {"child": 7, "action": "remove-worktree", "branch": "child-7"}
    ]


def test_status_main_exits_two_on_gherror(monkeypatch):
    from status import main

    def raise_gherror(query, **kw):
        raise gh.GhError(1, "epic not found")
    monkeypatch.setattr(gh_module, "graphql", raise_gherror)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# --- Review fixes: 4 defects + 2 minor follow-ups ---------------------------

# 1 (Critical): verify_pin.py's content fetch must issue a real GET. `-f`
# switches `gh api` to POST, which the contents endpoint rejects — this test
# pins the exact shape of the call so a future edit cannot silently
# reintroduce `-f`.

def test_verify_pin_fetch_source_issues_get_not_post(monkeypatch):
    # Asserts the EXACT args list gh.run_json receives, not a substring
    # check — a substring check on "ref=main" would still pass a
    # regression to `-F ref=main` (still a POST) or an added `-X POST`,
    # since those flags/values contain the same substring. Only an exact
    # match is airtight against that class of silent reintroduction.
    from verify_pin import _fetch_source
    import base64

    captured = []

    def fake_run_json(args, cwd=None):
        captured.append(args)
        return {"content": base64.b64encode(b"some content").decode()}

    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    _fetch_source("acme/app", "epic/scripts/gh.py", "main")

    assert captured[0] == ["api", "repos/acme/app/contents/epic/scripts/gh.py?ref=main"]


def test_verify_pin_fetch_source_url_encodes_special_characters_in_ref(monkeypatch):
    # An unencoded `&`/`#`/`/` in the ref corrupts the query string (`&`
    # starts a new query param, `#` starts a fragment GitHub's API would
    # never see). The ref must be percent-encoded before it lands in the URL.
    from verify_pin import _fetch_source
    import base64

    captured = []

    def fake_run_json(args, cwd=None):
        captured.append(args)
        return {"content": base64.b64encode(b"some content").decode()}

    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    _fetch_source("acme/app", "epic/scripts/gh.py", "feature/foo&bar#1")

    assert captured[0] == [
        "api",
        "repos/acme/app/contents/epic/scripts/gh.py?ref=feature%2Ffoo%26bar%231",
    ]


# 2 (Important): preflight worktree resolution must match the LONGEST path
# prefix, not the first match — otherwise a linked worktree nested under the
# main checkout (this repo's own layout) matches the main entry first, and
# the nested-worktree HARD check silently never fires.

def test_preflight_fetch_matches_longest_worktree_path_prefix(monkeypatch):
    from preflight import _fetch

    main_path = "/repo"
    linked_path = "/repo/.claude/worktrees/child-1"
    porcelain = _porcelain([(main_path, "main", False), (linked_path, "dark-mode-1", False)])
    monkeypatch.setattr(gh_module, "run_git", lambda args, cwd=None: porcelain)
    monkeypatch.setattr(
        gh_module, "run_json", lambda args, cwd=None: {"defaultBranchRef": {"name": "main"}}
    )
    monkeypatch.setattr("preflight.os.getcwd", lambda: linked_path)

    names, inside_worktree, invalid_start = _fetch("dark-mode")
    # cwd is inside the LINKED worktree (deeper, longer-path match), not the
    # main checkout whose path merely happens to be a string prefix of it.
    assert inside_worktree is True
    # And invalid_start must come from the linked worktree's OWN branch
    # (dark-mode-1, not the default branch "main" borrowed from main_entry).
    assert invalid_start is False


# 4 (Important): runnable() must exclude children already being driven (an
# In Progress status with no PR yet) from its own eligible set, so the set
# schedule.py counts as in_flight and the set runnable() treats as eligible
# are disjoint. Without this, an In Progress child both consumes a capacity
# slot AND re-appears in the wave, starving a Todo sibling despite free
# capacity.

def test_runnable_excludes_children_already_in_flight():
    in_progress = child(1, 0, status="In Progress")
    todo = child(2, 1, status="Todo")
    # max_parallel=2, in_flight=1 (child 1 is the one in flight) — capacity
    # for NEW starts is 1. Without the fix, child 1 (In Progress) is also
    # eligible and sorts first by position, starving child 2 even though
    # there is free capacity for it.
    assert runnable([in_progress, todo], max_parallel=2, in_flight=1) == [2]


def test_runnable_still_includes_todo_children_without_status():
    # GUARDS backward compatibility: existing callers/tests build children
    # via child() with NO "status" key at all. c.get("status") must default
    # to something that never equals "In Progress" (None), not exclude
    # everything.
    kids = [child(5, 1), child(3, 0)]
    assert runnable(kids, 3, 0) == [3, 5]


# Minor: preflight's default-branch lookup must fail CLOSED (exit 2), not
# silently fail open with default_branch=None (which would make the
# detached/default-branch half of invalid-start unenforceable whenever
# `gh repo view` errors).

def test_preflight_main_exits_two_when_default_branch_lookup_fails(monkeypatch):
    import os
    from preflight import main

    cwd = os.getcwd()
    porcelain = _porcelain([(cwd, "feature-xyz", False)])
    monkeypatch.setattr(gh_module, "run_git", lambda args, cwd=None: porcelain)

    def raise_gherror(args, cwd=None):
        raise gh.GhError(1, "HTTP 500: Internal Server Error")
    monkeypatch.setattr(gh_module, "run_json", raise_gherror)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--prefix", "dark-mode", "--child", "12", "--max-concurrent", "3"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# Minor: schedule.py's per-PR `gh pr view --json createdAt` call must not
# swallow a GhError into a silently-None opened_at (which re-strands the
# child from the merge queue exactly like the bug this task fixed) — it
# must surface as a normal CLI error instead.

def test_schedule_main_surfaces_gherror_from_pr_view_instead_of_swallowing(monkeypatch):
    from schedule import main

    children_resp = {
        "repository": {"issue": {"subIssues": {"nodes": [
            {"number": 3, "state": "OPEN", "blockedBy": {"nodes": []},
             "projectItems": {"nodes": [{"status": {"name": "In Review"}, "priority": None}]}},
        ]}}}
    }
    pr_map_resp = {"repository": {"pullRequests": {"nodes": [
        {"number": 101, "state": "OPEN", "headRefName": "dark-mode-3",
         "closingIssuesReferences": {"nodes": [{"number": 3}]}},
    ]}}}
    graphql_calls = iter([children_resp, pr_map_resp])
    monkeypatch.setattr(gh_module, "graphql", lambda query, **kw: next(graphql_calls))

    def fake_run_json(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            raise gh.GhError(1, "HTTP 500: Internal Server Error")
        raise AssertionError(f"unexpected run_json: {args}")
    monkeypatch.setattr(gh_module, "run_json", fake_run_json)
    captured = []
    monkeypatch.setattr("builtins.print", lambda x: captured.append(x))

    rc = main(["--epic", "42", "--repo", "acme/planning"])
    assert rc == 2
    assert "error" in json.loads(captured[0])


# --- Round 2: halt_reason regression from the runnable() disjointness fix --
#
# Excluding In-Progress/no-PR children from runnable()'s eligible set (round
# 1, defect 4) was correct, but halt_reason()'s live-work escape only knew
# about the open-PR form of "live but not runnable" (see the comment at
# schedule.py's open-PR check). An In-Progress/no-PR child now falls into
# NEITHER runnable() NOR the escape, so it was misread as a genuine blocker
# and halt_reason returned "transitive-block" during the entirely normal
# window between dispatch and PR-open.

def test_no_halt_for_in_progress_child_without_pr_yet():
    # GUARDS: the widened live-work escape recognizing In Progress (no PR
    # yet) as live work, same as an open PR. WOULD FAIL (returns
    # "transitive-block" instead of None) if the escape were reverted to
    # only check for an open PR.
    kids = [child(3, 0, status="In Progress")]
    assert halt_reason(kids, [], 3) is None


def test_halt_escape_does_not_swallow_genuine_transitive_block():
    # GUARDS against an over-broad fix: a parked child transitively blocking
    # its sibling, with NO live work (no open PR, no In Progress child)
    # anywhere, must still halt. Re-asserts test_halt_when_nothing_runnable_
    # and_epic_incomplete's fixture explicitly in this round-2 section so the
    # "did the escape get too broad" question has its own direct answer here.
    kids = [child(3, 0, parked=True), child(4, 1, blocked_by=[3])]
    assert halt_reason(kids, [], 3) == "transitive-block"


def test_halt_escape_does_not_swallow_genuine_no_runnable_work():
    # GUARDS against an over-broad fix: an all-parked epic with no live work
    # anywhere must still halt with "no-runnable-work".
    kids = [child(3, 0, parked=True)]
    assert halt_reason(kids, [], 3) == "no-runnable-work"


def test_no_halt_while_a_child_is_in_flight_with_open_pr_still_passes():
    # Re-affirms the PRE-EXISTING open-PR escape keeps working unchanged
    # after widening the condition to an OR.
    kids = [child(3, 0, pr={"number": 101, "state": "OPEN"})]
    assert halt_reason(kids, [], 3) is None


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


def test_load_rejects_wrong_type_for_step_and_keeps_defaults(tmp_path):
    """A wrong-typed step falls back to 0 while a valid sibling key is adopted."""
    (tmp_path / "o__n__7.json").write_text('{"step": "not an int", "errors": 5}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["step"] == 0  # Invalid type, default kept
    assert loaded["errors"] == 5  # Valid type, adopted


def test_load_rejects_bool_for_step_even_though_bool_is_int_subclass(tmp_path):
    """step must be int, not bool (which is technically an int in Python)."""
    (tmp_path / "o__n__7.json").write_text('{"step": true}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["step"] == 0


def test_load_rejects_wrong_type_for_last_changed_falls_back_to_empty_list(tmp_path):
    """A wrong-typed last_changed falls back to []."""
    (tmp_path / "o__n__7.json").write_text('{"last_changed": "checks"}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["last_changed"] == []


def test_load_preserves_none_as_valid_for_nullable_fingerprint(tmp_path):
    """None is explicitly valid for fingerprint even when stored."""
    (tmp_path / "o__n__7.json").write_text('{"fingerprint": null}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["fingerprint"] is None


def test_load_preserves_none_as_valid_for_nullable_last_activity_at(tmp_path):
    """None is explicitly valid for last_activity_at even when stored."""
    (tmp_path / "o__n__7.json").write_text('{"last_activity_at": null}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["last_activity_at"] is None


def test_load_rejects_wrong_type_for_fingerprint_falls_back_to_none(tmp_path):
    """A non-dict fingerprint is rejected; None is the default."""
    (tmp_path / "o__n__7.json").write_text('{"fingerprint": "string not dict"}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["fingerprint"] is None


def test_load_rejects_wrong_type_for_last_activity_at_falls_back_to_none(tmp_path):
    """A non-string last_activity_at is rejected; None is the default."""
    (tmp_path / "o__n__7.json").write_text('{"last_activity_at": 123}')
    loaded = watch_state.load("o/n", 7, str(tmp_path))
    assert loaded["last_activity_at"] is None


# --- pr_watch.py: the activity fingerprint --------------------------------
# The watcher is a dumb change-detector; mergeability.py remains the sole
# authority on what is actionable. So the fingerprint must move on ANY
# observable PR activity — most importantly a COMMENTED review, which is
# what CodeRabbit and Copilot actually post and which the old snapshot()
# deliberately dropped.

from pr_watch import FACETS, changed_facets
from pr_watch import fingerprint as pr_fingerprint


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
    assert pr_fingerprint(_pr(), []) == pr_fingerprint(_pr(), [])


def test_fingerprint_covers_every_facet():
    assert set(pr_fingerprint(_pr(), [])) == set(FACETS)


def test_fingerprint_ignores_rollup_ordering():
    a = _pr(statusCheckRollup=[{"name": "ci"}, {"name": "lint"}])
    b = _pr(statusCheckRollup=[{"name": "lint"}, {"name": "ci"}])
    assert pr_fingerprint(a, [])["checks"] == pr_fingerprint(b, [])["checks"]


def test_commented_review_moves_the_fingerprint():
    """REGRESSION: the CodeRabbit stall.

    The old snapshot() skipped COMMENTED reviews, so `--await coderabbitai`
    waited out the full deadline and parked the child even though the
    review had landed. CodeRabbit and Copilot post COMMENTED, never
    APPROVED/CHANGES_REQUESTED, so this is the common case, not an edge one.
    """
    before = pr_fingerprint(_pr(), [])
    after = pr_fingerprint(
        _pr(reviews=[{
            "author": {"login": "coderabbitai"},
            "state": "COMMENTED",
            "submittedAt": "2026-08-22T10:00:00Z",
        }]),
        [],
    )
    assert changed_facets(before, after) == ["reviews"]


def test_head_change_is_detected():
    assert changed_facets(pr_fingerprint(_pr(), []),
                          pr_fingerprint(_pr(headRefOid="zzz"), [])) == ["head"]


def test_check_conclusion_change_is_detected():
    before = pr_fingerprint(_pr(), [])
    after = pr_fingerprint(
        _pr(statusCheckRollup=[{"name": "ci", "status": "COMPLETED",
                                "conclusion": "SUCCESS"}]),
        [],
    )
    assert changed_facets(before, after) == ["checks"]


def test_legacy_status_context_shape_is_fingerprinted():
    """StatusContext entries carry `context`/`state`, not `name`/`status`."""
    before = pr_fingerprint(_pr(statusCheckRollup=[{"context": "cov", "state": "PENDING"}]), [])
    after = pr_fingerprint(_pr(statusCheckRollup=[{"context": "cov", "state": "SUCCESS"}]), [])
    assert changed_facets(before, after) == ["checks"]


def test_new_thread_and_thread_resolution_both_move_threads():
    empty = pr_fingerprint(_pr(), [])
    opened = pr_fingerprint(_pr(), [{"id": "t1", "isResolved": False}])
    resolved = pr_fingerprint(_pr(), [{"id": "t1", "isResolved": True}])
    assert changed_facets(empty, opened) == ["threads"]
    assert changed_facets(opened, resolved) == ["threads"]


def test_new_comment_moves_the_comments_facet():
    before = pr_fingerprint(_pr(), [])
    after = pr_fingerprint(_pr(comments=[{"id": "c1"}]), [])
    assert changed_facets(before, after) == ["comments"]


def test_edited_comment_moves_the_comments_facet():
    """Edited comment (same id, different body) is detected."""
    before = pr_fingerprint(_pr(comments=[{"id": "c1", "body": "looks good"}]), [])
    after = pr_fingerprint(_pr(comments=[{"id": "c1", "body": "actually, blocks merge"}]), [])
    assert changed_facets(before, after) == ["comments"]


def test_deleted_comment_moves_the_comments_facet():
    """Deleted comment is detected."""
    before = pr_fingerprint(_pr(comments=[{"id": "c1", "body": "nit"}]), [])
    after = pr_fingerprint(_pr(comments=[]), [])
    assert changed_facets(before, after) == ["comments"]


def test_changed_facets_with_empty_dict_prev_reports_changes():
    """Empty dict is a valid fingerprint; only None means arming."""
    before = {}
    after = pr_fingerprint(_pr(), [])
    assert changed_facets(before, after) == list(FACETS)


def test_author_may_be_a_plain_string():
    """Some gh payloads flatten author to a login string."""
    a = pr_fingerprint(_pr(reviews=[{"author": "octocat", "state": "APPROVED",
                                  "submittedAt": "2026-08-22T10:00:00Z"}]), [])
    b = pr_fingerprint(_pr(reviews=[{"author": {"login": "octocat"}, "state": "APPROVED",
                                  "submittedAt": "2026-08-22T10:00:00Z"}]), [])
    assert a["reviews"] == b["reviews"]


def test_changed_facets_reports_multiple_moves_in_facet_order():
    before = pr_fingerprint(_pr(), [])
    after = pr_fingerprint(_pr(headRefOid="zzz", comments=[{"id": "c1"}]), [])
    assert changed_facets(before, after) == ["head", "comments"]


def test_changed_facets_is_empty_when_nothing_moved():
    assert changed_facets(pr_fingerprint(_pr(), []), pr_fingerprint(_pr(), [])) == []


def test_changed_facets_is_empty_when_there_is_no_previous_fingerprint():
    """Arming a watch is not activity."""
    assert changed_facets(None, pr_fingerprint(_pr(), [])) == []


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


def test_jitter_applied_to_still_growing_value():
    """Jitter applies to exponential values before they hit ceiling."""
    # Step 3 gives 87s (before jitter), not yet clamped; jitter should spread it.
    assert backoff_delay(3, -1.0) == 70
    assert backoff_delay(3, 1.0) == 105
    assert backoff_delay(3, 0.0) == 87


def test_error_backoff_retry_after_takes_precedence():
    """Retry-After wins over x-ratelimit-reset when both present."""
    stderr = "x-ratelimit-reset: 1000300\nRetry-After: 47"
    assert error_backoff(1, stderr, now_epoch=1000000) == 47


def test_error_backoff_jittered_ladder_fallback_is_symmetric():
    """Ladder fallback applies symmetric jitter, same as backoff_delay."""
    assert error_backoff(3, "HTTP 502", jitter=-1.0) == backoff_delay(3, -1.0)
    assert error_backoff(3, "HTTP 502", jitter=1.0) == backoff_delay(3, 1.0)
    assert error_backoff(3, "HTTP 502", jitter=0.0) == backoff_delay(3, 0.0)


def test_error_backoff_retry_after_jitter_is_positive_only():
    """Retry-After never waits less than GitHub asked; jitter spreads above."""
    # 47 seconds, jittered by ±20% but only positive: [47, 47*1.2] = [47, 56.4]
    base = 47
    assert error_backoff(1, "Retry-After: 47", jitter=-1.0) == base  # 47 * (1 + 0.2 * abs(-1.0)) but min is base
    assert error_backoff(1, "Retry-After: 47", jitter=1.0) == round(base * (1 + 0.2))  # 56
    assert error_backoff(1, "Retry-After: 47", jitter=0.0) == base


def test_error_backoff_ratelimit_reset_jitter_is_positive_only():
    """x-ratelimit-reset never waits less than GitHub said; jitter spreads above."""
    # Epoch diff is 300s, jittered by ±20% but only positive: [300, 300*1.2] = [300, 360]
    stderr = "x-ratelimit-reset: 1000300"
    assert error_backoff(1, stderr, now_epoch=1000000, jitter=-1.0) == 300
    assert error_backoff(1, stderr, now_epoch=1000000, jitter=1.0) == 360
    assert error_backoff(1, stderr, now_epoch=1000000, jitter=0.0) == 300


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
    assert watch_state.load("o/n", 7, str(tmp_path))["step"] == 0


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


def test_reset_backoff_persists_across_a_failed_fetch(tmp_path, monkeypatch, capsys):
    """CARRIED from Task 4's review: main() applied --reset-backoff's
    cursor["step"] = 0 in memory, but the old error branch returned without
    saving, so the reset was silently lost if the fetch happened to fail."""
    args = ["--repo", "o/n", "--pr", "7", "--state-dir", str(tmp_path)]
    _install_tick(monkeypatch, _pr(), [])
    _pw.main(args)
    _pw.main(args)
    _pw.main(args)                      # step is now 2
    capsys.readouterr()
    _failing_fetch(monkeypatch, "HTTP 502: Bad Gateway")
    _pw.main(args + ["--reset-backoff"])
    capsys.readouterr()
    assert watch_state.load("o/n", 7, str(tmp_path))["step"] == 0
