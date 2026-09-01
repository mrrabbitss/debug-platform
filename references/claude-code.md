# Claude Code on Windows 11

Claude Code CLI is the primary host for this release. The Skill remains a
portable Agent Skill and does not require a Claude API key of its own.

## Discover and invoke

The official user-level discovery path is
`%USERPROFILE%\.claude\skills\gw-ap-debug\SKILL.md`. On Windows 11, prefer
cloning the canonical Git checkout directly into that real directory. The
supplied installer recognizes it as the canonical checkout and creates
junctions only for the Codex and OpenCode discovery paths. This follows
[Claude Code's Skill location contract](https://code.claude.com/docs/en/skills)
while avoiding a reported Windows first-invocation edge involving junctioned
personal Skills.

Start Claude Code in any working directory, run `/skills` to confirm that
`gw-ap-debug` is present, and invoke it directly:

```text
/gw-ap-debug D:\logs\gw D:\logs\ap 诊断 AP 频繁离线；使用当前 Claude 模型完成诊断，并允许读取内置方法和本地限界脱敏证据
```

Claude Code appends text following `/gw-ap-debug` to the loaded Skill, so paths,
the symptom, and model-egress approval can be supplied in one message. Natural
language that clearly requests GW/AP log diagnosis can also trigger the Skill.

## Interaction rules

- If the request already supplies at least one readable input path and explicit
  host-model egress approval, begin immediately instead of asking the user to
  restate fields.
- If essential input or approval is missing, ask one concise question that
  collects only the missing information.
- Give short progress updates at preparation, evidence search, and final
  validation boundaries. Do not paste routine command output unless it explains
  a failure.
- Continue after local preparation; `run` is not the final diagnosis.
- When a complete external diagnostic Skill is supplied, use it with the
  default run scope. Do not persist it unless the user explicitly asks. If the
  composed-method preflight exceeds its default budget, report the estimate and
  narrow the selected Markdown before considering an explicit higher limit.
- Return only the final validated Markdown report. If validation fails, explain
  the failed invariant and the smallest next action.

## Windows command runner

Prefer the wrapper below for every local Skill command. It automatically finds
Python 3.14, 3.13, 3.12, or 3.11 through the Windows Python launcher or PATH:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" doctor --check host-agent
```

Pass normal Skill CLI arguments after the script path. Use `-Python` only when a
specific compatible interpreter must be selected. The wrapper does not start a
nested Claude session and does not read Claude credentials.

## Discovery troubleshooting

- If `~/.claude/skills` did not exist when the current Claude session started,
  restart Claude Code once after installation.
- If `/skills` lists `gw-ap-debug` but `/gw-ap-debug` initially reports Unknown,
  make one harmless read-only tool call and retry. If the symptom persists,
  use a real checkout at `~/.claude/skills/gw-ap-debug` instead of a junction.
  This is a documented workaround for the upstream
  [Windows junction discovery issue](https://github.com/anthropics/claude-code/issues/41177),
  not a claim that native Claude execution was validated on this computer.
- Confirm that the final path contains `SKILL.md` and is not nested one level
  deeper.
- A personal Skill with the same directory name takes precedence over a project
  Skill. Remove or rename the stale duplicate rather than maintaining two
  independent copies.
- Run the installer again without `-Update` to verify either the direct
  canonical directory or an existing junction; it fails rather than repointing
  an unrelated path.
