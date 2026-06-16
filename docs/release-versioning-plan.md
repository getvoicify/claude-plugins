# Semantic-versioning release workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-bump each plugin's SemVer version from Conventional Commits on push to `main`, then tag and cut a GitHub Release.

**Architecture:** A pure, unit-tested Python core (`version.py`) maps commit messages to a bump. A thin orchestrator (`release.py`) reads the plugin set from `.claude-plugin/marketplace.json`, finds each plugin's last `<name>-v*` tag, and writes the bumped version into its `plugin.json`. A GitHub Actions workflow runs the orchestrator and performs the commit-back, tag, and Release per bumped plugin.

**Tech Stack:** Python 3 (stdlib only), pytest (dev/test), GitHub Actions, `gh` CLI, `jq` (runner-preinstalled), `actionlint`.

---

## File Structure

- Create: `scripts/release/version.py` — pure commit→bump logic. No I/O.
- Create: `scripts/release/release.py` — orchestration: marketplace parsing, plugin.json read/write, git tag/commit discovery, emits results JSON.
- Create: `tests/conftest.py` — puts `scripts/release` on `sys.path` for imports.
- Create: `tests/test_version.py` — unit tests for `version.py`.
- Create: `tests/test_release.py` — unit tests for `release.py` pure helpers.
- Create: `.github/workflows/release.yml` — the release workflow.
- Modify: `epic/README.md` — add a `## Releasing` section.

Conventional Commits used throughout this plan's commits so the workflow (once live) versions itself correctly.

---

## Task 1: Test scaffolding + `classify()`

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_version.py`
- Create: `scripts/release/version.py`

- [ ] **Step 1: Write `tests/conftest.py`**

```python
import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "release")
)
```

- [ ] **Step 2: Write the failing test** in `tests/test_version.py`

```python
import pytest

from version import classify


@pytest.mark.parametrize(
    "message, expected",
    [
        ("feat: add copilot gate", "minor"),
        ("feat(epic): add copilot gate", "minor"),
        ("fix: disarm --auto on park", "patch"),
        ("perf: faster gate scan", "patch"),
        ("feat!: drop legacy config", "major"),
        ("fix(api)!: rename field", "major"),
        ("feat: x\n\nBREAKING CHANGE: removes y", "major"),
        ("feat: x\n\nBREAKING-CHANGE: removes y", "major"),
        ("docs: tweak readme", None),
        ("chore: bump dep", None),
        ("refactor: tidy", None),
        ("style: format", None),
        ("ci: adjust", None),
        ("not a conventional commit", None),
        ("", None),
    ],
)
def test_classify(message, expected):
    assert classify(message) == expected
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'version'` (or `ImportError: cannot import name 'classify'`).

- [ ] **Step 4: Write minimal implementation** in `scripts/release/version.py`

```python
"""Pure Conventional-Commits → SemVer-bump logic. No I/O."""
import re

_HEADER = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\([^)]*\))?(?P<bang>!)?:\s?(?P<desc>.*)$"
)
_BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)

_MINOR_TYPES = {"feat"}
_PATCH_TYPES = {"fix", "perf"}


def classify(message):
    """Return 'major' | 'minor' | 'patch' | None for one commit message."""
    if not message:
        return None
    header = message.splitlines()[0]
    m = _HEADER.match(header)
    if not m:
        return None
    if m.group("bang") or _BREAKING_FOOTER.search(message):
        return "major"
    commit_type = m.group("type").lower()
    if commit_type in _MINOR_TYPES:
        return "minor"
    if commit_type in _PATCH_TYPES:
        return "patch"
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_version.py -v`
Expected: PASS (15 cases).

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_version.py scripts/release/version.py
git commit -m "feat: add conventional-commit classifier for release versioning"
```

---

## Task 2: `next_version()`

**Files:**
- Modify: `tests/test_version.py`
- Modify: `scripts/release/version.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_version.py`

```python
from version import next_version


@pytest.mark.parametrize(
    "current, messages, expected",
    [
        ("0.1.0", ["feat: x"], ("0.2.0", "minor")),
        ("0.1.0", ["fix: x"], ("0.1.1", "patch")),
        ("0.1.0", ["perf: x"], ("0.1.1", "patch")),
        ("0.1.0", ["feat!: x"], ("1.0.0", "major")),
        ("1.2.3", ["fix: a", "feat: b"], ("1.3.0", "minor")),
        ("1.0.0", ["fix: a", "feat!: b"], ("2.0.0", "major")),
        ("1.2.3", ["docs: a", "chore: b"], (None, None)),
        ("0.1.0", [], (None, None)),
    ],
)
def test_next_version(current, messages, expected):
    assert next_version(current, messages) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_version.py::test_next_version -v`
