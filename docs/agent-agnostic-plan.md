# Agent-agnostic epic plugin — implementation plan

Companion to `agent-agnostic-design.md`. Seven tasks, one PR each, all in
`getvoicify/claude-plugins`. Verification baseline for every task: from the
repo root, `pip install -r requirements-dev.txt && pytest tests/ -q` green
(Task 1 creates `requirements-dev.txt` and the CI workflow — its driver runs
the baseline only after creating that file; the suite passes today — 30
tests). No custom gates apply (this repo has no
`.agents/epic.yaml`/`.claude/epic.yaml` gate catalog). Tasks 2–6 all append
to `tests/test_skills_lint.py`: append under a distinct `# Task N` section
comment so the parallel pairs (3∥4, 5∥6) merge trivially.

Dependency order: **1 → 2 → 3; 1 → 4 → 5; {3,4} → 6; {5,6} → 7.**
(2 and 3 edit the same paragraphs of the same SKILL.md files — serialized on
purpose. 6 needs only the contracts from 3 and 4; 7 validates the final
merged state.)

## Task 1: Skills tree restructure + test CI (P0)

**Intent:** make `epic/skills/{epic,create,migrate}/SKILL.md` the canonical
source; keep Claude Code UX working; give the repo a test CI lane.

**Files:** `epic/commands/*.md` (shim or delete), `epic/skills/**` (new),
`epic/references/github-graphql.md` → `epic/skills/epic/references/`,
`tests/test_skills_lint.py` (new), `requirements-dev.txt` (new, pins
`pytest`), `.github/workflows/test.yml` (new: PR + main push → install
requirements-dev.txt → `pytest tests/ -q`).

**TDD order (outside-in):**
1. RED: write `tests/test_skills_lint.py` asserting: the three skill dirs
   exist; each SKILL.md has frontmatter `name` equal to its dir name and a
   non-empty `description`; the literal link `references/github-graphql.md`
   appears in `epic`'s SKILL.md and `../epic/references/github-graphql.md`
   in `create`'s and `migrate`'s, and each resolves relative to that
   SKILL.md's directory. (Exactly these two literal strings — do NOT try to
   resolve arbitrary paths mentioned in prose; the bodies contain
   working-repo paths and glob templates that never resolve in this repo.)
   Run — fails (no skills tree).
2. GREEN: move command bodies into SKILL.md files **verbatim except** three
   allowed changes: frontmatter reduced to `name` + `description`; an opening
   line "the text after the skill name is the epic/child reference"; and the
   reference-link rewrite — `${CLAUDE_PLUGIN_ROOT}/references/github-graphql.md`
   becomes `references/github-graphql.md` (epic) /
   `../epic/references/github-graphql.md` (create, migrate) — which is what
   turns this step's lint green. All other prose changes belong to Task 2.
   Move the reference file.
3. Claude compatibility check (empirical, design §Claude shims): install the
   plugin from this checkout (`claude plugin marketplace add` the checkout
   path, then install `epic` from it — consult `claude plugin --help` for
   exact syntax) and run non-interactively:
   `claude -p "/epic:create ping" --max-turns 2` — expect phase-1 brainstorm
   behavior (it asks about the idea), not "unknown command". If the output is
   empty or inconclusive, raise `--max-turns` before concluding. If command+skill
   names collide, delete `commands/`; otherwise reduce each command to the
   shim defined in design §Claude shims (`${CLAUDE_PLUGIN_ROOT}` and
   `$ARGUMENTS` are allowed there — the ban applies only under `skills/`).
   If non-interactive invocation proves impossible in this environment,
   STOP and ask the operator to run the check — do not skip it.
4. REFACTOR: update `epic/README.md` command table paths; add the CI
   workflow and confirm it runs on this task's own PR.

**Verify:** `pytest tests/ -q`; the step-3 Claude check passed; the PR shows
the test workflow green.

## Task 2: Prose neutralization (P0, blocked by 1)

**Intent:** every SKILL.md obeys the design's Guarded-capability rule.

**Files:** the three SKILL.md files, `tests/test_skills_lint.py`.

