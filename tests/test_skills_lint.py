# Task 1: skills tree structure lint.
# Tasks 2-6 append further sections below under their own "# Task N" comments.

import pathlib

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
    end = text.index("\n---\n", 4)
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


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_reference_link_appears_and_resolves(name):
    link = REFERENCE_LINKS[name]
    path = skill_md_path(name)
    body = path.read_text(encoding="utf-8")
    assert link in body, f"{path} must contain the literal link `{link}`"
    target = (path.parent / link).resolve()
    assert target.is_file(), f"{path}: `{link}` does not resolve to a file"
