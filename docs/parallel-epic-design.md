# Parallel epic execution + deterministic driver core — design

## Problem

The `epic` driver is hard-serialized and hard-capped, and both hurt.

**Serialization.** `run` drives "one child in flight at a time"
(`epic/skills/epic/SKILL.md:332`), justified by "strict up-to-date checks make
concurrent PRs invalidate each other". The justification is real but it only
applies to the *merge* step; implement, review and PR-open have no such
constraint. Today a five-child epic pays five full CI-and-bot-review latencies
end to end, when it could pay one.

**Round floors.** Every review loop is capped at a fixed count —
`PLAN_REVIEW_ROUNDS=3`, `PRE_PR_REVIEW_ROUNDS=3`, `CLAUDE_REVIEW_FIX_ROUNDS=3`,
`CODERABBIT_FIX_ROUNDS=3`, `COPILOT_FIX_ROUNDS=3`, `CI_FIX_ROUNDS=3` — and each
exhausted budget ends in STOP-and-hand-off (interactive) or park (`run`). A
round count is a poor proxy for "this loop is going nowhere": productive loops
die at three, and hopeless loops still burn three.

The hopeless ones share a cause. When a pre-PR loop cannot converge, it is
usually because **the step-1 pin is faulty** — the implementer built against a
claim that was already false, so the reviewer's findings are correct, unfixable
in the diff, and regenerate every round. More rounds and stronger models cannot
resolve a wrong premise; only re-pinning can.

**Unreliable waiting.** Waits are agent-driven sleeps —  `CI_ESTIMATE=420s`,
"then 300–600s wakes", capped by `MAX_WAIT_CYCLES`. There is no per-OS timer
primitive behind them; monitoring sometimes simply fails, and even when it
works the driver learns about a verdict up to ten minutes after it lands.

**Prose where computation belongs.** Eligibility ordering, blocker satisfaction,
worktree constraints, drift detection, the epic-completion predicate and the
sweep plan are all pure functions of `gh` JSON plus config, yet each is
re-derived from prose on every invocation by a model. That is the largest single
source of both token cost and run-to-run variance.

## Chosen approach

Two moves, one principle.

1. **An orchestrator drives N children in parallel and marshals a single merge
   queue.** Parallelism covers implement → reviews → PR-open. Merging stays
   strictly one-at-a-time, each PR rebasing on fresh `origin/main` at the moment
   it reaches the head of the queue — which is exactly the invariant strict
   up-to-date branch protection wants.

2. **Round budgets are replaced by convergence.** Loops run while they make
   measurable progress. A stalled loop is treated as a pin fault first
   (re-pin, restart once) and a park second. Ceilings, not counts, are the
   backstop.

The principle: **anything that can be deterministic is.** Every computation
above moves out of skill prose into a shipped, unit-tested `epic/scripts/`
toolkit that the skill calls. The model is left with what genuinely needs
judgment — writing code, deciding a finding is blocking, choosing a defensible
default at an architectural fork.

## Design decisions

### D1 — A deterministic core in `epic/scripts/`

Eight scripts, each a pure function of `gh` output plus resolved config, each
emitting JSON on stdout, each unit-tested against recorded fixtures.

| Script | Subsumes prose at | Emits |
|---|---|---|
| `config.py` | SKILL.md:46–128 | one resolved-config object (Layer 1 ∪ Layer 2, per-child-repo gates) |
| `schedule.py` | SKILL.md:193–196, 354 | `{wave: [...], merge_queue: [...], halt: ...}` |
| `preflight.py` | SKILL.md:129–141 | `pass`, or the exact violated HARD constraint |
| `pr_watch.py` | SKILL.md:318, 361–367 | first real state change, as one JSON event |
| `converge.py` | SKILL.md:222–229, 241–247 | `progress` \| `no_progress` for a round pair |
| `verify_pin.py` | SKILL.md:218–229 | per-claim `verified` \| `stale` \| `unverifiable` |
| `mergeability.py` | SKILL.md:291–318 | complete set of unmet merge requirements (D12) |
| `status.py` | SKILL.md:169–187 | drift report + the exact sweep plan |

