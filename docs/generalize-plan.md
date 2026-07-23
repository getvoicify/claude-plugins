# Generalizing the epic plugin — runbook

Spec: `docs/generalize-design.md`. All children live in
`getvoicify/claude-plugins`. Toolchain (from `.claude/epic.yaml`): venv pytest —
`python3 -m venv .venv && .venv/bin/pip install -q pytest pyyaml`, then
`.venv/bin/pytest tests/ -q`. Merge: squash, required check `test`, Copilot
review on, all threads resolved. No custom gates (`gates: {}`).

Dependency DAG (contract-first):

```
1 (reference + config contract)
├─→ 2 (driver + migrate skills) ─→ 4 (rename + OSS hygiene)
├─→ 3 (create skill)
2,3,4 ─→ 5 (stranger smoke)
```

(Edge 2→4: child 4's README lint widening is only green once child 2's README
cleanup has landed — the DAG encodes it rather than relying on priority order.)

README section ownership (D9 sequencing): child 2 owns driver-behavior prose —
the intro gangan mentions (L3-6), the migrate-table example (L19), the
Architecture "Epic home" bullet (L23 — normative D3/D4 prose, so it belongs to
the child implementing that behavior), the probe-order note (L220-221), and a
new back-compat backfill note (D4). Child 4 owns the install sections, the
dated record line (L50), the marketplace re-add migration paragraph, and the
README lint widening. The two migration notes are distinct — child 2's is the
D4 planning backfill, child 4's is the marketplace re-add. Both children
preserve the lint-pinned headings.

## Child 1 — Reference de-hardcoding + config contract (P0)

**Intent.** Make `epic/skills/epic/references/github-graphql.md` owner-agnostic
and define the `planning:` Layer-2 contract every other child consumes
(spec D1–D4).

