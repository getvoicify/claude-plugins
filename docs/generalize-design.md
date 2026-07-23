# Generalizing the epic plugin — design

## Problem

The epic plugin (skills `create`, `epic`, `migrate`) is hardwired to one GitHub
organization. A stranger installing it today cannot drive an epic without editing
skill prose. The coupling map (surveyed 2026-07-23):

- **No-seam constants** — the org login `getvoicify` appears inside every
  `organization(login:)` GraphQL query and the no-arg `gh search --owner` listing;
  the default planning repo `getvoicify/gangan` is baked into the Layer-1 load
  probe; the ProjectV2 node-ID fast-path cache (`PVT_…`/`PVTSSF_…`/option hashes)
  only works for org project #2; the prior-art path `docs/superpowers/` and the
  "default #2 (Gangan)" project prose have no config key.
- **Partial seam** — runtime ID re-resolution exists for `epic-config.project ≠ 2`
  but its query is itself pinned to `organization(login:"getvoicify")`, so no
  configuration can leave the org.
- **Convention-as-code** — the merge phase treats the `claude-review` required
  check as a universal PRIMARY gate; it is a getvoicify convention, not config.
- **Distribution** — marketplace name `tom-plugins`, plugin author
  `Tom (verygreenboi)`; no license.
- **Exemplary prose** — gangan-api/mobile/angular rosters, `[GAN-NNN]` ticket
  keys, `JAVA_HOME on gangan-mobile` examples.

The plugin is going open source; it must work for any GitHub org **or user
account** out of the box.

## Chosen approach: derive + config seam

Derive what can be derived; add one small config seam for what cannot; resolve
IDs at runtime always.

Rejected alternatives:

- **`/epic:init` bootstrap skill** — interrogates GitHub and writes config.
  Better onboarding, but a fourth skill to keep agent-agnostic across five
  harnesses, and it duplicates state the existing two-layer config already
  carries. Revisit as a later epic if setup friction proves real.
- **Pure inference (zero new config)** — discover planning repo/project
  interactively every invocation. Zero setup but makes every `create` a Q&A
  session and gives `run` mode (which may never ask) nothing to stand on.

## Design decisions

### D1 — Owner derivation

The owner half of `epic-config.repo` is the single authority for every
owner-scoped GraphQL query the **driver** and **migrate** run. No `org:` config
key exists. The no-arg epic listing (`/epic` with no number), which runs before
any epic-config is loaded, scopes to the owner of the cwd checkout's `origin`
remote; if that cannot be resolved, interactive modes ask, and there is no run
mode without an epic number.

### D2 — Always-runtime ID resolution, org/user dual

The cached node-ID table in `references/github-graphql.md` is deleted. Every
invocation resolves projectId + Status/Priority field & option IDs at runtime
from `(owner, project-number)`, trying `organization(login:)` first and falling
back to `user(login:)` (ProjectsV2 exist on user accounts). The fallback trigger
is `errors[].type == "NOT_FOUND"` parsed from the GraphQL response **body** —
`gh api graphql` exits non-zero even when partial data is returned, so exit
codes are not the signal; a NOT_FOUND on both owner kinds means the login or
project genuinely doesn't exist (STOP, don't loop). The reference documents the
exact recipe. Resolution happens once per
invocation, not per mutation. The existing STOP/park rule is unchanged: a
project lacking Status or Priority fields is a config error, never a reason to
fall back to some other project's IDs.

### D3 — Layer-2 `planning:` section (the one new seam)

`.agents/epic.yaml` / `.claude/epic.yaml` gain an optional top-level section:

```yaml
planning:
  repo: owner/name   # where new epic issues are homed
  project: 2         # ProjectV2 number on that owner
```

Consumers:

- **create** — epic home + project for materialization; absent → ask the
  operator (create is interactive by definition).
- **epic driver** — fallback for `epic-config.project` when the epic body omits
  it (see D4); also the default `repo` probe for the Layer-1 load when the
  epic-config cannot be found in the cwd repo. At Layer-1 load time no child
  exists yet, so "Layer 2" here means specifically **the cwd checkout's**
  `.agents/epic.yaml` (then `.claude/epic.yaml`); cwd not a git repo or no
  config file → interactive modes ask, `run` hard-stops.
- **migrate** — same defaults as create.

There is no hardcoded fallback behind the seam: seam absent + epic-config
silent → interactive modes repair via the existing missing-config flow; `run`
hard-stops with the existing "config missing/incomplete" message naming
`planning.project`.

### D4 — Project-number resolution order (back-compat)

