# Agent Runtime vNext Validation Record

Validated: 2026-08-10 on Windows

## Scope and source isolation

- vNext source: `GW_AP_Debug_Agent_Runtime_latest-main_vNext/GW_AP_Debug_Agent_Runtime_latest-main_vNext`.
- Baseline main commit recorded by the delivery: `da29b2b8b44ca7abcaa2796f77fc90354777ab44`.
- The original vNext delivery directory did not contain Git metadata. Its reviewed contents and fixes are now archived on the independent branch `codex/agent-runtime-vnext-opencode` in a separate Git worktree.
- `D:\GRXM\debugplatform` remained clean on `codex/llm-case-curation`; neither its working tree nor the `main` ref was changed.
- The default Win11 installer was deliberately not run because its runtime path, global Skill name and MCP name are not yet namespaced for side-by-side main/vNext installation.
- New-PC reproduction and manually namespaced parallel operation are documented in `docs/new-pc-vnext-opencode-setup.md`.

## Confirmed fixes before live testing

1. OpenCode configuration now uses the v1 schema accepted by installed OpenCode 1.18.15: server names are directly under `mcp`, with `$schema` and `enabled`.
2. Generated OpenCode configuration is documented as an explicit `OPENCODE_CONFIG` file or a snippet to merge into `opencode.json`; it is no longer implied that `opencode.mcp.json` auto-loads.
3. Claude MCP option ordering no longer lets the final variadic `--env` consume the server name, and native CLI failure is checked.
4. `debug_generate_report` is correctly declared WRITE, requires ENGINEER/ADMIN plus `confirm_write=true`, and is non-idempotent.
5. External-mode platform analysis is recorded as `deterministic / rule+agentic-evidence`, not falsely attributed to an external coding agent that has not submitted a result.
6. Windows E2E waits after forced process termination and retries transient temp-directory cleanup failures.
7. `scripts/test_opencode_integration.bat` provides an isolated, synthetic-data, real-LLM OpenCode test without touching personal OpenCode configuration.
8. Python lock verification compares canonical text instead of raw CRLF/LF byte hashes, so a clean Git-for-Windows checkout no longer fails `validate_all.bat Full` only because `core.autocrlf` changed line endings.
9. The locked `pypdf` floor is 6.15.0 so the Full dependency audit does not ship the two vulnerabilities reported for 6.14.2.

## Regression results

- Python compile checks for the changed backend/E2E files: PASS.
- Focused Agent Runtime, CLI/MCP and startup tests: **39 passed / 0 failed**.
- Complete backend suite: **172 passed / 1 skipped / 0 failed**.
- Backend line coverage: **75.46%**, above the enforced 75% gate.
- Repository Harness: **PASS, 13/13**, including 25 Workflow/OpenAPI operations.
- Architecture gate: PASS; 80 Python files, 11 Vue files, maximum measured complexity 47.
- PowerShell AST and OpenCode example JSON parse: PASS.
- Unified `scripts\validate_all.bat Full`: **PASS, 19/19 steps** (`20260810-111013-full`, 270.83 seconds), including locked dependency bootstrap, audits, frontend/extension builds, Doctor, runtime smoke and browser E2E.
- Python, frontend and VS Code extension production dependency audits: PASS with no known vulnerabilities.

### Native Windows Agent Runtime E2E

`scripts/run_agent_runtime_e2e.py`: **PASS with exit code 0**.

```json
{
  "events": 10,
  "tool_count": 12,
  "analysis_engine": "rule+agentic-evidence-external",
  "workspace_index": "COMPLETED",
  "report": "html",
  "model_scan": "embedding",
  "single_port_web": true,
  "analysis_provider": "deterministic"
}
```

This also confirms the previous Python 3.14/Windows SQLite cleanup race no longer changes a successful business test into exit code 1.

## Real OpenCode CLI + LLM + MCP result

Installed CLI: **OpenCode 1.18.15**.

- `opencode auth list`: no stored provider credentials were required for this test.
- Model probe: `opencode/deepseek-v4-flash-free` returned the requested exact marker.
- Full command: `scripts/test_opencode_integration.bat` with an explicit Python environment.
- Data: only `sample_data/collectDebuginfo_demo.zip` and a temporary copy of `sample_data/repository`.
- Isolation: temporary loopback port, SQLite database, workspace, `OPENCODE_CONFIG`, XDG config/data/cache, and explicit deny rules for shell/edit/task/web/external-directory tools.

Final result: **PASS**. The final post-fix rerun completed in 163.5 seconds (an earlier
full pass completed in 151.8 seconds).

OpenCode loaded the Skill and completed these actual tool calls:

1. `gw-ap-debug_debug_status`;
2. `gw-ap-debug_debug_create_case`;
3. `gw-ap-debug_debug_ingest`;
4. `gw-ap-debug_debug_attach_workspace`;
5. `gw-ap-debug_debug_diagnose`;
6. `gw-ap-debug_debug_evidence_bundle`.

Persisted verification:

```json
{
  "case_id": "CASE-0986b5a03e364cc3",
  "artifact_count": 2,
  "parsed_event_count": 10,
  "analysis_count": 1,
  "analysis_provider": "deterministic",
  "analysis_model": "rule+agentic-evidence",
  "external_llm_result_persisted": false
}
```

The OpenCode LLM produced `OPENCODE_GWAP_E2E_PASS` and a final diagnosis with `CONFIRMED`, `PROBABLE`, and missing-information/`UNKNOWN` sections citing real `EVT-*` IDs. It identified the synthetic invalid channel 165 in a 2.4 GHz profile and the resulting hostapd/driver failure chain.

## Current product boundary

The tested version can genuinely use OpenCode as the external final reasoner over Runtime evidence. It does **not** yet import OpenCode as an in-process model provider, and there is no tool/API to submit the OpenCode final text back as the authoritative platform `AnalysisRun`. The persisted run is intentionally the deterministic platform baseline; the OpenCode result remains in the OpenCode session.

Additional release limitations:

1. The default installer is single-instance only and can overwrite an existing global `gw-ap-debug` Skill/MCP installation; side-by-side main/vNext installer namespacing is still required.
2. Claude Code was not installed/authenticated in this validation, so only its static config/contract was checked.
3. Real company logs must not be sent to a free/external model without explicit consent and an approved endpoint policy. This validation used synthetic data only.
4. Evidence items are individually bounded, but a total MCP response-size budget is still advisable before very large production cases.

## Safe reproduction

Without changing global OpenCode configuration:

```bat
scripts\test_opencode_integration.bat
```

If Python auto-discovery does not select the vNext environment:

```bat
scripts\test_opencode_integration.bat -PythonExe D:\path\to\venv\Scripts\python.exe -Model opencode/deepseek-v4-flash-free
```

Do not run `scripts\install_agent_runtime.bat` beside an installed main runtime until the installer, Skill, MCP server, CLI and runtime-data names are namespaced.