**Files.** `epic/skills/epic/references/github-graphql.md`,
`tests/test_skills_lint.py`, `.claude/epic.yaml` (this repo's own D4 backfill).

**TDD order.**
1. RED: lint tests — (a) forbidden-literal rule (D9) scoped to the reference
   file AND `epic/commands/` (already literal-clean, so only the reference is
   red) — `gangan`, `getvoicify` case-insensitive, `PVT_`/`PVTSSF_`/`PVTF_`;
   SKILL.md/README scope-widening happens in children 2–4 as each file is
   cleaned; (b) reference must contain an `organization`→`user` fallback recipe
   (assert both `organization(login:` and `user(login:` appear in the file);
   (c) reference must NOT contain a cached-ID table (absence of the node-ID
   shapes covers this via (a) — examples must use placeholders like
   `<projectId>`, never realistically-shaped IDs).
2. GREEN: rewrite the reference — delete the fast-path ID table; make the
   resolution query the only path, parameterized on `(owner, project)`, with
   the org→user dual recipe documented, keyed on `errors[].type == "NOT_FOUND"`
   parsed from the response body (NOT exit codes — `gh api graphql` exits
   non-zero even with partial data; NOT_FOUND on both owner kinds = login or
   project doesn't exist → STOP); owner defined as "owner half of
   `epic-config.repo`" (D1); remove ALL "default 2"/project-2 prose (D4 — the
   current file has it in the resolution note and the `projectItems` filter);
   missing-fields STOP/park rule kept in substance (a project lacking
   Status/Priority → STOP/park; never substitute another project's IDs) but
   reworded — the current text says "never fall back to the cached project-2
   ids", which dangles once the table is gone; issue-type queries owner-derived
   with the user-account fallback note (D6); reframe the reference's
   Claude-Review and Copilot sections as conditional per D5 — claude-review
   applies only when listed in `merge.required_checks`, Copilot only when
   `merge.copilot_review` is true; keep the incantations (this child owns the
   file; leaving those sections universal would contradict the D5 prose child
   2 writes in the SKILL.md that points here); document the D3 `planning:`
   schema and the D4 resolution order in a NEW config-contract section (the
   current file has no config section — the Constants table being deleted is
   the closest thing).
3. Backfill `planning: {repo: getvoicify/claude-plugins, project: 2}` into
   `.claude/epic.yaml` (D4 — keeps this repo's own epics drivable the moment
   children 2/3 land).

**Verification.** Full suite green. Manual: run the rewritten resolution query
once via `gh api graphql` against owner `getvoicify` project 2 and confirm the
returned IDs match the previously cached values (retrievable from the worktree
via `git show origin/main:epic/skills/epic/references/github-graphql.md`); run
the org-form query with a user login (e.g. `verygreenboi`) and confirm the
response body carries `errors[].type == "NOT_FOUND"` — the fallback trigger.

**Notes.** This child does NOT touch SKILL.md prose (children 2–3). The lint's
file scope must be exactly reference + `epic/commands/` in this child, widening
later — otherwise the suite is red on main between merges.

## Child 2 — Driver + migrate skill generalization (P0, blocked by 1)

**Intent.** `epic/skills/epic/SKILL.md` and `epic/skills/migrate/SKILL.md`
consume the child-1 contract; review gates become config-conditional (D5);
back-compat backfill documented.

**Files.** `epic/skills/epic/SKILL.md`, `epic/skills/migrate/SKILL.md`,
`epic/README.md` (driver-behavior sections), `tests/test_skills_lint.py`.

**TDD order.**
1. RED: widen the forbidden-literal lint scope to the two SKILL.md files ONLY
   (README widening belongs to child 4, which owns the final README pass — see
   D9 sequencing); add a lint asserting the driver skill carries the D5
   canonical sentence, whitespace-normalized (mirroring the existing
   config-sentence lint pattern — which lowercases the haystack, so lowercase
   the needle too): "The Claude Review gate applies only when `claude-review`
   is listed in the repo's `merge.required_checks`; when absent, skip it and
   note the skip."
2. GREEN: Layer-1 load probe order becomes: cwd repo → `planning.repo` from
   the CWD CHECKOUT's `.agents/epic.yaml`/`.claude/epic.yaml` (no hardcoded
   `getvoicify/gangan`; at Layer-1 load time there is no child yet, so the cwd
   checkout is the only Layer-2 source — say so explicitly; neither found →
   interactive ask / `run` STOP); no-arg listing scopes `--owner` to the cwd
   origin's owner (D1); default-project prose → D4 resolution order
   (`epic-config.project` → `planning.project` → STOP) — then `grep -i
   default` over BOTH owned SKILL.md files and delete every default-#2 phrase
   (at least epic:10,50,126 and migrate:10,65,76 today; the lint cannot catch
   "default #2", so the case-insensitive grep is the completeness check, not
   the enumeration); the two frontmatter `description:` lines (epic:3,
   migrate:3) lose their "org Project" phrasing (contradicts user-account
   support; this child owns both files); D5 conditional Claude-Review
   gate carrying the canonical sentence (skip-cleanly-noted path shaped like
   Copilot N/A); exemplary prose neutralized (D8: JAVA_HOME example, Paystack
   caveat, migrate's issue-number examples); README edits limited to
   driver-behavior prose it owns (see the ownership preamble — intro lines,
   migrate-table example, Architecture "Epic home" bullet, probe-order note)
   plus a short back-compat backfill note (D4) written ONLY with placeholders —
   `planning: {repo: <owner>/<planning-repo>, project: <n>}` — never a literal
   gangan example, because child 4's README lint bans it; preserve the
   lint-pinned headings (`## Installing` + five agent subsections, the
   gh-scopes Requirements string, `## Smoke checklist`).
3. Refactor: none expected beyond prose tightening.

**Verification.** Full suite green. D4 spot-check: do NOT invoke `/epic` — the
installed plugin runs the stale marketplace cache, not this worktree's skill.
Instead script the resolution steps the edited prose specifies:
`gh issue view 9 --repo getvoicify/claude-plugins --json body`, parse the
epic-config (it omits `project:`), confirm the cwd checkout's
`.claude/epic.yaml` supplies `planning.project: 2` (child-1 backfill), and run
the reference's resolution query with that `(owner, project)` — resolved IDs
returned means the D4 chain holds end-to-end.

**Notes.** Probe-order change is behavior, not just prose: the old order was
`getvoicify/gangan` then cwd; the new order is cwd then `planning.repo`. State
this reversal explicitly in the PR body — it is the intended generalization,
and the README's old "by design" note about gangan-first probing is deleted
with it.

## Child 3 — Create skill generalization (P0, blocked by 1)

**Intent.** `epic/skills/create/SKILL.md` consumes the contract: planning seam
for epic home + project, docs-config prior-art path, neutral prose.

**Files.** `epic/skills/create/SKILL.md`, `tests/test_skills_lint.py`.

**TDD order.**
1. RED: widen the forbidden-literal lint to the create skill; add a lint that
   the create skill carries the canonical planning sentence,
   whitespace-normalized: "Epic home and project come from `planning:` in the
   cwd repo's `.agents/epic.yaml` (fallback `.claude/epic.yaml`); when absent,
   ask the operator." — plus a `spec_dir` token-presence check for the D7
   prior-art change.
2. GREEN: epic home = `planning.repo`, absent → AskUserQuestion-or-equivalent
   interactive fallback (capability-conditional phrasing per the agent-agnostic
   rules — keep the existing guarded-capability lint green); project =
   `planning.project` same fallback — then `grep -i default` over the file and
   delete every default-#2 phrase (at least create:11,107,122 today; the lint
   cannot catch "default #2", so the case-insensitive grep is the completeness
   check, not the enumeration); epic-config template
   literal uses placeholders, not `getvoicify/gangan`; prior-art search per
   D7; repo multi-select roster and `[GAN-NNN]` examples per D8.
3. Refactor: none expected.

**Verification.** Full suite green, including the pre-existing
guarded-capability and config-sentence lints (this file is dense with guarded
paragraphs — do not break their delimiting).

## Child 4 — Distribution rename + OSS hygiene (P1, blocked by 2)

**Intent.** D10 + D11: marketplace rename everywhere, plugin author neutralized,
MIT LICENSE, contribution note, README public framing. Blocked by 2 because
this child widens the README lint, which is only green once child 2's README
cleanup has landed.

**Files.** `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`,
`epic/.claude-plugin/plugin.json`, `epic/.codex-plugin/plugin.json` (its
`description` carries the same phrasing; it has no author field — that omission
stays), `epic/commands/epic.md`, `epic/commands/migrate.md`,
`tests/test_skills_lint.py` (the `tom-plugins` name pin), `epic/README.md`,
root `README.md` (create if absent), `LICENSE`.

**TDD order.**
1. RED: flip the catalog-name test to the chosen name (both manifests asserted
   equal AND equal to the chosen constant); add a LICENSE-present test (root
   `LICENSE` exists, first line contains `MIT`); widen the forbidden-literal
   lint to `epic/README.md` at its D9 README scope (`gangan` + node-ID shapes
   banned; `getvoicify` only inside the `getvoicify/claude-plugins` slug) and
   add the OLD marketplace name to the README's forbidden set (catches prose
   leftovers like "adds the `tom-plugins` marketplace" and the
   `marketplace upgrade tom-plugins` line that the rename buckets miss).