`epic-config.project` → Layer-2 `planning.project` → STOP (no default `2`).
Existing epics whose bodies omit `project` keep working the moment their repo's
epic.yaml carries `planning.project` — a one-line backfill, no re-migration of
epic issues. The gangan repos add
`planning: {repo: getvoicify/gangan, project: 2}`; this repo's own
`.claude/epic.yaml` adds `planning: {repo: getvoicify/claude-plugins, project: 2}`.
That is the entire seamless-back-compat story. (The gangan-repo backfills are
operator actions in external repos, outside this epic's children; this epic
delivers only this repo's own backfill plus the documented one-liner.)

### D5 — Review gates become pure config

The Claude Review action is driven as the PRIMARY post-PR gate **only when**
`claude-review` appears in the repo's `merge.required_checks`; otherwise the
driver skips it cleanly (the same shape as Copilot's existing N/A path — noted,
never silently). Copilot stays behind `merge.copilot_review`. No prose may
assume a review workflow exists in every repo.

### D6 — Issue types and other probes

The existing runtime probes stay (Epic issue type with `tracking-epic` label
fallback) but their queries follow D1/D2: owner-derived, org→user dual, and the
user-account path (which has no org issue types) must take the fallback without
error.

### D7 — Prior-art path

`create`'s prior-art search uses the docs dirs from the target repo's Layer-2
`docs:` config (spec_dir/runbook_dir) instead of the hardcoded
`docs/superpowers/`.

### D8 — Exemplary prose

Gangan-flavored examples are replaced with neutral placeholders (`acme-api`,
`[TICKET-123]`, "a mobile repo that needs JAVA_HOME"). Examples must not name
real repos of any org.

### D9 — Lint enforcement (the ratchet)

`tests/test_skills_lint.py` gains a forbidden-literal rule with two scopes:

- **`epic/skills/` + `epic/commands/`** — `gangan`, `getvoicify`
  (case-insensitive) and the node-ID shapes `PVT_`/`PVTSSF_`/`PVTF_` are all
  forbidden. Reference examples use placeholders (`<projectId>`,
  `<statusFieldId>`), never realistically-shaped IDs.
- **`epic/README.md`** — `gangan` and the node-ID shapes are forbidden;
  `getvoicify` is permitted ONLY inside the literal slug
  `getvoicify/claude-plugins`, because the README's install commands and
  smoke-record links must name the repo's real home until any future repo move
  (out of scope here). Any other `getvoicify` occurrence fails.

Scope-widening is sequenced with the cleanups so the suite is never red on
main: child 1 enforces reference + `epic/commands/` (already clean), children
2/3 widen over the SKILL.md files they clean, child 4 widens over the README
last (it owns the final README pass). This turns "generalized" from a claim
into an invariant.

### D10 — Distribution naming

**DECISION (locked at materialization approval, 2026-07-23): the
marketplace/catalog name is `epic-plugins`.** It replaces `tom-plugins` in
both manifests, the test pin, and every README reference. (Shortlist
considered: `agent-epics` — reads as a product, not a catalog;
`verygreenboi-plugins` — personal branding on an OSS catalog; keeping
`tom-plugins` — the thing being shed.)

Renaming breaks existing installs: users must re-add the marketplace under the
new name (`claude` plugin-marketplace re-add; `codex plugin marketplace add`).
The README documents the migration in the rename PR. Only Claude Code and Codex
consume the marketplace name — Kimi, OpenCode, and Cursor install via the
skills-dir copy from a clone (URL unchanged), so their install paths are
name-independent.

**Decision record:** at materialization approval this shortlist is replaced by
the chosen name, recorded here AND in the rename child's issue body — a fresh
driver session must never have to infer the name from a recommendation.

`epic/.claude-plugin/plugin.json` author becomes the neutral project identity
(final string decided in the rename child alongside the marketplace name).

### D11 — License

MIT, `LICENSE` at repo root, copyright the plugin author. A short
"Contributing" note in the root README (issues/PRs welcome; tests must pass).

## Success criteria

1. **Lint-proven decoupling** — the D9 lint passes over the full shipped
   surface at its two scopes: zero `gangan`/`getvoicify`/node-ID literals in
   `epic/skills/` + `epic/commands/`; zero `gangan`/node-ID literals and no
   `getvoicify` outside the `getvoicify/claude-plugins` slug in
   `epic/README.md`.
2. **Stranger-context smoke** — a user-account-owned scratch repo (non-org, no
   getvoicify affiliation) with its own user-level ProjectV2 drives `status`,
   a create dry-run, and a migrate step-1 abort successfully, proving D1/D2/D3
   behaviorally, zero mutations to real epics.
3. **Seamless back-compat** — this repo's own epics (e.g. #9) still resolve
   correctly under the generalized driver after the one-line D4 backfill; no
   epic issue body is edited.
4. **OSS hygiene shipped** — LICENSE present; marketplace renamed with install
   migration documented; the two name-consuming install paths (Claude Code,
   Codex) re-verified live under the new name; the three name-independent paths
   (Kimi, OpenCode, Cursor — skills-dir copy from an unchanged clone URL)
   confirmed unaffected by inspection.

## Out of scope

- Moving/publishing the repo to a public home (follow-up epic).
- An `/epic:init` onboarding skill.
- Shipping a reusable claude-review workflow for consumer repos.
- Non-GitHub forges (GitLab etc.).
- De-branding historical docs under `docs/` — they are records of this repo's
  own epics, not plugin surface.
- Multi-project epics (children tracked on different ProjectsV2 per repo).
