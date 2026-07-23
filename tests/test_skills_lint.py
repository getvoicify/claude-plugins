# Task 1: skills tree structure lint.
# Tasks 2-6 append further sections below under their own "# Task N" comments.

import json
import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "epic" / "skills"

SKILL_NAMES = ["epic", "create", "migrate"]

# Each skill's SKILL.md must contain its literal reference-link string, and
# that literal path must resolve relative to the SKILL.md's own directory.
REFERENCE_LINKS = {
    "epic": "references/github-graphql.md",
    "create": "../epic/references/github-graphql.md",
    "migrate": "../epic/references/github-graphql.md",
}


def skill_md_path(name):
    return SKILLS_ROOT / name / "SKILL.md"


def parse_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: SKILL.md must open with YAML frontmatter"
    end = text.find("\n---\n", 4)
    assert end != -1, f"{path}: SKILL.md missing closing frontmatter delimiter (---)"
    frontmatter = yaml.safe_load(text[4:end])
    assert isinstance(frontmatter, dict), f"{path}: frontmatter must be a YAML mapping"
    return frontmatter


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_dir_exists(name):
    assert (SKILLS_ROOT / name).is_dir(), f"missing skill dir epic/skills/{name}"
    assert skill_md_path(name).is_file(), f"missing epic/skills/{name}/SKILL.md"


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_frontmatter_name_matches_dir(name):
    frontmatter = parse_frontmatter(skill_md_path(name))
    assert frontmatter.get("name") == name, (
        f"epic/skills/{name}/SKILL.md frontmatter `name` must equal its dir name"
    )


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_frontmatter_description_non_empty(name):
    frontmatter = parse_frontmatter(skill_md_path(name))
    description = frontmatter.get("description")
    assert isinstance(description, str) and description.strip(), (
        f"epic/skills/{name}/SKILL.md frontmatter `description` must be non-empty"
    )


