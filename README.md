# Epic Plugins

An agent-agnostic toolkit for driving GitHub epics — the **`epic-plugins`**
marketplace catalog. It ships a single plugin, `epic`: a unified GitHub epic
driver that runs epics one child at a time using native **sub-issues** for
hierarchy, native **blocked-by relations** for dependencies, and a configured
**ProjectV2 board** for live status. It works across Claude Code, Codex CLI,
Kimi Code, Cursor CLI, and OpenCode.

See [`epic/README.md`](epic/README.md) for the full command reference,
per-agent install instructions, configuration model, and the release smoke
checklist.

## Installing

Distributed as the `epic-plugins` marketplace, or as a plain copy of the skill
tree. Full per-agent steps live in [`epic/README.md`](epic/README.md#installing).

## Contributing

Issues and pull requests are welcome. The plugin's behavior is guarded by a
lint suite — before opening a PR, make sure it passes:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -q
```

`feat:` / `fix:` commits drive the automated SemVer release; `docs` / `chore`
commits do not. Keep commit subjects accurate to
[Conventional Commits](https://www.conventionalcommits.org/).

## License

[MIT](LICENSE).
