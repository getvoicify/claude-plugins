# Task 1: skills tree structure lint.
# Tasks 2-6 append further sections below under their own "# Task N" comments.

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


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_capability_tokens_only_in_guarded_paragraphs(name):
    path = skill_md_path(name)
    violations = []
    for para in split_paragraphs(path.read_text(encoding="utf-8")):
        # Hard-wrapped markdown may break a marker phrase across lines;
        # normalize intra-paragraph whitespace before matching.
        normalized = " ".join(para.lower().split())
        hits = CAPABILITY_TOKEN_RE.findall(normalized)
        if not hits:
            continue
        guarded = any(marker in normalized for marker in GUARD_MARKERS) and "otherwise" in normalized
        if not guarded:
            first_line = para.strip().splitlines()[0]
            violations.append(f"{sorted({h.lower() for h in hits})} in paragraph starting: {first_line!r}")
    assert not violations, (
        f"{path}: capability tokens outside guarded paragraphs (need "
        f"'if your harness' or 'if supported' AND 'otherwise'):\n" + "\n".join(violations)
    )
