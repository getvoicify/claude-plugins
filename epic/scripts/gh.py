"""The only module in epic/scripts that performs I/O."""
import json
import subprocess


class GhError(RuntimeError):
    def __init__(self, returncode, stderr):
        super().__init__(stderr)
        self.returncode = returncode
        self.stderr = stderr


def run_json(args, cwd=None):
    """Run `gh <args>` and parse stdout as JSON."""
    try:
        out = subprocess.check_output(["gh", *args], text=True, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        raise GhError(exc.returncode, exc.stderr) from exc
    return json.loads(out)


def graphql(query, **variables):
    """Run a GraphQL query and return the unwrapped `data` payload."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in sorted(variables.items()):
        args += ["-F", f"{key}={value}"]
    return run_json(args)["data"]
