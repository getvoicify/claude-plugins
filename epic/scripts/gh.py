"""The only module in epic/scripts that performs I/O."""
import json
import re
import subprocess


class GhError(RuntimeError):
    def __init__(self, returncode, stderr):
        super().__init__(stderr)
        self.returncode = returncode
        self.stderr = stderr


def run_json(args, cwd=None):
    """Run `gh <args>` and parse stdout as JSON."""
    try:
        out = subprocess.check_output(["gh", *args], text=True, stderr=subprocess.PIPE, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        raise GhError(exc.returncode, exc.stderr) from exc
    return json.loads(out)


def graphql(query, **variables):
    """Run a GraphQL query and return the unwrapped `data` payload."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in sorted(variables.items()):
        args += ["-F", f"{key}={value}"]
    return run_json(args)["data"]


def run_git(args, cwd=None):
    """Run `git <args>` and return raw stdout text.

    The one non-`gh` shell-out, kept here so `subprocess` still appears
    nowhere else — callers (e.g. preflight.py) do their own parsing of the
    returned text; this function only executes the command.
    """
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        raise GhError(exc.returncode, exc.stderr) from exc


_ORIGIN_URL_RE = re.compile(r"[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


def resolve_repo_from_cwd(cwd=None):
    """`owner/name` of the cwd checkout's `origin` remote."""
    url = run_git(["remote", "get-url", "origin"], cwd=cwd).strip()
    match = _ORIGIN_URL_RE.search(url)
    if not match:
        raise GhError(1, f"cannot parse owner/repo from origin url: {url!r}")
    return f"{match.group('owner')}/{match.group('name')}"
