---
name: epic
description: Drive a GitHub epic (sub-issues + a project board) — one child per interactive invocation, N in parallel under `run`
---

The text after the skill name is the epic/child reference.

You are driving a GitHub epic. Epics are issues with **native sub-issues** as children
and **native blocked-by relations** as the dependency graph; live state is tracked in
**the configured project board** (resolve its number `epic-config.project` → Layer-2
`planning.project` → STOP; there is no hardcoded default). New epics are homed in the
planning repo (`planning.repo` from the checkout's epic.yaml); children live in the
working repos. Legacy epics may be homed elsewhere — the `repo` field of the
epic-config decides.

Read `references/github-graphql.md` NOW — every GraphQL
incantation, project/field ID, lifecycle table, and the PR-mapping rule live there.

A `status` invocation only reports. A `next` / `<child#>` invocation drives at most
ONE child **through merge + sweep + Status=Done**, then STOPs. `--stop-at-pr` opts out
of the merge phase (stop at PR-open, Status=In Review) — forbidden on `run`. A `run`
invocation loops the same per-child mechanics autonomously until the epic is done.

## Arguments

```
/epic <epic#> [status | next | run | <child#>] [--stop-at-pr] [--sweep] [--serial]
```

- `<epic#>` required. With no argument: list open epics and STOP — scope `--owner` to
  the cwd origin's owner (owner half of `git remote get-url origin`):
  `gh search issues --owner <owner> --state open "type:Epic" --json number,title,repository`
  falling back to label `tracking-epic` if issue types are not in use. If
  `git remote get-url origin` yields no owner (no `origin` remote, or the cwd is not a
  git repo), the no-arg listing itself asks the operator for an owner when a human is
  driving — never emit `gh search issues` with an unresolved `<owner>`; in
  `run`/non-interactive contexts it STOPs: "can't infer owner — pass an epic number or
  run from a repo checkout."
- Mode defaults to `status`.
- `--stop-at-pr`: valid on `next`/`<child#>` only. On `run` → STOP: "`--stop-at-pr` is
  not compatible with `run`; drop the flag or use `next`."
- `--sweep`: valid on `status` only. Opts into the destructive reconcile (worktree
  removal, branch deletion, Project-field mutation). Without it `status` is pure
  read-only and only REPORTS the drift + what `--sweep` would change.
- `--serial`: valid on `run` only. Drives one child at a time, as the pre-parallel
  driver did. On `status`/`next`/`<child#>` → STOP: "`--serial` applies to `run`
  only." If your harness supports subagents, `run` dispatches children in parallel
  by default; otherwise it drives serially automatically, making `--serial` a
  no-op there.

## Two-layer config — load fresh EVERY invocation (stateless recovery)

### Layer 1: per-epic `epic-config` (epic issue body)

1. Locate the epic issue — cwd repo FIRST:
   `gh issue view <epic#> --repo <cwd owner/name> --json body,state,title,number`. If
   not found there, fall back to `planning.repo` from the CWD CHECKOUT's epic.yaml — at
   Layer-1 load time no child has been selected yet, so the cwd checkout is the only
   Layer-2 source: check `.agents/epic.yaml` first, then `.claude/epic.yaml`, and read
   `planning.repo`. Neither the cwd repo nor a resolvable `planning.repo` locates the
   epic (or the cwd checkout has no epic.yaml) → interactive modes
   (`status`/`next`/`<child#>`) ASK the operator which repo homes it; `run` STOPs:
   "`run` aborted: cannot locate epic #<n> — no cwd match and no `planning.repo` in the
   cwd checkout's epic.yaml." Legacy epics homed in working repos are covered by the
   cwd-first order.
2. Parse and resolve BOTH layers with `python epic/scripts/config.py --epic <epic#>
   --repo <owner/name>`. It emits one resolved-config JSON object and is the only
   supported parser — never regex-extract the block by hand. It enforces the
   `epic` / `<epic#>` match, the kebab-case `worktree_prefix`, the D4 project
   order (`epic-config.project` → `planning.project` → STOP), and per-child-repo
   gate resolution. A `ConfigError` on stdout is a STOP in every mode.
   `epic-config` also accepts optional `max_parallel` (int, default 3) — the
   global cap on children in flight across all involved repos.
3. NO `children_source` key and NO `## Dependency model` section are expected:
   children come from the sub-issue API; blockers from native `blockedBy` relations.
   If the body still has a task-list (`- [ ] #NNN`) → STOP: "legacy epic — run
   `/epic:migrate <epic#>` first."

### Layer 2: per-repo epic.yaml

Carries what varies per repo: `toolchain` (commands + prefix + notes), `merge`
(method, `required_checks`, approvals, `require_last_push_approval`,
`required_review_thread_resolution`, `copilot_review` (bool — when true the driver
requests a Copilot review at PR creation and gates merge on its threads), strictness),
`docs` dirs, `worktrees`
(root, `max_concurrent`), and the `gates` catalog (each: `hook` = `pre-review` |
`pr-test-plan`, `required_when`, `procedure`). Load it from the checkout of whichever
repo the CURRENT child lives in: check `.agents/epic.yaml` first, then
`.claude/epic.yaml`. Neither `.agents/epic.yaml` nor `.claude/epic.yaml` exists →
STOP: "repo <owner/name> has no epic.yaml — author `.agents/epic.yaml` before
driving children there."

Honor the target repo's `CLAUDE.md` and global CLAUDE.md throughout every drive (TDD,
context-mode routing, the Claude Review action gate, GitHub Copilot review, etc. —
`toolchain.notes` summarizes per-repo specifics but does not replace them).

