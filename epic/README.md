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

**Recommended route**: clone this repo, then copy the three skill dirs into
your project's `.agents/skills/`:

```sh
git clone https://github.com/getvoicify/claude-plugins
mkdir -p <your-project>/.agents/skills
cp -R claude-plugins/epic/skills/epic \
      claude-plugins/epic/skills/create \
      claude-plugins/epic/skills/migrate \
      <your-project>/.agents/skills/
```

That one copy serves Cursor CLI, Kimi Code, and OpenCode (and Codex CLI per
its docs' `.agents/skills` support — design-sourced; verified only via the
plugin flow below). The per-agent directories listed under each agent are
alternatives.

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

Invocation: `/epic:create <rough idea>`, `/epic <epic#> status`,
`/epic:migrate <epic#>`.

Headless note: `claude -p` cannot read the plugin's SKILL.md outside the cwd
without `--add-dir` pointing at your clone of this repo (or the installed
plugin's cache dir shown by `claude plugin list`); the `status` leg additionally
needs workspace trust + a read-only `gh` allowlist in the project's settings —
without these the turn budget burns silently.

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

If the marketplace was added previously, refresh it first:

```sh
codex plugin marketplace upgrade tom-plugins
```

Reason: a previously-added marketplace serves a stale cached snapshot
(observed: 0.7.0 served when main was 0.9.0, until upgrade).

The CLI's interactive `/plugins` browser and the ChatGPT desktop app remain
alternatives to the non-interactive install.

Pickup: start a new session after install (CLI) / restart the desktop app for
repo-catalog pickup.

Verified with codex-cli 0.145.0 (2026-07-22): marketplace resolved via `.agents/plugins/marketplace.json`, plugin installed to `~/.codex/plugins/cache/tom-plugins/epic/<version>`, and `epic:create` / `epic:epic` / `epic:migrate` all listed as available skills.

Invocation: explicit — run `/skills` or type `$` to mention a skill in your
prompt (e.g. `$epic:create a rate-limiter epic`, `$epic:epic 9 status`);
implicit — Codex can choose a skill on its own when your task matches the
skill `description`.

Network note: the driver's `status` and `migrate` need GitHub API access, and
the default `codex exec` sandbox blocks api.github.com — run those legs with
an operator-approved network-enabled profile (`workspace-write` +
`network_access=true`); `-s danger-full-access` also works but disables
filesystem sandboxing entirely, not just the network block.

### Kimi Code

Copy `epic/skills/*` into one of Kimi's skills directories.

- **User scope**, two groups:
  - *Brand dirs*: `~/.kimi/skills/`, `~/.claude/skills/`, `~/.codex/skills/` —
    **all** that exist are merged; on a skill-name conflict the precedence is
    kimi > claude > codex (`merge_all_available_skills` defaults to true).
  - *Generic dirs*: `~/.config/agents/skills/` else `~/.agents/skills/` —
    first-existing-wins: `~/.agents/skills/` is ignored entirely if
    `~/.config/agents/skills/` exists.
- **Project scope**: `.agents/skills/` (project root = the nearest ancestor
  directory containing `.git`). The same two-group split as user scope (above)
  applies at project level, so a project `.config/agents/skills/` — if
  present — would shadow `.agents/skills/`.

Invocation: `/skill:<name>` with trailing text as the request, e.g.
`/skill:create a rate-limiter epic`.

### Cursor CLI

Copy `epic/skills/*` into one of Cursor's skills directories:

- **Project**: `.cursor/skills/` or `.agents/skills/`
- **User**: `~/.cursor/skills/` or `~/.agents/skills/`

Invocation: by default skills are applied automatically when the agent
determines they are relevant; they can also be manually invoked by typing
`/` in Agent chat and searching for the skill name — the skill name is the
dir name, so `/create`, `/epic`, `/migrate` (e.g. `/epic 9 status`,
`/create a rate-limiter epic`; the docs pin only the `/skill-name` form —
trailing-argument form to be confirmed in the cross-agent smoke).

### OpenCode

Copy `epic/skills/*` into one of OpenCode's skills directories:

- **Project**: `.opencode/skills`, `.claude/skills`, or `.agents/skills`
- **Global**: `~/.config/opencode/skills`, `~/.claude/skills`, or
  `~/.agents/skills`

Invocation: skills trigger implicitly by description-match — there is no
user-facing slash command. OpenCode lists each skill's name + description in
its `skill` tool and the agent loads one by calling e.g.
`skill({ name: "epic" })` when your request matches, so name the skill in
your prompt: ask `Use the epic skill: status for epic #9`,
`Use the create skill: a rate-limiter epic`, or
`Use the migrate skill: convert epic #9`.

## Smoke checklist

The manual verification script for a release across all five agents.
(Executing it is a separate task — this section is the script.)

Last smoke: 2026-07-22, verified with claude 2.1.217, codex-cli 0.145.0,
kimi 0.29.0. OpenCode and Cursor CLI legs pending (auth/install — see
issue #16). The gangan-default-epic-home probe order (skills try
getvoicify/gangan then the cwd repo) is by design.

**Preconditions**

- [ ] All five CLIs (Claude Code, Codex CLI, Kimi Code, Cursor CLI, OpenCode)
      installed **and** authenticated.
- [ ] `gh` CLI authenticated with scopes `repo, project, read:org`
      (`gh auth status` shows them).
- [ ] A `python3` runtime is permitted (with `pyyaml` installable) —
      REQUIRED: the skills' strict config-parse steps mandate python +
      pyyaml, never regex-only extraction (see `skills/epic/SKILL.md`).
      If the harness does not permit `python3`, STOP and grant it before
      driving. (The config-fixture legs' Layer-2 lookup dry-runs, which
      only locate and read the file, tolerated its absence — an
      observation scoped to those legs, not the skills generally.)
- [ ] A real epic to run `status` against — this epic is
      [getvoicify/claude-plugins#9](https://github.com/getvoicify/claude-plugins/issues/9).

**Per agent** — repeat for each of Claude Code, Codex CLI, Kimi Code,
Cursor CLI, OpenCode:

- [ ] Install per that agent's [Installing](#installing) subsection, using
      that subsection's invocation line for the legs below.
- [ ] Invoke `create` — reach the phase-1 questions, then abort by answering
      them with "smoke test — abort".
- [ ] Invoke the driver's `status` on the real epic
      (getvoicify/claude-plugins#9) and confirm it reports children + Project
      status.
- [ ] Invoke `migrate` and stop after its step 1, "Read & parse (no
      mutations yet)" (per `skills/migrate/SKILL.md`) — abort before any
      writes.

Claude Code leg, headless (`claude -p`): pass `--add-dir` pointing at your
clone of this repo (or the installed plugin's cache dir shown by
`claude plugin list`) or the plugin's SKILL.md outside the cwd is unreadable;
the `status` leg also needs workspace trust + a read-only `gh` allowlist in
the scratch project's settings — without these the turn budget burns
silently.

**Config-fixture legs** — a self-contained dry-run of the driver's Layer-2
lookup order. The driver loads per-repo config from the checkout of the repo
the *current child* lives in (resolved by matching `origin` URLs), never from
an arbitrary cwd — so a throwaway repo can't be exercised via `status` on a
real epic. Instead these legs ask the agent under test to walk the lookup
itself.

Setup (once):

```sh
mkdir epic-smoke-l2
cd epic-smoke-l2
git init
```

Minimal schema-shaped config used by Legs A and B — write this exact content:

```yaml
toolchain:
  prefix: ""
  commands:
    test: "true"
merge:
  method: squash
gates: {}
```

Execution (each leg): in the throwaway repo, with the epic skills installed,
ask the agent under test: "follow the epic driver skill's Layer-2 config
lookup for this repo and state which file it loaded and its parsed contents."

- [ ] **Leg A** — the config exists at `.agents/epic.yaml` **only**
      (`mkdir .agents`, write the block above there; no `.claude/epic.yaml`).
      Pass: the agent names `.agents/epic.yaml` as the loaded file and
      reports the parsed `toolchain`/`merge`/`gates`. Shadowing note: lookup
      is file-level and first-found-wins — this file must be complete, since
      its mere presence hides any `.claude/epic.yaml` entirely.
- [ ] **Leg B** — the config exists at `.claude/epic.yaml` **only**
      (`rm .agents/epic.yaml`, `mkdir .claude`, write the same block there).
      Pass: the agent names `.claude/epic.yaml` as the loaded file and
      reports the same parsed contents. Shadowing note: to change such a
      repo's config, migrate the whole file to `.agents/epic.yaml` or keep
      editing `.claude/epic.yaml` — never add a gate-only
      `.agents/epic.yaml`, which would shadow the entire fallback file.
- [ ] **Leg C** — **neither** file exists (`rm .claude/epic.yaml`). Pass:
      the agent reports the driver's missing-config STOP, naming BOTH paths
      (`.agents/epic.yaml` and `.claude/epic.yaml`) as absent and
      `.agents/epic.yaml` as the file to author before driving children.

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
