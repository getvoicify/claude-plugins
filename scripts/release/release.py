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


def _codex_plugin_json(repo_root, source):
    return Path(repo_root) / source / ".codex-plugin" / "plugin.json"


def read_version(repo_root, source):
    return json.loads(_plugin_json(repo_root, source).read_text())["version"]


def _patch_version(path, new_version):
    data = json.loads(path.read_text())
    data["version"] = new_version
    path.write_text(json.dumps(data, indent=2) + "\n")


def write_version(repo_root, source, new_version):
    _patch_version(_plugin_json(repo_root, source), new_version)
    codex_path = _codex_plugin_json(repo_root, source)
    if codex_path.is_file():
        _patch_version(codex_path, new_version)


def _git(*args):
    return subprocess.check_output(["git", *args], text=True)


def _tag_to_version(tag, name):
    """(major, minor, patch) for a well-formed `<name>-vX.Y.Z` tag, else None."""
    rest = tag.removeprefix(f"{name}-v")
    parts = rest.split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _source_subpath(source):
    """Plugin source dir relative to repo root (strips a leading './')."""
    return source.removeprefix("./")


def latest_tag(name):
    """Latest well-formed `<name>-vX.Y.Z` tag by SemVer order, or None."""
    prefix = f"{name}-v"
    candidates = []
    for tag in _git("tag", "--list", f"{prefix}*").splitlines():
        version = _tag_to_version(tag, name) if tag else None
        if version is not None:
            candidates.append((version, tag))
    if not candidates:
        return None
    return max(candidates)[1]


def commits_since(tag, source):
    """Full messages of commits since `tag` (or all) touching `source`."""
    path = _source_subpath(source)
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
