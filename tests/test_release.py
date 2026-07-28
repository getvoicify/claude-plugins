import json

import pytest

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


def _write_manifest(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_write_version_writes_both_manifests(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    codex_pj = tmp_path / "epic" / ".codex-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})
    _write_manifest(codex_pj, {"name": "epic", "version": "0.1.0"})

    release.write_version(tmp_path, "./epic", "0.2.0")

    assert json.loads(claude_pj.read_text())["version"] == "0.2.0"
    assert json.loads(codex_pj.read_text())["version"] == "0.2.0"
    assert codex_pj.read_text().endswith("\n")


def test_write_version_writes_kimi_manifest_at_repo_root(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    kimi_pj = tmp_path / ".kimi-plugin" / "plugin.json"  # REPO ROOT, not under epic/
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})
    _write_manifest(
        kimi_pj,
        {"name": "epic", "version": "0.1.0", "skills": "./epic/skills/"},
    )

    release.write_version(tmp_path, "./epic", "0.2.0")

    data = json.loads(kimi_pj.read_text())
    assert data["version"] == "0.2.0"
    assert data["skills"] == "./epic/skills/"  # extras preserved
    assert kimi_pj.read_text().endswith("\n")


def test_write_version_skips_absent_kimi_manifest(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})

    release.write_version(tmp_path, "./epic", "0.2.0")

    assert json.loads(claude_pj.read_text())["version"] == "0.2.0"
    assert not (tmp_path / ".kimi-plugin").exists()


def test_write_version_preserves_codex_manifest_extras(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    codex_pj = tmp_path / "epic" / ".codex-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})
    _write_manifest(
        codex_pj,
        {
            "name": "epic",
            "version": "0.1.0",
            "description": "an epic plugin",
            "skills": "./skills",
        },
    )

    release.write_version(tmp_path, "./epic", "0.2.0")

    data = json.loads(codex_pj.read_text())
    assert data["version"] == "0.2.0"
    assert data["skills"] == "./skills"
    assert data["description"] == "an epic plugin"


def test_write_version_converges_drifted_manifests(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    codex_pj = tmp_path / "epic" / ".codex-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.7.0"})
    _write_manifest(codex_pj, {"name": "epic", "version": "0.5.0"})

    release.write_version(tmp_path, "./epic", "0.8.0")

    assert json.loads(claude_pj.read_text())["version"] == "0.8.0"
    assert json.loads(codex_pj.read_text())["version"] == "0.8.0"


def test_write_version_skips_absent_codex_manifest(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})

    release.write_version(tmp_path, "./epic", "0.2.0")

    assert json.loads(claude_pj.read_text())["version"] == "0.2.0"
    assert not (tmp_path / "epic" / ".codex-plugin").exists()


def test_write_version_leaves_claude_untouched_when_codex_malformed(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    codex_pj = tmp_path / "epic" / ".codex-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})
    codex_pj.parent.mkdir(parents=True, exist_ok=True)
    codex_pj.write_text("{not json")
    claude_before = claude_pj.read_text()

    with pytest.raises(json.JSONDecodeError):
        release.write_version(tmp_path, "./epic", "0.2.0")

    assert claude_pj.read_text() == claude_before


def test_write_version_leaves_codex_untouched_when_kimi_malformed(tmp_path):
    claude_pj = tmp_path / "epic" / ".claude-plugin" / "plugin.json"
    codex_pj = tmp_path / "epic" / ".codex-plugin" / "plugin.json"
    kimi_pj = tmp_path / ".kimi-plugin" / "plugin.json"
    _write_manifest(claude_pj, {"name": "epic", "version": "0.1.0"})
    _write_manifest(codex_pj, {"name": "epic", "version": "0.1.0"})
    kimi_pj.parent.mkdir(parents=True, exist_ok=True)
    kimi_pj.write_text("{not json")
    codex_before = codex_pj.read_text()
    claude_before = claude_pj.read_text()

    with pytest.raises(json.JSONDecodeError):
        release.write_version(tmp_path, "./epic", "0.2.0")

    assert codex_pj.read_text() == codex_before
    assert claude_pj.read_text() == claude_before


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
