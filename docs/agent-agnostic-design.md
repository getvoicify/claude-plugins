# Agent-agnostic epic plugin — design

## Problem

The epic plugin is locked to Claude Code. Its three workflows (`/epic:create`,
`/epic`, `/epic:migrate`) live as Claude command markdown with Claude-only
mechanisms baked in:

- `${CLAUDE_PLUGIN_ROOT}` to locate `references/github-graphql.md` (`epic/commands/epic.md:12`, `create.md:13`, `migrate.md:11`)
- Claude tool names in the prose: `AskUserQuestion` (`epic.md:91,100,289`, `create.md:22`, `migrate.md:52`), subagent dispatch (`epic.md:179-213` and further occurrences through `:341`, `create.md:26,59-60` — illustrative, the lint matches all), `ScheduleWakeup` for the `run` loop (`epic.md:310`)
- `.claude-plugin/plugin.json` manifest + marketplace, `/plugin update` cache keyed on its `version`
- Working-repo config pinned to the Claude-flavored path `.claude/epic.yaml`

The operator wants to drive epics from whichever agent/model fits the task —
at minimum **Kimi Code** and **OpenAI Codex CLI**, plus **OpenCode** and
**Cursor CLI** — with full parity for all three workflows, without maintaining
per-agent forks.

## Decisions (from brainstorming)

