# Making the epic plugin installable in Kimi Code — design

## Problem

Kimi Code's native plugin marketplace can't install this repo:

```
Error: Failed to install https://github.com/getvoicify/claude-plugins:
No manifest at kimi.plugin.json or .kimi-plugin/plugin.json
```

The plugin already ships native manifests for Claude (`epic/.claude-plugin/plugin.json`)
and Codex (`epic/.codex-plugin/plugin.json`), and supports Kimi/OpenCode/Cursor via the
Agent Skills skills-directory copy. But Kimi *also* has a plugin-marketplace install path
that expects a Kimi manifest, which the repo doesn't provide. This adds that manifest so
`/plugins install <repo>` works in Kimi, without disturbing the existing skills-dir route.

## Research findings (Kimi Code v0.29.1)

Grounded in the official docs, the `kimi` binary's embedded source, and a real installed
example (`obra/superpowers`):

1. **Manifest location — repo root only.** Kimi looks for `.kimi-plugin/plugin.json` (or a
   bare `kimi.plugin.json`) at the **repo root**. For GitHub installs the plugin root is the
   repo root; the resolver accepts only `owner/repo` (no subdir) and descends at most one
   level into the release zip. The manifest therefore **cannot** live at `epic/.kimi-plugin/`
   the way the Codex manifest lives inside `epic/`.
2. **Skills path is repo-root-relative.** `skills` entries must start with `./` and resolve
   against the plugin root (repo root), not the manifest's own directory. So the manifest
   points `skills: "./epic/skills/"` — which registers `/skill:epic`, `/skill:create`,
   `/skill:migrate` (each `epic/skills/<name>/SKILL.md`'s frontmatter `name` is the
   invocation name; non-builtin skills get no plugin prefix).
3. **No repo catalog needed.** Unlike Codex (`.agents/plugins/marketplace.json`), Kimi's
   "marketplace" is Moonshot's central catalog; a GitHub-URL install reads the plugin
   manifest directly from the downloaded release.
4. **Install is a TUI slash command**, not a CLI subcommand: `/plugins install
   https://github.com/getvoicify/claude-plugins`, then `/plugins list|info|enable|disable|remove|reload`.
5. **Bare-URL install pulls the latest release TAG**, falling back to default-branch HEAD
   only when no release exists. This repo has releases, so the manifest must ship in a
   release tag before the bare URL resolves it; `/plugins install <repo>/tree/main` installs
   HEAD directly for pre-release use.
6. **`version` is optional display metadata** to Kimi (update-detection compares release
   tags, not the manifest field) — kept in version-lockstep for consistency.

## Design

### D1 — Root `.kimi-plugin/plugin.json`

A repo-root `.kimi-plugin/plugin.json` (dot-dir, matching the `.claude-plugin`/`.codex-plugin`
convention and the real `superpowers` example):

```json
{
  "name": "epic",
  "version": "0.13.0",
  "description": "Unified GitHub epic driver: drive epics via sub-issues + ProjectV2, brainstorm new epics into existence, migrate legacy task-list epics",
  "skills": "./epic/skills/"
}
```

Created at the current version (`0.13.0`, matching the Claude + Codex manifests); this
change's own release then bumps all three to the next version in lockstep. `name` satisfies
Kimi's `^[a-z0-9][a-z0-9_-]{0,63}$` id regex and matches the Claude/Codex plugin name
`epic`; `description` is the shared one-liner. No repo catalog file is added (finding 3).

### D2 — Release version-lockstep

`scripts/release/release.py`'s `write_version()` gains a third manifest:
`_kimi_plugin_json(repo_root)` = `<repo_root>/.kimi-plugin/plugin.json` (repo-root path, NOT
`<source>/…` — the Kimi manifest is at root, unlike Codex/Claude which are under `epic/`),
patched **skip-if-absent** exactly like the Codex path so a future single-manifest plugin
can't crash a release under `set -euo pipefail`. `read_version()` stays Claude-only (one
source of truth). `.github/workflows/release.yml` stages the Kimi manifest inside an
`if [ -f ]` guard after the existing Claude/Codex `git add` lines.

### D3 — Lint

`tests/test_skills_lint.py` gains Kimi-manifest tests mirroring the Codex ones: manifest
present, valid JSON, `name == "epic"`, `version` equals the Claude manifest's (lockstep),
and `skills` is present, `./`-prefixed, and resolves (relative to repo root) to a real
directory whose immediate subdirectories carry `SKILL.md`.

### D4 — Docs

`epic/README.md`'s Kimi Code section gains the native install route alongside the existing
skills-dir copy (which stays — it's release-independent and still valid):

- `/plugins install https://github.com/getvoicify/claude-plugins` (installs the latest
  release tag — works from the release this change ships in onward), with a note that
  `…/tree/main` installs HEAD for pre-release use, and the `/plugins list|enable|reload`
  management commands.

The forbidden-literal README lint continues to pass (no new `gangan`/bare-`getvoicify`/
`tom-plugins`/node-ID literals; the `getvoicify/claude-plugins` slug is allowed).

## Success criteria

1. `.kimi-plugin/plugin.json` exists at the repo root, valid per Kimi's schema, `name`
   `epic`, `skills` `./epic/skills/`, version-locked to the Claude + Codex manifests.
2. The release machinery bumps all three manifests in lockstep (Claude, Codex, Kimi); the
   lockstep lint covers the Kimi manifest.
3. The README documents the native `/plugins install` route + the release-tag caveat, and
   the full lint suite stays green.
4. After this change ships in a release, `/plugins install https://github.com/getvoicify/claude-plugins`
   resolves the manifest and registers `/skill:epic`, `/skill:create`, `/skill:migrate` in
   Kimi (verified live, or documented as pending an operator with a Kimi install if the
   drive machine can't reach the TUI).

## Out of scope

- The existing skills-dir copy route (unchanged).
- Hosting a custom Kimi marketplace catalog (finding 3 — not needed for GitHub-URL install).
- OpenCode/Cursor native manifests (they remain skills-dir installs, as designed).
- Any change to the Claude or Codex manifests beyond the shared version bump.

## Verification note (release-tag chicken-and-egg)

This change lands as a `feat`, so its squash-merge cuts a new release whose tag includes the
Kimi manifest. Native bare-URL install therefore begins working from that release. The
`/tree/main` route is the interim path for testing before the release ships.
