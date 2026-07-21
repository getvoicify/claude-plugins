---
description: Convert a legacy task-list epic to sub-issues + org Project
argument-hint: "<epic#> [--repo owner/name]"
---

You are migrating a legacy epic (task-list children in the issue body, old fat
`epic-config`, `## Dependency model` prose) to the new model: native sub-issues,
native blocked-by relations, configured-project tracking (epic-config `project`, default #2), slim `epic-config`. After
migration the `/epic` driver speaks ONLY the new model.

Read `${CLAUDE_PLUGIN_ROOT}/references/github-graphql.md` first — all mutations, IDs,
the PR-mapping rule, and the `blockingIssueId` gotcha live there.

## Arguments

`<epic#>` required. `--repo owner/name` optional — defaults to the cwd repo's
`nameWithOwner` (legacy epics live in working repos, e.g. gangan-api #278–#282).
The epic issue is NOT moved; `epic-config.repo` records where it lives.

## Procedure

### 1. Read & parse (no mutations yet)

1. `gh issue view <epic#> --repo <repo> --json body,title,state,number`.
   ALREADY-MIGRATED BRANCH (check before parsing): if the body is already slim — the
   task-list section AND the `## Dependency model` section are both absent — do NOT
   re-parse or rewrite (the live body no longer carries the legacy edges/ticks, so
   re-parsing it would wipe state with nothing in-band to recover from). Instead:
   - if prior state recovery is needed, recover it from the "pre-migration body, for
     the record" backup comment;
   - otherwise report "already migrated" and STOP.
   If NEITHER a parseable (non-slim) body NOR a backup comment is present, STOP with a
   diagnostic — do not proceed and risk destroying unrecoverable state.
2. Parse the legacy `epic-config` YAML block strictly (pyyaml via sandbox runner).
   Carry forward: `worktree_prefix`, `spec`, `runbook`, `custom_gates`, and optional `project`. Map
   `children_source`:
   - `task-list` → parse `- [ ] #NNN` / `- [x] #NNN` lines (the `#NNN` is the child;
     `[x]` = operator considered it done).
   - `{label: <name>}` → `gh issue list --repo <repo> --label <name> --state all`.
3. Parse the `## Dependency model` prose. Extract every explicit edge ("#A blocks
   #B", "F1 blocks ALL", priority orderings). This is interpretation of prose —
   list every inferred edge for confirmation in step 2.
4. Build the child→PR map per the PR-mapping rule (worktree-prefix-filtered,
   `closingIssuesReferences` via GraphQL) to infer per-child reality:
   merged-PR → Done · open-PR → In Review · `FAILED:` comment present → Parked ·
   else → Todo. Cross-check `[x]` ticks: a tick without a MERGED PR is suspicious —
   flag it, don't trust it.

### 2. Confirm with the operator (the only gate)

Present one table: child · title · inferred Status · inferred blockers · tick state ·
PR (state). Ask via `AskUserQuestion`: approve as-is, or correct specific rows
(blockers and Status are the error-prone inferences). Also confirm the `docs_repo`
for the slim config (default: the repo the epic lives in).

### 3. Mutate (idempotent — safe to re-run after a partial failure)

1. For each child: `addSubIssue` to the epic (skip if already a sub-issue);
   `reprioritizeSubIssue` to match the legacy priority order.
2. For each confirmed dependency edge: `addBlockedBy` (skip if already present).
3. Add epic + all children to the configured project (epic-config `project`, default #2) (`addProjectV2ItemById` is idempotent);
   set Status per the confirmed table; Priority only if the operator assigned any.
4. Rewrite the epic body via `gh issue edit --body-file`. HARD ORDERING REQUIREMENT —
   the backup MUST exist before any destructive edit:
   - FIRST post the full original body verbatim as an issue comment ("pre-migration
     body, for the record") and VERIFY it posted (re-fetch the comment / confirm the
     returned URL+id). Do NOT proceed to the rewrite until the backup is confirmed
     present — a crash between rewrite and backup would lose the legacy edges/ticks
     irrecoverably, so the order is non-negotiable.
   - ONLY THEN rewrite the body: replace the fat `epic-config` with the slim schema
     (`epic`, `repo`, `docs_repo`, `worktree_prefix`, `spec`, `runbook`, `custom_gates`, and optional `project`)
     — when the epic tracks a NON-DEFAULT board, the slim config MUST carry `project: <n>` so future
     `/epic` runs track the same project number;
   - DELETE the task-list section and the `## Dependency model` section (their data
     now lives in sub-issues/relations — leaving them would create dual sources of
     truth);
   - preserve all other prose (abstract, links).
5. Tag the epic with the Epic issue type if the org has one (skip silently if not).

### 4. Verify & report

Re-query `subIssues` + each child's `blockedBy` + `projectItems` and diff against
the confirmed table — fix any gap. Then report: children linked, edges created,
Project statuses set, body-rewrite diff summary, and "drive with `/epic <n> next`".

## Constraints

- NEVER mutate before step-2 approval.
- Do not close/reopen any issue during migration — Status fields reflect reality;
  issue state stays untouched.
- A child referenced in the task-list but deleted/transferred on GitHub → report it
  and continue (operator decides whether to recreate).
- Gate names in `custom_gates` must exist in the relevant repo's `.claude/epic.yaml`
  catalog; unknown names → ask (keep-and-add-to-catalog-later vs drop).
