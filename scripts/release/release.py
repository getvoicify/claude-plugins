"""Orchestrates per-plugin SemVer releases. Run from the repo root."""
import json
import subprocess
import sys
from pathlib import Path

from version import next_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_plugins(marketplace_path):
    data = json.loads(Path(marketplace_path).read_text())
    return [(p["name"], p["source"]) for p in data["plugins"]]


def _plugin_json(repo_root, source):
    return Path(repo_root) / source / ".claude-plugin" / "plugin.json"


def read_version(repo_root, source):
    return json.loads(_plugin_json(repo_root, source).read_text())["version"]


def write_version(repo_root, source, new_version):
    path = _plugin_json(repo_root, source)
    data = json.loads(path.read_text())
    data["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n")


def _git(*args):
    return subprocess.check_output(["git", *args], text=True)


def latest_tag(name):
    """Latest `<name>-vX.Y.Z` tag by SemVer order, or None."""
    prefix = f"{name}-v"
    tags = [t for t in _git("tag", "--list", f"{prefix}*").splitlines() if t]
    if not tags:
        return None

    def key(tag):
        return tuple(int(p) for p in tag[len(prefix):].split("."))

    return max(tags, key=key)


def commits_since(tag, source):
    """Full messages of commits since `tag` (or all) touching `source`."""
    path = source.lstrip("./")
    rng = f"{tag}..HEAD" if tag else "HEAD"
    out = _git("log", rng, "--format=%B%x00", "--", path)
    return [chunk.strip() for chunk in out.split("\x00") if chunk.strip()]


def compute(repo_root=REPO_ROOT, marketplace_path=None):
    """Bump plugin.json files as needed; return list of release actions."""
    marketplace_path = marketplace_path or (Path(repo_root) / ".claude-plugin" / "marketplace.json")
    results = []
    for name, source in load_plugins(marketplace_path):
        tag = latest_tag(name)
        current = read_version(repo_root, source)
        if tag is None:
            # Bootstrap: seed a baseline tag at the current version, no bump.
            results.append({"name": name, "source": source, "version": current, "bump": "bootstrap"})
            continue
        new_version, bump = next_version(current, commits_since(tag, source))
        if new_version is None:
            continue
        write_version(repo_root, source, new_version)
        results.append({"name": name, "source": source, "version": new_version, "bump": bump})
    return results


if __name__ == "__main__":
    json.dump(compute(), sys.stdout)
    sys.stdout.write("\n")
