# Kimi Code Native Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repo-root Kimi plugin manifest (`.kimi-plugin/plugin.json`) so `kimi`'s `/plugins install <repo>` resolves the epic plugin, with release version-lockstep, lint, and README docs.

**Architecture:** A root-level `.kimi-plugin/plugin.json` points `skills` at `./epic/skills/` (paths resolve relative to the repo root in Kimi). The release script and workflow gain the Kimi manifest as a third version-locked manifest (skip-if-absent). Lint mirrors the existing Codex-manifest tests; the README's Kimi section gains the native-install route.

**Tech Stack:** JSON manifest, Python 3 (`scripts/release/release.py`, pytest), GitHub Actions YAML, Markdown.

## Global Constraints

- Kimi manifest lives at the **repo root**: `.kimi-plugin/plugin.json` — NOT under `epic/` (Kimi's GitHub resolver only accepts `owner/repo`; the plugin root is the repo root).
- Manifest `name` is `epic`; `skills` is `./epic/skills/` (repo-root-relative, `./`-prefixed); `description` is the shared one-liner `Unified GitHub epic driver: drive epics via sub-issues + ProjectV2, brainstorm new epics into existence, migrate legacy task-list epics`.
- `version` starts at `0.13.0` (current Claude + Codex manifest version) and stays version-locked to them.
- Toolchain: from the worktree root, `python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt` once, then `.venv/bin/pytest tests/ -q`.
- No AI-attribution trailers in any commit (`Co-Authored-By`/`Generated with` forbidden — global CLAUDE.md).
- README edits must keep the forbidden-literal lint green: no `gangan`, no `tom-plugins`, no node-ID shapes, no bare `getvoicify` (only the `getvoicify/claude-plugins` slug is allowed).
- Conventional Commits drive releases: Task 1 uses a `feat:` subject (adds the manifest = new capability → cuts a release that ships the manifest in a tag); Tasks 2–3 use `chore:`/`test:`/`docs:` as noted.

---

### Task 1: Kimi manifest + lint

**Files:**
- Create: `.kimi-plugin/plugin.json`
- Modify: `tests/test_skills_lint.py` (add Kimi-manifest constants + tests; rename the generic skills-field helper)

**Interfaces:**
- Consumes: existing `tests/test_skills_lint.py` helpers `REPO_ROOT`, `EPIC_DIR`, `CLAUDE_MANIFEST_PATH`, `load_json`, `check_version_lockstep`, `SKILL_NAMES`, and the existing `check_codex_skills_field(manifest_path, base_dir)`.
- Produces: `KIMI_MANIFEST_PATH` constant and a renamed generic `check_skills_field(manifest_path, base_dir)` used by both Codex and Kimi skills-field tests.

- [ ] **Step 1: Write the failing tests.** In `tests/test_skills_lint.py`, add the constant next to `CODEX_MANIFEST_PATH` (~line 215):

```python
KIMI_MANIFEST_PATH = REPO_ROOT / ".kimi-plugin" / "plugin.json"
```

Rename the existing `check_codex_skills_field` to `check_skills_field` (it is already base-agnostic — it resolves `skills` against the passed base dir), and update its two existing call sites (`test_codex_manifest_skills_field_present_and_resolves` and `test_codex_skills_path_escaping_epic_dir_is_rejected`) to call `check_skills_field`. Then add the Kimi tests after the Codex skills tests (~line 328):

```python
def test_kimi_manifest_name_is_epic():
    assert load_json(KIMI_MANIFEST_PATH).get("name") == "epic", (
        ".kimi-plugin/plugin.json `name` must be 'epic'"
    )


def test_kimi_manifest_version_lockstep_with_claude_manifest():
    check_version_lockstep(KIMI_MANIFEST_PATH, CLAUDE_MANIFEST_PATH)


def test_kimi_manifest_description_non_empty():
    description = load_json(KIMI_MANIFEST_PATH).get("description")
    assert isinstance(description, str) and description.strip(), (
        ".kimi-plugin/plugin.json `description` must be a non-empty string"
    )


def test_kimi_manifest_skills_field_present_and_resolves():
    # Kimi resolves `skills` relative to the plugin root = the REPO ROOT (not
    # the manifest's own dir), so `./epic/skills/` must resolve to epic/skills/
    # and contain the three skill dirs.
    check_skills_field(KIMI_MANIFEST_PATH, REPO_ROOT)
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `.venv/bin/pytest tests/test_skills_lint.py -q -k kimi`
Expected: FAIL — `missing .kimi-plugin/plugin.json` (manifest absent). Also run the full file once to confirm the helper rename didn't break the Codex callers: `.venv/bin/pytest tests/test_skills_lint.py -q -k "codex or kimi"` — Codex skills tests PASS, Kimi tests FAIL.

- [ ] **Step 3: Create the manifest.** Write `.kimi-plugin/plugin.json` at the repo root:

```json
{
  "name": "epic",
  "version": "0.13.0",
  "description": "Unified GitHub epic driver: drive epics via sub-issues + ProjectV2, brainstorm new epics into existence, migrate legacy task-list epics",
  "skills": "./epic/skills/"
}
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS (all Kimi tests green; Codex + every pre-existing test still green).

- [ ] **Step 5: Commit.**

```bash
git add .kimi-plugin/plugin.json tests/test_skills_lint.py
git commit -m "feat: add Kimi Code plugin manifest at repo root"
```

---

### Task 2: Release version-lockstep for the Kimi manifest

**Files:**
- Modify: `scripts/release/release.py` (add `_kimi_plugin_json` + patch it in `write_version`)
- Modify: `.github/workflows/release.yml` (stage the Kimi manifest, skip-if-absent)
- Modify: `tests/test_release.py` (add write-Kimi + skip-absent-Kimi tests)
- Modify: `tests/test_skills_lint.py` (extend the release-workflow git-add lint to require the Kimi manifest)

**Interfaces:**
- Consumes: `release.write_version(repo_root, source, new_version)`, `release._patch_version`, and the `_write_manifest(path, data)` helper in `tests/test_release.py`.
- Produces: `release._kimi_plugin_json(repo_root)` returning `Path(repo_root)/".kimi-plugin"/"plugin.json"` (repo-root path — NOT `source`-relative, unlike `_plugin_json`/`_codex_plugin_json`).

- [ ] **Step 1: Write the failing release tests.** In `tests/test_release.py`, after `test_write_version_writes_both_manifests`, add:

```python
def test_write_version_writes_kimi_manifest_at_repo_root(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    kimi_pj = tmp_path / ".kimi-plugin" / "plugin.json"  # REPO ROOT, not under epic/
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})
    _write_manifest(
        kimi_pj,
        {"name": "epic", "version": "0.1.0", "skills": "./epic/skills/"},
    )

    release.write_version(tmp_path, "./epic", "0.2.0")

    data = json.loads(kimi_pj.read_text())
    assert data["version"] == "0.2.0"
    assert data["skills"] == "./epic/skills/"  # extras preserved
    assert kimi_pj.read_text().endswith("\n")


def test_write_version_skips_absent_kimi_manifest(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})

    release.write_version(tmp_path, "./epic", "0.2.0")

    assert json.loads(claude_pj.read_text())["version"] == "0.2.0"
    assert not (tmp_path / ".kimi-plugin").exists()
```

Also extend the workflow lint in `tests/test_skills_lint.py`: rename `test_release_workflow_git_add_stages_both_manifests` to `test_release_workflow_git_add_stages_all_manifests` and add `.kimi-plugin/plugin.json` to the checked tuple:

```python
    for manifest in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".kimi-plugin/plugin.json",
    ):
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `.venv/bin/pytest tests/test_release.py -q -k kimi` then `.venv/bin/pytest tests/test_skills_lint.py -q -k stages_all_manifests`
Expected: FAIL — the write-Kimi test fails (`write_version` doesn't touch the root Kimi manifest yet); the workflow lint fails (release.yml doesn't `git add .kimi-plugin/plugin.json`). The skip-absent test may already pass (counterfactual guard) — that's fine.

- [ ] **Step 3: Extend `release.py`.** Add the helper near `_codex_plugin_json` (~line 21):

```python
def _kimi_plugin_json(repo_root):
    return Path(repo_root) / ".kimi-plugin" / "plugin.json"
```

Update `write_version` to patch the Kimi manifest (skip-if-absent), keeping the Claude patch last:

```python
def write_version(repo_root, source, new_version):
    codex_path = _codex_plugin_json(repo_root, source)
    if codex_path.is_file():
        _patch_version(codex_path, new_version)
    kimi_path = _kimi_plugin_json(repo_root)
    if kimi_path.is_file():
        _patch_version(kimi_path, new_version)
    _patch_version(_plugin_json(repo_root, source), new_version)
```

- [ ] **Step 4: Stage the Kimi manifest in `release.yml`.** Read `.github/workflows/release.yml` around the existing `git add` block (~lines 51–54). After the Codex `fi`, add a guarded stage for the repo-root Kimi manifest, matching the surrounding indentation:

```yaml
              if [ -f ".kimi-plugin/plugin.json" ]; then
                git add ".kimi-plugin/plugin.json"
              fi
```

(Note: the Kimi manifest path is `.kimi-plugin/plugin.json` at the repo root — NOT `${src#./}/.kimi-plugin/...` like the Claude/Codex manifests under `epic/`.)

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — the write-Kimi, skip-absent-Kimi, and `stages_all_manifests` tests green; all pre-existing release + lockstep tests green. Sanity-check the release script imports cleanly: `.venv/bin/python -c "import sys; sys.path.insert(0, 'scripts/release'); import release; print('ok')"` → `ok`.

- [ ] **Step 6: Commit.**

```bash
git add scripts/release/release.py .github/workflows/release.yml tests/test_release.py tests/test_skills_lint.py
git commit -m "chore: version-lockstep the Kimi manifest in the release flow"
```

---

### Task 3: README native-install docs

**Files:**
- Modify: `epic/README.md` (add the native `/plugins install` route to the `### Kimi Code` section)
- Modify: `tests/test_skills_lint.py` (add a Kimi-section route-presence lint)

**Interfaces:**
- Consumes: the existing `### Kimi Code` subsection and `extract_h2_section`/section helpers already used by the README lints.
- Produces: `test_kimi_section_documents_native_install` asserting the native route is documented.

- [ ] **Step 1: Write the failing lint.** In `tests/test_skills_lint.py`, near the other README-section tests, add:

```python
def test_kimi_section_documents_native_install():
    text = read_epic_readme()
    kimi_section = extract_subsection(text, "### Kimi Code")
    normalized = " ".join(kimi_section.lower().split())
    assert "/plugins install" in normalized, (
        "the Kimi Code README section must document the native "
        "`/plugins install` route"
    )
```

If a `read_epic_readme()` / `extract_subsection(text, heading)` helper is not already present for `###`-level extraction, reuse whatever the sibling README-section tests use to isolate the `### Kimi Code` block (grep the test file for how the five agent subsections are extracted) rather than introducing a new mechanism.

- [ ] **Step 2: Run the lint to verify it fails.**

Run: `.venv/bin/pytest tests/test_skills_lint.py -q -k kimi_section_documents_native_install`
Expected: FAIL — `/plugins install` not yet in the Kimi section.

- [ ] **Step 3: Add the native-install docs.** In `epic/README.md`'s `### Kimi Code` section, ADD (do not remove the existing skills-dir copy instructions) a native-install subsection. Insert after the existing skills-dir copy paragraph, before the next `###`:

```markdown
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
```

- [ ] **Step 4: Run the full suite to verify it passes.**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS — the new route lint green; the forbidden-literal README lint still green (the added text contains only the allowed `getvoicify/claude-plugins` slug). Confirm: `grep -n "getvoicify" epic/README.md | grep -v "getvoicify/claude-plugins"` → no output; `grep -ni -e gangan -e tom-plugins epic/README.md` → no output.

- [ ] **Step 5: Commit.**

```bash
git add epic/README.md tests/test_skills_lint.py
git commit -m "docs: document Kimi native /plugins install route"
```

---

## Self-Review

**Spec coverage:** D1 manifest → Task 1; D2 release lockstep → Task 2; D3 lint → Tasks 1 (manifest lints) + 2 (workflow git-add lint); D4 docs → Task 3. Success criteria 1–3 covered by Tasks 1–3; criterion 4 (live `/plugins install` verification) is a post-merge/post-release smoke — see Verification note below (it depends on the release tag this PR cuts, so it is validated after merge, or documented as pending).

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The one soft reference — Task 3 Step 1's "reuse whatever the sibling README-section tests use to isolate the `### Kimi Code` block" — is deliberate: the exact `###`-extraction helper name must match the existing test file, so the implementer greps for it rather than risk inventing a divergent one. All other code is literal.

**Type consistency:** `check_skills_field(manifest_path, base_dir)` (renamed from `check_codex_skills_field`) — same two-arg signature, called for Codex (`EPIC_DIR` base) and Kimi (`REPO_ROOT` base). `_kimi_plugin_json(repo_root)` takes only `repo_root` (root-relative), distinct from `_plugin_json(repo_root, source)`/`_codex_plugin_json(repo_root, source)` — intentional, matching the manifest's root location. `KIMI_MANIFEST_PATH`, `check_version_lockstep`, `load_json`, `SKILL_NAMES` reused as defined.

## Verification note (post-merge smoke)

Because Kimi installs the latest **release tag**, native `/plugins install <bare-url>` can only be verified once this PR's `feat` merge cuts a release whose tag includes `.kimi-plugin/plugin.json`. Two verification paths: (a) before merge, `/plugins install https://github.com/getvoicify/claude-plugins/tree/main` installs HEAD from the branch/main and should register the three skills; (b) after the release ships, the bare-URL route works. If the drive machine can reach Kimi's TUI, run (a) against this branch and record the result; otherwise note native verification as pending an operator with a Kimi install (the manifest + lint prove the schema-correctness statically).