Expected: FAIL — `ImportError: cannot import name 'next_version'`.

- [ ] **Step 3: Write minimal implementation** — append to `scripts/release/version.py`

```python
_PRECEDENCE = {"major": 3, "minor": 2, "patch": 1}


def next_version(current, messages):
    """Return (new_version, bump) or (None, None) if no release is warranted."""
    bumps = [b for b in (classify(m) for m in messages) if b]
    if not bumps:
        return (None, None)
    bump = max(bumps, key=_PRECEDENCE.__getitem__)
    major, minor, patch = (int(p) for p in current.split("."))
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return (f"{major}.{minor}.{patch}", bump)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_version.py -v`
Expected: PASS (all cases, Task 1 + Task 2).

- [ ] **Step 5: Commit**

```bash
git add tests/test_version.py scripts/release/version.py
git commit -m "feat: compute next semver from a set of commit messages"
```

---

## Task 3: `release.py` pure helpers (marketplace + plugin.json I/O)

**Files:**
- Create: `tests/test_release.py`
- Create: `scripts/release/release.py`

- [ ] **Step 1: Write the failing test** in `tests/test_release.py`

```python
import json

import release


def test_load_plugins(tmp_path):
    manifest = tmp_path / "marketplace.json"
    manifest.write_text(
        json.dumps(
            {"name": "tom-plugins", "plugins": [{"name": "epic", "source": "./epic"}]}
        )
    )
    assert release.load_plugins(manifest) == [("epic", "./epic")]


def test_write_then_read_version_roundtrip(tmp_path):
    pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    pj.parent.mkdir(parents=True)
    pj.write_text(json.dumps({"name": "epic", "version": "0.1.0"}, indent=2) + "\n")

    release.write_version(tmp_path, "./epic", "0.2.0")

    assert release.read_version(tmp_path, "./epic") == "0.2.0"
    # value updated and trailing newline preserved
    assert json.loads(pj.read_text())["version"] == "0.2.0"
    assert pj.read_text().endswith("\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_release.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'release'`.

- [ ] **Step 3: Write minimal implementation** in `scripts/release/release.py`

```python
"""Orchestrates per-plugin SemVer releases. Run from the repo root."""
import json
import subprocess
import sys
from pathlib import Path

from version import next_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_plugins(marketplace_path):
    data = json.loads(Path(marketplace_path).read_text())
    return [(p["name"], p["source"]) for p in data["plugins"]]


def _plugin_json(repo_root, source):
    return Path(repo_root) / source / ".claude-plugin" / "plugin.json"


def read_version(repo_root, source):
    return json.loads(_plugin_json(repo_root, source).read_text())["version"]


def write_version(repo_root, source, new_version):
    path = _plugin_json(repo_root, source)
    data = json.loads(path.read_text())
    data["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_release.py -v`
Expected: PASS (2 cases).

- [ ] **Step 5: Commit**

```bash
git add tests/test_release.py scripts/release/release.py
git commit -m "feat: add marketplace parsing and plugin.json version I/O"
```

---

## Task 4: `release.py` git discovery + orchestration `main()`

**Files:**
- Modify: `scripts/release/release.py`

No unit test (git-coupled; the bump arithmetic it relies on is already covered by Task 2). Verified manually via the workflow dry-run in Task 5.

- [ ] **Step 1: Append git helpers + orchestration** to `scripts/release/release.py`

```python
def _git(*args):
    return subprocess.check_output(["git", *args], text=True)


def latest_tag(name):
    """Latest `<name>-vX.Y.Z` tag by SemVer order, or None."""
    prefix = f"{name}-v"
    tags = [t for t in _git("tag", "--list", f"{prefix}*").splitlines() if t]
    if not tags:
        return None

    def key(tag):
        return tuple(int(p) for p in tag[len(prefix):].split("."))

    return max(tags, key=key)


def commits_since(tag, source):
    """Full messages of commits since `tag` (or all) touching `source`."""
    path = source.lstrip("./")
    rng = f"{tag}..HEAD" if tag else "HEAD"
    out = _git("log", rng, "--format=%B%x00", "--", path)
    return [chunk.strip() for chunk in out.split("\x00") if chunk.strip()]


def compute(repo_root=REPO_ROOT, marketplace_path=None):
    """Bump plugin.json files as needed; return list of release actions."""
    marketplace_path = marketplace_path or (Path(repo_root) / ".claude-plugin" / "marketplace.json")
    results = []
    for name, source in load_plugins(marketplace_path):
        tag = latest_tag(name)
        current = read_version(repo_root, source)
        if tag is None:
            # Bootstrap: seed a baseline tag at the current version, no bump.
            results.append({"name": name, "source": source, "version": current, "bump": "bootstrap"})
            continue
        new_version, bump = next_version(current, commits_since(tag, source))
        if new_version is None:
            continue
        write_version(repo_root, source, new_version)
        results.append({"name": name, "source": source, "version": new_version, "bump": bump})
    return results


if __name__ == "__main__":
    json.dump(compute(), sys.stdout)
    sys.stdout.write("\n")
```

