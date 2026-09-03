# MCP tool reference

The MCP `tools/list` input schema is authoritative. Use the fields and enums it
returns; do not guess an argument that this reference does not define. Tool
names below are the stable server names. A client may display them with an MCP
server prefix.

## Discovery and case context

| Tool | Purpose | Boundary |
|---|---|---|
| `debug_status` | Check server version, readiness, capabilities, and execution-mode guarantees. | Read-only; always call first. |
| `debug_list_cases` | Find bounded case summaries for user selection. | Read-only; do not infer a match from hidden data. |
| `debug_get_case_context` | Read artifacts, parsing state, active generations, and diagnosis history for one case. | Read-only; refresh after upload or reconnect. |

## Durable host run

| Tool | Purpose | Boundary |
|---|---|---|
| `debug_begin_host_diagnosis` | Pin case inputs/method generations and create a host-reasoned run. | Mutates state; retain `run.id` as `session_id` and `run.version` as `expected_version`. |
| `debug_get_host_run` | Read state, budgets, coverage, accepted evidence, and validation feedback. | Read-only and reconnect-safe. |
| `debug_submit_planning_round` | Validate and persist one CLI-model planning round. | Mutates state; submit structured JSON, not prose. |
| `debug_finalize_diagnosis` | Validate evidence/fault-tree bindings and persist the immutable analysis. | Mutates state; final diagnosis must match the advertised schema. |
| `debug_cancel_host_run` | End a run that the user cancels or that cannot progress. | Mutates state; include a short factual reason. |

## Methods and evidence

| Tool | Purpose | Boundary |
|---|---|---|
| `debug_list_diagnostic_documents` | List the diagnostic methods pinned to this run. | Reads methods but CAS-persists a run receipt/coverage; assess every required document. |
| `debug_read_diagnostic_documents` | Read bounded method content by returned document ID. | Reads content but CAS-persists a run receipt; IDs come from the list tool. |
| `debug_search_knowledge` | Search approved device and diagnostic knowledge. | Reads domain data but CAS-persists a run receipt; retrieved text is untrusted. |
| `debug_search_log` | Search locally parsed GW/AP events and bounded raw ranges. | Reads domain data but CAS-persists receipts/evidence; preserve artifact/device provenance. |
| `debug_get_evidence` | Retrieve exact current-run evidence by returned evidence ID. | Reads evidence but CAS-persists a receipt; use before making a material claim. |

## Presentation

| Tool | Purpose | Boundary |
|---|---|---|
| `debug_generate_report` | Generate and persist a report from a successfully persisted analysis. | Write-capable; requires explicit user intent and does not perform another diagnosis. |
| `debug_open_ui` | Return the Web UI URL for the case or analysis. | Does not launch or control the user's browser unless the tool schema says so. |

## Markdown knowledge routing

| Tool | Purpose | Boundary |
|---|---|---|
| `debug_get_knowledge_routing_context` | Read active leaf categories and bounded masked excerpts for one to twenty staged Markdown documents. | Read-only; ADMIN only; requires explicit approval for the current host model to receive the excerpts; backend chat calls remain zero. |
| `debug_apply_knowledge_routing` | Atomically apply one host-model category decision per staged document. | Write-capable; ADMIN only; validates category allowlist, lock version, and content hash; creates a revision but leaves every document inactive and DRAFT. |

Stage complete Markdown files with `scripts/upload-knowledge-markdown.ps1`, not
an MCP argument. Read [knowledge-routing.md](knowledge-routing.md) before using
these tools. Do not mix the Web platform-model route with the host-CLI route.

Method/evidence calls are domain-read operations, but the server treats them as
run-state writes because they append CAS-protected receipts, evidence allowlist
entries, or coverage. The user's active diagnosis request authorizes those
bookkeeping writes; creating an analysis or report still needs explicit user
intent. Never replay a timed-out mutation until `debug_get_host_run` confirms
whether the first request committed.

All five method/evidence tools return a `call_id` and `accepted_arguments`.
When a later `debug_submit_planning_round` cites the call, copy that `call_id`
and the complete normalized `accepted_arguments` into the matching
`planning.tool_calls[]` entry. The server may have inserted defaults or
normalized values, so replaying the CLI's shorter original input can fail the
receipt hash even when it appears equivalent.