def test_parse_frontmatter_missing_closing_delimiter_fails_clearly(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: broken\ndescription: never closed\n", encoding="utf-8")
    with pytest.raises(AssertionError) as excinfo:
        parse_frontmatter(path)
    message = str(excinfo.value)
    assert str(path) in message, "failure must name the malformed file"
    assert "closing frontmatter delimiter" in message


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_reference_link_appears_and_resolves(name):
    link = REFERENCE_LINKS[name]
    path = skill_md_path(name)
    body = path.read_text(encoding="utf-8")
    assert link in body, f"{path} must contain the literal link `{link}`"
    target = (path.parent / link).resolve()
    assert target.is_file(), f"{path}: `{link}` does not resolve to a file"


# Task 2: guarded-capability rule (design §Guarded-capability rule).
#
# - `${CLAUDE_PLUGIN_ROOT}` is banned outright under epic/skills/ (any file).
# - Capability tokens — AskUserQuestion, ScheduleWakeup, subagent, spawn,
#   dispatch (case-insensitive, word-stem variants like Spawned / dispatched /
#   sub-agent) — may appear in a SKILL.md only inside a blank-line-delimited
#   paragraph that also contains a canonical guard marker ("if your harness"
#   or "if supported") AND an "otherwise" clause naming the fallback.
#   Applies to SKILL.md files only, not references/.

CAPABILITY_TOKEN_RE = re.compile(
    r"\b(?:askuserquestion|schedulewakeup|sub-?agent\w*|spawn\w*|dispatch\w*)",
    re.IGNORECASE,
)
GUARD_MARKERS = ("if your harness", "if supported")
# Whole word `otherwise` followed (after optional punctuation/space) by at
# least one word character — the clause must NAME its fallback.
OTHERWISE_CLAUSE_RE = re.compile(r"\botherwise\b\W*\w")


def split_paragraphs(text):
    return [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]


def test_claude_plugin_root_banned_under_skills():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted(SKILLS_ROOT.rglob("*"))
        if path.is_file()
        and "${CLAUDE_PLUGIN_ROOT}" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, (
        "`${CLAUDE_PLUGIN_ROOT}` is banned under epic/skills/ "
        f"(zero occurrences allowed); found in: {offenders}"
    )


def find_capability_violations(text):
    violations = []
    for para in split_paragraphs(text):
        # Hard-wrapped markdown may break a marker phrase across lines;
        # normalize intra-paragraph whitespace before matching.
        normalized = " ".join(para.lower().split())
        hits = CAPABILITY_TOKEN_RE.findall(normalized)
        if not hits:
            continue
        guarded = any(marker in normalized for marker in GUARD_MARKERS) and OTHERWISE_CLAUSE_RE.search(normalized)
        if not guarded:
            first_line = para.strip().splitlines()[0]
            violations.append(f"{sorted({h.lower() for h in hits})} in paragraph starting: {first_line!r}")
    return violations


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_capability_tokens_only_in_guarded_paragraphs(name):
    path = skill_md_path(name)
    violations = find_capability_violations(path.read_text(encoding="utf-8"))
    assert not violations, (
        f"{path}: capability tokens outside guarded paragraphs (need "
        f"'if your harness' or 'if supported' AND an 'otherwise' clause naming "
        f"the fallback):\n" + "\n".join(violations)
    )


def test_bare_trailing_otherwise_is_flagged_unguarded():
    # An `otherwise` clause must NAME the fallback; a paragraph that ends in a
    # bare "otherwise:" with no fallback content is not a guard.
    text = "If your harness supports subagents, dispatch one; otherwise:"
    assert find_capability_violations(text), (
        "a bare trailing 'otherwise:' names no fallback and must be flagged"
    )


def test_otherwise_embedded_in_larger_word_is_flagged_unguarded():
    # "otherwise" must match on word boundaries, not as a substring.
    text = "If your harness supports subagents, spawn one and proceed otherwisely."
    assert find_capability_violations(text), (
        "'otherwise' inside a larger word is not an otherwise clause"
    )


def test_otherwise_naming_fallback_is_guarded():
    text = (
        "If your harness supports subagents, dispatch one; otherwise, ask "
        "numbered questions in chat and wait for the reply."
    )
    assert find_capability_violations(text) == []


# Task 3: neutral working-repo config path (design §Decisions, runbook §Task 3).
#
# Each SKILL.md that mentions `epic.yaml` must contain the canonical
# lookup-order sentence naming `.agents/epic.yaml` primary and
# `.claude/epic.yaml` fallback. Presence of the sentence only — error-message
# literals and examples may name the primary path alone. SKILL.md scope only
# (README excluded).

CONFIG_LOOKUP_SENTENCE = "check `.agents/epic.yaml` first, then `.claude/epic.yaml`"


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_config_lookup_order_sentence_present(name):
    path = skill_md_path(name)
    text = path.read_text(encoding="utf-8")
    if "epic.yaml" not in text:
        pytest.skip(f"{path} does not mention epic.yaml")
    # The sentence hard-wraps in prose; normalize whitespace the same way
    # find_capability_violations does before the substring match.
    normalized = " ".join(text.lower().split())
    assert CONFIG_LOOKUP_SENTENCE in normalized, (
        f"{path} mentions epic.yaml but lacks the canonical lookup-order "
        f"sentence: {CONFIG_LOOKUP_SENTENCE!r}"
    )


# Task 4: Codex plugin manifest + repo catalog (design §Per-agent packaging,
# runbook §Task 4; schema pinned from live Codex docs — issue #13 step-1 pin,
# fetched 2026-07-22).
#
# - `epic/.codex-plugin/plugin.json`: name "epic", version in lockstep with
#   the Claude manifest (read dynamically — never hardcoded), non-empty
#   description, and a `skills` field that must be PRESENT (omission would
#   be a vacuous pass), `./`-prefixed, and resolve (relative to `epic/`) to
#   a directory containing all three skill dirs.
# - Root `.agents/plugins/marketplace.json`: Codex repo catalog. Its top-level
#   `name` is pinned to "epic-plugins" alongside the Claude catalog
#   (`.claude-plugin/marketplace.json`) — both must match MARKETPLACE_NAME and
#   each other (see test_marketplace_catalog_names_are_epic_plugins). It must
#   also carry at least one plugins[] entry with source.source == "local" and a
#   `./`-prefixed path resolving to `epic/`.

EPIC_DIR = REPO_ROOT / "epic"
CLAUDE_MANIFEST_PATH = EPIC_DIR / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST_PATH = EPIC_DIR / ".codex-plugin" / "plugin.json"
CODEX_CATALOG_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
CLAUDE_CATALOG_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# The marketplace/catalog name is the same across BOTH repo catalogs (Claude's
# `.claude-plugin/marketplace.json` and Codex's `.agents/plugins/marketplace.json`).
# Locked at materialization approval (spec D10) — do not re-derive.
MARKETPLACE_NAME = "epic-plugins"


def load_json(path):
    assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{path.relative_to(REPO_ROOT)}: invalid JSON: {exc}")


def test_codex_manifest_name_is_epic():
    manifest = load_json(CODEX_MANIFEST_PATH)
    assert manifest.get("name") == "epic", (
        "epic/.codex-plugin/plugin.json `name` must be 'epic'"
    )


def check_version_lockstep(codex_manifest_path, claude_manifest_path):
    codex_version = load_json(codex_manifest_path).get("version")
    claude_version = load_json(claude_manifest_path).get("version")
    # Guard against a vacuous None == None pass when both manifests omit
    # `version` — each must be a non-empty string BEFORE comparing.
    for manifest_path, version in (
        (codex_manifest_path, codex_version),
        (claude_manifest_path, claude_version),
    ):
        assert isinstance(version, str) and version.strip(), (
            f"{manifest_path} must declare a non-empty string `version`, "
            f"got {version!r}"
        )
    assert codex_version == claude_version, (
        f"{codex_manifest_path} version ({codex_version!r}) must equal "
        f"{claude_manifest_path} version ({claude_version!r})"
    )


def test_codex_manifest_version_lockstep_with_claude_manifest():
    check_version_lockstep(CODEX_MANIFEST_PATH, CLAUDE_MANIFEST_PATH)


def test_version_lockstep_rejects_versionless_manifests(tmp_path):
    # Both manifests omitting `version` must FAIL the lint — `.get("version")`
    # yielding None == None is a vacuous pass, not lockstep.
    codex_path = tmp_path / "codex-plugin.json"
    claude_path = tmp_path / "claude-plugin.json"
    codex_path.write_text(json.dumps({"name": "epic"}), encoding="utf-8")
    claude_path.write_text(json.dumps({"name": "epic"}), encoding="utf-8")
    with pytest.raises(AssertionError, match="version"):
        check_version_lockstep(codex_path, claude_path)


def test_codex_manifest_description_non_empty():
    description = load_json(CODEX_MANIFEST_PATH).get("description")
    assert isinstance(description, str) and description.strip(), (
        "epic/.codex-plugin/plugin.json `description` must be a non-empty string"
    )


def check_codex_skills_field(manifest_path, epic_dir):
    skills = load_json(manifest_path).get("skills")
    # PRESENT and a string — an omitted `skills` field must fail, not
    # vacuously pass (issue #13 pin, blocking defect 2).
    assert isinstance(skills, str), (
        f"{manifest_path} must declare a `skills` field (string path)"
    )
    assert skills.startswith("./"), (
        f"`skills` path must be `./`-prefixed, got {skills!r}"
    )
    # Resolve fully (symlinks included) and require containment — a
    # `./`-prefixed path like `./../somewhere` must not escape the plugin dir.
    epic_dir_resolved = epic_dir.resolve()
    skills_dir = (epic_dir_resolved / skills).resolve()
    assert skills_dir.is_relative_to(epic_dir_resolved), (
        f"`skills` path {skills!r} resolves to {skills_dir}, "
        f"which escapes the plugin dir {epic_dir_resolved}"
    )
    assert skills_dir.is_dir(), (
        f"`skills` path {skills!r} does not resolve to a directory under {epic_dir}"
    )
    for name in SKILL_NAMES:
        assert (skills_dir / name).is_dir(), (
            f"`skills` dir {skills!r} is missing the `{name}` skill dir"
        )


def test_codex_manifest_skills_field_present_and_resolves():
    check_codex_skills_field(CODEX_MANIFEST_PATH, EPIC_DIR)


def test_codex_skills_path_escaping_epic_dir_is_rejected(tmp_path):
    # A `./`-prefixed path may still escape the plugin dir (`./../somewhere`).
    # Build an escaping target that EXISTS and contains all three skill dirs,
    # so every current check would pass — the lint must still reject it.
    epic_dir = tmp_path / "epic"
    manifest_path = epic_dir / ".codex-plugin" / "plugin.json"
    manifest_path.parent.mkdir(parents=True)
    outside = tmp_path / "outside-skills"
    for name in SKILL_NAMES:
        (outside / name).mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"name": "epic", "skills": "./../outside-skills"}),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="skills"):
        check_codex_skills_field(manifest_path, epic_dir)


