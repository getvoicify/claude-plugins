# epic — unified GitHub epic driver

Drives GitHub epics one child at a time across your repos, using native
**sub-issues** for hierarchy, native **blocked-by relations** for dependencies, and
**a configured project board** for live status. Replaces bespoke per-repo
`.claude/commands/epic.md` variants.

## Commands

The canonical source for each workflow is an [Agent Skills](https://agentskills.io)
tree: `skills/{epic,create,migrate}/SKILL.md`. The Claude Code `commands/*.md`
files are thin shims that read and follow the matching SKILL.md, so the familiar
`/epic:<name>` invocations keep working unchanged.

| Command (Claude Code) | Canonical skill | Purpose |
|---|---|---|
| `/epic <epic#> [status\|next\|run\|<child#>] [--stop-at-pr] [--sweep] [--serial]` | `skills/epic/SKILL.md` | Drive an epic. `next`/`<child#>` take one child through merge + sweep (or stop at PR with `--stop-at-pr`); `run` loops autonomously to completion, dispatching the current wave of runnable children in parallel by default (see [Concurrency](#concurrency) below) — pass `--serial` to force one child at a time instead. `--sweep` (valid on `status` only) opts into the destructive worktree/branch/Project-field reconcile. |
| `/epic:create [rough idea]` | `skills/create/SKILL.md` | Brainstorm session → spec + runbook (docs PR) → epic + sub-issues + dependencies + Project items. Nothing touches GitHub until you approve the breakdown. |
| `/epic:migrate <epic#> [--repo owner/name]` | `skills/migrate/SKILL.md` | Convert a legacy task-list epic (e.g. a working repo's #101–#105) to the new model. |

## Architecture

- **Epic home**: new epics live in the planning repo (`planning.repo` from the
  checkout's epic.yaml); children live in their working repos as cross-repo sub-issues.
  Legacy epics stay where they are.
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
- Each working repo carries `.agents/epic.yaml`, or the `.claude/epic.yaml` fallback.
- `epic/scripts/` — the deterministic core the skills shell out to (see
  [Scripts](#scripts)) — requires **Python 3** with **`pyyaml`** installed and
  importable at runtime; `config.py` imports `yaml` directly and hard-fails on
  import if it's missing.

## Concurrency

Two independent caps bound how many children run at once, each enforced by a
different script — they are not the same check:

- **Global**, across every repo the epic touches: `epic-config.max_parallel`
  (an optional int in the epic issue's `epic-config` block) — **defaults to
  3** when omitted (`epic/scripts/config.py` applies this default). Enforced
  by `schedule.py`'s `runnable(children, max_parallel, in_flight)` when it
  computes the wave; `runnable()` takes no repo argument and never sees
  `worktrees.max_concurrent` — it only bounds the global count.
- **Per repo**: that repo's `worktrees.max_concurrent` in its
  `.agents/epic.yaml` (or the `.claude/epic.yaml` fallback) — **also
  defaults to 3**. This per-repo default was previously undocumented (a gap
  flagged in review); it is recorded here explicitly. Enforced by
  `preflight.py`'s `check()` via its `concurrency-cap` violation, run before
  each child's drive starts. Unlike `epic-config.max_parallel`, no script
  currently supplies this fallback in code — `preflight.py --max-concurrent`
  is a required argument with no built-in default — so set
  `worktrees.max_concurrent` explicitly in each working repo's epic.yaml
  rather than relying on an implicit value.

Regardless of wave width, merging stays serialized to one PR at a time: the
FIFO merge queue `schedule.py` returns admits only its head, so each PR
rebases exactly once onto the `main` its predecessor just produced. Pass
`--serial` to `run` to drive one child at a time end-to-end instead of
dispatching the wave in parallel (this is also the automatic behavior when
the driving harness has no subagent support).

## Scripts

`epic/skills/epic/SKILL.md` shells out to nine modules under `epic/scripts/`
instead of re-deriving their algorithms in prose. Eight of the nine are
pure-Python CLIs; `gh.py` is a library — the sole module that shells out to
`gh`/`git` on the other eight's behalf (see
[github-graphql.md](skills/epic/references/github-graphql.md) for the queries
the GraphQL-issuing scripts run). The eight CLIs follow the same exit-code
convention:
**`0`** success, **`1`** a definite negative answer the driver acts on
(violations found, requirements unmet, a stall/no-progress signal), **`2`** a
usage/config error — except where noted below, since not every script has a
"negative answer" to report.

| Script | What it does | Invocation | Exit codes |
|---|---|---|---|
| `config.py` | Resolves the two-layer epic config (the epic-config block + the target repo's epic.yaml) into one JSON object | `python3 epic/scripts/config.py --epic <epic#> --repo <owner/name>` | `0` resolved; `2` config/parse error (bad YAML, invalid prefix, no project number, `gh` failure) — never returns `1` |
| `preflight.py` | Checks the HARD worktree constraints: prefix validity, an already-existing worktree, the concurrency cap, a nested or detached/`main` start | `python3 epic/scripts/preflight.py --prefix <prefix> --child <n> --max-concurrent <n>` | `0` no violations; `1` one or more violations found; `2` `gh`/git error |
| `schedule.py` | Computes the runnable wave, the FIFO merge queue, and any halt reason from live sub-issue, blocker, and PR state | `python3 epic/scripts/schedule.py --epic <epic#> --repo <owner/name> [--max-parallel <n>]` (`--max-parallel` defaults to 3) | `0` no halt reason; `1` a halt reason is present; `2` `gh` error |
| `mergeability.py` | Derives the complete unmet-merge-requirement set for a PR (draft, behind/dirty, failing or missing checks, unresolved threads, review decision) — the sole gating authority, see [github-graphql.md](skills/epic/references/github-graphql.md) | `python3 epic/scripts/mergeability.py --repo <owner/name> --pr <pr#>` | `0` clean/mergeable; `1` requirements unmet; `2` `gh` error |
| `pr_watch.py` | One tick of a PR watch: reports any activity (head, checks, reviews, threads, comments) or how long until the next tick. Never sleeps, never expires | `python3 epic/scripts/pr_watch.py --repo <owner/name> --pr <pr#> [--reset-backoff\|--resume-backoff] [--state-dir <dir>] [--stop]` | `0` activity observed, PR closed, or watch stopped; `1` no activity yet — schedule the next tick at `next_tick_in_s`; `2` eight consecutive `gh` failures |
| `status.py` | Reports epic completion, Project-field drift, and (with `--sweep-plan`) which worktrees are safe to remove — read-only, never mutates | `python3 epic/scripts/status.py --epic <epic#> --repo <owner/name> [--sweep-plan]` | `0` always (the report itself carries the finding); `2` `gh` error — never returns `1` |
| `converge.py` | Compares two rounds of review findings and reports whether the blocking set converged, made progress, or made none | `python3 epic/scripts/converge.py --prev <path> --curr <path>` | `0` `converged` or `progress`; `1` `no_progress` this round (two consecutive `1`s across driver invocations is the STALL signal); `2` file/JSON read error |
| `verify_pin.py` | Classifies every `verified:`/`assumption:` claim in a context pin against the actual repo source at the pinned ref | `python3 epic/scripts/verify_pin.py --pin <path> [--repo <owner/name>]` (`--repo` resolves from the cwd's `origin` remote when omitted) | `0` no claim is stale; `1` at least one claim is stale; `2` the pin file can't be read |
| `gh.py` | The sole I/O boundary: every `gh`/`git` shell-out anywhere in `epic/scripts/` goes through this module | n/a — library only, no `argparse`/`main()`; imported by the other eight scripts | n/a |

## Defaults

Merge-through is the default terminal behavior everywhere (`--stop-at-pr` to opt
out, valid on `next`/`<child#>` only). Concurrency is governed by the two caps
in [Concurrency](#concurrency) above. Worktrees live under `.worktrees/` (or
`worktrees.root`) and are swept only after their PR merges.

## Migrating an existing install

Epics whose bodies omit `project:` keep working the moment the epic.yaml carries a
`planning:` block — a one-line backfill, no re-migration of epic issues. The driver
resolves the project number in D4 order (`epic-config.project` → `planning.project` →
STOP) from the epic.yaml of **the checkout you run `/epic` from** (the cwd checkout —
at epic-load time no child is selected yet, so that checkout is the only Layer-2
source), so add to that checkout's `.agents/epic.yaml` (or the `.claude/epic.yaml`
fallback):

```yaml
planning: {repo: <owner>/<planning-repo>, project: <n>}
```

For a multi-repo epic, replicate the same `planning:` block into EACH involved repo's
epic.yaml (just like the other Layer-2 config), so whichever checkout you drive from
carries it. That backfill is the entire back-compat story.

### Re-adding the marketplace after the rename

The marketplace catalog is now **`epic-plugins`** (it was previously registered
under an older name). A previously-added registration keeps serving the old
cached catalog, so **remove that stale registration first**, then re-add from
the source repo — the re-add resolves under `epic-plugins`:

Claude Code:

```sh
claude plugin marketplace list                       # find the old registration
claude plugin marketplace remove <old-marketplace>   # drop the stale catalog
claude plugin marketplace add getvoicify/claude-plugins
claude plugin install epic@epic-plugins
```

Codex CLI:

```sh
codex plugin marketplace list
codex plugin marketplace remove <old-marketplace>
codex plugin marketplace add getvoicify/claude-plugins
codex plugin add epic@epic-plugins
```

Fresh installs skip this — follow [Installing](#installing) directly.

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
claude plugin install epic@epic-plugins
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

Register this repo's plugin catalog — this adds the `epic-plugins` marketplace
from the repo's `.agents/plugins/marketplace.json`:

```sh
codex plugin marketplace add getvoicify/claude-plugins
```

Then install the `epic` plugin non-interactively (available since codex-cli
0.145.0):

```sh
codex plugin add epic@epic-plugins
```

If the marketplace was added previously, refresh it first:

```sh
codex plugin marketplace upgrade epic-plugins
```

Reason: a previously-added marketplace serves a stale cached snapshot
(observed: 0.7.0 served when main was 0.9.0, until upgrade).

The CLI's interactive `/plugins` browser and the ChatGPT desktop app remain
alternatives to the non-interactive install.

Pickup: start a new session after install (CLI) / restart the desktop app for
repo-catalog pickup.

Verified with codex-cli 0.145.0 (2026-07-22): marketplace resolved via `.agents/plugins/marketplace.json`, plugin installed to `~/.codex/plugins/cache/epic-plugins/epic/<version>`, and `epic:create` / `epic:epic` / `epic:migrate` all listed as available skills.

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

**Native install (Kimi plugin marketplace).** From Kimi's TUI:

```text
/plugins install https://github.com/getvoicify/claude-plugins
```

This reads the repo-root `.kimi-plugin/plugin.json` and registers `/skill:epic`,
`/skill:create`, `/skill:migrate`. Note: a bare repo URL installs the latest
GitHub **release tag**, so this route works from the first release that includes
the Kimi manifest onward; to install the current `main` before a release, use
`/plugins install https://github.com/getvoicify/claude-plugins/tree/main`.
Manage installs with `/plugins list`, `/plugins enable`, `/plugins reload`.

Invocation: `/skill:<name>` with trailing text as the request, e.g.
`/skill:create a rate-limiter epic`.

### Cursor CLI

Copy `epic/skills/*` into one of Cursor's skills directories:

- **Project**: `.cursor/skills/` or `.agents/skills/`
- **User**: `~/.cursor/skills/` or `~/.agents/skills/`

Invocation: by default skills are applied automatically when the agent
determines they are relevant; they can also be manually invoked by typing
`/` in Agent chat and searching for the skill name — the skill name is the
dir name, so `/create`, `/epic`, `/migrate`, with trailing arguments
(e.g. `/epic 9 status`, `/create a rate-limiter epic`).

Headless note: plain `cursor-agent -p` auto-rejects ALL tool calls (despite
the help text's claim of full tool access) — the gh-dependent legs (`status`,
`migrate`) need `--force` (full auto-approval; `--auto-review` exists as an
untested middle option). For headless smoke runs use the full command
`cursor-agent -p --output-format text --trust --force "<prompt>"`.

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

In a scratch project with no `origin` remote, name the repo explicitly in
migrate prompts — the skill's `--repo` default resolves from the cwd
repo's origin (e.g. `Use the migrate skill: --repo getvoicify/claude-plugins
epic 9`). Status has no `--repo` argument and works remote-less via
org-level search.

## Smoke checklist

The manual verification script for a release across all five agents.
(Executing it is a separate task — this section is the script.)

Last smoke: all five agents verified 2026-07-22/23 — claude 2.1.217,
codex-cli 0.145.0, kimi 0.29.0, opencode 1.17.13 (run as
`opencode -m opencode/deepseek-v4-flash-free` — the configured default
model was broken on the smoke machine, hence the explicit flag), cursor-agent
2026.07.20.

Cross-org / user-account smoke (org-independence): passed 2026-07-23 —
`epic-plugins/epic@0.13.0`, Claude Code 2.1.218 only. This run varies the
*owner* axis, not the agent axis (cross-agent parity was already proven by the
five-agent record above): a throwaway user-account scratch context — a
scratch repo plus a user-level ProjectV2 under a personal user account (a
user, not an org) — created solely for this run and deleted afterward. Four
legs, all PASS, zero mutations:

- **A** — `/epic <n> status`: owner derived from the owner half of
  `epic-config.repo`; ProjectV2 resolved via `organization(login:)`
  NOT_FOUND → `user(login:)`; with `epic-config.project` omitted, the
  no-default D4 fallback picked up `planning.project`.
- **B** — `/epic:create` (dry-run): epic home + project stated from the
  `planning:` seam in the cwd repo's `.agents/epic.yaml` without asking the
  operator (D3); the project *number* is the uniquely seam-sourced datum
  (the home repo is confounded by cwd == `planning.repo`).
- **C** — `/epic:migrate`: legacy read+parse with owner derived from the
  cwd/config; halted at the confirmation gate with no mutation.
- **D** — `/epic 9 status` (back-compat regression): the repo's own closed
  epic #9 still resolved its project via the `.claude/epic.yaml`
  `planning.project` backfill.

Zero-mutation evidence: the real epic's comment count was unchanged and the
scratch resources gained no items or sub-issues.

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
