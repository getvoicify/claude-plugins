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