# Task 5: release lockstep across both plugin manifests (design §Versioning /
# release, runbook §Task 5).
#
# The release workflow's commit step must `git add` BOTH manifest paths.
# Parsed from the actual `git add` invocation line(s) — not bare string
# presence anywhere in the file, so a comment naming the Codex manifest
# cannot vacuously pass.

RELEASE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"
GIT_ADD_RE = re.compile(r"^[ \t]*git add[ \t]+(.+)$", re.MULTILINE)


def test_release_workflow_git_add_stages_both_manifests():
    text = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
    invocations = GIT_ADD_RE.findall(text)
    assert invocations, (
        f"{RELEASE_WORKFLOW_PATH.relative_to(REPO_ROOT)}: no `git add` "
        "invocation found in the release workflow"
    )
    staged = " ".join(invocations)
    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        assert manifest in staged, (
            f"{RELEASE_WORKFLOW_PATH.relative_to(REPO_ROOT)}: the `git add` "
            f"invocation(s) must stage {manifest}; found: {invocations}"
        )


def test_marketplace_catalog_names_are_epic_plugins():
    # BOTH repo catalogs — Claude's `.claude-plugin/marketplace.json` and
    # Codex's `.agents/plugins/marketplace.json` — must declare the same
    # top-level `name`, equal to the locked MARKETPLACE_NAME constant. (Do
    # not confuse `.claude-plugin/plugin.json`, the PLUGIN manifest whose
    # name is "epic", with the root marketplace CATALOG.)
    codex_catalog = load_json(CODEX_CATALOG_PATH)
    claude_catalog = load_json(CLAUDE_CATALOG_PATH)
    assert codex_catalog.get("name") == MARKETPLACE_NAME, (
        f".agents/plugins/marketplace.json top-level `name` must be "
        f"'{MARKETPLACE_NAME}', got {codex_catalog.get('name')!r}"
    )
    assert claude_catalog.get("name") == MARKETPLACE_NAME, (
        f".claude-plugin/marketplace.json top-level `name` must be "
        f"'{MARKETPLACE_NAME}', got {claude_catalog.get('name')!r}"
    )
    assert codex_catalog.get("name") == claude_catalog.get("name"), (
        "both marketplace catalogs must declare the SAME top-level `name`"
    )


