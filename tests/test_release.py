import json

import release


def test_load_plugins(tmp_path):
    manifest = tmp_path / "marketplace.json"
    manifest.write_text(
        json.dumps(
            {"name": "tom-plugins", "plugins": [{"name": "epic", "source": "./epic"}]}
        )
    )
    assert release.load_plugins(manifest) == [("epic", "./epic")]


def test_write_then_read_version_roundtrip(tmp_path):
    pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    pj.parent.mkdir(parents=True)
    pj.write_text(json.dumps({"name": "epic", "version": "0.1.0"}, indent=2) + "\n")

    release.write_version(tmp_path, "./epic", "0.2.0")

    assert release.read_version(tmp_path, "./epic") == "0.2.0"
    assert json.loads(pj.read_text())["version"] == "0.2.0"
    assert pj.read_text().endswith("\n")