**TDD order:**
1. RED: extend the lint test with the Guarded-capability rule exactly as
   specified in design §Guarded-capability rule: `${CLAUDE_PLUGIN_ROOT}`
   banned under `epic/skills/`; the tokens `AskUserQuestion`,
   `ScheduleWakeup`, `subagent`, `spawn`, `dispatch` (case-insensitive) may
   appear only in a blank-line-delimited paragraph that also contains
   `if your harness` or `if supported` AND `otherwise`. Run — fails on the
   current bodies (the real source says "subagent"/"Spawn", e.g. the
   driver's implementer/reviewer sections and create's Explore step — note
   the phrases "Agent tool"/"Task tool" do NOT occur; the token list above
   is what actually matches).
2. GREEN: rewrite each flagged paragraph using the design's canonical
   fallbacks (structured questions → numbered plain-text; subagents →
   inline sequential; scheduler → in-session loop). Confirm no
   `${CLAUDE_PLUGIN_ROOT}` occurrences remain (Task 1's link rewrite
   removed all three; this clause of the lint should pass already).
3. REFACTOR: read each workflow start-to-end once for coherence — checking
   specifically that EACH capability mention has its own sensible fallback
   (the paragraph lint is a floor, not the bar: one guard phrase in a long
   bullet block legitimizes every token in it, so don't lint-game it).

**Verify:** `pytest tests/ -q`; `grep -r CLAUDE_PLUGIN_ROOT epic/skills/`
returns nothing; re-run Task 1's step-3 Claude check (the prose rewrite is
exactly the change that could regress Claude behavior — operator-assisted
if non-interactive invocation is unavailable).

## Task 3: Neutral config path (P1, blocked by 2)

**Intent:** working repos may use `.agents/epic.yaml` (primary) or
`.claude/epic.yaml` (fallback, checked second).

**Files:** all three SKILL.md files (epic and create read config; migrate
references `.claude/epic.yaml` too), `epic/README.md`,
`tests/test_skills_lint.py`.

