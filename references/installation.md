# CLI installation and discovery

The Skill follows the Agent Skills directory format. Keep the complete directory together so relative `scripts/`, `references/`, and `runtime/` paths continue to work.

## Primary Windows 11 host

Claude Code CLI is the primary host. Codex CLI is the preferred fallback and
OpenCode CLI is an additional compatible host. Install once at user scope so
the Skill is available in every local project:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_user_skill.ps1 -Clients All -RunBootstrap -RunValidation
```

The installer registers Claude first, then the Codex and OpenCode compatibility
paths. It automatically selects Python 3.14, 3.13, 3.12, or 3.11 and reports
which CLI executables are currently discoverable. A missing auxiliary CLI is a
warning, not an installation failure.

After installation, start Claude Code, run `/skills`, and invoke
`/gw-ap-debug <log paths> <symptom>`. See [claude-code.md](claude-code.md) for
the concise interaction contract.

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
On Windows 11, clone it directly to `~/.claude/skills/gw-ap-debug`; the script
uses that real directory for Claude and creates only the missing Codex/OpenCode
junctions. It recognizes an already-canonical target and refuses to overwrite
any unrelated directory:

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

Diagnostic methods live outside the installed Skill. Bundled or reviewed base
methods are initialized in writable state and then composed into immutable
directories under `<state>/method-packs/generations`. The persistent selection
is an atomically replaced `<state>/method-packs/active.json` pointer; the
backend validates that pointer, its generation manifest, and both method hashes
before loading. Install reviewed base replacements without editing the Skill:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" sync-methods --fault-tree /path/to/fault-tree.md --log-analysis /path/to/log-analysis.md
```

Existing, different base methods are preserved unless `--force` is explicit.
`GW_AP_DEBUG_METHODS_DIR` may select a different writable base-method directory,
but the bundled backend still receives `<state>/method-packs` as its generation
and case-binding control root.

Complete diagnostic Skills can be parsed into external method packs and
composed on top of this base; no imported code is executed. `run` and
`diagnose` accept repeatable `--diagnostic-skill <path>` options and default to
`--diagnostic-skill-scope run`. This creates an immutable generation plus a
temporary per-case binding without changing the persistent registry or active
pointer. Use `import-skill-methods --skill <path> --dry-run` to preview. Rerun
without `--dry-run`, or use `--diagnostic-skill-scope persistent`, only to
install the knowledge for later runs; `list-method-packs` and
`remove-method-pack` operate on persistent packs. Every host-agent run
preflights the active composed methods, even when no external Skill is supplied.
The default rejects a UTF-8-byte upper-bound estimate above 120,000;
deliberately raise `--max-host-method-tokens` only for a selected model with a
verified larger context window. See
[composable-knowledge.md](composable-knowledge.md).

In commands, replace `<SKILL_DIR>` with the absolute directory containing
`SKILL.md`; the CLI may be running from an unrelated project directory. On
Windows prefer `scripts\gw_ap_debug.ps1`; on Linux/macOS prefer
`scripts/gw_ap_debug.sh`. Run `doctor --check package`, then `bootstrap`, then
`doctor --check host-agent` after installation.