### Checkout resolution (cross-repo children)

Children may live in a different repo than the cwd. Resolve the local checkout: scan
the cwd's parent directory for sibling checkouts whose `origin` matches the child's
`owner/name` (`git -C <dir> remote get-url origin`). No local checkout → STOP and ask
the operator to clone it; never drive via API-only edits.

### Missing or malformed config

- **Interactive modes** (`status`/`next`/`<child#>`): repair interactively — identify
  the precise gap; ask per missing field (via `AskUserQuestion` if your harness
  supports structured questions; otherwise as numbered plain-text questions, waiting
  for the reply) with defaults
  (`worktree_prefix` → kebab-slug of the epic title; `spec`/`runbook` → glob
  `<docs.spec_dir|runbook_dir>/*<slug>*.md` in `docs_repo`; a gate name unknown in EVERY
  involved repo's catalog → offer the catalog as multi-select + "drop it"; a truly novel
  gate is NOT inventable — STOP: "add gate `<name>` to `<repo>/.agents/epic.yaml` first"
  (a name valid in another involved repo's catalog is SKIPPED for the current child,
  never an error)). Offer to persist the
  repaired block via `gh issue edit --body-file`; declined → use for this invocation
  only.
- **`run` mode**: no interactive questions of any kind. Hard-stop: "`run` aborted: epic #<n>
  config missing/incomplete (`<field>`). Run `/epic <n> status` once interactively,
  then re-invoke."

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

## Epic-completion lifecycle (all modes)

The epic issue has its OWN item on the configured project board (the `project` resolved via the D4 order `epic-config.project` → `planning.project` → STOP) — the driver owns its Status just
like the children's. Every invocation MUST observe it: fetch the epic node's
`projectItems` + Status alongside the sub-issues (the discovery query in the reference
includes it); epic not on the configured project → `addProjectV2ItemById` (idempotent) before any
Status write. Definitions used by every rule below: a child is **parked-open** when its
issue is OPEN with Project Status = Parked; the epic is **complete** when EVERY
sub-issue's `state == CLOSED` (iterate `subIssues.nodes[].state` — do NOT use
`subIssuesSummary.completed`, which can undercount closed-as-not-planned children).

1. **Drift self-heal**: epic issue CLOSED **and** complete, but its Project Status ≠
   Done → set it to Done immediately — in every mode EXCEPT plain `status` without
   `--sweep`, which only REPORTS the drift (read-only contract). Per the lifecycle
   table, `status --sweep` repairs the epic's own item ONLY in this CLOSED+complete
   drift case; an OPEN epic's own Status is driver-owned and NOT a sweep target.
   Epic CLOSED while any
   child is still OPEN → anomaly (likely human-closed early): REPORT it, never stamp
   Done, never auto-reopen.