2. GREEN: rename in both manifests + README name-consuming install lines
   (`epic@<new-name>`, Claude/Codex marketplace-add lines — Kimi/OpenCode/
   Cursor sections are name-independent clone+copy, leave their mechanics
   untouched); author field; LICENSE file; contribution note; README
   install-migration paragraph (old installs must re-add the marketplace —
   include the exact commands, and note the old `tom-plugins` registration
   must be removed first); neutralize the README's remaining gangan record
   line (L50, dated rollout mention) to satisfy the widened lint — preserving
   the lint-pinned headings (five `### <agent>` install subsections, gh-scopes
   string, `## Smoke checklist`); sweep the remaining branding/prose this
   child owns — precisely enumerated because none of it is lint-visible:
   `.claude-plugin/marketplace.json` `owner.name`,
   `.agents/plugins/marketplace.json` `interface.displayName` ("Tom's
   Plugins"), and the "org Project(V2)" phrasing in FOUR JSON descriptions
   (both plugin.json manifests + the plugin entry in each marketplace
   manifest) and TWO command descriptions (`epic/commands/epic.md`,
   `epic/commands/migrate.md` — create.md is already neutral).
3. Refactor: none.

**Verification.** Full suite green. Post-merge (this child's own smoke, not
CI): re-add the marketplace under the new name in Claude Code and confirm
`epic` resolves; `codex plugin marketplace add` + `codex plugin add` with the
new name (Codex CLI 0.145.0 is installed and authenticated on the drive
machine — verified in the previous epic). Record results in the PR or a
follow-up commit to the README's verified-flow lines. The other three agents
need no re-verification (name-independent — criterion 4 as amended).

**Notes.** The chosen name is recorded in spec D10 (as a decision, not a
shortlist — replaced at materialization approval) AND restated in this child's
issue body; never infer it from a recommendation. `tests/test_release.py`'s
`tom-plugins` literal is a tmp_path fixture (rename-immune — confirmed), but
run the release tests anyway. Version-lockstep machinery reads paths, not
names — release is unaffected; still, watch the first post-rename release for
the manifests both bumping (regression guard exists from the lockstep epic).

## Child 5 — Stranger-context smoke (P1, blocked by 2, 3, 4)

**Intent.** Prove success criteria 2–4 behaviorally: a non-org user-account
context drives the plugin; existing epics still work; installs work under the
new name.

**Files.** `epic/README.md` (smoke-record update only). No plugin-surface
changes expected; fallout fixes are in-scope but bounded (same rule as the
agent-agnostic smoke child: caveat-class fixes inline, anything larger spins
out).

**Recipe.**
1. Preconditions: `gh` authenticated with `repo, project, read:org` **plus
   `delete_repo`** (for teardown; `gh auth refresh -s delete_repo`); the
   installed epic plugin updated to include children 1–4 — remove the old
   `tom-plugins` marketplace registration, then re-add under the post-rename
   name using the exact commands from the README migration paragraph child 4
   wrote — so `/epic` runs the generalized skill, not a stale cache (the
   previous epic hit exactly this staleness). Drive this child interactively:
   the setup's riskiest step has a human-only fallback, so an unattended `run`
   reaching this child parks it — pre-provisioning before a `run` also works.
   Record the issue #9 comment count BEFORE any leg (the zero-mutations
   baseline).
