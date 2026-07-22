# epic — unified GitHub epic driver

Drives GitHub epics one child at a time across the gangan repos, using native
**sub-issues** for hierarchy, native **blocked-by relations** for dependencies, and
**the configured org Project (default #2 "Gangan")** for live status. Replaces the per-repo
`.claude/commands/epic.md` variants in gangan-mobile and gangan-api.

## Commands

The canonical source for each workflow is an [Agent Skills](https://agentskills.io)
tree: `skills/{epic,create,migrate}/SKILL.md`. The Claude Code `commands/*.md`
files are thin shims that read and follow the matching SKILL.md, so the familiar
`/epic:<name>` invocations keep working unchanged.

| Command (Claude Code) | Canonical skill | Purpose |
|---|---|---|
| `/epic <epic#> [status\|next\|run\|<child#>] [--stop-at-pr]` | `skills/epic/SKILL.md` | Drive an epic. `next`/`<child#>` take one child through merge + sweep (or stop at PR with `--stop-at-pr`); `run` loops autonomously to completion. |
| `/epic:create [rough idea]` | `skills/create/SKILL.md` | Brainstorm session → spec + runbook (docs PR) → epic + sub-issues + dependencies + Project items. Nothing touches GitHub until you approve the breakdown. |
| `/epic:migrate <epic#> [--repo owner/name]` | `skills/migrate/SKILL.md` | Convert a legacy task-list epic (e.g. gangan-api #278–#282) to the new model. |

## Architecture

- **Epic home**: new epics live in `getvoicify/gangan` (planning repo); children live
  in their working repos as cross-repo sub-issues. Legacy epics stay where they are.
- **Two-layer config**:
  - Per-epic: a slim fenced `epic-config` YAML block in the epic issue body
    (`epic`, `repo`, `docs_repo`, `worktree_prefix`, `spec`, `runbook`, `custom_gates`).
  - Per-repo: `.agents/epic.yaml` in each working repo, with `.claude/epic.yaml` as
    the fallback (checked second) — toolchain, verified merge-gate facts (required
    checks, approvals, thread resolution), docs dirs, worktree policy, and the
    custom-gate catalog. Adding a gate = a PR to that repo, not a plugin release.
    **Shadowing trap**: lookup is file-level and first-found-wins — a
    `.agents/epic.yaml` shadows the *entire* `.claude/epic.yaml`, not
    individual keys. In a fallback-only repo, either migrate the whole file to
    `.agents/epic.yaml` or keep editing `.claude/epic.yaml`; never create a
    gate-only `.agents/epic.yaml`, because that partial primary file would
    silently hide the rest of the fallback config.
- **Status tracking**: the driver writes Project Status transitions
  (Todo → In Progress → In Review → Done, or Parked) at each lifecycle point. No
  body-checkbox editing — sub-issue closure updates `subIssuesSummary` automatically.
- **Reference**: `skills/epic/references/github-graphql.md` holds every GraphQL incantation,
  verified project/field IDs, the PR-mapping rule, and API gotchas.

## Requirements

- `gh` CLI authenticated with scopes `repo, project, read:org`.
- Local checkouts of the working repos as siblings (the driver resolves a child's
  checkout by matching `origin` URLs in the cwd's parent directory).
- Each working repo carries `.agents/epic.yaml`, or the `.claude/epic.yaml` fallback
  (gangan-api, gangan-mobile, gangan-angular-workspace: done 2026-06-10).

## Defaults

Merge-through is the default terminal behavior everywhere (`--stop-at-pr` to opt
out). One child in flight at a time. Worktrees live under `.worktrees/` and are
swept only after their PR merges. Tunable budget constants are documented in the
driver command.

## Installing

**Recommended route**: copy `epic/skills/*` into your project's
`.agents/skills/` — that one copy serves Cursor CLI, Kimi Code, and OpenCode
(and Codex CLI per its docs' `.agents/skills` support; the plugin flow below is
the verified Codex route). The per-agent directories listed under each agent
are alternatives.

Whichever directory you choose, the three skills install **as a suite** — the
`epic`, `create`, and `migrate` dirs must land as siblings, because `create`
and `migrate` link to the shared `../epic/references/github-graphql.md`.

All agents need the `gh` CLI authenticated with scopes `repo, project, read:org`
(see [Requirements](#requirements)).

### Claude Code

Add this repo (or a local checkout of it) as a plugin marketplace, then install
the plugin:

```sh
claude plugin marketplace add getvoicify/claude-plugins   # or /path/to/checkout
claude plugin install epic@tom-plugins
```

Skills are auto-discovered from the plugin's `skills/` dir; the familiar
`/epic:<name>` commands keep working via the shims (see [Commands](#commands)).

### Codex CLI

Register this repo's plugin catalog — this adds the `tom-plugins` marketplace
from the repo's `.agents/plugins/marketplace.json`:

```sh
codex plugin marketplace add getvoicify/claude-plugins
```

Then install the `epic` plugin non-interactively (available since codex-cli
0.145.0):

```sh
codex plugin add epic@tom-plugins
```

The CLI's interactive `/plugins` browser and the ChatGPT desktop app remain
alternatives to the non-interactive install.

Pickup: start a new session after install (CLI) / restart the desktop app for
repo-catalog pickup.

Verified with codex-cli 0.145.0 (2026-07-22): marketplace resolved via `.agents/plugins/marketplace.json`, plugin installed to `~/.codex/plugins/cache/tom-plugins/epic/<version>`, and `epic:create` / `epic:epic` / `epic:migrate` all listed as available skills.

### Kimi Code

Copy `epic/skills/*` into one of Kimi's skills directories.

- **Project scope**: `.agents/skills/` (project root = the nearest ancestor
  directory containing `.git`). The same two-group split as user scope applies
  at project level, so a project `.config/agents/skills/` — if present — would
  shadow `.agents/skills/`.
- **User scope**, two groups:
  - *Brand dirs*: `~/.kimi/skills/`, `~/.claude/skills/`, `~/.codex/skills/` —
    **all** that exist are merged; on a skill-name conflict the precedence is
    kimi > claude > codex (`merge_all_available_skills` defaults to true).
  - *Generic dirs*: `~/.config/agents/skills/` else `~/.agents/skills/` —
    first-existing-wins: `~/.agents/skills/` is ignored entirely if
    `~/.config/agents/skills/` exists.

Invocation: `/skill:<name>` with trailing text as the request, e.g.
`/skill:create a rate-limiter epic`.

### Cursor CLI

Copy `epic/skills/*` into one of Cursor's skills directories:

- **Project**: `.cursor/skills/` or `.agents/skills/`
- **User**: `~/.cursor/skills/` or `~/.agents/skills/`

### OpenCode

Copy `epic/skills/*` into one of OpenCode's skills directories:

- **Project**: `.opencode/skills`, `.claude/skills`, or `.agents/skills`
- **Global**: `~/.config/opencode/skills`, `~/.claude/skills`, or
  `~/.agents/skills`

## Smoke checklist

The manual verification script for a release across all five agents.
(Executing it is a separate task — this section is the script.)

**Preconditions**

- [ ] All five CLIs (Claude Code, Codex CLI, Kimi Code, Cursor CLI, OpenCode)
      installed **and** authenticated.
- [ ] `gh` CLI authenticated with scopes `repo, project, read:org`
      (`gh auth status` shows them).
- [ ] A real epic to run `status` against — this epic is
      [getvoicify/claude-plugins#9](https://github.com/getvoicify/claude-plugins/issues/9).

**Per agent** — repeat for each of Claude Code, Codex CLI, Kimi Code,
Cursor CLI, OpenCode:

- [ ] Install per that agent's [Installing](#installing) subsection.
- [ ] Invoke `create` — reach the phase-1 questions, then abort.
- [ ] Invoke the driver's `status` on the real epic
      (getvoicify/claude-plugins#9) and confirm it reports children + Project
      status.
- [ ] Invoke `migrate`'s inspection step and abort before any writes.

**Config-fixture legs** — verifies the two-layer per-repo config lookup:

- [ ] Throwaway repo with **only** `.agents/epic.yaml`: driver `status` honors
      it. Shadowing note: lookup is file-level and first-found-wins — this
      file must be complete, since its mere presence hides any
      `.claude/epic.yaml` entirely.
- [ ] Another throwaway repo with **only** `.claude/epic.yaml`: driver
      `status` honors the fallback. Shadowing note: to change such a repo's
      config, migrate the whole file to `.agents/epic.yaml` or keep editing
      `.claude/epic.yaml` — never add a gate-only `.agents/epic.yaml`, which
      would shadow the entire fallback file.

## Releasing

Versioning is automated. On every push to `main`, the `release` workflow reads each
plugin from `.claude-plugin/marketplace.json`, inspects the commits since that plugin's
last `<name>-v*` tag, and bumps its `version` in `plugin.json` from
[Conventional Commits](https://www.conventionalcommits.org/):

- `feat: …` → **minor** (`0.1.0` → `0.2.0`)
- `fix: …` / `perf: …` → **patch** (`0.1.0` → `0.1.1`)
- `feat!: …` or a `BREAKING CHANGE:` footer → **major** (`0.1.0` → `1.0.0`)
- `docs` / `chore` / `refactor` / `style` / `ci` / `test` → no release

The workflow commits the bump back to `main` (`chore(release): … [skip ci]`), pushes a
`<name>-vX.Y.Z` tag, and cuts a GitHub Release with auto-generated notes. Bumping the
`version` is what makes `/plugin update` deliver the change to installed users — so just
write Conventional Commits and push; the version takes care of itself.

The first run with no tag seeds a baseline tag at the current `plugin.json` version
without bumping.