2. **Completion**: in `next` / `<child#>` / `run`, when the epic is observed complete (typically because the
   last child's merge + sweep closed it, and no child is parked-open), finish the epic: close the
   epic issue IF still open
   (`gh issue close <epic#> --repo <repo> --comment "All children closed — epic complete."`)
   and set the epic's own Project Status = **Done**. `status --sweep` may repair the
   epic item's Status but NEVER closes the epic issue — closing is a drive-mode action.

## Mode: `status`

Report only — no driving, and **read-only by default** (mutates NOTHING unless
`--sweep` is passed):

1. Fetch epic + sub-issues (+ `blockedBy`, `projectItems`) per the reference. Build
   the child→PR map per the PR-mapping rule, per child repo.
2. `git worktree list` in each involved checkout — list `${worktree_prefix}-*`.
3. Print per child: state (todo / in-progress / PR-open / **merged** /
   closed-unmerged / parked), Project Status (flag drift between reality and the
   Project field — REPORT the drift, reality wins; only repair the field under
   `--sweep`), blockers and whether each is satisfied; then WIP count vs cap, and the
   **next-eligible child** (lowest sub-issue position whose blockers are all satisfied,
   skipping parked). Also report the EPIC's own Project Status: epic CLOSED+complete with Status ≠ Done (drift; sweep-fixable), epic OPEN but complete (finish via a drive mode), and epic CLOSED with open children (anomaly; report only — never sweep-fixable).
4. **Reconcile + sweep** — gated behind `--sweep` (HARD: nothing here runs without it):
   fix stale Project Status values (children AND the epic's own item — an epic that is CLOSED and complete gets Status=Done; `--sweep` never closes the epic issue); remove a worktree ONLY if a PR whose
   `closingIssuesReferences` includes that child is MERGED (then prune + delete the
   local branch). Without `--sweep`, do NONE of this — instead list exactly what
   `--sweep` WOULD remove/fix (drifted fields, mergeable-worktree removals). Then STOP.

## Mode: `next` or `<child#>`

### 1. Select & guard

- `next`: highest-priority eligible child = first by sub-issue order (respect
  `reprioritizeSubIssue` ordering; Project Priority field P0>P1>P2 breaks ties) with
  all blockers satisfied, not parked, not closed, no open PR. `<child#>`: verify the
  same eligibility; if blocked, name the blocker(s) and STOP.
- Run ALL worktree HARD checks in the child's repo checkout.
- **Closed-unmerged PR recovery**: if the selected child has a PR closed UNMERGED by a
  human (issue still open) with its worktree/branch still present, do NOT re-drive into
  the HARD uniqueness STOP — detect it and reuse/reopen the existing branch + worktree to
  continue. Ambiguous (in `run`) → park with a precise diagnostic naming the closed PR.
- **Interrupted-drive recovery**: the same reasoning applies to a drive that never
  reached a PR at all. `preflight.py` reports `worktree-exists` whenever a worktree
  for this child is already present — that is correct and HARD when a SECOND drive
  is about to start on a child ALREADY being driven THIS invocation/wave (the
  genuine double-drive case: two concurrent drivers — dispatched as parallel
  subagents if your harness supports them, otherwise two overlapping inline
  invocations — on the same worktree/branch corrupt each other's work, so that STOP
  must stay). But a worktree left over from a crashed or killed drive step (no PR
  yet; Status may still read In Progress from before the crash) is not that case —
  nothing is currently touching it. Distinguish the two by whether this selection is
  what is CURRENTLY dispatching the child: if this invocation is the one about to
  drive it (a human explicitly naming it via `<child#>`, or `run`'s own per-wave
  dispatch deciding to resume it — see the `run` mode's stateless-recovery note
  below for how such a child is re-surfaced), reuse the existing worktree/branch and
  resume from wherever `gh`'s live state says the child actually is (no PR yet →
  resume from Context/pin onward; PR open → resume from prose-gate resolution)
  instead of hard-STOPping. If instead another drive for this exact child is
  concurrently active in this same session, the HARD STOP
  applies unchanged. Never guess — when it is genuinely unclear whether another
  agent is mid-drive, `run` parks with a diagnostic naming the ambiguity;
  interactive modes STOP and ask.
- **Done → In Progress | Parked (reverse)**: a child re-opened after Status=Done (or
  whose merged PR was reverted) is re-eligible; on selection reset its Project Status off
  Done (→ In Progress, or Parked if it then parks).
- Announce the chosen child + one-line reason. Set Project Status = **In Progress**.

### 2. Drive (delegated implementation; per-repo recipe from epic.yaml + runbook)

Apply the child repo's `toolchain.prefix` to every build/test command. STOP if a
required prefix resolves empty (e.g. a repo whose toolchain needs an env prefix but the
config leaves it blank).

1. **Worktree** per HARD constraints.
2. **Context**: `gh issue view` the child; read `spec` + `runbook` from the
   `docs_repo` checkout. Do NOT skim sibling children.
3. **Step-1 pin (adversarial, iterative)**: dispatch a read-only adversarial
   reviewer subagent if your harness supports subagents (otherwise perform the
   review yourself inline, as a separate sequential pass) to attack the child's
   spec/runbook slice against current reality — verify every load-bearing claim
   against the relevant sources (other repos' code for API contracts, `origin/main`
   for drift since the docs merged; never curl — use the sandbox runner or `gh`).

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
   "blocking"}]`. `blocking: true` means the finding would cause the drive to
   build the wrong thing or fail verification; everything else is a residual,
   recorded in the PR body rather than looped on — `converge.py` computes every
   verdict solely from findings flagged `blocking: true`, so label consistently
   (this definition governs step 6's reviews too). Feed consecutive rounds to
   `python epic/scripts/converge.py`, which returns `converged`, `progress` or
   `no_progress`. On `converged`, stop. On `progress`, run another round. On
   `no_progress`, do NOT stop yet — run one more round to confirm: a single
   `no_progress` is not a stall, only two consecutive ones are. There is NO
   round budget. Two consecutive `no_progress` verdicts are a STALL — see the
   convergence contract below. Post the final pin — verified claims, every
   amendment, every assumption, AND any residual findings — as a child-issue
   comment before any code.
4. **Implementer subagent** (if supported; otherwise implement inline yourself,
   sequentially): TDD per runbook — failing tests first, implement, full
   suite green (toolchain commands from epic.yaml), commit.
5. **`pre-review` gates**: run each `custom_gates` entry whose hook is `pre-review`
   and whose `required_when` matches this child. Failure → interactive: STOP and hand
   off; `run`: park.
6. **Pre-PR adversarial reviews**: run two read-only reviews framed as
   devil's-advocate critiques — as parallel subagents if supported, otherwise
   performed inline, sequentially (the reviews still happen, in-session) — a
   spec-compliance reviewer (does the diff FULLY satisfy the child's spec/runbook,
   no gaps?) and a quality reviewer (logic bugs, security, missing tests,
   repo-convention violations). Both return the structured-finding JSON above.
   Implementer fixes; re-run BOTH reviewers on the amended diff, feeding each round
   to `converge.py`. Stop on `converged`; on `progress`, run another round; on
   `no_progress`, run one more round to confirm — a single `no_progress` is not
   a stall, only two consecutive ones are (same rule as step 3).
   Residual (non-blocking) nits are recorded in the PR body, not loop fuel. There
   is NO round budget. When work is delegated, trust-but-verify every
   subagent summary. The
   local CodeRabbit CLI pass is RETIRED here. The post-PR **Claude Review action** is a
   config-conditional gate: it applies only when `claude-review` is listed in the
   repo's `merge.required_checks` (then it fires automatically on PR open, step 7, and
   is resolved in the merge phase, step 1, as just another required check); when that
   check is absent, no Claude Review gate runs — skip it cleanly and note the skip, the
   same shape as the Copilot N/A path. CodeRabbit's bot review and Copilot are likewise
   gated in the merge phase, not run locally pre-PR.
7. **PR**: rebase on `origin/main`; `gh pr create` → base `main`, body `Closes #<n>`
   (same-repo — children's PRs always close issues in their own repo), summary, test
   plan including every `pr-test-plan` gate's record. Comment the PR URL on the
   child. When `claude-review` is listed in the repo's `merge.required_checks`, opening
   the PR automatically triggers the **Claude Review action**
   (`.github/workflows/claude-review.yml` — the `claude-review` required check): it
   reviews the head, posts inline comments, and submits a formal APPROVE /
   REQUEST_CHANGES review, resolved in the merge phase, step 1, as just another
   required check. When `claude-review` is not a required check, no such gate
   runs — skip it cleanly and note the skip. When
   `copilot_review` is enabled, request a Copilot review via the
   `requested_reviewers` call in the reference's "Review threads + Copilot review"
   section; a 422 means Copilot review is not available on this repo → treat Copilot
   as **N/A** (do not gate on it) and note that. Record whether the request succeeded
   (gates the Copilot wait/fix loop). Set Project Status = **In Review**.

### Convergence contract (replaces every review round budget)

A STALL is two consecutive `no_progress` verdicts from `converge.py`. A stall is
treated as a PIN FAULT until proven otherwise:

1. Re-run `verify_pin.py` over the pin; surface every `stale` claim.
2. Re-run the step-3 reviewer against the PLAN, not the diff, supplying the
   stalled findings as evidence.
3. Pin amends → post the amendment as a child comment, reset the convergence
   history, restart the loop. Bounded by `REPIN_ATTEMPTS` (1) — a second re-pin
   means the problem is not the pin. Pin does NOT amend (no `stale` claim, no
   plan defect found) → the pin is not the fault; skip straight to item 4.
4. Terminal handling — reached when the pin does not amend, or when a stall
   recurs after a re-pin → interactive: ASK the operator with the surviving
   findings and the suspect claims (via `AskUserQuestion` if your harness
   supports structured questions; otherwise as numbered plain-text questions,
   waiting for the reply); `run`: park.

The only hard ceiling is a resource: `CHILD_DRIVE_CEILING_S` (7200) from drive
start to merge-queue entry. CI/CodeRabbit/Copilot waits carry no deadline of
their own — `pr_watch.py` ticks indefinitely and a long silence is reported,
never parked. Time spent waiting at the head of the merge queue is NOT charged
to the child.

### 3. Merge phase (default) — or stop

**If `--stop-at-pr`:** report (child, worktree, branch, PR URL, gates, reviews —
including a **Claude review status** field = one of "pending" / "approved (green)"
/ "changes requested (red)" / "N/A (not required)" (the Claude Review gate applies
only when `claude-review` is listed in the repo's `merge.required_checks`; when
absent, skip it and note the skip. This mirrors the Copilot "N/A (not enabled)"
state), CodeRabbit status, and a **Copilot status** field = one of "not requested"
/ "requested, pending" / "clean" / "N/A (not enabled)") and STOP. Worktree stays
intact. Status stays In Review. No `--auto` was armed (it is armed only in the
merge phase), so nothing to disarm.

**Otherwise drive to merge, through the queue.** A child enters the merge phase
only at the head of the FIFO merge queue (`schedule.py`), and only one PR is ever
in the merge phase — width-1 is what lets each PR rebase exactly once, onto the
`main` its predecessor just produced.

1. **Ask GitHub what is unmet** — `python epic/scripts/mergeability.py --repo
   <owner/name> --pr <n>` returns the COMPLETE requirement set from the live
   ruleset plus current PR state. This is the authority; the repo's `epic.yaml`
   `merge` block is a declaration of intent that the script cross-checks and
   reports drift against. Resolve by code (where a bullet below says "park", that
   means: `run` follows the circuit-breakers below; interactive modes STOP and
   hand off with the same diagnostic):
   - `behind-base` → update the branch onto freshly fetched `origin/main`.
   - `conflict` → rebase and resolve. Budget `CONFLICT_ATTEMPTS`, which also
     bounds RECURRING `behind-base`: exceeding it means the base is moving faster
     than a CI cycle completes → park with exactly that diagnosis.
   - `check-failing:<name>` → diagnose from the run, dispatch the implementer if
     your harness supports subagents (otherwise make the fix inline), push.
   - `check-pending:<name>` → arm the watch and YIELD, never sleep and never
     block: `python epic/scripts/pr_watch.py --repo <owner/name> --pr <n>
     --reset-backoff`. It returns immediately with either
     `{"event":"activity",...}` (exit 0 — re-run `mergeability.py` and act) or
     `{"event":"waiting","next_tick_in_s":N}` (exit 1 — schedule the next tick
     at N seconds, clamped to your scheduler's range). There is no key to
     select and no deadline to set: the watch reports ANY PR activity and
     `mergeability.py` stays the sole authority on what is actionable.
   - `check-missing:<name>` → a required check never started; diagnose why
     (workflow trigger misconfigured? branch protection stale?) rather than
     waiting forever — if it still hasn't started, park with a diagnostic naming
     the missing check.
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
   - `blocked-unexplained:<state>` → the module's fail-closed backstop: a
     blocking `mergeStateStatus` (`BLOCKED`, `UNKNOWN`, or any value added later)
     that checks, threads, and review decision could not explain. Treat it as a
     genuine blocker, never as noise — GitHub is refusing the merge for a reason
     the driver could not derive. Re-run `mergeability.py` once after a short
     wait, since some states (notably `UNKNOWN`) are transient while GitHub
     computes mergeability. If it persists: disarm `--auto` if it was armed, then
     park with the raw `mergeStateStatus` value named in the diagnostic —
     interactive STOPs and hands off with the same diagnostic — since that string
     is what a human needs to diagnose it.
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
   the PR is MERGED. There is no partial satisfaction — every requirement the
   repo imposes must be resolved.

4. On MERGED (verify `state == MERGED`): confirm the child issue auto-closed
   (close it if not), Status = **Done**, sweep the worktree (remove + prune +
   delete branch). If this was the LAST child (epic now complete: every
   sub-issue `state == CLOSED`, none parked-open), apply the epic-completion
   rule: close the epic issue if still open and set the epic's own Project
   Status = **Done**.

5. Report; interactive modes then STOP (they drive ONE child only).

**HARD: before any STOP or park, if `--auto` was armed, run
`gh pr merge <pr> --disable-auto` and note the disarm in the report** — never
leave a STOPPED PR armed to auto-merge unresolved findings.

## Mode: `run` (autonomous — epic to completion)

An orchestrator loop. Each cycle:

1. Recover ALL state from `gh` — stateless recovery is unchanged and HARD.
2. `python epic/scripts/schedule.py --epic <n> --repo <owner/name> --max-parallel
   <epic-config.max_parallel>` → the runnable wave, the FIFO merge queue, and any
   halt reason (`--repo` names the epic's own home repo, already resolved in step 1
   — never inferred from cwd, since the epic may be homed in a separate planning
   repo from the checkout you are standing in). ALWAYS pass `--max-parallel` from
   the resolved config — `schedule.py` defaults to 3 when the flag is omitted, so
   a configured `max_parallel` of, say, 5 is silently ignored unless this flag
   carries it through; `schedule.py`'s `runnable()` is the ONLY place the global
   cap is enforced (see "Concurrency is capped twice" below).
3. Dispatch one **drive subagent per wave member, in parallel**, if your harness
   supports subagents; otherwise drive the wave sequentially, in-session, in the
   wave order `schedule.py` returned (a child without a PR yet isn't in the merge
   queue, so the merge queue can't order this step). `--serial` forces the
   sequential path. **Before each child's drive subagent starts** (or, in the
   serial path, before driving it inline), set that child's Project Status =
   **In Progress** — HARD, not optional: `schedule.py`'s `in_flight` count,
   `runnable()`'s own-child exclusion, and `halt_reason()`'s live-work escape all
   key off this Status value. Skipping it means a dispatched-but-not-yet-PR-opened
   child looks identical to an untouched Todo child on the very next cycle, so it
   gets dispatched a SECOND time — two drive subagents then race on the same
   worktree/branch/PR, and the second one's preflight STOPs on `worktree-exists`.
4. Marshal the merge queue: admit `merge_queue[0]` ONLY, run the merge phase for
   it — its requirement-resolution step (§3 "Ask GitHub what is unmet") re-derives
   what's unmet straight from `gh` and `mergeability.py` every time it runs, so a
   child resumed mid-merge on a later wake needs no separate resume path, it just
   re-enters that step where GitHub's live state says it is — then recompute.
5. Reschedule. The wake delay is the MINIMUM `next_tick_in_s` across all live
   watches, clamped to your scheduler's range (`ScheduleWakeup` has a 60s
   floor, so early ticks clamp up; an in-session loop honours them as-is).
   One scheduler for the whole epic, never one per child. No eligible unparked child left and the
   epic complete (every sub-issue `state == CLOSED`, none parked-open, and the
   sub-issue list is non-empty — an EMPTY children fetch is NEVER completion; treat
   it as a recovery failure and STOP to diagnose, since `epic_complete([])` is
   vacuously true and closing a live epic on a bad fetch is unrecoverable) → apply
   the epic-completion rule, final report, TERMINATE.

**Merging is the one serialized step.** Everything upstream of it — worktree
setup, context, pin, implementation, gates, pre-PR reviews, PR-open, prose-gate
resolution — runs across the whole wave concurrently. Only the merge phase is
width-one: strict up-to-date checks mean two PRs merging at once would invalidate
each other's rebase, so the FIFO merge queue admits `merge_queue[0]` alone,
letting each PR rebase exactly once onto the `main` its predecessor just
produced. Parallelism buys the wave everything before that line.

**A drive subagent owns exactly one child** — dispatched per wave member if your
harness supports subagents, otherwise executed inline, sequentially, in the wave
order `schedule.py` returned (`--serial` forces this path even when subagents
are available): worktree → context → pin → implement → `pre-review` gates → pre-PR
reviews → PR-open → Status = In Review → prose-gate resolution, ending when every
prose gate is clean. It NEVER rebases for merge, NEVER arms `--auto`, NEVER
merges, and never touches another child's worktree or branch.

**Prose-gate resolution yields rather than waits.** Bot-review latency (CI,
CodeRabbit, Copilot, Claude Review) is the largest single cost in a child's
lifecycle, but a drive subagent cannot survive a scheduled wake if your
harness supports subagents — so a subagent that has nothing left to do but
wait arms the watch and RETURNS with a `waiting` outcome naming the PR and its
`next_tick_in_s`; otherwise the same step, run inline, arms the watch and
returns control to the run loop the same way. The run loop owns every tick and
re-drives the child — via a fresh subagent or by re-entering the inline step —
when activity fires. The child is left at Status = In Review with an open PR,
which is exactly the state the run loop re-derives from `gh` on the next wake.
Nothing sits idle, and a killed session loses at most one cursor.

**Concurrency is capped twice**: globally by `epic-config.max_parallel`
(`MAX_PARALLEL`, default 3) across all involved repos — enforced by
`schedule.py`'s `runnable()` when it computes the wave — and per repo by
that repo's `worktrees.max_concurrent`, enforced by `preflight.py`'s
`check()` via its `concurrency-cap` violation before each child's drive
starts.

**Unattended invariants:** no interactive questions of any kind (on an
architectural fork: pick the
most conservative defensible default — smallest blast radius, reversible, matches
existing repo patterns (e.g. extend an existing module over introducing a new service) —
record decision + rationale in PR body and a child comment; park only if no defensible
default exists. HARD EXCEPTION: for a contract/API-defining child or a P0 child, NEVER
auto-decide an architectural fork — park for human input instead; a wrong default there
is consumed downstream before any human sees it). Stateless — recover ALL
state each wake from `gh` (epic-config, sub-issues, blockedBy, Project fields,
PR map, `git worktree list`). On any resume, confirm each in-flight branch is
fast-forwardable with a clean working tree before touching it; on divergence,
rebase/reconcile within `CONFLICT_ATTEMPTS` — NEVER force-overwrite. **A child
Status = In Progress with no PR and an existing `git worktree list` entry for it
is a resume candidate, not a permanent live-work signal**: `schedule.py`'s
`runnable()` deliberately excludes it from the wave (it is what `in_flight`
counts), so `run` does not re-dispatch it through the normal wave path — but
if its worktree is confirmed idle (no drive step for it is active THIS cycle —
whether that step ran as a subagent, if your harness supports them, or inline;
otherwise the same idleness check applies to the single in-session driver), resume
it directly rather than leaving it to sit forever behind `halt_reason()`'s
live-work escape. Left
unresumed indefinitely, an abandoned In-Progress child is a silent stall: the
escape correctly keeps the epic from false-halting on it, but nothing ever
drives it to completion either — treat a child in this state that has
exceeded `CHILD_DRIVE_CEILING_S` since its Status last changed, with no active
drive, as due for resume on the very next cycle. **Never
STOP or park with `--auto` armed** — run `gh pr merge <pr> --disable-auto`
first and record it.

Tunables (do not exceed): `MAX_PARALLEL=3`, `STALL_ROUNDS=2`, `REPIN_ATTEMPTS=1`,
`WATCH_FLOOR_S=15`, `WATCH_MULT=1.8`, `WATCH_CEIL_S=900`,
`CHILD_DRIVE_CEILING_S=7200`, `CONFLICT_ATTEMPTS=2`, `PARK_SIGNATURE_THRESHOLD=3`.
`STALL_ROUNDS` names the convergence contract's two-consecutive-`no_progress`
threshold; `WATCH_FLOOR_S`/`WATCH_MULT`/`WATCH_CEIL_S` are `pr_watch.py`'s own
jittered-exponential-backoff parameters (floor, multiplier, ceiling) for the
delay it requests between ticks; the rest are shared with the per-child
mechanics described above.

CI/CodeRabbit/Copilot waits are never bounded by a deadline. A watch ends only
when the PR stops being OPEN, when `pr_watch.py --stop` is run, or when the run
terminates. A long silence is REPORTED by `status.py` (`watches[]`: child, PR,
`quiet_s`, and the facet that last moved) and is never a reason to park.

**Circuit breakers:**
- Convergence stall after re-pin / gate unfixable / a resource ceiling exceeded /
  a subagent BLOCKED with no defensible path (if your harness runs steps via
  subagents — otherwise an inline step stalled the same way) → **park**: if
  `--auto` is armed, FIRST run `gh pr merge <pr> --disable-auto`; comment
  `FAILED: <precise reason + evidence URLs>` on the child, with a machine-readable
  trailer `epic-park: {"code":…, "gate":…, "signature":…, "waiting_on_human":…}`;
  set Status = **Parked**; leave worktree + PR intact. **Siblings continue** — a
  park never stalls the wave, and the merge queue keeps draining around it.
- **Armed-but-refusing**: the head-of-queue PR has `--auto` armed and
  `mergeability.py` reports an EMPTY requirement set, yet the PR still has not
  merged within one hour (3600s) measured from the PR's own
  `autoMergeRequest.enabledAt` (as reported by `gh`, not from when this cycle
  happened to notice it) — GitHub is refusing a merge that
  nothing in the visible requirement set explains (an org-level ruleset, a
  GitHub-side merge queue, a required deployment). Disarm
  (`gh pr merge <pr> --disable-auto`), then park with a diagnostic recording
  that GitHub reports nothing outstanding yet refuses to merge, naming the
  current `mergeStateStatus`, with the same `epic-park:` trailer as above;
  interactive modes STOP and hand off with the same diagnostic. This unblocks
  the queue: the next child is admitted to the merge phase on the following
  cycle.
- `approval-missing` parks are `waiting_on_human: true` and are excluded from the
  systemic-cause count: an epic correctly waiting on your approval is working.
- **Global halt** (no reschedule, full report) exactly when `schedule.py` returns
  a halt reason: `systemic:<signature>` (`PARK_SIGNATURE_THRESHOLD` parks sharing
  one signature — assume CI down, `main` broken or a ruleset change, and
  re-verify via `gh api repos/<owner>/<repo>/rules/branches/main`),
  `no-runnable-work`, or `transitive-block`.

## Failure handling

- Blocked child requested explicitly → name the unmet blockers, STOP.
- Subagent BLOCKED/NEEDS_CONTEXT (if your harness runs steps via subagents;
  otherwise treat an inline step's BLOCKED/NEEDS_CONTEXT failure the same way) →
  more context / stronger model / split / escalate; never silently retry.
- Repo-specific caveats (emulator/simulator wedges, screenshot determinism, third-party
  API rate limits, migration safety) → `toolchain.notes` + `gates` in that repo's
  epic.yaml.
- Epic body edits are NEVER needed for ticking — sub-issue closure updates
  `subIssuesSummary` automatically; the Project board is the human-facing view.
- Transient `gh`/GraphQL failures (5xx, timeout, rate limit) → follow the
  `## Error handling` policy in `references/github-graphql.md`: classify transient
  vs permanent, never blind-retry a mutation (re-verify first), sleep-to-reset on
  rate limits, and in `run` mode park the child on exhausted retries rather than
  crashing the loop.
