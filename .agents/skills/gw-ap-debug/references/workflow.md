# Host diagnosis workflow

## Contents

- Case preparation and large-file upload
- Host-run lifecycle
- Evidence loop
- Completion and recovery

## Case preparation and large-file upload

Use `debug_list_cases` and `debug_get_case_context` to reuse the intended case.
Do not choose merely by the most recent timestamp when several cases match.

The remote MCP control plane must not carry an archive or a full raw log. When
the file exists on the CLI computer, run the helper bundled with this Skill:

```powershell
$skillDirectory = Join-Path $env:USERPROFILE '.claude\skills\gw-ap-debug'
& (Join-Path $skillDirectory 'scripts\upload-debug-artifact.ps1') `
  -CaseId CASE_ID `
  -Path 'D:\logs\collectDebuginfo.tar.gz' `
  -SourceDeviceType AP `
  -SourceDeviceRole PRIMARY
```

For Codex, the default user-level directory is
`%USERPROFILE%\.agents\skills\gw-ap-debug`; a repository-discovered agent should
use the absolute directory of the Skill it actually loaded.

The helper derives the REST data-plane URL from `DEBUGPLATFORM_MCP_URL`, unless
`DEBUGPLATFORM_API_BASE_URL` or `-ApiBaseUrl` is supplied. It streams a
multipart upload outside the model context and starts parsing by default. It
prints structured JSON containing the artifact and parse-job results. Never
paste the bearer token into an argument, transcript, or report; the helper reads
`DEBUGPLATFORM_MCP_TOKEN` from the process environment.

If the helper is unavailable, ask the user to upload through the Web UI. Do not
replace it with Base64 in an MCP call.

## Host-run lifecycle

The durable backend run, rather than the transport connection, owns diagnosis
state. Keep the returned `run.id` as `session_id`. Every mutating tool requires
the latest returned run `version` as `expected_version`. On a version conflict,
call `debug_get_host_run` with `session_id` and reconcile before retrying.

Expected lifecycle:

```text
CREATED -> METHODS_READ -> SEARCHING -> DRAFT_SUBMITTED
        -> VALIDATED -> COMPLETED
        -> REJECTED -> DRAFT_SUBMITTED
        -> CANCELLED / FAILED / EXPIRED
```

The server's live contract is authoritative. A budget stop is recorded in run
metadata rather than invented as a lifecycle status. Do not restart a run merely
because the CLI or MCP transport reconnects.

## Evidence loop

1. Read all method documents required by the run.
2. In the CLI model's local reasoning, form a bounded set of checks and identify
   the evidence gaps that this round will address. This is not yet a server
   submission or pre-approval step.
3. Call the necessary method/evidence tools within the advertised budget. Each
   call returns a `call_id` and server-normalized `accepted_arguments` after its
   receipt has been persisted. Advance `expected_version` from the returned
   `run.version` before the next state-changing call.
4. Build the round's `planning.tool_calls` from those completed calls. Reuse
   each `call_id` and copy its `accepted_arguments` verbatim into `arguments`;
   an original request with omitted defaults will not match the receipt.
5. Submit the planning round once, including the checks, observations,
   evidence-gated assessments, and completed tool receipts. Use
   `debug_get_evidence` before submission when an exact supporting or
   contradicting excerpt is needed.
6. Re-check run coverage and either form the next local plan to close remaining
   gaps or finalize.

Keep host-run reads compact. The normal progress/finalization read uses
`include_evidence=false` and `include_planning_payloads=false`; the coverage
ledger and receipt metadata are enough to choose the next step. Retrieve exact
needed excerpts with bounded `debug_get_evidence` calls. Request the complete
evidence or planning history only once when recovering state that cannot be
reconstructed from the compact response. Repeated full-history reads can add
hundreds of thousands of input tokens without improving the diagnosis.

`debug_submit_planning_round` does not accept a proposed search and then execute
it. The server requires every referenced tool call to have an existing receipt,
so submitting before the corresponding method/evidence calls is an ordering
error.

The backend enforces the round, tool-call, elapsed-time, evidence, and output
budgets. The CLI model must honor a budget stop and report incomplete coverage.
It must not claim knowledge of its own exact token consumption unless the CLI
provides that metric and the server explicitly accepts host-reported metadata.

## Completion and recovery

`debug_finalize_diagnosis` validates the payload against the run's evidence
allowlist and fault-tree snapshot. A successful result is persisted as an
immutable platform analysis that the existing Web UI can render.

For recoverable validation errors, inspect the named field, refresh the host run
when necessary, and submit a corrected payload. Do not repeatedly submit an
unchanged payload. For authentication, missing-case, method-version, or artifact
drift errors, stop and explain the operator action required.

After success, `debug_generate_report` may render the persisted analysis and
`debug_open_ui` may return its browser URL. These are presentation steps, not
additional reasoning passes.