They are libraries with CLIs, not a framework: each is independently runnable,
so a harness without subagents still benefits from every one of them.

Runtime `python3` is already a dependency — SKILL.md:61 mandates "python +
pyyaml via the sandbox runner" for config parsing, and forbids regex-only
extraction. This deepens that dependency rather than adding one, and it holds
identically for the Claude, Codex and Kimi manifests.

### D2 — Orchestrator + parallel drive subagents

`run` becomes wave-based. Each cycle:

1. `config.py` → resolved config (fresh every wake; stateless recovery is
   unchanged).
2. `schedule.py` → the runnable wave and the merge-queue order.
3. Dispatch one **drive subagent** per wave member, concurrently.
4. Marshal the merge queue (D3).
5. Recompute and repeat.

A drive subagent owns exactly one child: worktree → context → pin → implement →
`pre-review` gates → pre-PR review loop → PR-open → Status = In Review → **prose-gate
resolution** (Claude Review where required, CodeRabbit, Copilot where requested,
thread resolution, merge-gating `custom_gates`), ending when every prose gate is
clean and the child is eligible for the merge queue.

Prose-gate resolution belongs inside the parallel region deliberately: bot
review latency is the largest single cost in a child's lifecycle, and
serializing it would surrender most of the win. Only the steps that genuinely
contend — rebase-for-merge, the CI run on the rebased head, arming `--auto`, and
the merge itself — are serialized at the head of the queue (D3).

A drive subagent **never rebases for merge, never arms `--auto`, never merges**,
and never touches another child's worktree or branch.

Concurrency is capped twice. Per repo, by that repo's existing
`worktrees.max_concurrent` (default 3) — it is already in each repo's
`epic.yaml` and already means "worktrees for this epic in this checkout", so it
needs no new semantics. Globally, by a new optional `epic-config.max_parallel`
(default 3), which bounds total children in flight across all involved repos.

Parallel dispatch is a harness capability, so it follows the repo's
guarded-capability prose rule: dispatch drive subagents in parallel if the
harness supports subagents, otherwise drive the wave sequentially, in
merge-queue order, in-session. `run --serial` forces the sequential path
regardless of capability.

Interactive `next` and `<child#>` are unchanged: one child, then STOP.

### D3 — The merge queue is FIFO, marshalled, computed, and never stored

`schedule.py` emits `merge_queue` as the ordered list of children whose PR is
open and whose **prose gates are all clean**, where clean means each gate is
green *or* legitimately not applicable: `claude-review` absent from the repo's
`merge.required_checks`, or a Copilot review the PR-time request could not
obtain (the existing 422 → N/A path). A gate that is pending, red, or has
unresolved threads keeps the child out of the queue — it is still being driven
in parallel by its own subagent.

**Order is FIFO on readiness.** The sort key is `became_ready_at`: the latest
timestamp among the child's merge-gating gates going clean — review
`submittedAt`, check `completedAt`, thread resolution — all already present in
the GraphQL payload, so the key is derived, not stored, and the queue survives
the stateless-recovery requirement below. Ties break on sub-issue position.
These are GitHub's timestamps rather than the driver's local clock, which keeps
the order identical across harnesses, machines and OSes.

FIFO is chosen over sub-issue or Priority order for two reasons. It is
**correctness-free**: every member of a wave is DAG-independent by construction,
since a `blockedBy` child is not runnable until its blocker is CLOSED, which
requires that blocker to have merged — so no ordering among wave members can
violate a dependency. And it **eliminates head-of-line blocking**: under
sub-issue order a merge-ready child waits behind a lower-numbered sibling still
grinding through bot review, which is precisely the idle this design exists to
remove.

