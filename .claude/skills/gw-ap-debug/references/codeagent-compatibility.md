# Huawei CodeArts/CodeAgent compatibility

The portable integration has two independent paths:

1. **Skill + MCP** — preferred when the client can start a local stdio MCP server.
2. **Skill + CLI fallback** — use `../scripts/gwap.ps1` when the client can run an approved
   PowerShell command but its MCP configuration or tool-name prefix differs.

The Skill is instructions, not a network transport. At least one of MCP or CLI execution must be
available for the agent to obtain Runtime evidence.

## Stable identifiers

- Skill ID: `gw-ap-debug`
- Suggested MCP configuration name: `gw-ap-debug-vnext`
- MCP server-reported name: `gw-ap-debug`
- Stable raw tool names: `debug_status`, `debug_create_case`, `debug_ingest`, `debug_wait_job`,
  `debug_inspect`, `debug_search`, `debug_evidence_bundle`, `debug_attach_workspace`,
  `debug_code_context`, `debug_diagnose`, `debug_generate_report`, `debug_open_ui`

Clients may prefix or normalize tool names. For example, `debug_status` may appear as a raw name,
`gw-ap-debug-vnext_debug_status`, or a normalized equivalent. Match the stable `debug_*` suffix and
the tool description; do not treat a changed prefix as an incompatible server.

## Ports and transports

- The MCP process uses **stdio** JSON-RPC. It does not listen on a TCP port.
- `GWAP_RUNTIME_URL` points to the local FastAPI data plane. The isolated vNext default is
  `http://127.0.0.1:8766`.
- A CodeArts/OpenCode `serve` or Web UI port belongs to that coding-agent client and is unrelated to
  the Debug Runtime port.

Run `scripts/probe_codeagent_compatibility.bat` from the repository root to verify Skill discovery,
known config schemas, Runtime health, the direct MCP handshake, a read-only `debug_status` data-plane
call, raw tool names and the client MCP registration without reading credentials or log bodies.

The tested company profile with a root `nga` launcher, internal `bin/codeagent.exe`, CodeArts project
Skill and OpenCode V1 project MCP config can be started with no arguments:

```bat
scripts\start_huawei_codeagent_vnext.bat
```

The Runtime client bypasses environment and Windows system proxies only for loopback URLs. Generated
MCP environments also set `NO_PROXY=127.0.0.1,localhost,::1`; do not disable the company proxy globally,
because the coding-agent model connection may still require it.

When a company wrapper launches `nga` while the internal binary is `bin/codeagent.exe`, run the
repository-level `scripts/collect_codeagent_diagnostics.bat`. Share only its files named
`codeagent-diagnostics-shareable.*`; keep the raw per-command probe reports local.
