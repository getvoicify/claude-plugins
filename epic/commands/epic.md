---
description: Drive a GitHub epic (sub-issues + org Project) one child at a time
argument-hint: "<epic#> [status | next | run | <child#>] [--stop-at-pr] [--sweep]"
---

You are driving a GitHub epic. Epics are issues with **native sub-issues** as children
and **native blocked-by relations** as the dependency graph; live state is tracked in
**the configured org project** (epic-config `project`, default #2 "Gangan"). New epics are homed in the planning repo
`getvoicify/gangan`; children live in the working repos. Legacy epics may be homed
elsewhere — the `repo` field of the epic-config decides.

Read `${CLAUDE_PLUGIN_ROOT}/references/github-graphql.md` NOW — every GraphQL
incantation, project/field ID, lifecycle table, and the PR-mapping rule live there.

A `status` invocation only reports. A `next` / `<child#>` invocation drives at most
ONE child **through merge + sweep + Status=Done**, then STOPs. `--stop-at-pr` opts out
of the merge phase (stop at PR-open, Status=In Review) — forbidden on `run`. A `run`
invocation loops the same per-child mechanics autonomously until the epic is done.

## Arguments

```
/epic <epic#> [status | next | run | <child#>] [--stop-at-pr] [--sweep]
```

- `<epic#>` required. With no argument: list open epics and STOP —
  `gh search issues --owner getvoicify --state open "type:Epic" --json number,title,repository`
  falling back to label `tracking-epic` if issue types are not in use.
- Mode defaults to `status`.
- `--stop-at-pr`: valid on `next`/`<child#>` only. On `run` → STOP: "`--stop-at-pr` is
  not compatible with `run`; drop the flag or use `next`."
- `--sweep`: valid on `status` only. Opts into the destructive reconcile (worktree
  removal, branch deletion, Project-field mutation). Without it `status` is pure
  read-only and only REPORTS the drift + what `--sweep` would change.

## Two-layer config — load fresh EVERY invocation (stateless recovery)

### Layer 1: per-epic `epic-config` (epic issue body)

1. `gh issue view <epic#> --repo getvoicify/gangan --json body,state,title,number`
   (if not found there, try the cwd repo — legacy epics are homed in working repos).
2. Parse the fenced `epic-config` YAML block STRICTLY (python + pyyaml via the sandbox
   runner; `pip install --break-system-packages --quiet pyyaml` only on ImportError;
   never regex-only extraction). Keys:
   - `epic` (int) — must equal `<epic#>` (mismatch → STOP).
   - `repo` (`owner/name`) — where THIS epic issue lives.
   - `project` (int, optional) — org ProjectV2 number for status tracking; omit to
     default to `2` (Gangan). When set to another number, the driver re-resolves
     projectId + field & option ids at runtime (see github-graphql.md).
   - `docs_repo` (`owner/name`) — working repo where `spec`/`runbook` paths resolve.
   - `worktree_prefix` — must match `^[a-z0-9]+(-[a-z0-9]+)*$` (else STOP: "invalid
     worktree_prefix (must be kebab-case)"); guards every shell interpolation.
   - `spec`, `runbook` — paths relative to `docs_repo` root.
   - `custom_gates` (list, optional, default `[]`) — epic-level UNION of all children's
     gate names across repos; resolved PER-CHILD-REPO against that child's
     `.claude/epic.yaml` gate catalog (Layer 2). A name absent from the CURRENT child's
     repo catalog but present in another involved repo's is SKIPPED for this child (not
     fatal); a name unknown in EVERY involved repo's catalog → see "Missing or malformed
     config".
3. NO `children_source` key and NO `## Dependency model` section are expected:
   children come from the sub-issue API; blockers from native `blockedBy` relations.
   If the body still has a task-list (`- [ ] #NNN`) → STOP: "legacy epic — run
   `/epic:migrate <epic#>` first."

### Layer 2: per-repo `.claude/epic.yaml` (each child's repo checkout)

Carries what varies per repo: `toolchain` (commands + prefix + notes), `merge`
(method, `required_checks`, approvals, `require_last_push_approval`,
`required_review_thread_resolution`, `copilot_review` (bool — when true the driver
requests a Copilot review at PR creation and gates merge on its threads), strictness),
`docs` dirs, `worktrees`
(root, `max_concurrent`), and the `gates` catalog (each: `hook` = `pre-review` |
`pr-test-plan`, `required_when`, `procedure`). Load it from the checkout of whichever
repo the CURRENT child lives in. Missing file → STOP: "repo <owner/name> has no
`.claude/epic.yaml` — author it before driving children there."

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
  the precise gap; `AskUserQuestion` per missing field with defaults
  (`worktree_prefix` → kebab-slug of the epic title; `spec`/`runbook` → glob
  `<docs.spec_dir|runbook_dir>/*<slug>*.md` in `docs_repo`; a gate name unknown in EVERY
  involved repo's catalog → offer the catalog as multi-select + "drop it"; a truly novel
  gate is NOT inventable — STOP: "add gate `<name>` to `<repo>/.claude/epic.yaml` first"
  (a name valid in another involved repo's catalog is SKIPPED for the current child,
  never an error)). Offer to persist the
  repaired block via `gh issue edit --body-file`; declined → use for this invocation
  only.
- **`run` mode**: no `AskUserQuestion` ever. Hard-stop: "`run` aborted: epic #<n>
  config missing/incomplete (`<field>`). Run `/epic <n> status` once interactively,
  then re-invoke."

## Worktree constraints (HARD — every drive, enforced in the child's repo checkout)

1. **Deterministic**: one worktree per child at `<worktrees.root>/${worktree_prefix}-<n>`
   on branch `${worktree_prefix}-<n>`, from latest `origin/main`. Never reuse for a
   different issue, never elsewhere (especially not `.claude/worktrees/`), never rename.
2. **No nesting / clean origin**: never start from `main`, inside another worktree, or
   detached HEAD. If currently inside one → STOP, do not nest.
3. **Per-issue uniqueness**: existing worktree/branch `${worktree_prefix}-<n>` → STOP
   (already being driven).
4. **Concurrency cap**: count `${worktree_prefix}-*` worktrees (`git worktree list`);
   ≥ `worktrees.max_concurrent` (default 3) → STOP.
5. **Auto-clean on merge ONLY**: never remove mid-flight/on-failure/at STOP; remove
   only after the child's PR is MERGED.

## Epic-completion lifecycle (all modes)

The epic issue has its OWN item on the configured org project (epic-config `project`, default #2) — the driver owns its Status just
like the children's. Every invocation MUST observe it: fetch the epic node's
`projectItems` + Status alongside the sub-issues (the discovery query in the reference
includes it); epic not on the configured project → `addProjectV2ItemById` (idempotent) before any
Status write. Definitions used by every rule below: a child is **parked-open** when its
issue is OPEN with Project Status = Parked; the epic is **complete** when EVERY
sub-issue's `state == CLOSED` (iterate `subIssues.nodes[].state` — do NOT use
`subIssuesSummary.completed`, which can undercount closed-as-not-planned children).

1. **Drift self-heal**: epic issue CLOSED **and** complete, but its Project Status ≠
   Done → set it to Done immediately — in every mode EXCEPT plain `status` without
   `--sweep`, which only REPORTS the drift (read-only contract). Epic CLOSED while any
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
- **Done → In Progress | Parked (reverse)**: a child re-opened after Status=Done (or
  whose merged PR was reverted) is re-eligible; on selection reset its Project Status off
  Done (→ In Progress, or Parked if it then parks).
- Announce the chosen child + one-line reason. Set Project Status = **In Progress**.

### 2. Drive (subagent-driven development; per-repo recipe from epic.yaml + runbook)

Apply the child repo's `toolchain.prefix` to every build/test command. STOP if a
required prefix resolves empty (e.g. JAVA_HOME on gangan-mobile).

1. **Worktree** per HARD constraints.
2. **Context**: `gh issue view` the child; read `spec` + `runbook` from the
   `docs_repo` checkout. Do NOT skim sibling children.
3. **Step-1 pin (adversarial, iterative)**: dispatch a read-only adversarial
   reviewer subagent to attack the child's spec/runbook slice against current
   reality — verify every load-bearing claim against the relevant sources (other
   repos' code for API contracts, `origin/main` for drift since the docs merged;
   never curl — use the sandbox runner or `gh`). Where it finds defects, amend the
   plan via the pin (merged docs are not edited), then RE-RUN the reviewer on the
   amended plan — one round is often not enough. Loop until a round returns zero
   BLOCKING findings (blocking = would cause the drive to build the wrong thing or
   fail verification; everything else is a residual), budget `PLAN_REVIEW_ROUNDS`
   (default 3); exhausted with blocking findings still open → interactive: STOP and
   hand off; `run`: park. Post the final pin — verified claims, every amendment,
   AND any residual findings — as a child-issue comment before any code.
4. **Implementer subagent**: TDD per runbook — failing tests first, implement, full
   suite green (toolchain commands from epic.yaml), commit.
5. **`pre-review` gates**: run each `custom_gates` entry whose hook is `pre-review`
   and whose `required_when` matches this child. Failure → interactive: STOP and hand
   off; `run`: park.
6. **Pre-PR adversarial reviews**: run two read-only subagents framed as
   devil's-advocate critics — a spec-compliance reviewer (does the diff FULLY
   satisfy the child's spec/runbook, no gaps?) and a quality reviewer (logic bugs,
   security, missing tests, repo-convention violations). Implementer fixes; re-run BOTH
   reviewers on the amended diff until a single round returns zero BLOCKING findings
   from both (same blocking definition as step 3; residual nits are recorded in the
   PR body, not loop fuel) — one round is often not enough (a fix can introduce new
   defects). Budget
   `PRE_PR_REVIEW_ROUNDS` (default 3); exhausted with blocking findings open → interactive:
   STOP and hand off; `run`: park. Trust-but-verify every subagent summary. The
   local CodeRabbit CLI pass is RETIRED here — the **Claude Review action** is now
   the primary post-PR review gate (fires automatically on PR open, step 7; gated
   in the merge phase, step 3). CodeRabbit's bot review and Copilot are likewise
   gated in the merge phase, not run locally pre-PR.