A gate that goes red and later clean again resets that child's
`became_ready_at`, sending it to the back of the queue. That is the correct FIFO
semantic — the child only just became ready, and its earlier readiness was
withdrawn.

**Gate-free children fall back to PR open time.** A child can be legitimately
clean with nothing ever having been *cleared*: every gate `na` (`claude-review`
not in `required_checks` and a Copilot request that 422'd), or a repo with no
prose gates at all. Such a child has no gate timestamps, so `became_ready_at`
falls back to the PR's `opened_at` — if nothing gated the PR, it was merge-ready
the moment it opened, and that is its honest FIFO position. Without this
fallback the child has no sort key, becomes indistinguishable from one whose
gates are still pending, and is stranded out of the queue permanently — a
gate-free repo could never merge anything. `opened_at` comes from
`gh pr view --json createdAt` on each wake, so the derived-never-stored property
is unaffected.

The trade accepted here is that Project **Priority** no longer influences merge
order: a P0 child that becomes ready second merges second. At queue width 1 this
costs one merge cycle, which does not justify reintroducing head-of-line
blocking. Priority still governs *drive* order — `schedule.py` continues to use
sub-issue position and Priority when choosing which children enter the wave,
which is where the signal actually matters.

Only `merge_queue[0]` may enter the merge phase, and only one PR is ever in the
merge phase. Head-of-queue admission runs, in order: rebase on freshly fetched
`origin/main` → push → wait for checks via `pr_watch.py`, running the CI fix
loop on failure → arm `gh pr merge --auto` → watch to MERGED → sweep +
Status = Done → recompute.

Arming happens only once the rebased head's checks are green. This is stricter
than today's "arm, then fix CI after" (SKILL.md:310–318) and it is deliberate:
at width 1 there is no throughput argument for arming early, and an armed PR
whose CI later goes green with a finding still open is precisely the hazard the
existing "arm `--auto` LAST" rule exists to prevent.

Head-of-queue admission does not run once and hope; it **loops until the PR is
mergeable by GitHub's own account**, per D12. Width 1 is what makes that
terminate: while a PR holds the head slot, no sibling merge can push `main`
forward underneath it, so a branch made current at admission stays current
through to merge. Staleness is resolved exactly once per child rather than once
per sibling merge.

This is what removes the thrash the current serialization avoids by brute force.
A PR that is not at the head is *allowed* to be behind `main`; it rebases once,
at admission, onto the `main` the previous merge just produced. Each PR
therefore pays at most one rebase-and-revalidate cycle rather than one per
sibling merge.

**Non-trivial rebases re-arm review.** If head-of-queue rebase resolves
conflicts, the diff is no longer the diff the pre-PR reviewers approved: re-run
the pre-PR review loop on the rebased head before arming `--auto`. A clean
fast-forward rebase does not re-arm it. This is a deterministic trigger — "did
the rebase report conflicts" — not a judgment call.

**The queue is derived, never persisted.** Stateless recovery from `gh` is a
HARD invariant (SKILL.md:342); a queue file would break it. `schedule.py`
recomputes the identical order from the identical inputs on every wake.

### D4 — Structured findings and fingerprints

Convergence can only be deterministic if findings are comparable. Every
reviewer — step-3 plan reviewer, both pre-PR reviewers — returns a JSON array:

```json
[{"file": "src/auth/Auth.kt", "anchor": "refresh()", "category": "spec-gap",
  "claim": "token refresh path ignores the 401 retry the runbook requires",
  "blocking": true}]
```

The fingerprint is `sha256(file | category | normalize(claim))`, where
`normalize` lowercases, collapses whitespace and strips code spans. **`anchor`
is deliberately excluded** — line numbers and symbol positions shift as the diff
changes, and a finding that moved is the same finding.

`converge.py` compares consecutive rounds' *blocking* fingerprint sets:

- `blocking(N)` empty → `converged`.
- `blocking(N) ⊊ blocking(N-1)` → `progress` (findings resolved).
- `blocking(N) \ blocking(N-1) ≠ ∅` → `progress` (new evidence; the loop is
  still learning).
- `blocking(N) == blocking(N-1)`, non-empty → `no_progress`.

The model still decides whether a finding is blocking. It no longer decides
whether the loop is getting anywhere.

### D5 — Budget-free convergence: stall → re-pin → park

`STALL_ROUNDS = 2` consecutive `no_progress` verdicts declare a **stall**. A
stall is a pin fault until proven otherwise:

1. Run `verify_pin.py` over the pin's claims; surface every `stale` one.
2. Re-run the step-3 plan reviewer against the **plan**, not the diff, with the
   stalled findings supplied as evidence.
3. If the pin amends: post the amendment as a child comment, reset the
   convergence history, restart the diff loop. Bounded by `REPIN_ATTEMPTS = 1`
   per child — a second re-pin means the problem is not the pin.
4. Stall again after re-pin → interactive: `AskUserQuestion` with the surviving
   findings and the suspect pin claims; `run`: park.

Identical semantics apply to the post-PR gate loops. A Claude Review, CodeRabbit
or Copilot loop that returns a byte-identical blocking set twice has stalled;
CI failures fingerprint on `(check name, failing test ids)` and stall the same
way. `CONFLICT_ATTEMPTS` survives unchanged — a rebase conflict is a discrete
event with a natural retry count, not a convergence problem.

The only hard ceilings are resource ones: `CHILD_DRIVE_CEILING_S = 7200` from
drive start to merge-queue entry (that is, covering pre-PR *and* prose-gate
loops, the whole span a drive subagent owns), and `PR_WATCH_DEADLINE_S = 3600`
per individual wait. Exceeding either parks the child with the ceiling named.
Time spent waiting at the head of the merge queue is not charged to the child —
it is a scheduling artifact, not a lack of progress.

### D6 — Pin claims are tagged and mechanically re-verifiable

This is the direct strike at the root cause. Every load-bearing claim in the
step-3 pin carries a machine-checkable provenance tag:

```
- verified: src/auth/Auth.kt@origin/main#refresh — returns Result<Token>
- assumption: billing API accepts partial refunds (no readable source)
```

`verify_pin.py` re-checks each `verified:` claim by resolving `path@ref` and
confirming the named symbol is present, reporting `verified`, `stale` (the ref
resolves but the symbol is gone or moved) or `unverifiable` (the path or ref
does not resolve). It runs at pin time and again at every stall.

**The symbol match is word-bounded, not a substring test.** A substring match
reports `verified` for a claim naming `refresh` when the source contains only
`refreshToken` or `_refresh` — exactly the state after the real symbol was
renamed. That is a false `verified`: the check would confirm the very pin rot it
exists to catch, and the driver would proceed confidently on a false premise.
Matching on `\b`-delimited identifiers closes the prefix/suffix class. An
occurrence inside a comment or string literal still counts as present; removing
that residual needs language-aware parsing and is deliberately out of scope,
since the identifier class is the one that actually fires.

A load-bearing claim that cannot be verified is never silently built upon:
interactive asks; `run` records it explicitly as an assumption in both the pin
comment and the PR body, so a wrong premise is visible in review rather than
discovered three rounds deep. `run`'s existing HARD exception stands — on a
contract/API-defining or P0 child, an unverifiable load-bearing claim parks for
human input rather than proceeding on an assumption.

### D7 — `pr_watch.py` replaces sleep cycles

One blocking poller covering, in a single loop keyed on the PR's head SHA: check
suite conclusions, the `claude-review` check and formal review state, CodeRabbit
review state, Copilot review state, and review-thread resolution.

> **Superseded.** The `--await`/`--deadline` vocabulary and `snapshot()` described
> below were replaced by the tick model in `docs/durable-pr-watch-design.md`.
> `pr_watch.py` no longer blocks, awaits keys, or parks on a deadline; this
> passage is kept for historical context only.

**The `--await` vocabulary is exactly `snapshot()`'s keys** — `head`, `checks`,
`threads_unresolved`, or a review-author **login** (e.g. `coderabbitai`,
`copilot-pull-request-reviewer`, or a human's GitHub username) — never a check
name or a bot's product name. `claude-review` is a required STATUS CHECK, not a
review author, so it folds into the `checks` key, not a key of its own:

```
$ python epic/scripts/pr_watch.py --repo o/r --pr 101 \
    --await checks,coderabbitai --deadline 1800
{"event":"coderabbitai","state":"CHANGES_REQUESTED","head":"a1b2c3","waited_s":73}
```

(An earlier version of this example passed `--await checks,claude-review,coderabbit`
— neither `claude-review` nor `coderabbit` is a key `snapshot()` ever produces, so
nothing would ever change and the driver would wait the full deadline and park
falsely. See `references/github-graphql.md`'s "`pr_watch.py --await` vocabulary"
section for the full key list and the one known divergence from
`mergeability.py`'s required-check filtering.)

It returns on the **first** awaited state change and exits, so the driver reacts
in tens of seconds rather than at the next 300–600s wake. Timing uses
`time.monotonic()` with adaptive backoff (fast early polls, widening to a
ceiling) and no shell `sleep` anywhere — correct and identical on macOS, Linux
and Windows, which is what the current arrangement lacks.

Head-SHA keying is what makes it trustworthy: a verdict on a superseded head is
reported as such rather than mistaken for a verdict on the current one. This
retires `CI_ESTIMATE`, `MAX_WAIT_CYCLES` and `MERGE_WAIT_CYCLES`; a wait now
ends at a deadline, and a deadline breach parks with the specific check or
reviewer that never posted — carrying the final snapshot, so a timeout never
leaves the driver blind about what it was waiting on.

Three properties keep the watcher from reproducing the failure it replaces:

- **It answers immediately when the answer already exists.** Reporting only
  *transitions* means a gate that reached its final state before the watcher
  launched — routine, since watching starts after the PR is opened — would never
  change and the driver would block the whole deadline. So the first snapshot is
  checked against a terminal-state set before any polling begins, and a settled
  gate is reported at once, marked as an immediate answer rather than an
  observed transition.
- **It survives transient failures.** A single 502 partway through a long CI
  wait must not end the watch; consecutive failures are counted, tolerated, and
  only after a bounded run does the watcher give up — with a structured `error`
  event, never a traceback. An unresilient watcher is precisely the "monitoring
  sometimes just fails" complaint this design set out to fix.
- **It reads the payload shapes GitHub actually emits.** `reviews[].author` is
  an object (`{"login": …}`), not a string, and `statusCheckRollup` mixes
  CheckRun with legacy StatusContext entries. Both are normalized. Fixtures that
  invent simpler shapes hide real crashes, and this module is only worth its
  cost if it is correct against live output.

Review folding is latest-wins per author, but `COMMENTED` reviews are skipped:
GitHub does not treat a comment as dismissing an approval, and folding one over
an earlier `APPROVED` would emit a phantom "approval lost" transition and send
the driver into needless fix-loop work.

### D8 — Circuit breakers rebuilt for parallelism

`CONSECUTIVE_PARK_HALT` is meaningless once children run concurrently —
"consecutive" has no ordering to refer to. It is replaced by a **shared-signature
check**. Every park comment gains a machine-readable trailer:

```
epic-park: {"code": "gate-stall", "gate": "claude-review",
            "signature": "9f2a…"}
```

where `signature = sha256(gate | normalize(reason))`. `schedule.py` raises
`halt` when any of these hold:

- `PARK_SIGNATURE_THRESHOLD = 3` parks share one signature — parks that agree
  are evidence of a systemic cause (CI down, `main` broken, ruleset changed), so
  halt and re-verify the live ruleset via
  `gh api repos/<owner>/<repo>/rules/branches/main`. **Waiting-on-human parks are
  excluded** from this count: an epic whose children all correctly park on
  `approval-missing` (D12) is functioning as designed, not failing systemically,
  and halting it would be exactly wrong.
- Nothing runnable remains and the epic is incomplete.
- A parked child transitively blocks every remaining child (computed from the
  `blockedBy` graph, as today).

`GLOBAL_PARK_THRESHOLD` is retired: three unrelated parks across a wide epic is
not a systemic signal, and under parallelism it fires constantly. One park never
stalls its siblings — the wave continues and the merge queue keeps draining.

### D9 — Tunables: what dies, what survives, what is new

**Retired** — `PLAN_REVIEW_ROUNDS`, `PRE_PR_REVIEW_ROUNDS`,
`CLAUDE_REVIEW_FIX_ROUNDS`, `CODERABBIT_FIX_ROUNDS`, `COPILOT_FIX_ROUNDS`,
`CI_FIX_ROUNDS`, `CI_ESTIMATE`, `MAX_WAIT_CYCLES`, `MERGE_WAIT_CYCLES`,
`CONSECUTIVE_PARK_HALT`, `GLOBAL_PARK_THRESHOLD`.

**Survives** — `CONFLICT_ATTEMPTS = 2`.

**New** — `MAX_PARALLEL = 3`, `STALL_ROUNDS = 2`, `REPIN_ATTEMPTS = 1`,
`PR_WATCH_DEADLINE_S = 3600`, `CHILD_DRIVE_CEILING_S = 7200`,
`PARK_SIGNATURE_THRESHOLD = 3`.

`CONFLICT_ATTEMPTS` now bounds recurring `behind-base` as well as `conflict`
(D12): both mean "the base moved under us", and both are terminal in the same
way once the base is moving faster than a CI cycle can complete.

### D10 — Skill surface changes

- Frontmatter `description` loses "one child at a time"; the driver's contract
  is now "drive a GitHub epic — one child per interactive invocation, N in
  parallel under `run`".
- `## Arguments` gains `run --serial`.
- `epic-config` gains optional `max_parallel` (absent → 3). No Layer-2 schema
  change: `worktrees.max_concurrent` already carries per-repo capacity.
- The algorithm prose replaced by D1's scripts is deleted, not duplicated — a
  script and a prose restatement of the same algorithm will drift, and the prose
  is the copy that silently wins. Each site becomes a call plus the contract of
  the result.

### D11 — Lint and test obligations

`tests/test_skills_lint.py` must stay green: capability tokens (subagents,
parallel dispatch, `ScheduleWakeup`) only in guarded paragraphs
(`test_capability_tokens_only_in_guarded_paragraphs`), the config-lookup-order
sentence intact (`test_config_lookup_order_sentence_present`), reference links
resolving (`test_reference_link_appears_and_resolves`), and the D5
conditional-gate sentence preserved
(`test_driver_skill_carries_d5_conditional_gate_sentence`).

New lint: no retired tunable name may survive anywhere in `epic/`, and every
script named in the skill must exist and be executable — the same ratchet shape
the repo already uses for forbidden literals.

### D12 — Mergeability is derived from GitHub, never enumerated in prose

The driver currently reasons about merge readiness from a prose list of gates it
knows about. Every stall of the "it just sat there" kind is that list being
incomplete: a review thread nobody resolved, a branch that went stale, an
approval dismissed by the last push. The list can never be complete, because the
repo's ruleset can change without the skill changing.

So the driver stops enumerating and asks. `mergeability.py` takes a repo and PR
and emits the **complete set of unmet merge requirements**, each with a stable
code and a prescribed resolution:

| Code | Derived from | Resolution |
|---|---|---|
| `behind-base` | `mergeStateStatus == BEHIND` | update branch onto fresh `main` |
| `conflict` | `mergeStateStatus == DIRTY` | rebase + resolve, `CONFLICT_ATTEMPTS` |
| `check-failing:<name>` | `statusCheckRollup` | CI fix loop |
| `check-pending:<name>` | `statusCheckRollup` | wait via `pr_watch.py` |
| `check-missing:<name>` | ruleset ∖ rollup | required check never started — diagnose, do not wait forever |
| `thread-unresolved:<id>` | `reviewThreads(isResolved: false)` | address, push, `resolveReviewThread` |
| `changes-requested:<actor>` | `reviewDecision` | fix loop for that reviewer |
| `approval-missing` | ruleset `required_approving_review_count` | see approval policy below |
| `approval-stale` | `require_last_push_approval` | re-request review of the new head |
| `draft` | `isDraft` | mark ready |

Two sources, both authoritative and both machine-readable: the live ruleset
(`gh api repos/<owner>/<repo>/rules/branches/main`) for what is required, and
`gh pr view --json mergeStateStatus,mergeable,reviewDecision,statusCheckRollup,isDraft`
plus the review-threads GraphQL query for what is currently true. The repo's
`epic.yaml` `merge` block stops being the source of truth for gating and becomes
what it should always have been: a declaration of intent that
`mergeability.py` cross-checks against the live ruleset, reporting drift.

**The HARD exit condition.** The merge phase does not exit successfully until
`mergeability.py` returns an empty requirement set — equivalently, until GitHub
reports a mergeable `mergeStateStatus` — or the PR is `MERGED`. There is no
partial satisfaction and no "the gates I knew about are green, so proceed".
Every requirement the repo imposes must be resolved.

**The set must never be silently empty.** A requirement table derived from parts
can fail to explain a block, and an empty set is read as "mergeable" — which
would reintroduce, inside this very module, the incomplete-enumeration failure it
exists to eliminate. So the derivation fails closed on three axes:

- **Unexplained blocks.** `CLEAN`, `UNSTABLE` and `HAS_HOOKS` are GitHub's
  mergeable states; every other value — `BLOCKED`, `DIRTY`, `BEHIND`, `DRAFT`,
  `UNKNOWN`, and anything GitHub adds later — blocks. If a blocking state yields
  no derived requirement, the set carries `blocked-unexplained:<state>` rather
  than reporting clean.
- **Authoritative fields beat the ruleset.** `reviewDecision == REVIEW_REQUIRED`
  emits `approval-missing` on its own, never conditioned on the ruleset carrying
  `required_approving_review_count` — a partial ruleset fetch, or a repo still on
  classic branch protection, must not be able to make a blocked PR read clean.
- **Both rollup shapes.** `statusCheckRollup` mixes modern CheckRun entries
  (`name`/`status`/`conclusion`) with legacy StatusContext entries
  (`context`/`state`). Both are normalized; treating only the former leaves a
  commit-status repo with a permanent unnameable pending check.

The converse guard matters too: only checks the ruleset declares required may
gate. `UNSTABLE` means GitHub considers the PR mergeable and a *non-required*
check failed, so gating on every rollup entry would fix-loop on a check nobody
requires. When the ruleset declares no required checks, all checks gate — fail
closed on ignorance, with `blocked-unexplained` as the backstop.

**Stale branches.** `behind-base` is a clean fast-forward and is simply applied;
`conflict` goes through the existing rebase-and-resolve path. The interaction
that actually bites is `require_last_push_approval`: updating the branch
dismisses the approval, so a naive loop alternates between staleness and missing
approval forever. Width-1 admission breaks that cycle for sibling merges, and
the residual case — a human pushing `main` from outside the epic faster than a
CI cycle completes — is detected as `behind-base` recurring more than
`CONFLICT_ATTEMPTS` times and parks with exactly that diagnosis, rather than
looping.

**Review threads.** Every unresolved thread must reach a terminal state, and
"the code was changed" is not one of them — `resolveReviewThread` must actually
be called, which is the specific step that silently goes missing today. Three
terminal states, all deterministic to detect: addressed (fixed, pushed,
resolved), rebutted (replied with rationale, resolved — the driver is allowed to
disagree with a reviewer, not to ignore one), and outdated (`isOutdated == true`
because the code moved underneath it; resolved with a reply naming the change).
A thread left dangling keeps `thread-unresolved:<id>` in the requirement set, so
it cannot be forgotten — it blocks the HARD exit condition by construction.

**Approvals.** `approval-missing` is the one requirement the driver structurally
cannot satisfy: it must not approve its own PR, and no config can change that.
It parks with the requirement named and the PR left intact and unarmed, so a
human approves and the next wake picks it straight back up. This is a park that
means "waiting on you", and it is reported distinctly from a failure park so it
never counts toward the systemic-cause signature threshold in D8.

## Success criteria

1. A five-child epic with no `blockedBy` edges opens five PRs concurrently and
   merges them one at a time in readiness order, each rebased on the `main` its
   predecessor produced, with no CI re-run storms.
2. No merge-ready child ever waits on a sibling that is not itself merging: a
   child whose gates go clean first reaches the head of the queue first,
   regardless of sub-issue number or Priority.
3. No review loop terminates on a round count. A loop that resolves findings
   runs as long as it keeps resolving them.
4. A faulty pin claim is surfaced by `verify_pin.py` as `stale` before the
   implementer builds on it, and a stalled loop re-pins before it parks.
5. A CI or bot verdict is acted on within one poll interval of landing, on
   macOS, Linux and Windows, with no shell `sleep`.
6. The merge phase never exits successfully on a partially satisfied repo: it
   ends at `mergeStateStatus == CLEAN`/`MERGED`, or it parks naming the exact
   unmet requirement. A stale branch, an unresolved review thread and a
   dismissed approval each produce a named requirement and a specific action,
   never an unexplained wait.
7. One parked child never halts its siblings; a halt requires three parks
   agreeing on a signature, an empty runnable set, or a transitive block.
8. Every script has unit tests over recorded `gh` fixtures and is deterministic:
   identical inputs produce byte-identical output.
9. `run --serial` reproduces today's behaviour, and harnesses without subagents
   degrade to it automatically.

## Out of scope

- File-disjointness analysis as a parallelism precondition. DAG independence
  plus a serialized merge queue is sufficient; touch-path prediction from
  spec/runbook is unreliable and would force frequent needless serialization.
- Concurrent merging. The merge queue is deliberately width-1.
- Replacing `gh` with direct REST/GraphQL clients in the scripts. They shell out
  to `gh` and parse its JSON, inheriting its auth.
- Any change to `epic:create` or `epic:migrate`.
- Cross-epic parallelism. Scope is children within one epic.

## Files added/changed

**Added**

- `epic/scripts/{config,schedule,preflight,pr_watch,converge,verify_pin,mergeability,status}.py`
- `tests/fixtures/gh/*.json` — recorded `gh` responses, including one fixture per
  `mergeStateStatus` value (`BEHIND`, `DIRTY`, `BLOCKED`, `UNSTABLE`, `CLEAN`)
- `tests/test_epic_scripts.py` — unit tests for the eight scripts
- `docs/parallel-epic-plan.md` — implementation plan

**Changed**

- `epic/skills/epic/SKILL.md` — frontmatter, `## Arguments`, worktree
  constraints, both review loops, merge phase, `run` mode, circuit breakers,
  tunables
- `epic/skills/epic/references/github-graphql.md` — queries the scripts issue
- `epic/README.md` — `--serial`, `max_parallel`, the scripts' runtime dependency
- `tests/test_skills_lint.py` — retired-tunable and script-existence ratchets
