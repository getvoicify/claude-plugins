# Semantic-versioning release workflow — design

**Date:** 2026-06-16
**Status:** Approved (brainstorming → spec)
**Repo:** `getvoicify/claude-plugins` (marketplace `tom-plugins`)

## Problem

A Claude Code plugin's `version` (in `<plugin>/.claude-plugin/plugin.json`) is the
**cache key** Claude Code uses to decide whether an update is available. If the version
is not bumped when plugin content changes, `/plugin update` and auto-update skip the
plugin and users never receive the change. Bumping by hand on every push is error-prone.

Goal: automate per-plugin SemVer bumps from Conventional Commits, on push to `main`.

## Decisions (from brainstorming)

1. **Trigger:** GitHub Action on push to `main` (immediate, commit-back model — not a release PR).
2. **Bump driver:** Conventional Commits — `feat`→minor, `fix`/`perf`→patch, `!` or
   `BREAKING CHANGE`→major, all other types → no release.
3. **Outputs:** version bump committed back to `main`, git tag, and a GitHub Release with
   auto-generated notes. **No** `CHANGELOG.md`.
4. **Scope:** per-plugin from day one — only commits touching a plugin's `source` dir bump
   that plugin; tags are namespaced `<name>-vX.Y.Z`.
5. **0.x rule:** standard SemVer — `BREAKING CHANGE` bumps major (`0.1.0`→`1.0.0`).
6. **Approach:** self-contained, no third-party release actions; the plugin set is read
   from `.claude-plugin/marketplace.json` so adding a plugin needs no workflow change.

## Components

### 1. `scripts/release/version.py` — pure logic (unit-tested)
No I/O. Two functions:
- `classify(message: str) -> "major" | "minor" | "patch" | None` — parse one commit's
  subject + body per Conventional Commits. Recognizes `type(scope)!: …`, a `BREAKING
  CHANGE:` / `BREAKING-CHANGE:` footer, `feat`, `fix`, `perf`; returns `None` for
  non-releasing types (`docs`, `chore`, `test`, `ci`, `refactor`, `style`, build, etc.)
  and for unparseable messages.
- `next_version(current: str, messages: list[str]) -> tuple[str | None, str | None]` —
  highest-precedence bump wins (major > minor > patch); returns `(None, None)` when no
  message triggers a release (→ caller skips that plugin).

### 2. `scripts/release/release.py` — orchestration (thin)
- Read `.claude-plugin/marketplace.json`; for each entry derive `name` and `source` dir.
- Find the latest `<name>-v*` tag (`git tag --list`, sorted by SemVer).
  - **Bootstrap (no such tag):** do not bump; emit a baseline action that tags the
    current `plugin.json` version (e.g. `epic-v0.1.0`) so later commits bump from a known
    point. The non-conventional "Initial commit" never triggers a phantom release.
- Collect commit messages since that tag that touched `source/**`
  (`git log <tag>..HEAD -- <source>`).
- Call `next_version`; if bumped, write the new `version` into `<source>/.claude-plugin/plugin.json`
  (JSON read/modify/write, preserving formatting as far as practical).
- Emit machine-readable results (name, old→new version, bump type, release-note body) for
  the workflow to consume (e.g. JSON on stdout / `$GITHUB_OUTPUT`).

### 3. `.github/workflows/release.yml`
- `on: push: branches: [main]`.
- `permissions: contents: write` (push bump + tag, create Release).
- `concurrency: group: release, cancel-in-progress: false` (serialize runs).
- Steps: checkout `fetch-depth: 0` (full history + tags) → set up Python →
  run `release.py` → for each bumped/bootstrapped plugin: `git commit -m
  "chore(release): <name> vX.Y.Z [skip ci]"`, `git tag <name>-vX.Y.Z`, push branch+tags,
  `gh release create <name>-vX.Y.Z --generate-notes` (or notes from `release.py`).

## Loop / safety
- Release commit message contains `[skip ci]` → GitHub skips a re-triggered run.
- Pushes made with the default `GITHUB_TOKEN` do not trigger new workflow runs anyway
  (belt and suspenders).
- `concurrency` prevents overlapping releases racing on tags.

## Data flow
```
push to main
  → checkout (full history + tags)
  → release.py reads marketplace.json
      for each plugin:
        last <name>-v* tag → commits since touching source/** → version.next_version()
        bump? write plugin.json : skip (or bootstrap-tag if no tag yet)
  → for each released plugin:
        commit "[skip ci]" + tag <name>-vX.Y.Z + push + gh release create
```

## Testing
- **`tests/test_version.py` (pytest), TDD first:** table of cases — `feat:`→minor,
  `fix:`/`perf:`→patch, `feat!:`/`fix!:`→major, `BREAKING CHANGE:` footer→major,
  `docs:`/`chore:`/`refactor:`/unparseable→None, multi-commit highest-wins, and
  `next_version` arithmetic across each bump type from a `0.x` and a `1.x` base.
- **`actionlint`** to validate `release.yml`.
- Orchestration (`release.py`) is kept thin enough to rely on the tested core; no git
  integration test required for v1.

## Out of scope (YAGNI)
- CHANGELOG.md, release-PR review gate, pre-release/RC channels, signed tags,
  monorepo cross-plugin dependency bumps.

## Files added
- `scripts/release/version.py`, `scripts/release/release.py`
- `tests/test_version.py`
- `.github/workflows/release.yml`
- `## Releasing` section in `epic/README.md` documenting the conventional-commit → auto-release flow
