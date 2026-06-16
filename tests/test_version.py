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
