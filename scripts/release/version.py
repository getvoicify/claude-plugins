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