- **Skills-first single source.** The plugin's canonical form becomes an
  [Agent Skills](https://agentskills.io) tree (`SKILL.md` standard). All five
  agents — Claude Code included — consume the *same files*. Per-agent layer is
  thin packaging + install docs only.
  - Rejected: *neutral core + per-agent generators* (build machinery, format
    drift as five agent formats churn) and *hand-maintained per-agent copies*
    (guaranteed drift). Both conflict with "no deadline — do it right".
- **Capability-conditional prose.** One text serves all agents; see
  §Guarded-capability rule for the exact, lintable pattern.
- **Neutral working-repo config path.** `.agents/epic.yaml` becomes the
  primary config location; `.claude/epic.yaml` remains a documented fallback
  (checked second) so existing working repos keep working unchanged. Error
  messages and examples name the primary path.
- **Epic home**: this epic and its children live in `getvoicify/claude-plugins`.

## Architecture

### Skills tree (canonical source)

```
epic/
  skills/
    epic/                 # the driver
      SKILL.md
      references/
        github-graphql.md # single shared copy lives in the hub skill
    create/
      SKILL.md            # points at ../epic/references/github-graphql.md
    migrate/
      SKILL.md            # same
  commands/               # Claude-only thin shims, kept ONLY if Claude Code
                          # tolerates command+skill name coexistence
  .claude-plugin/plugin.json
  .codex-plugin/plugin.json   # new: Codex bundle manifest, same skills
```

- The GraphQL reference stays a **single copy**, inside the `epic` skill's
  `references/` dir. `create` and `migrate` reference it by the literal
  sibling-relative path `../epic/references/github-graphql.md`; the `epic`
  skill uses `references/github-graphql.md`. The lint test asserts exactly
  these two literal link strings appear and resolve relative to each SKILL.md
  — it does NOT attempt to resolve arbitrary paths mentioned in prose (the
  bodies legitimately contain working-repo paths and glob templates that can
  never resolve in this repo).
- Frontmatter is the cross-agent intersection: `name` (must equal the dir
  name — OpenCode enforces this) + `description`. No `$ARGUMENTS` templating —
  Kimi (`/skill:<name>`), Codex, and Claude all pass trailing text as the
  request; each SKILL.md opens with "the text after the skill name is the
  epic/child reference".
- **Claude shims:** each `epic/commands/*.md` becomes a short command whose
  body says "Read and follow `${CLAUDE_PLUGIN_ROOT}/skills/<name>/SKILL.md`;
  treat `$ARGUMENTS` as the request." `${CLAUDE_PLUGIN_ROOT}`/`$ARGUMENTS`
  are ALLOWED in `commands/` (Claude-only files); banned only under `skills/`.
  If child 1 finds Claude Code rejects command+skill name coexistence (this
  is undocumented — resolve empirically), `commands/` is deleted instead;
  skills alone still provide `/epic:<name>` invocation.

### Guarded-capability rule (the lintable prose pattern)

Inside `epic/skills/**/SKILL.md`:

- `${CLAUDE_PLUGIN_ROOT}` — banned outright (zero occurrences).
- Capability tokens — `AskUserQuestion`, `ScheduleWakeup`, `subagent`,
  `spawn`, `dispatch` (case-insensitive) — may appear only in a **guarded
  paragraph**: a blank-line-delimited block that also contains one of the
  canonical fallback markers `if your harness` or `if supported`,
  followed by an `otherwise` clause naming the fallback.
- Canonical fallbacks: structured questions → numbered plain-text questions,
  waiting for the reply; subagent dispatch → perform the steps inline,
  sequentially (reviews still happen, in-session); scheduler (`run` loop) →
  an in-session loop.

The paragraph-level rule is deterministic (split on blank lines, no sentence
parsing) and is what `tests/test_skills_lint.py` implements.

### Per-agent packaging & install

| Agent | Skills location | Install / invocation |
|-------|-----------------|----------------------|
| Claude Code | plugin `skills/` auto-discovered | existing marketplace; `/epic:<name>` |
| Codex CLI | `.agents/skills` (repo) or `~/.agents/skills` (user) | `.codex-plugin/plugin.json` bundle + root catalog `.agents/plugins/marketplace.json`; `codex plugin marketplace add getvoicify/claude-plugins`. Caveat: marketplace *add* registers the catalog; the install step's CLI syntax must be verified against live docs (some flows route via the ChatGPT desktop app) |
| Kimi Code | `~/.kimi/skills/`, `~/.claude/skills/`, `~/.codex/skills/` (brand, that precedence); `~/.config/agents/skills/` else `~/.agents/skills/` (generic — first existing wins); project `.agents/skills/` (re-verify this cell at implementation) | copy/clone `epic/skills/*` into one dir; `/skill:<name>` |
| Cursor CLI | `.cursor/skills` (project) / `~/.cursor/skills` (user) — verify exact dirs against cursor.com/docs/skills at implementation | copy `epic/skills/*` in |
| OpenCode | project `.opencode/skills`, `.claude/skills`, `.agents/skills`; global `~/.config/opencode/skills`, `~/.claude/skills`, `~/.agents/skills` | copy `epic/skills/*` in |

The three skills install **as a suite** (sibling dirs) — required by the
shared-reference relative path. Install docs also cover the hard dependency:
`gh` CLI authenticated with `repo` + `project` scopes (same as today).

### Versioning / release

`.claude-plugin/plugin.json` `version` stays the single source of truth.
`scripts/release/release.py` mirrors the bumped version into
`.codex-plugin/plugin.json`, and `.github/workflows/release.yml` commits
**both** manifests (today it `git add`s only the Claude one — left unchanged,
the first CI release would silently drift them).

### CI

New `.github/workflows/test.yml`: on PR and push to main, install pinned dev
deps (`requirements-dev.txt`, new — pins `pytest`) and run `pytest tests/ -q`.
Without this, no lint test in this epic runs anywhere but a contributor's
machine (the repo currently has no test CI and no dependency manifest).

## Success criteria

1. Each of the three workflows is drivable end-to-end from each of the five
   agents (Claude Code included) by following that agent's install doc —
   verified by running the manual smoke checklist once per agent in child 7.
2. Claude Code behavior is unregressed — re-verified in child 7's Claude leg
   AFTER the prose rewrite (child 2), not just at restructure time.
3. Structural lint tests (frontmatter validity, the two literal reference
   links resolve, guarded-capability rule, manifest version lockstep,
   `release.yml` names both manifests) pass in the new test CI workflow.
4. Config lookup order works behaviorally: the child-7 checklist includes a
   fixture leg — one throwaway repo with only `.agents/epic.yaml`, one with
   only `.claude/epic.yaml`; the driver honors both.

## Out of scope (YAGNI)

- Gemini CLI validation (skills tree likely works there; not verified)
- Automated in-agent E2E tests — manual smoke checklist instead
- Kimi flow-skills, Codex hooks, or any per-agent UX polish beyond plain prose
- MCP-based distribution
- Publishing to any registry beyond the existing Claude marketplace + Codex
  `marketplace add` from this repo

## Files added/changed

- `epic/skills/{epic,create,migrate}/SKILL.md` (moved+converted from `commands/`)
- `epic/skills/epic/references/github-graphql.md` (moved)
- `epic/commands/*.md` → shims or deleted (child-1 decision)
- `epic/.codex-plugin/plugin.json` (new); root `.agents/plugins/marketplace.json`
  (Codex repo catalog — confirm exact requirement against live docs in child 4)
- `.github/workflows/test.yml`, `requirements-dev.txt` (new)
- `scripts/release/release.py` (+ version mirroring),
  `.github/workflows/release.yml` (+ commit both manifests),
  `tests/test_release.py`
- `tests/test_skills_lint.py` (new)
- `epic/README.md` (per-agent install + smoke checklist)
