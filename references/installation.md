# CLI installation and discovery

The Skill follows the Agent Skills directory format. Keep the complete directory together so relative `scripts/`, `references/`, and `runtime/` paths continue to work.

## Project scope

- Codex: `<repo>/.agents/skills/gw-ap-debug/SKILL.md`
- Claude Code: `<repo>/.claude/skills/gw-ap-debug/SKILL.md`
- OpenCode: `<repo>/.opencode/skills/gw-ap-debug/SKILL.md`

OpenCode also discovers the Claude-compatible and agent-compatible locations, so a project using both Codex and OpenCode can share the `.agents/skills` copy. However, `OPENCODE_DISABLE_EXTERNAL_SKILLS` disables those compatibility locations; use `.opencode/skills` when that isolation setting is enabled. Claude Code needs its `.claude/skills` path; use a symlink or directory junction to the canonical directory when supported, otherwise copy the complete Skill directory.

## User scope

- Codex: `~/.agents/skills/gw-ap-debug/SKILL.md`
- Claude Code: `~/.claude/skills/gw-ap-debug/SKILL.md`
- OpenCode: `~/.config/opencode/skills/gw-ap-debug/SKILL.md`

OpenCode also discovers `~/.agents/skills` and `~/.claude/skills`.

For a user-level installation shared across projects and CLIs, keep one Git
checkout as the canonical copy and register it with the supplied setup script.
The script creates only missing junctions or symlinks and refuses to overwrite
an existing unrelated directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_user_skill.ps1 -Clients All -RunBootstrap -RunValidation
```

```bash
bash scripts/setup_user_skill.sh --clients all --bootstrap --validate
```

Use `-PlanOnly` or `--plan` to preview changes. Use `-Update` or `--update` on
later runs for a fast-forward-only update from `origin/skillonly`.

## Host model credentials

The Skill uses the model already selected by the host CLI and never needs that model provider's API credential. Do not pass a provider key to `debug_platform_skill.py`, store it in the Skill, or expose it as a Skill-specific environment variable.

Treat every environment variable inherited by OpenCode as reachable by model-invoked shell processes. When an OpenCode custom provider uses an environment-backed key, configure a reviewed `shell.env` hook or a separate local credential-holding proxy to remove that variable from tool subprocesses, and keep shell/Web/subagent permissions narrowly scoped. The Skill does not install a provider-specific hook automatically because provider IDs and credential variable names are owned by the host configuration.

## Runtime state

The installed Skill directory is treated as read-only. Virtual environments, SQLite data, uploads, backend logs, secrets, and run bundles default to the OS user state directory:

- Windows: `%LOCALAPPDATA%\gw-ap-debug`
- Linux/macOS: `$XDG_STATE_HOME/gw-ap-debug` or `~/.local/state/gw-ap-debug`

Override with `GW_AP_DEBUG_STATE_DIR` or `--state-dir`. Updating or replacing the Skill does not remove this external state.

The backend virtual environment stores a fingerprint of `pyproject.toml`, `constraints.lock`, `uv.lock`, and the Python major/minor version. A changed or missing fingerprint makes readiness fail until `bootstrap` refreshes the environment. Backend launches set `PYTHONDONTWRITEBYTECODE=1`, so importing the vendored runtime does not create `__pycache__` files inside the installed Skill.

Host-agent validation keys and rollback anchors live under `<state>/host-validation-keys`. They contain no model API credential or diagnostic content, but an unfinished host bundle depends on them and fails closed if they are lost. A successfully finalized report remains portable.

The active fault tree and log-analysis method copies live under `<state>/methods`, not in the installed Skill. They are initialized from the bundled defaults on first bootstrap/start. Use `GW_AP_DEBUG_METHODS_DIR` to select another writable directory, or install reviewed replacements without editing the Skill:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" sync-methods --fault-tree /path/to/fault-tree.md --log-analysis /path/to/log-analysis.md
```

Existing, different method files are preserved unless `--force` is explicit.

In commands, replace `<SKILL_DIR>` with the absolute directory containing `SKILL.md`; the CLI may be running from an unrelated project directory. Run `doctor --check package`, then `bootstrap`, then `doctor --check host-agent` after installation.