7. **PR**: rebase on `origin/main`; `gh pr create` → base `main`, body `Closes #<n>`
   (same-repo — children's PRs always close issues in their own repo), summary, test
   plan including every `pr-test-plan` gate's record. Comment the PR URL on the
   child. Opening the PR automatically triggers the **Claude Review action**
   (`.github/workflows/claude-review.yml` — the `claude-review` required check): it
   reviews the head, posts inline comments, and submits a formal APPROVE /
   REQUEST_CHANGES review; it is the PRIMARY review gate driven in step 3. When
   `copilot_review` is enabled, request a Copilot review via the
   `requested_reviewers` call in the reference's "Review threads + Copilot review"
   section; a 422 means Copilot review is not available on this repo → treat Copilot
   as **N/A** (do not gate on it) and note that. Record whether the request succeeded
   (gates the Copilot wait/fix loop). Set Project Status = **In Review**.

### 3. Merge phase (default) — or stop

**If `--stop-at-pr`:** report (child, worktree, branch, PR URL, gates, reviews —
including a **Claude review status** field = one of "pending" / "approved (green)" /
"changes requested (red)" derived from the `claude-review` check conclusion + the
formal review state (see the reference's "Claude Review action" section), CodeRabbit
status, and a **Copilot status** field = one of "not requested" /
"requested, pending" / "clean" / "N/A (not enabled)" with any unresolved comment
count, derived from the Copilot review-state query in the reference) and STOP.
Worktree stays intact. Status stays In Review. No `--auto` was armed (it is armed only
in the merge phase), so nothing to disarm.

**Otherwise drive to merge.** Arm `--auto` LAST — only after every PROSE gate is
satisfied. Do NOT arm it at the start (an armed PR would merge the instant CI goes
green with findings still open).

1. **Prose-gate resolution (BEFORE arming `--auto`):** drive these to clean first —
   - **Claude Review action (PRIMARY gate)** → the `claude-review` required check
     carries Claude's verdict on the LATEST head: red = REQUEST_CHANGES (or no
     verdict — the check is fail-closed), green = APPROVE. On REQUEST_CHANGES, read
     the inline review comments + the formal review body, address them via the
     implementer, push, then wait for the re-review of the new head (every push
     re-triggers it). Budget `CLAUDE_REVIEW_FIX_ROUNDS`. NEVER arm `--auto` while
     `claude-review` is red.
   - CodeRabbit `CHANGES_REQUESTED` (where review/approval is required) → address via
     implementer, push; each push dismisses stale approvals — always wait for review
     of the LATEST head. Budget `CODERABBIT_FIX_ROUNDS`.
   - GitHub Copilot review (ONLY if the PR-time request succeeded — skip cleanly if
     N/A): every Copilot comment MUST be resolved before merge: address via
     implementer, push, then resolve the thread via `resolveReviewThread` (re-request
     review of the LATEST head per the reference's "Review threads + Copilot review"
     section). Budget `COPILOT_FIX_ROUNDS`.
   - `required_review_thread_resolution: true` repos: resolve every thread.
   - all `custom_gates` whose hook gates merge.
2. **Arm `gh pr merge <pr> --auto --<merge.method>`** — now that every prose gate is
   confirmed satisfied — then run the CI fix-loop (CI fix-rounds MAY happen after
   arming; prose-gate resolution must NOT):
   - required check failing → diagnose from the run, dispatch implementer, push.
     Budget `CI_FIX_ROUNDS`.
   - behind strict `main` / conflict → rebase + resolve. Budget `CONFLICT_ATTEMPTS`.
     Conflicts are never terminal until the budget is exhausted.
   - a stuck CI check that never posts a verdict → poll up to `MAX_WAIT_CYCLES`.
3. On MERGED (verify `state == MERGED`): confirm the child issue auto-closed (close it
   if not), Status = **Done**, sweep the worktree (remove + prune + delete branch). If this was the LAST child (epic now complete: every sub-issue `state == CLOSED`,
   none parked-open), apply the epic-completion rule: close the epic issue if still
   open and set the epic's own Project Status = **Done**.
4. Report and STOP (interactive modes drive ONE child only).

Budgets exhausted (interactive) → STOP with a precise diagnostic; leave PR + worktree.
**HARD: before any such STOP, if `--auto` was armed, run
`gh pr merge <pr> --disable-auto` and note the disarm in the report** — never leave a
STOPPED PR armed to auto-merge unresolved findings.

## Mode: `run` (autonomous — epic to completion)

Same per-child mechanics in a self-scheduling loop. One child in flight at a time
(strict up-to-date checks make concurrent PRs invalidate each other).

**Unattended invariants:** no `AskUserQuestion` (on an architectural fork: pick the
most conservative defensible default — smallest blast radius, reversible, matches
existing repo patterns (e.g. extend an existing module over introducing a new service) —
record decision + rationale in PR body and a child comment; park only if no defensible
default exists. HARD EXCEPTION: for a contract/API-defining child or a P0 child, NEVER
auto-decide an architectural fork — park for human input instead; a wrong default there
is consumed downstream before any human sees it). Stateless — recover ALL
state each wake from `gh` (epic-config, sub-issues, blockedBy, Project fields,
PR map, `git worktree list`). **Never STOP or park with auto-merge armed unless every
prose gate is confirmed satisfied** — on any park/STOP that leaves a PR behind while
`--auto` is armed, run `gh pr merge <pr> --disable-auto` first and record it in the
FAILED/park comment. Tunables (do not exceed): `CI_ESTIMATE=420s`,
`CI_FIX_ROUNDS=3`, `PLAN_REVIEW_ROUNDS=3`, `PRE_PR_REVIEW_ROUNDS=3`,
`CLAUDE_REVIEW_FIX_ROUNDS=3`, `CODERABBIT_FIX_ROUNDS=3`,
`COPILOT_FIX_ROUNDS=3`,
`CONFLICT_ATTEMPTS=2`, `MERGE_WAIT_CYCLES=4`, `MAX_WAIT_CYCLES=12`,
`GLOBAL_PARK_THRESHOLD=3`, `CONSECUTIVE_PARK_HALT=2`.

**Per-cycle:** recover & select (no eligible, unparked child left → if the epic is complete (all children CLOSED, none parked-open) apply the epic-completion rule (close epic if open + epic Status=Done); then final report, TERMINATE) → resume check (open PR from a prior cycle → verify worktree/branch
integrity, then jump to fix-loop; closed-unmerged PR with worktree present →
closed-unmerged PR recovery) → preflight HARD checks → drive to PR → merge phase → on
MERGED: Status=Done + sweep → reschedule next cycle (`ScheduleWakeup`). On resume into a
fix-loop: fetch the PR head SHA and confirm the local branch is fast-forwardable with a
clean working tree before touching it; on divergence, rebase/reconcile within
`CONFLICT_ATTEMPTS` or park with a diagnostic — NEVER force-overwrite. Sleep only for
CI/CodeRabbit/Copilot waits
(~`CI_ESTIMATE`, then 300–600s wakes), capped at `MAX_WAIT_CYCLES` total wakes per
stuck wait; never idle-burn. A CI check or review verdict that never arrives within
`MAX_WAIT_CYCLES` → park the child with a precise diagnostic (name the check/reviewer
that never posted) rather than rescheduling forever.

**Circuit breakers:**
- Per-issue budget exhausted / gate unfixable / subagent BLOCKED with no defensible
  path / `MAX_WAIT_CYCLES` exceeded → **park**: if `--auto` is armed, FIRST run
  `gh pr merge <pr> --disable-auto`; comment `FAILED: <precise reason + evidence URLs>`
  on the child (the durable parked-marker; note the disarm), set Status = **Parked**,
  leave worktree + PR intact, continue with the next unblocked child. Children blocked
  by a parked child are skipped.
- Merge-deadlock (green but unmerged after `MERGE_WAIT_CYCLES`) → disarm `--auto`
  (`gh pr merge <pr> --disable-auto`), then park with a diagnostic naming the actual
  unmet gate (approval source? strict up-to-date? thread resolution?) per the repo's
  epic.yaml `merge` block.
- **Global halt** (no reschedule, full report): `CONSECUTIVE_PARK_HALT` consecutive
  parks, OR total parked ≥ `GLOBAL_PARK_THRESHOLD`, OR a parked child that every
  remaining child transitively depends on (compute from the `blockedBy` graph).
  Assume systemic cause (CI down, `main` broken, ruleset changed — re-verify
  epic.yaml `merge` facts against the live ruleset:
  `gh api repos/<owner>/<repo>/rules/branches/main`).

## Failure handling

- Blocked child requested explicitly → name the unmet blockers, STOP.
- Subagent BLOCKED/NEEDS_CONTEXT → more context / stronger model / split / escalate;
  never silently retry.
- Repo-specific caveats (emulator/simulator wedges, screenshot determinism, Paystack
  rate limits, migration safety) → `toolchain.notes` + `gates` in that repo's
  epic.yaml.
- Epic body edits are NEVER needed for ticking — sub-issue closure updates
  `subIssuesSummary` automatically; the Project board is the human-facing view.
- Transient `gh`/GraphQL failures (5xx, timeout, rate limit) → follow the
  `## Error handling` policy in `references/github-graphql.md`: classify transient
  vs permanent, never blind-retry a mutation (re-verify first), sleep-to-reset on
  rate limits, and in `run` mode park the child on exhausted retries rather than
  crashing the loop.
