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
