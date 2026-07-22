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
# - Root `.agents/plugins/marketplace.json`: Codex repo catalog, top-level
#   name "tom-plugins", at least one plugins[] entry with
#   source.source == "local" and a `./`-prefixed path resolving to `epic/`.

EPIC_DIR = REPO_ROOT / "epic"
CLAUDE_MANIFEST_PATH = EPIC_DIR / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST_PATH = EPIC_DIR / ".codex-plugin" / "plugin.json"
CODEX_CATALOG_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"


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


def test_codex_catalog_name_is_tom_plugins():
    catalog = load_json(CODEX_CATALOG_PATH)
    assert catalog.get("name") == "tom-plugins", (
        ".agents/plugins/marketplace.json top-level `name` must be 'tom-plugins'"
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
