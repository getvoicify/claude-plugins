---
description: Brainstorm a new epic into existence (spec, runbook, sub-issues, Project)
argument-hint: "[rough idea]"
---

You are running an epic-creation brainstorm. The input is a rough idea (from
`$ARGUMENTS` or asked for if empty); the product is a fully materialized epic: a
reviewed spec + runbook landed via a docs PR, an epic issue in the planning repo
`getvoicify/gangan` with a slim `epic-config` block, child issues in their working
repos linked as sub-issues with native blocked-by relations, and every item on org
Project #2 with Status/Priority set.

Read `${CLAUDE_PLUGIN_ROOT}/references/github-graphql.md` before the materialize
phase — it has every mutation, ID, and gotcha (`blockingIssueId`, not
`blockedByIssueId`).

This is a CONVERSATION with distinct phases. Do not rush to materialize; nothing is
created on GitHub until the operator approves the full breakdown in phase 4.

## Phase 1 — Diverge (understand the problem)

1. Restate the rough idea. Ask the operator (via `AskUserQuestion`, batched) about:
   the user/business problem, success criteria, hard constraints, deadline pressure,
   and which repos are plausibly involved (gangan-api / gangan-mobile /
   gangan-angular-workspace — multi-select).
2. Spawn read-only Explore subagents over each implicated repo to map the affected
   surface: existing modules, prior art, related specs under `docs/superpowers/`,
   open issues/PRs touching the same area (`gh search`).
3. Surface 2–3 distinct solution approaches with trade-offs. Present them; let the
   operator pick or blend. Challenge scope — actively propose what to CUT.

## Phase 2 — Converge (shape the work)

1. Agree the scope boundary: in / out / later.
2. Break the work into children, each: a crisp deliverable, one repo, mergeable as a
   single PR, TDD-able, sized ≤ ~1 day of driving. Cross-repo epics are normal —
   contract-first ordering (e.g. API child blocks mobile/web children consuming it).
3. Draw the dependency graph (these become native blocked-by relations — keep it a
   DAG, prefer shallow). Assign Priority (P0 = blocks the epic, P1 = core, P2 = nice).
4. For each child, check the target repo's `.claude/epic.yaml` gate catalog: tag the
   `custom_gates` whose `required_when` will match (e.g. migrations →
   `db-migration-safety`; iOS-touching → `ios-simulator-smoke`; UI → `axe-a11y-audit`).

## Phase 3 — Draft (spec + runbook)

1. Pick the `docs_repo` = the working repo where most children land (ask if unclear).
2. Write two files into a branch of the `docs_repo` checkout, following its
   `.claude/epic.yaml` `docs` dirs and existing naming convention
   (`YYYY-MM-DD-<slug>-{design,plan}.md` style — mirror neighbors):
   - **Spec** (`docs.spec_dir`): problem, chosen approach + rejected alternatives,
     architecture/contract decisions, success criteria, out-of-scope list.
   - **Runbook** (`docs.runbook_dir`): per-child recipe — intent, files/modules in
     play, TDD order, verification commands (use the repo's `toolchain`), gate notes.
3. Present both drafts for operator review. Iterate until approved.

## Phase 4 — Review (the gate before anything touches GitHub)

Present the complete materialization plan as one table: child title · repo ·
blockers · Priority · gates, plus the epic title and `worktree_prefix` (kebab-slug,
operator can override). Require explicit approval. Any edit → update and re-present.

## Phase 5 — Materialize (only after approval)

1. **Docs PR**: push the branch, `gh pr create` in `docs_repo` (conventional commit
   subject per that repo's standards). Record the PR URL.
2. **Epic issue** in `getvoicify/gangan`: title `Epic: <name>`; body = a short
   abstract, link to spec/runbook paths + docs PR, and the fenced `epic-config`:

   ```yaml
   epic: <assigned after creation — edit the body to backfill>
   repo: getvoicify/gangan
   docs_repo: <owner/name>
   worktree_prefix: <kebab>
   spec: <docs.spec_dir>/<file>.md
   runbook: <docs.runbook_dir>/<file>.md
   custom_gates: [<union of children's gates>]
   ```

   Create, then immediately `gh issue edit` to backfill `epic:` with the real number.
   Tag with the Epic issue type if the org has one (skip silently if not).
3. **Children**: create each issue in its working repo — body: deliverable,
   acceptance criteria, runbook section pointer, gate tags. Then:
   - `addSubIssue` each to the epic (cross-repo works);
   - `reprioritizeSubIssue` to match the agreed drive order;
   - `addBlockedBy` per the dependency graph;
   - add epic + every child to Project #2 (`addProjectV2ItemById`, idempotent),
     set Status = Todo and the agreed Priority on each.
4. **Verify** (trust-but-verify your own mutations): re-query the epic's `subIssues`
   + each child's `blockedBy` + `projectItems` and diff against the approved plan.
   Fix any gap before reporting.
5. **Report**: epic URL, docs PR URL, child URLs with their blockers/priorities, and
   the suggested first command: `/epic <n> next` (after the docs PR merges — the
   driver reads spec/runbook from `origin/main` of `docs_repo`).

## Constraints

- Never create GitHub objects before phase-4 approval. If the session dies mid-
  materialize, re-running phase 5 must be idempotent: check for an existing epic
  issue with the same title before creating; `addSubIssue`/`addProjectV2ItemById`
  tolerate re-runs.
- Child issues must be self-sufficient for a driver session that reads ONLY that
  child + spec + runbook (the `/epic` driver does not skim siblings).
- Respect each repo's commit-message and PR conventions (e.g. gangan-api requires a
  `[GAN-NNN]` ticket key).