def test_codex_catalog_has_local_epic_entry():
    catalog = load_json(CODEX_CATALOG_PATH)
    plugins = catalog.get("plugins")
    assert isinstance(plugins, list) and plugins, (
        ".agents/plugins/marketplace.json must declare a non-empty `plugins` list"
    )
    matches = []
    for entry in plugins:
        source = entry.get("source") or {}
        if source.get("source") != "local":
            continue
        path = source.get("path")
        if not (isinstance(path, str) and path.startswith("./")):
            continue
        if (REPO_ROOT / path).resolve() == EPIC_DIR.resolve():
            matches.append(entry)
    assert matches, (
        ".agents/plugins/marketplace.json needs at least one plugins[] entry "
        "with source.source == 'local' and a `./`-prefixed path resolving to epic/"
    )


# Task 6: per-agent install docs + smoke checklist (design §Per-agent
# packaging & install, runbook §Task 6; Kimi + Cursor cells pinned from live
# docs — issue #15 step-1 pin, fetched 2026-07-22).
#
# - `epic/README.md` `## Installing` must carry a `###` subsection per agent:
#   Claude Code, Codex CLI, Kimi Code, Cursor CLI, OpenCode.
# - Content guard: the `gh` CLI scopes string `repo, project, read:org` must
#   appear inside the `## Requirements` section (a mention elsewhere in the
#   README does not count).
# - A top-level `## Smoke checklist` section must exist (executing it is
#   Task 7's job — this lint asserts the document only).

EPIC_README_PATH = EPIC_DIR / "README.md"

