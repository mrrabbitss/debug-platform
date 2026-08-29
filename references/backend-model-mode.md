# Optional backend-model mode

Use this mode only when the user explicitly wants the embedded backend to call
an approved OpenAI-compatible endpoint. It is not needed for Claude Code,
Codex CLI, or OpenCode CLI host-model reasoning.

## Configure safely

Place the API key in the process environment, never in a command argument, file, report, or shell history.

PowerShell:

```powershell
$env:GW_AP_DEBUG_MODEL_API_KEY = "secret"
python "<SKILL_DIR>/scripts/debug_platform_skill.py" configure-model --model-base-url "https://approved.example/v1" --model "model-name" --test
```

POSIX shell:

```sh
export GW_AP_DEBUG_MODEL_API_KEY='secret'
python "<SKILL_DIR>/scripts/debug_platform_skill.py" configure-model --model-base-url 'https://approved.example/v1' --model 'model-name' --test
```

`configure-model` creates or updates the named database profile, activates it, and optionally tests it. This avoids the old first-start problem where a previously created mock profile stayed active after environment variables changed.

The server applies its endpoint allowlist, private-network, proxy, and certificate rules. Use `GW_AP_DEBUG_MODEL_PROXY_URL` only when the deployment requires an approved proxy.

## Run

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" run --mode backend-model --approve-model-egress --title "issue" --log "/path/to/log"
```

Backend-model mode requires `--approve-model-egress`. It cannot be combined with `--approve-host-model-egress`. If the endpoint fails or output validation rejects the response, retain and disclose the deterministic fallback.

Check readiness before a real-model run:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" doctor --check backend-model
```