2. Setup (concrete commands, not reference recipes — the reference
   deliberately documents driving, not project provisioning):
   - `gh repo create verygreenboi/epic-smoke-scratch --private --clone`
   - `gh project create --owner verygreenboi --title "Epic Smoke"
     --format json` (capture the project number from the output)
   - `gh project field-create <num> --owner verygreenboi --name Priority
     --data-type SINGLE_SELECT --single-select-options "P0,P1,P2"`
   - The built-in Status field ships only Todo/In Progress/Done: add
     In Review + Parked via the GraphQL `updateProjectV2Field` mutation,
     passing the FULL option list — existing options included WITH their `id`s
     to preserve identity, new ones without; every option needs
     name+color+description (all non-null). Query the field first — extend the
     reference's resolution query to also select existing options'
     `color`/`description`, since the base query returns only `{id,name}`.
     This is the smoke's riskiest step — if the mutation rejects, fall back to
     recreating options by hand in the web UI and note it in the evidence.
   - In the scratch clone: author `.agents/epic.yaml` by mirroring this
     repo's `.claude/epic.yaml` (same key set — a partial file triggers the
     driver's missing-config repair Q&A), with `planning:
     {repo: verygreenboi/epic-smoke-scratch, project: <num>}` and worktree
     root pointed at a scratch-local path; create a scratch epic issue
     (epic-config with `repo: verygreenboi/epic-smoke-scratch`, no `project:`
     key — so leg A also exercises the D4 fallback) and one scratch
     legacy-style issue for leg C. The epic-config template comes from the
     generalized create skill's placeholder template (child 3) — read it from
     THIS repo's checkout; the setup deliberately interleaves two directories
     (scratch clone for authoring/legs, this checkout for the epic.yaml
     mirror and template), so keep track of which one you are in.
3. Legs — A–C run FROM INSIDE the scratch clone (D1 derives owner from the
   epic-config/cwd origin; running them from this repo would probe the wrong
   owner). Claude Code only — cross-agent parity was proven by the previous
   epic; this smoke tests org-independence, not agent-independence:
   - **A**: `/epic <scratch-epic#> status` — proves D1 owner derivation +
     D2 `user(login:)` fallback + D4 `planning.project` fallback end-to-end.
   - **B**: create dry-run — before aborting at phase 1, ask the session to
     STATE the epic home and project number it would materialize to; the
     pass-evidence is that it names `verygreenboi/epic-smoke-scratch` and
     `<num>` from the `planning:` seam WITHOUT asking the operator — proves
     D3 (a bare phase-1 abort never consults the seam and would prove
     nothing).
   - **C**: migrate step-1 "Read & parse" abort on the scratch legacy issue —
     proves the migrate path's derivation.
   - **D**: `/epic 9 status` from THIS repo's checkout — regression leg,
     proves D4 back-compat (criterion 3). Issue #9 is CLOSED with Status=Done;
     a report showing exactly that (all children merged, no drift) IS the
     pass — do not read the closed state as a failed leg.
4. Teardown: `gh project delete <num> --owner verygreenboi` and
   `gh repo delete verygreenboi/epic-smoke-scratch --yes`; verify the issue #9
   comment count equals the step-1 baseline (zero mutations).
5. Update the README smoke record (dated, versions).

**Verification.** All legs pass with verbatim evidence on the child issue; full
suite green if any fallout fix touched lintable surface.

## Sequencing note

Children 2 and 3 both edit `tests/test_skills_lint.py` (scope-widening the same
lint) and child 4 touches it too — 2 and 3 are DAG-parallel and will conflict
textually; the driver's one-child-at-a-time discipline makes this a non-issue
(rebase handles it). Suggested drive order: 1, 2, 3, 4, 5.
