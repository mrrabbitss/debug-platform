# MCP tools

The MCP surface intentionally stays small:

- `debug_status` — runtime/agent-mode status.
- `debug_create_case` — create case; write approval.
- `debug_ingest` — upload + parse log; write approval.
- `debug_wait_job` — wait for background job.
- `debug_inspect` — bounded structured event inspection.
- `debug_search` — bounded Agentic Search over knowledge/graphs/memory/code/commit.
- `debug_evidence_bundle` — preferred External Agent reasoning payload.
- `debug_attach_workspace` — read-only same-machine workspace registration/index; write approval.
- `debug_code_context` — bounded Code Graph search.
- `debug_diagnose` — persisted platform analysis; in External mode no second Chat LLM; write approval.
- `debug_generate_report` — generate and persist a report; write approval.
- `debug_open_ui` — optional localhost Web UI.

MCP is only an adapter over the existing local HTTP runtime and typed Tool Registry. It owns no diagnosis logic.