**TDD order:**
1. RED: lint test asserts each SKILL.md that mentions `epic.yaml` contains
   one canonical lookup-order sentence ("check `.agents/epic.yaml` first,
   then `.claude/epic.yaml`" — exact wording fixed by the test). Scope: the
   test checks for the presence of that sentence, NOT that every mention
   names both paths (error-message literals and examples name the primary
   path only).
2. GREEN: update config-loading prose, error messages, and examples across
   the three skills + README's two-layer-config section.

**Verify:** `pytest tests/ -q`; re-run Task 1 step 3's Claude check (this
task also rewrites SKILL.md prose after Task 2's re-check).

## Task 4: Codex plugin manifest (P1, blocked by 1)

**Intent:** the repo is installable into Codex CLI as a plugin bundling the
same skills.

**Files:** `epic/.codex-plugin/plugin.json` (new), root
`.agents/plugins/marketplace.json` (Codex repo-catalog file — current docs
indicate repo-marketplace install requires it; confirm in step 1 and add
whatever root-level catalog the docs mandate), `epic/README.md` (Codex
verified-commands stub), `tests/test_skills_lint.py`.

**Precondition (gates step 4 only — steps 1–3 need no CLI):** `codex` CLI
installed and authenticated. If it is not available on this machine, STOP
and ask the operator before step 4 — the smoke step is mandatory, not
optional (deferring it to Task 7 stacks first-ever manifest validation on
top of four other agents' fallout). On a step-4 STOP, Task 5 may proceed
(it consumes only the authored manifest); Task 6 waits for the README stub.

**Order (doc-read precedes RED — the schema comes from live docs, not
memory):**
1. Fetch current Codex plugin docs (developers.openai.com/codex/build-plugins
   and the marketplace page) via the sandbox fetch tool (`curl`/WebFetch are
   blocked in this environment — use `ctx_fetch_and_index`). Note: docs
   distinguish `codex plugin marketplace add` (registers a catalog) from
   installing; confirm the actual local-install command.
2. RED: lint test asserts the manifest exists, is valid JSON, `name ==
   "epic"`, `version` equals `epic/.claude-plugin/plugin.json`'s, and every
   skills path it declares resolves — field names per the fetched docs.
3. GREEN: author the manifest (+ root catalog file if the docs require one).
4. Smoke: install from the local checkout with the documented command;
   `/skills` (or equivalent) lists the three epic skills.
5. Record the verified add/install commands in `epic/README.md` under a
   "Codex" install stub — Task 6 consumes them from there and must not have
   to re-derive them.

**Verify:** `pytest tests/ -q`; the smoke step passed; README stub present.

## Task 5: Release lockstep (P1, blocked by 4)

**Intent:** one SemVer, two manifests, zero drift — including in CI.

**Files:** `scripts/release/release.py`, `.github/workflows/release.yml`,
`tests/test_release.py`, `tests/test_skills_lint.py`.

**TDD order:**
1. RED: extend `test_release.py`: `write_version()` writes the version to
   BOTH manifests; a fixture with drifted manifests is repaired. Add a lint
   assertion in `tests/test_skills_lint.py` that
   `.github/workflows/release.yml` mentions
   `.codex-plugin/plugin.json` (the workflow currently `git add`s only the
   Claude manifest — without this, the first CI release writes the Codex
   manifest but never commits it, silently drifting them until Task 4's
   version-equality lint fails on an unrelated PR).
2. GREEN: minimal changes in `release.py`'s version-write path and the
   workflow's `git add` line.

**Verify:** `pytest tests/ -q`.

## Task 6: Install docs + smoke checklist (P1, blocked by 3, 4)

**Intent:** a new user on any of the five agents can install and run the
suite from `epic/README.md` alone.

**Files:** `epic/README.md`, `tests/test_skills_lint.py`.

**TDD order:**
1. RED: lint test asserts README has an install section per agent (Claude
   Code, Codex, Kimi, Cursor, OpenCode), a dependencies section covering
   `gh` auth scopes, and a smoke-checklist section.
2. GREEN: write the sections from design §Per-agent packaging — the skills
   locations and caveats (Kimi's dir-precedence rules;
   Codex add-vs-install; Cursor dirs) are IN that table; the verified Codex
   commands come from the README stub Task 4 recorded. Fresh research is
   limited to re-verifying the two cells the design flags (Cursor dirs,
   Kimi project-level dir) via `ctx_fetch_and_index`. The smoke checklist,
   per agent:
   install → invoke `create` (reach phase-1 questions, abort) → invoke the
   driver's `status` on a real epic → invoke `migrate`'s inspection step
   and abort. Plus the two config-fixture legs from design success-criterion
   4 (throwaway repo with only `.agents/epic.yaml`; another with only
   `.claude/epic.yaml`; driver `status` honors both).

**Verify:** `pytest tests/ -q`. (Executing the commands is Task 7's job —
this task's deliverable is the document; its lint proves structure, not
execution.)

## Task 7: Cross-agent smoke + fallout fixes (P1, blocked by 5, 6)

**Intent:** run the Task-6 checklist for real in ALL FIVE agents — Claude
Code, Kimi Code, Codex CLI, Cursor CLI, OpenCode (the Claude leg re-proves
criterion 2 after the prose rewrite); fix what breaks, within bounds.

**Preconditions (STOP and ask the operator for any that are missing):**
- The five CLIs installed and authenticated on this machine.
- `gh` authenticated with `repo` + `project` scopes.
- A real epic to run `status` against — use THIS epic: its issue number is
  stated in this child's issue body, and the child is in any case linked to
  the epic via the native sub-issue parent relation (queryable per the
  GraphQL reference). If neither yields it, STOP and ask; the operator may
  also name another epic.

**Bounds (agreed up front):** structural fixes (prose, paths, manifest
fields, README corrections) land in this task's single PR. Any agent leg
whose failure needs more than that spins out as a NEW follow-up child —
report it to the operator; do not expand this task.

**Order:** per finding — if the defect class is structural, reproduce it as
a lint-test case first (RED), fix (GREEN); else document it in README
caveats. Re-run that agent's checklist leg after each fix.

**Verify:** all five checklist legs + the two config-fixture legs pass;
`pytest tests/ -q`; agent CLI versions recorded in the PR body.

## Self-Review

- Task 1 carries the only structural risk (command/skill coexistence in
  Claude Code) — resolved empirically inside that task, both outcomes
  acceptable, with an explicit operator escalation if non-interactive
  verification is impossible.
- Cross-skill relative reference (`../epic/references/`) is the one
  deviation from skill self-containment; the lint pins the two literal
  links and install docs mandate suite installation.
- 2 → 3 is serialized because both rewrite the same paragraphs (the
  driver's gate/config prose interleaves capability tokens and
  `epic.yaml` mentions); parallel PRs would conflict semantically, not
  just textually.
- Task 4's Codex smoke is mandatory with an operator escape hatch, so
  first-contact manifest validation cannot silently slide into Task 7.
- Task 7 is bounded by the spin-out rule; unbounded "fix what breaks" was
  rejected in review.
