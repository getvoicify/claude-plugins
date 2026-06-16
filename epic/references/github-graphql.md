# GitHub GraphQL reference — epic-driver plugin

Verified live against the `getvoicify` org on 2026-06-10. All commands in this plugin
share these incantations. Run everything through `gh api graphql` (never curl/wget).

## Constants (fast-path cache — re-resolve at runtime if any query 404s)

| Thing | Value |
|---|---|
| Planning repo (default epic home) | `getvoicify/gangan` |
| Org project | number **2** ("Gangan") |
| `projectId` | `PVT_kwDOBw1T1M4BaShO` |
| Status field id | `PVTSSF_lADOBw1T1M4BaShOzhVLLe8` |
| Status options | Todo `d36861b6` · In Progress `8d20dd23` · In Review `3c61b8ea` · Done `9d096808` · Parked `bc4e1fe5` |
| Priority field id | `PVTSSF_lADOBw1T1M4BaShOzhVLc2k` |
| Priority options | P0 `714602c0` · P1 `bc09f009` · P2 `88d520fc` |

Re-resolution query when an ID has gone stale:

```graphql
{ organization(login:"getvoicify") { projectV2(number:2) {
    id
    status: field(name:"Status")   { ... on ProjectV2SingleSelectField { id options { id name } } }
    priority: field(name:"Priority"){ ... on ProjectV2SingleSelectField { id options { id name } } }
} } }
```

## Error handling (extends the 404 / re-resolve note above)

A 404 / stale-ID means *re-resolve* (see above). Everything else falls into **transient**
(retry or sleep-resume) vs **permanent** (surface / park). Classify, never blind-retry.

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
| 404 / stale node id | recoverable | re-resolve IDs (above), retry |
| 4xx other (422, 401, schema/validation errors) | permanent | surface / park — never retry |

In `run` mode an exhausted-retry transient failure parks the child with a diagnostic
(Status → Parked) — it must NOT crash the driver loop.

## Sub-issues (children discovery + linking)

Read children of an epic (works cross-repo; paginate past 50):

```graphql
{ repository(owner:"<owner>", name:"<repo>") { issue(number:<epic#>) {
    id title state
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
  fieldId: "<Status field id>", value: {singleSelectOptionId: "<option id>"}}) { projectV2Item { id } } }
```

Find an issue's existing project item without re-adding: use `projectItems(first:5)`
on the Issue node and filter `project { number } == 2`. `addProjectV2ItemById` is
idempotent (returns the existing item) — safe to call blindly.

**Lifecycle transitions the driver MUST write:**

| Event | Status |
|---|---|
| child created / migrated | Todo |
| worktree created, drive started | In Progress |
| PR opened | In Review |
| PR merged + swept | Done |
| parked (circuit breaker) | Parked |

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

## Review threads + Copilot review (pre-merge gates)

The driver MUST resolve every review thread and ensure every Copilot comment is
resolved before merge. These incantations make those gates executable.

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
"Copilot not available":

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

## Issue types

Org has Task / Bug / Feature (+ Epic once created — requires `admin:org` scope or org
Settings → Planning). Tag with:

```graphql
mutation { updateIssueIssueType(input: {issueId: "<node id>", issueTypeId: "<type id>"}) { issue { number } } }
```

List type ids: `{ organization(login:"getvoicify") { issueTypes(first:10){ nodes { id name } } } }`.
If the Epic type doesn't exist yet, skip tagging silently — it is cosmetic.

## Required token scopes

`repo, project, read:org`. `createIssueType` additionally needs `admin:org` (skip if absent).
