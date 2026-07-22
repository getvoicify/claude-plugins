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

### Codex CLI

Register this repo's plugin catalog (`.agents/plugins/marketplace.json`):

```sh
codex plugin marketplace add getvoicify/claude-plugins
```

That command only registers the catalog — there is no non-interactive install
command. Install the `epic` plugin through the CLI's interactive `/plugins`
browser, or through the ChatGPT desktop app.

Pickup: start a new session after install (CLI) / restart the desktop app for
repo-catalog pickup.

Install smoke pending on this machine — commands per developers.openai.com/codex docs (fetched 2026-07-22); to be verified when the parked smoke runs.

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
