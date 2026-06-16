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


def test_tag_to_version_parses_well_formed():
    assert release._tag_to_version("epic-v1.2.3", "epic") == (1, 2, 3)


def test_tag_to_version_rejects_prerelease_and_garbage():
    assert release._tag_to_version("epic-v1.0.0-rc1", "epic") is None
    assert release._tag_to_version("epic-vfoo", "epic") is None
    assert release._tag_to_version("epic-v1.2", "epic") is None


def test_source_subpath_strips_dot_slash_prefix_only():
    assert release._source_subpath("./epic") == "epic"
    assert release._source_subpath("epic") == "epic"


def test_latest_tag_skips_malformed(monkeypatch):
    monkeypatch.setattr(
        release, "_git", lambda *a: "epic-v1.0.0\nepic-v1.2.0\nepic-v2.0.0-rc1\n"
    )
    assert release.latest_tag("epic") == "epic-v1.2.0"


def test_latest_tag_none_when_no_tags(monkeypatch):
    monkeypatch.setattr(release, "_git", lambda *a: "\n")
    assert release.latest_tag("epic") is None