- [ ] **Step 2: Smoke-test the parsing path locally (no tags yet → bootstrap)**

Run: `python scripts/release/release.py`
Expected: prints `[{"name": "epic", "source": "./epic", "version": "0.1.0", "bump": "bootstrap"}]` and leaves `epic/.claude-plugin/plugin.json` unchanged (`git status --short` shows nothing).

- [ ] **Step 3: Re-run the unit suite (no regressions)**

Run: `python -m pytest tests/ -v`
Expected: PASS (all Task 1–3 cases still green).

- [ ] **Step 4: Commit**

```bash
git add scripts/release/release.py
git commit -m "feat: add git tag discovery and per-plugin release orchestration"
```

---

## Task 5: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: release

on:
  push:
    branches: [main]

permissions:
  contents: write

concurrency:
  group: release
  cancel-in-progress: false

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.x"

      - name: Compute version bumps
        id: compute
        run: |
          results="$(python scripts/release/release.py)"
          echo "results=$results" >> "$GITHUB_OUTPUT"
          echo "$results" | python -m json.tool

      - name: Commit, tag, and release
        env:
          GH_TOKEN: ${{ github.token }}
          RESULTS: ${{ steps.compute.outputs.results }}
        run: |
          set -euo pipefail
          if [ "$(echo "$RESULTS" | jq 'length')" -eq 0 ]; then
            echo "No releases."; exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          echo "$RESULTS" | jq -c '.[]' | while read -r row; do
            name=$(echo "$row" | jq -r .name)
            ver=$(echo "$row" | jq -r .version)
            bump=$(echo "$row" | jq -r .bump)
            src=$(echo "$row" | jq -r .source)
            tag="${name}-v${ver}"
            if [ "$bump" != "bootstrap" ]; then
              git add "${src#./}/.claude-plugin/plugin.json"
              git commit -m "chore(release): ${name} v${ver} [skip ci]"
            fi
            git tag -a "$tag" -m "${name} v${ver}"
          done
          git push origin HEAD --follow-tags
          echo "$RESULTS" | jq -c '.[]' | while read -r row; do
            name=$(echo "$row" | jq -r .name)
            ver=$(echo "$row" | jq -r .version)
            tag="${name}-v${ver}"
            gh release create "$tag" --title "${name} v${ver}" --generate-notes
          done
```

- [ ] **Step 2: Lint the workflow**

Run: `actionlint .github/workflows/release.yml`
(If `actionlint` is not installed: `brew install actionlint`.)
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add per-plugin semantic-version release workflow"
```

---

## Task 6: Document the release flow

**Files:**
- Modify: `epic/README.md`

- [ ] **Step 1: Append a `## Releasing` section** to `epic/README.md`

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add epic/README.md
git commit -m "docs: document the automated release/versioning flow"
```

---

## Self-Review

**Spec coverage:** trigger (Task 5) ✓; conventional-commit bump rules (Tasks 1–2) ✓;
per-plugin scope via marketplace.json + namespaced tags (Tasks 4–5) ✓; bump+tag+Release
outputs, no CHANGELOG (Task 5) ✓; bootstrap baseline (Task 4) ✓; loop safety `[skip ci]`
+ `GITHUB_TOKEN` + concurrency (Task 5) ✓; standard 0.x→major rule (Task 2) ✓;
self-contained / no third-party release actions (Task 5) ✓; tests pytest + actionlint
(Tasks 1–5) ✓; README docs (Task 6) ✓.

**Placeholder scan:** none — every code/command step is concrete.

**Type/name consistency:** `classify`, `next_version`, `load_plugins`, `read_version`,
`write_version`, `latest_tag`, `commits_since`, `compute` are used identically across
tasks; result keys `name`/`source`/`version`/`bump` match between `release.py` and the
workflow's `jq` reads; tag format `<name>-vX.Y.Z` is consistent in `latest_tag`,
`compute`, and the workflow.