INSTALL_AGENT_HEADINGS = [
    "Claude Code",
    "Codex CLI",
    "Kimi Code",
    "Cursor CLI",
    "OpenCode",
]


def read_epic_readme():
    assert EPIC_README_PATH.is_file(), "missing epic/README.md"
    return EPIC_README_PATH.read_text(encoding="utf-8")


def extract_h2_section(text, heading):
    """Body of the `## <heading>` section, up to the next `## ` or EOF."""
    match = re.search(
        rf"^## {re.escape(heading)}[ \t]*$(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


@pytest.mark.parametrize("agent", INSTALL_AGENT_HEADINGS)
def test_installing_has_subsection_per_agent(agent):
    installing = extract_h2_section(read_epic_readme(), "Installing")
    assert installing is not None, (
        "epic/README.md must have a `## Installing` section"
    )
    assert re.search(rf"^### {re.escape(agent)}[ \t]*$", installing, re.MULTILINE), (
        f"epic/README.md `## Installing` must contain a `### {agent}` subsection"
    )


def test_readme_states_gh_cli_scopes():
    requirements = extract_h2_section(read_epic_readme(), "Requirements")
    assert requirements is not None, (
        "epic/README.md must have a `## Requirements` section"
    )
    assert "repo, project, read:org" in requirements, (
        "epic/README.md `## Requirements` must state the `gh` CLI scopes "
        "`repo, project, read:org` (a mention elsewhere does not count)"
    )


def test_readme_has_smoke_checklist_section():
    assert re.search(r"^## Smoke checklist[ \t]*$", read_epic_readme(), re.MULTILINE), (
        "epic/README.md must have a `## Smoke checklist` section"
    )


# Child 4: OSS hygiene — root LICENSE (spec D11) + distribution rename (D10).

LICENSE_PATH = REPO_ROOT / "LICENSE"


def test_root_license_present_and_mit():
    assert LICENSE_PATH.is_file(), "root LICENSE file must exist (spec D11)"
    lines = LICENSE_PATH.read_text(encoding="utf-8").splitlines()
    # Guard the empty-file case explicitly — indexing lines[0] on an empty
    # LICENSE would raise IndexError (an ugly error) instead of an actionable
    # assertion failure.
    assert lines, "root LICENSE must not be empty"
    first_line = lines[0]
    assert "MIT" in first_line, (
        f"root LICENSE first line must name the MIT license, got {first_line!r}"
    )


# Child 4: README-scoped slug-aware forbidden-literal lint (spec D9, README
# scope). The shared `find_forbidden_literals` bans `getvoicify` as a plain
# substring, but the README legitimately links to the `getvoicify/claude-plugins`
# source repo (7 slugs today). So the README gets its OWN slug-aware matcher —
# scoped to the README only, so the already-clean owner-agnostic files never
# gain permission to reintroduce a bare slug:
#  - `gangan` (case-insensitive) and ProjectV2 node-ID shapes — banned outright.
#  - `tom-plugins` — the OLD marketplace name, banned outright (catches prose
#    leftovers the rename buckets miss).
#  - `getvoicify` — banned EXCEPT inside the literal `getvoicify/claude-plugins`
#    source slug (strip the slug, then any residual bare `getvoicify` is a leak).

ALLOWED_SOURCE_SLUG = "getvoicify/claude-plugins"
# Anchored on BOTH sides so ONLY the exact slug token is carved out — a
# collision on either boundary must NOT be treated as allowed:
#  - trailing: `getvoicify/claude-plugins-evil` (negative lookahead rejects a
#    following word char or hyphen);
#  - leading: `xgetvoicify/claude-plugins` or `my-getvoicify/claude-plugins`
#    (negative lookbehind rejects a preceding word char or hyphen).
# A surrounding space, `/` (as in a URL `github.com/getvoicify/claude-plugins`),
# `.`, `)`, `#`, or start/end-of-line is NOT `[\w-]`, so the legitimate source
# slug still matches and is carved out.
ALLOWED_SOURCE_SLUG_RE = re.compile(
    r"(?<![\w-])" + re.escape(ALLOWED_SOURCE_SLUG) + r"(?![\w-])"
)


def find_readme_forbidden_literals(text):
    hits = []
    lowered = text.lower()
    if "gangan" in lowered:
        hits.append("gangan")
    if NODE_ID_SHAPE_RE.search(text):
        hits.append("PVT node-id shape")
    if "tom-plugins" in lowered:
        hits.append("tom-plugins")
    # Remove only the EXACT allowed source slug (anchored), then any remaining
    # `getvoicify` is a bare owner reference or an impostor slug that must not
    # appear.
    residual = ALLOWED_SOURCE_SLUG_RE.sub("", lowered)
    if "getvoicify" in residual:
        hits.append("getvoicify (outside getvoicify/claude-plugins)")
    return hits


def test_readme_slug_aware_matcher_is_non_vacuous():
    # The source slug is allowed on its own; a bare owner mention is not.
    assert find_readme_forbidden_literals("git clone getvoicify/claude-plugins") == []
    assert "getvoicify (outside getvoicify/claude-plugins)" in (
        find_readme_forbidden_literals("owner getvoicify elsewhere")
    )
    # Collision impostors on EITHER side must still be flagged — the carve-out
    # must match the EXACT slug token, not a longer slug that merely shares a
    # boundary with it. TRAILING collision (`…-evil`/`…-fork`): an unanchored
    # strip would delete the real-slug prefix and leave only `-evil`, hiding the
    # bare owner. LEADING collision (`xgetvoicify/…`, `my-getvoicify/…`): a
    # trailing-only anchor would strip the `getvoicify/claude-plugins` substring
    # and leave `x`, hiding what is really a bare-getvoicify usage.
    assert "getvoicify (outside getvoicify/claude-plugins)" in (
        find_readme_forbidden_literals("git clone getvoicify/claude-plugins-evil")
    )
    assert "getvoicify (outside getvoicify/claude-plugins)" in (
        find_readme_forbidden_literals("git clone getvoicify/claude-plugins-fork")
    )
    assert "getvoicify (outside getvoicify/claude-plugins)" in (
        find_readme_forbidden_literals("git clone xgetvoicify/claude-plugins")
    )
    assert "getvoicify (outside getvoicify/claude-plugins)" in (
        find_readme_forbidden_literals("git clone my-getvoicify/claude-plugins")
    )
    # The EXACT slug bounded by any non-word, non-hyphen delimiter on either
    # side stays allowed — the anchors must not over-reject. Trailing: space,
    # `/`, `.`, `)`, `#`, EOL. Leading: start-of-line, space, `/` (as in a URL
    # `github.com/getvoicify/claude-plugins`), `(`.
    for trailing in ("", " x", "/issues/9", ".git", ")", "#9"):
        assert (
            find_readme_forbidden_literals(f"getvoicify/claude-plugins{trailing}") == []
        ), f"exact slug with trailing {trailing!r} should be allowed"
    for leading in ("", "git clone ", "github.com/", "("):
        assert (
            find_readme_forbidden_literals(f"{leading}getvoicify/claude-plugins") == []
        ), f"exact slug with leading {leading!r} should be allowed"
    # gangan, the old marketplace name, and node-ID shapes are banned outright.
    assert "gangan" in find_readme_forbidden_literals("gangan-api done")
    assert "tom-plugins" in find_readme_forbidden_literals("claude plugin install epic@tom-plugins")
    assert "PVT node-id shape" in find_readme_forbidden_literals("id: PVT_kwDOxxxx")
    assert "PVT node-id shape" in find_readme_forbidden_literals("statusFieldId: PVTSSF_xxxx")


def test_readme_has_no_forbidden_literals():
    hits = find_readme_forbidden_literals(read_epic_readme())
    assert not hits, (
        f"epic/README.md: forbidden literal(s) {sorted(set(hits))} — the README "
        f"must be owner-agnostic (only the `{ALLOWED_SOURCE_SLUG}` source slug is "
        f"allowed), carry no `gangan`, no `tom-plugins`, and no ProjectV2 node-ID "
        f"shapes"
    )


# Task 7: forbidden-literal ratchet (design §D9, runbook §Children 1-3).
#
# Scope grows one file at a time as each is cleaned: the github-graphql
# reference plus every file under epic/commands/ (child 1), the driver +
# migrate SKILL.md files (child 2), AND the create SKILL.md (child 3). The
# README is still out of scope here — it is child 4's. Keeping the scope to
# only-cleaned files is what lets the suite stay green on main between merges.
#
# Two forbidden classes:
#  - `gangan`, `getvoicify` — owner/org slugs, matched CASE-INSENSITIVELY.
#  - ProjectV2 node-ID shapes — matched CASE-SENSITIVELY by an alternation that
#    catches all three prefixes: `PVT_` (project), `PVTF_` (field), `PVTSSF_`
#    (single-select field). A bare `PVT_` substring would miss the `PVTSSF_`
#    field IDs, so the alternation is mandatory. Examples must use placeholders
#    (`<projectId>`, `<statusFieldId>`), never realistically-shaped IDs.

GITHUB_GRAPHQL_REFERENCE = SKILLS_ROOT / "epic" / "references" / "github-graphql.md"
EPIC_COMMANDS_DIR = REPO_ROOT / "epic" / "commands"

FORBIDDEN_CI_LITERALS = ("gangan", "getvoicify")
NODE_ID_SHAPE_RE = re.compile(r"PVT(?:_|SSF_|F_)")


def forbidden_literal_scope_paths():
    """Files the ratchet covers so far: the github-graphql reference and every
    file under epic/commands/ (child 1), the driver and migrate SKILL.md files
    (child 2), plus the create SKILL.md (child 3). Enumerated explicitly rather
    than globbing epic/skills/**/SKILL.md so the still-dirty README (child 4's)
    stays out of scope. Later children widen it."""
    paths = [GITHUB_GRAPHQL_REFERENCE]
    paths.extend(p for p in sorted(EPIC_COMMANDS_DIR.rglob("*")) if p.is_file())
    paths.append(SKILLS_ROOT / "epic" / "SKILL.md")
    paths.append(SKILLS_ROOT / "migrate" / "SKILL.md")
    paths.append(SKILLS_ROOT / "create" / "SKILL.md")
    return paths


def find_forbidden_literals(text):
    """Forbidden literals present in `text`: owner/org slugs (case-insensitive)
    and realistically-shaped ProjectV2 node IDs (case-sensitive)."""
    hits = []
    lowered = text.lower()
    hits.extend(literal for literal in FORBIDDEN_CI_LITERALS if literal in lowered)
    if NODE_ID_SHAPE_RE.search(text):
        hits.append("PVT node-id shape")
    return hits


@pytest.mark.parametrize(
    "path",
    forbidden_literal_scope_paths(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_forbidden_literals_in_scope(path):
    hits = find_forbidden_literals(path.read_text(encoding="utf-8"))
    assert not hits, (
        f"{path.relative_to(REPO_ROOT)}: forbidden literal(s) {sorted(set(hits))} — "
        f"the github-graphql reference and epic/commands/ must be owner-agnostic "
        f"and use placeholder IDs (<projectId>, <statusFieldId>, …)"
    )


def test_node_id_shape_lint_is_non_vacuous():
    # Synthetic fixtures — prove the case-sensitive node-ID lint actually flags
    # a project ID (`PVT_…`) AND a single-select field ID (`PVTSSF_…`). This
    # stays green once the reference table is deleted, so it can't rot into an
    # `assert "PVT" in <reference_text>` that becomes un-greenable.
    assert find_forbidden_literals("id: PVT_kwDOxxxx") == ["PVT node-id shape"]
    assert find_forbidden_literals("statusFieldId: PVTSSF_xxxx") == ["PVT node-id shape"]
    # The field prefix `PVTF_` is caught by the same alternation.
    assert NODE_ID_SHAPE_RE.search("PVTF_xxxx")
    # A bare `PVT_`-only matcher would miss these — the alternation must not.
    assert NODE_ID_SHAPE_RE.search("PVTSSF_xxxx")


def test_reference_documents_org_to_user_fallback():
    text = GITHUB_GRAPHQL_REFERENCE.read_text(encoding="utf-8")
    assert "organization(login:" in text, (
        "github-graphql.md must keep the organization(login:) resolution query"
    )
    assert "user(login:" in text, (
        "github-graphql.md must document the organization→user(login:) fallback "
        "recipe (org-form NOT_FOUND falls back to a user login)"
    )


def test_issue_types_section_is_org_only_no_user_fallback():
    # `User` has NO `issueTypes` field: a `user(login:){issueTypes}` query is an
    # `undefinedField` VALIDATION error, which the error-handling table
    # classifies as permanent → PARK. So the issue-type probe must NOT reuse the
    # org→user NOT_FOUND fallback framing (that dual recipe is correct for
    # ID/review probes, but a trap here). Issue types are org-only: a user owner
    # simply has none → skip tagging silently.
    text = GITHUB_GRAPHQL_REFERENCE.read_text(encoding="utf-8")
    section = extract_h2_section(text, "Issue types")
    assert section is not None, (
        "github-graphql.md must have an `## Issue types` section"
    )
    assert "user(login:" not in section, (
        "issue types are org-only — the section must not instruct a "
        "`user(login:` issue-types query (User has no `issueTypes` field)"
    )
    assert "NOT_FOUND" not in section, (
        "issue types are org-only — drop the org→user NOT_FOUND fallback "
        "framing from the issue-type probe; a user owner simply has no types"
    )


# Task 8: D5 config-conditional Claude-Review gate sentence (design §D5,
# runbook §Child 2).
#
# The driver SKILL.md must carry the canonical config-conditional Claude-Review
# sentence, so the review gate reads as conditional on config and no prose
# assumes the workflow exists in every repo. Whitespace-normalized and
# lowercased exactly like the config-lookup lint (which lowercases the haystack
# — so the needle is lowercase too).

D5_CLAUDE_REVIEW_SENTENCE = (
    "the claude review gate applies only when `claude-review` is listed in "
    "the repo's `merge.required_checks`; when absent, skip it and note the skip."
)


def test_driver_skill_carries_d5_conditional_gate_sentence():
    path = skill_md_path("epic")
    text = path.read_text(encoding="utf-8")
    # The sentence hard-wraps in prose; normalize whitespace and lowercase the
    # same way test_config_lookup_order_sentence_present does before matching.
    normalized = " ".join(text.lower().split())
    assert D5_CLAUDE_REVIEW_SENTENCE in normalized, (
        f"{path} must carry the D5 canonical config-conditional Claude-Review "
        f"sentence: {D5_CLAUDE_REVIEW_SENTENCE!r}"
    )


# Task 9: create skill planning seam (child 3, design §D3/D4/D7).
#
# create/SKILL.md must carry the canonical planning-seam sentence naming the
# `planning:` config source for epic home + project, with the interactive
# fallback. Whitespace-normalized and lowercased exactly like the config-lookup
# and D5 lints (the haystack is lowercased — so the needle is lowercase too).
# A single test on the create path only: epic/migrate use different planning
# phrasing, so this is NOT parametrized over SKILL_NAMES (mirrors the D5 lint).

CREATE_PLANNING_SENTENCE = (
    "epic home and project come from `planning:` in the cwd repo's "
    "`.agents/epic.yaml` (fallback `.claude/epic.yaml`); when absent, ask the "
    "operator."
)


def test_create_skill_carries_planning_seam_sentence():
    path = skill_md_path("create")
    text = path.read_text(encoding="utf-8")
    # The sentence hard-wraps in prose; normalize whitespace and lowercase the
    # same way test_config_lookup_order_sentence_present does before matching.
    normalized = " ".join(text.lower().split())
    assert CREATE_PLANNING_SENTENCE in normalized, (
        f"{path} must carry the canonical planning-seam sentence: "
        f"{CREATE_PLANNING_SENTENCE!r}"
    )


def test_create_skill_prior_art_uses_docs_config_not_superpowers():
    # D7: create's prior-art search must use the target repo's Layer-2 docs
    # dirs (docs.spec_dir / docs.runbook_dir) instead of the hardcoded
    # `docs/superpowers/`. The `spec_dir` token-presence half is only a floor
    # (create already references docs.spec_dir); the load-bearing guard is that
    # the hardcoded `docs/superpowers/` path is gone.
    text = skill_md_path("create").read_text(encoding="utf-8").lower()
    assert "docs/superpowers" not in text, (
        "create/SKILL.md prior-art search must not hardcode `docs/superpowers/` "
        "(D7: use the target repo's docs.spec_dir / docs.runbook_dir dirs)"
    )
    assert "spec_dir" in text, (
        "create/SKILL.md must reference the docs-config `spec_dir` for prior-art"
    )
