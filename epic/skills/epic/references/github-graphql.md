# GitHub GraphQL reference — epic-driver plugin

All commands in this plugin share these incantations. Every owner-scoped query
derives its owner from the owner half of `epic-config.repo` (see the config
contract below) — there is no `org:` config key. Run everything through
`gh api graphql` (never curl/wget).

## Config contract (Layer-2 `planning:` + project-number resolution)

The working repo's epic config (`.agents/epic.yaml` first, then
`.claude/epic.yaml`) may carry an optional top-level Layer-2 section that names
where epics live and which ProjectV2 tracks them:

```yaml
planning:
  repo: owner/name    # where new epic issues are homed
  project: <number>   # ProjectV2 number on that owner
```

**Owner** for every owner-scoped GraphQL query is the owner half of
`epic-config.repo` (or `planning.repo` at Layer-1 load, before any epic-config
exists). No `org:` key — an owner may be an organization or a user account, and
the resolution recipe below tries both.

**Project number** resolves in this order — there is no hardcoded default:

1. `epic-config.project` (the epic issue body's own project number); else
2. Layer-2 `planning.project`; else
3. STOP — interactive/attended modes surface via the missing-config flow; `run`
   mode hard-stops with the "config missing/incomplete" message naming
   `planning.project`. Never assume a project number.

## Runtime ID resolution (owner-agnostic; the sole resolution path)

There is no cached ID table. Every invocation resolves `projectId` + the Status
and Priority field & option IDs at runtime from `(owner, project-number)`, once
per invocation (not per mutation). Try the `organization(login:)` form first:

```graphql
{ organization(login:"<owner>") { projectV2(number:<project>) {   # <owner> = owner half of epic-config.repo; <project> per the resolution order above
    id
    status: field(name:"Status")   { ... on ProjectV2SingleSelectField { id options { id name } } }
    priority: field(name:"Priority"){ ... on ProjectV2SingleSelectField { id options { id name } } }
} } }
```

**org → user fallback.** `gh api graphql` exits **non-zero even when it returns
partial data**, so the exit code is NOT the signal — parse the JSON response
**body**. If `data.organization` is null and `errors[]` carries an entry with
`type == "NOT_FOUND"`, the owner is a user account, not an org: re-run the exact
same query with the `user(login:)` form (ProjectsV2 exist on user accounts too):

```graphql
{ user(login:"<owner>") { projectV2(number:<project>) {   # identical shape; owner resolved as a user account
    id
    status: field(name:"Status")   { ... on ProjectV2SingleSelectField { id options { id name } } }
    priority: field(name:"Priority"){ ... on ProjectV2SingleSelectField { id options { id name } } }
} } }
```

If the `user(login:)` form ALSO returns `errors[].type == "NOT_FOUND"` (a
NOT_FOUND on **both** owner kinds), the login or project genuinely doesn't exist
— **STOP, do not loop.** Surface in interactive/attended mode; hard-stop `run`.

Capture the returned `id` as `<projectId>`; the `status:` / `priority:` aliases
carry `<statusFieldId>` / `<priorityFieldId>` and their option ids
(`<todoOptionId>`, `<inProgressOptionId>`, `<inReviewOptionId>`, `<doneOptionId>`,
`<parkedOptionId>`, `<p0OptionId>` …). Every example below uses these
placeholders — never realistically-shaped IDs.

If the resolved project has **no** `Status` / `Priority` single-select field, or
is missing the expected option names, that is a **config error** — **STOP and
surface in interactive/attended mode, or park the child (Status → Parked) in
`run` mode.** Never substitute another project's IDs to paper over it.

## Error handling

A 404 / stale node ID means *re-resolve* the IDs via the resolution query above
(see "Runtime ID resolution"). Everything else falls into **transient** (retry
or sleep-resume) vs **permanent** (surface / park). Classify, never blind-retry.

**Reads (queries).** On 5xx or timeout: bounded retry, up to 3 attempts, exponential
backoff (e.g. 1s → 2s → 4s). After the 3rd fails, treat as a transient infra failure
(surface in attended mode; park the child with a diagnostic in `run` mode).

**Mutations (`addSubIssue` / `addBlockedBy` / `updateProjectV2ItemFieldValue` etc.).**
NEVER blind-retry — a 5xx/timeout may have partially applied. Re-verify with a follow-up
query before any retry:
- effect already present (sub-issue linked / `blockedBy` contains the blocker / field
  already set to the target option) → treat as **success**, do not re-apply.
- effect absent and state unambiguous → safe to retry once.
- state ambiguous (can't confirm either way) → **abort the cycle**; do not risk a
  double-apply. (`addProjectV2ItemById` is the one exception — idempotent, returns the
  existing item, safe to repeat.)

**Rate limits.** On secondary-rate-limit, a GraphQL error of type `RATELIMITED`, or a
`403` carrying `retry-after` / `x-ratelimit-remaining: 0` (+ `x-ratelimit-reset` epoch),
sleep until the reset (honour `retry-after` seconds, else `reset - now`) then resume.
Do NOT hammer — repeated hits escalate the block.

**Classification quick-table:**

| Signal | Class | Action |
|---|---|---|
| 5xx, timeout | transient | retry w/ backoff (≤3); reads blind, mutations re-verify-first |
| `RATELIMITED` / `403` + `retry-after` / `x-ratelimit-remaining: 0` | transient | sleep to reset, resume |
| 404 / stale node id | recoverable | re-resolve IDs (see Runtime ID resolution), retry |
| 4xx other (422, 401, schema/validation errors) | permanent | surface / park — never retry |

In `run` mode an exhausted-retry transient failure parks the child with a diagnostic
(Status → Parked) — it must NOT crash the driver loop.

## Sub-issues (children discovery + linking)

Read children of an epic (works cross-repo; paginate past 50):

```graphql
{ repository(owner:"<owner>", name:"<repo>") { issue(number:<epic#>) {
    id title state
    projectItems(first: 5) { nodes { id project { number } fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } }
    issueType { name }
    subIssuesSummary { total completed }
    subIssues(first: 50, after: <cursor|null>) {
      pageInfo { hasNextPage endCursor }
      nodes { id number title state
              repository { nameWithOwner }
              blockedBy(first: 20) { nodes { number state repository { nameWithOwner } } }
              projectItems(first: 5) { nodes { id project { number } } } }
    }
} } }
```

Link / unlink / reorder (cross-repo confirmed working):

```graphql
mutation { addSubIssue(input: {issueId: "<parent node id>", subIssueId: "<child node id>"}) { issue { number } } }
mutation { removeSubIssue(input: {issueId: "<parent>", subIssueId: "<child>"}) { issue { number } } }
mutation { reprioritizeSubIssue(input: {issueId: "<parent>", subIssueId: "<child>", afterId: "<sibling>"}) { issue { number } } }
```

## Native issue dependencies (replaces the old `## Dependency model` prose)

**GOTCHA (verified):** the argument is `blockingIssueId`, NOT `blockedByIssueId`.

```graphql
mutation { addBlockedBy(input: {issueId: "<blocked node id>", blockingIssueId: "<blocker node id>"}) {
  issue { number blockedBy(first:10){ nodes { number repository { name } } } } } }
mutation { removeBlockedBy(input: {issueId: "<blocked>", blockingIssueId: "<blocker>"}) { issue { number } } }
```

Eligibility rule for `next` selection: a child is **unblocked** when every issue in its
`blockedBy` list is CLOSED *and* (if it was driven by this plugin) its closing PR is
MERGED — cross-check via the PR-mapping rule below. Cross-repo blockers work.

## ProjectV2 (status tracking)

Add an issue to the project, then set fields (two steps; capture the returned item id):

```graphql
mutation { addProjectV2ItemById(input: {projectId: "<projectId>", contentId: "<issue node id>"}) { item { id } } }
mutation { updateProjectV2ItemFieldValue(input: {
  projectId: "<projectId>", itemId: "<item id>",
  fieldId: "<statusFieldId>", value: {singleSelectOptionId: "<option id>"}}) { projectV2Item { id } } }
```

Find an issue's existing project item without re-adding: use `projectItems(first:5)`
on the Issue node and filter `project { number } == <project>` (the resolved project
number — see the config contract). `addProjectV2ItemById` is
idempotent (returns the existing item) — safe to call blindly.

**Lifecycle transitions the driver MUST write:**

| Event | Status |
|---|---|
| child created / migrated | Todo |
| worktree created, drive started | In Progress |
| PR opened | In Review |
| PR merged + swept | Done |
| parked (circuit breaker) | Parked |
| epic complete (last child merged + swept, none parked-open) — drive modes only | close epic issue if open; epic's own item → Done |
| epic CLOSED + complete but its item ≠ Done | epic's own item → Done (self-heal; plain `status` without `--sweep` only reports) |

## PR-mapping rule (child → PR, used by status + sweep)

Build the child→PR map from PR metadata, never by guessing from branch names.
Two `gh` quirks (verified): `closingIssuesReferences` is GraphQL-only (not exposed by
`gh pr list/view --json`), and `gh --jq` has no `--arg` passthrough — pipe to real `jq`.

```bash
owner="${repo%/*}"; name="${repo#*/}"
gh api graphql -F owner="$owner" -F name="$name" -f query='
  query($owner: String!, $name: String!) {
    repository(owner: $owner, name: $name) {
      pullRequests(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
        nodes { number title state mergedAt headRefName
                closingIssuesReferences(first: 100) { nodes { number } } } } } }' \
| jq --arg prefix "${worktree_prefix}-" '
    [.data.repository.pullRequests.nodes[]
     | select(.headRefName | startswith($prefix))
     | { number, state, mergedAt, headRefName,
         closes: [.closingIssuesReferences.nodes[].number] }]'
```

The `startswith($prefix)` filter via `jq --arg` is mandatory — `--search 'X- in:head'`
token-matches and returns unrelated PRs. A PR counts as **merged** only when
`state == "MERGED"` / `mergedAt != null`. Paginate past 100 PRs / 100 closes refs if
an epic ever exceeds them (silent truncation would corrupt the mapping).

## Review threads + Copilot review (config-conditional pre-merge gates)

The driver resolves review threads before merge (governed by
`merge.required_review_thread_resolution`). The Copilot gate is
**config-conditional**: it applies **only when `merge.copilot_review` is true** —
when it is false or absent, skip the Copilot gate cleanly and note the skip
(never silently). When enabled, ensure every Copilot comment is resolved before
merge. These incantations make those gates executable.

Discover review threads on a PR (the comment author login identifies the bot —
CodeRabbit vs GitHub Copilot; the Copilot reviewer bot login is
`copilot-pull-request-reviewer[bot]`):

```graphql
{ repository(owner:"<owner>", name:"<repo>") { pullRequest(number:<pr#>) {
    reviewThreads(first:100) {
      nodes { id isResolved isOutdated path
              comments(first:1) { nodes { author { login } body } } }
    }
} } }
```

Resolve a thread (pass a `threadId` from the discovery query's `nodes[].id`):

```graphql
mutation { resolveReviewThread(input:{threadId:"<thread id>"}) { thread { isResolved } } }
```

Request a Copilot review (REST — adds the Copilot reviewer bot). **GOTCHA:** 422s if
Copilot code review is not enabled for the repo — that 422 is how the driver detects
"Copilot not available" (skip the gate and note it):

```bash
gh api -X POST repos/{owner}/{repo}/pulls/{number}/requested_reviewers \
  -f "reviewers[]=copilot-pull-request-reviewer[bot]"
```

Detect Copilot review state on a PR — "not requested" vs "requested, pending" vs
"reviewed". `reviewRequests` still listing the bot = pending; once Copilot reviews it
moves out of `reviewRequests` into `latestReviews`/`reviews`:

```graphql
{ repository(owner:"<owner>", name:"<repo>") { pullRequest(number:<pr#>) {
    reviewRequests(first:20) { nodes { requestedReviewer { ... on Bot { login } } } }
    latestReviews(first:20) { nodes { author { login } state } }
    reviews(first:50) { nodes { author { login } state } }
} } }
```

State: bot in `reviewRequests` → **requested, pending**; bot absent everywhere →
**not requested**; bot in `latestReviews`/`reviews` → **reviewed** (then walk
`reviewThreads` above for its unresolved comments).

## Claude Review action (config-conditional review gate)

**Config-conditional:** this gate applies **only when `claude-review` is listed
in the repo's `merge.required_checks`.** When it is absent, skip the Claude
Review gate cleanly and note the skip (never silently) — do not assume the
workflow exists in every repo. When it IS required, treat it as the primary
post-PR gate as follows.

The `claude-review` workflow (`.github/workflows/claude-review.yml`) reviews the latest
head of every non-draft PR, posts inline comments, submits a formal
APPROVE / REQUEST_CHANGES review, and exposes a REQUIRED status check named
`claude-review`. The check is the authoritative gate (it is **fail-closed**: a missing
verdict is red); the formal review is the human-visible verdict. Read both on the
LATEST head.

Read the `claude-review` check conclusion on the head commit (definitive pass/fail):

```bash
gh pr view <pr#> --json statusCheckRollup \
  --jq '.statusCheckRollup[] | select(.name=="claude-review" or .context=="claude-review") | {name, status, conclusion}'
```

`conclusion == "SUCCESS"` → **approved (green)**; `FAILURE` → **changes requested
(red)**; `null`/`status != "COMPLETED"` → **pending** (still reviewing the head; wait,
do not arm `--auto`). Cross-check the formal review state and read the review body +
inline comments to drive fixes:

```bash
gh pr view <pr#> --json reviews \
  --jq '[.reviews[] | select(.author.login | test("claude|github-actions"))] | last | {state, body}'
```

`state == "CHANGES_REQUESTED"` → address every inline comment + the body, push, and
wait for the re-review of the new head (each push re-triggers the action and supersedes
the prior run via the per-PR concurrency group). `APPROVED` + green check → gate
satisfied. The exact check `name` GitHub reports is the job id (`claude-review`);
verify it once against `statusCheckRollup` if a repo renames the job.

## Issue types

Issue types are **org-only** — the `issueTypes` connection exists on
`Organization`, and the GraphQL `User` type has no `issueTypes` field, so there
is no user-owner form of this probe (unlike ID resolution, there is no org→user
fallback here). An org may define Task / Bug / Feature (+ Epic once created —
Epic requires `admin:org` scope or org Settings → Planning). Tag with:

```graphql
mutation { updateIssueIssueType(input: {issueId: "<node id>", issueTypeId: "<type id>"}) { issue { number } } }
```

List type ids via the `organization(login:)` form only:

```graphql
{ organization(login:"<owner>") { issueTypes(first:10){ nodes { id name } } } }
```

If the owner is a **user account** rather than an org, it simply has no issue
types — do NOT attempt a user-owner variant of this query (there is none; asking
`issueTypes` on a `User` is a schema/validation error the error table above
classifies as permanent, not a fallback). Skip type-tagging silently (it is
cosmetic). Likewise, if the Epic type doesn't exist yet, skip tagging silently.

## Required token scopes

`repo, project, read:org`. `createIssueType` additionally needs `admin:org` (skip if absent).

## Deterministic script layer (`epic/scripts/`)

Everything above is available to drive by hand. `epic/scripts/` packages the
incantations the driver calls every cycle into small deterministic CLIs
(Python 3 + `pyyaml`), so the driver shells out to a script instead of
re-deriving the same query/parse logic in prose each time. This section
documents, with runnable snippets, exactly what those scripts issue —
verified against each script's `main()`, not reconstructed from memory.

### Review threads — `pr_watch.py` and `mergeability.py`

Both scripts issue the identical query (the same `_THREADS_QUERY` constant
appears verbatim in each file):

```graphql
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}
```

Run it directly:

```bash
gh api graphql -F owner=<owner> -F name=<repo> -F pr=<pr#> -f query='
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){nodes{id isResolved isOutdated path}}
    }
  }
}'
```

`pr_watch.py` uses `isResolved` to compute the `threads_unresolved` count it
polls on; `mergeability.py` uses the same field to emit a
`thread-unresolved:<id>` requirement per unresolved thread. Neither script's
Python currently reads `isOutdated` or `path` — they travel in the response
for downstream use (thread routing, stale-thread detection) but are not yet
consumed by either script.

### `gh pr view --json` field sets

Three scripts each request a different, narrower field set tailored to what
they compute — there is no single shared set:

| Script | `--json` fields | Used for |
|---|---|---|
| `pr_watch.py` | `headRefOid,statusCheckRollup,reviews,mergeStateStatus` | head-SHA change detection, check/review settlement polling |
| `mergeability.py` | `mergeStateStatus,isDraft,statusCheckRollup,reviewDecision` | the unmet-requirement list (draft, behind/dirty, failing checks, missing approval) |
| `schedule.py` | `createdAt,reviews` (one call per child with a mapped PR, inside `_populate_prs`), plus `mergeability.py`'s own field set again via `mergeability._fetch` | `opened_at` (the FIFO merge-queue fallback for gate-free children) and the real `gates`/`gate_cleared_at` readiness `became_ready_at` sorts on — reusing `mergeability.requirements()` rather than re-deriving check/review/thread logic |

None of the four requests GitHub's `mergeable` boolean — it races the
mergeability computation, so the driver derives the same answer
field-by-field from `mergeStateStatus` + `statusCheckRollup` +
`reviewDecision` + thread state instead (see "sole gating authority" below).

Runnable examples:

```bash
gh pr view <pr#> --repo <owner>/<repo> --json headRefOid,statusCheckRollup,reviews,mergeStateStatus
gh pr view <pr#> --repo <owner>/<repo> --json mergeStateStatus,isDraft,statusCheckRollup,reviewDecision
gh pr view <pr#> --repo <owner>/<repo> --json createdAt,reviews
```

### Waiting is a tick, never a sleep — `pr_watch.py`

`pr_watch.py` takes no key to select and sets no deadline. One invocation is
one tick: it loads a cursor, fetches the PR once, and exits.

```bash
python3 epic/scripts/pr_watch.py --repo <owner>/<repo> --pr <pr#> --reset-backoff
```

| Exit | Event | What the driver does |
|---|---|---|
| `0` | `{"event":"activity","changed":["reviews"],"head":…}` | Re-run `mergeability.py`, act on what is unmet |
| `0` | `{"event":"pr-closed","state":"MERGED"}` | Stop watching |
| `1` | `{"event":"waiting","next_tick_in_s":283,"quiet_s":1240}` (quiet tick; the very first tick also carries `"armed":true`) | Schedule the next tick |
| `1` | `{"event":"waiting","next_tick_in_s":283,"reason":"gh-error","consecutive_errors":3}` (a failed `gh` call; note there is no `quiet_s` key on this shape — a driver that reads it unconditionally will `KeyError`) | Schedule the next tick |
| `2` | `{"event":"error","detail":…,"consecutive":8}` | Sustained `gh` outage — diagnose |
| `0` (via `--stop`) | `{"event":"stopped","cursor_removed":true\|false}` | Watch ended; cursor removed if one existed |

`changed` names the facets that moved: `head`, `checks`, `reviews`, `threads`,
`comments`. Every review counts, `COMMENTED` included — that is what makes a
CodeRabbit or Copilot review visible, since neither submits a formal verdict.

`next_tick_in_s` is a REQUEST, not a command: clamp it to your scheduler's
range. The backoff runs `WATCH_FLOOR_S` (15) upward by `WATCH_MULT` (1.8) each
quiet tick — 15 → 27 → 49 → 87 → 157 → 283 → 510 → `WATCH_CEIL_S` (900) — and is
jittered ±20% so parallel wave members desynchronise. It returns to the floor
only on a real head change; `--reset-backoff` forces the floor after a push,
`--resume-backoff` asserts the default of continuing from the stored step. On
a failed `gh` call the same ladder backs off the retry, honouring an explicit
`Retry-After` or `x-ratelimit-reset` value parsed out of `gh`'s stderr when one
is present — `gh` usually prints prose rather than raw headers, so treat this
as best-effort, not a proven rate-limit integration.

The cursor lives in `$EPIC_WATCH_DIR` (default `~/.cache/epic/watch`) and is
disposable — losing it costs one wasted fast-tier poll. `--stop` deletes it and
ends the watch.

Because the script no longer judges gates, the old divergence between its
`checks` key and the ruleset's required checks is gone: `mergeability.py` is
the sole authority on what is required, and `pr_watch.py` only reports that
something moved.

### Ruleset source of truth — `gh api repos/<owner>/<repo>/rules/branches/main`

`mergeability.py` treats the repo's live branch-protection ruleset, never a
cached or hand-maintained list, as the source of truth for which status
checks are required:

```bash
gh api repos/<owner>/<repo>/rules/branches/main
```

It collapses the returned array to `{"required_status_checks": [...]}` by
keeping only entries with `type == "required_status_checks"` and reading
each one's `parameters.required_status_checks[].context`. A `404` (no
ruleset configured on the repo) is treated as an empty ruleset — every
reported check is then optional, `statusCheckRollup` alone drives
requirements — never as an error.

### Sub-issue / blockedBy / project-field query — `schedule.py`

`schedule.py` computes the runnable wave and merge queue from one query that
pulls every child's state, blockers, and Status/Priority project fields in a
single round trip:

```graphql
query($owner:String!,$name:String!,$epic:Int!){
  repository(owner:$owner,name:$name){
    issue(number:$epic){
      subIssues(first:100){
        nodes{
          number state
          repository{ nameWithOwner }
          blockedBy(first:20){nodes{number}}
          projectItems(first:5){
            nodes{
              status: fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}
              priority: fieldValueByName(name:"Priority"){... on ProjectV2ItemFieldSingleSelectValue{name}}
            }
          }
        }
      }
    }
  }
}
```

Run it directly:

```bash
gh api graphql -F owner=<owner> -F name=<repo> -F epic=<epic#> -f query='
query($owner:String!,$name:String!,$epic:Int!){
  repository(owner:$owner,name:$name){
    issue(number:$epic){
      subIssues(first:100){
        nodes{
          number state
          repository{ nameWithOwner }
          blockedBy(first:20){nodes{number}}
          projectItems(first:5){
            nodes{
              status: fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}
              priority: fieldValueByName(name:"Priority"){... on ProjectV2ItemFieldSingleSelectValue{name}}
            }
          }
        }
      }
    }
  }
}'
```

`schedule.py` separately re-runs the `pullRequests(first:100,
orderBy:{field:UPDATED_AT, direction:DESC})` PR-mapping query documented
above (see "PR-mapping rule") to attach each child's open/merged PR before
computing the wave and the merge queue — ONCE PER DISTINCT repo among the
`repository.nameWithOwner` values the query above returns, not once for the
epic's own repo alone. A cross-repo epic's children are resolved against
their OWN repo's open PRs this way (`status.py` mirrors the same per-repo
PR-map fetch for the same reason).

### `mergeability.py` is the sole gating authority

**`mergeability.py` is the only supported consumer of PR state, the branch
ruleset, and review threads for a merge/no-merge decision.** The driver
calls `python3 epic/scripts/mergeability.py --repo <owner>/<repo> --pr
<pr#>` and acts on its `requirements` / `clean` output — it must never
hand-roll its own mergeability judgment by reading `statusCheckRollup` or
`mergeStateStatus` directly and inferring merge-readiness from them.
`pr_watch.py` reads overlapping raw state (the same review-threads query, a
different `gh pr view --json` set) but only to detect *that something
changed* so the driver knows when to re-run `mergeability.py` — it never
itself decides whether a PR is mergeable.

**Note on this reference's origin:** the task brief that seeded this section
named one combined `gh pr view --json` field set
(`headRefOid,statusCheckRollup,reviews,mergeStateStatus,mergeable,
reviewDecision,isDraft`) shared by every script. That combined set does not
exist in the code — the table above reflects each script's actual,
narrower request.
