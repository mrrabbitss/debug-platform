# Claude Code on Windows 11

Claude Code CLI is the primary host for this release. The Skill remains a
portable Agent Skill and does not require a Claude API key of its own.

## Discover and invoke

The user-level discovery path is
`%USERPROFILE%\.claude\skills\gw-ap-debug\SKILL.md`. The supplied installer
creates a directory junction from that path to the canonical Git checkout.

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
- Confirm that the final path contains `SKILL.md` and is not nested one level
  deeper.
- A personal Skill with the same directory name takes precedence over a project
  Skill. Remove or rename the stale duplicate rather than maintaining two
  independent copies.
- Run the installer again without `-Update` to verify the existing junction;
  it fails rather than repointing an unrelated path.
