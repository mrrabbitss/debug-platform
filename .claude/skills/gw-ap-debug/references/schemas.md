# Planning and diagnosis payloads

The JSON Schema advertised by each MCP tool is authoritative. This reference
explains the stable semantic contract and is not permission to add fields that
the live schema rejects.

## Planning round

Call `debug_submit_planning_round` with the live-schema wrapper fields
`session_id`, `expected_version`, and `round_number`. Put the following
structured object under its `planning` field:

```json
{
  "read_document_ids": ["METHOD_ID"],
  "method_assessments": [
    {
      "method_document_id": "METHOD_ID",
      "relevance": "RELEVANT",
      "rationale": "Observed signal and why the method applies",
      "matched_signals": ["bounded signal"]
    }
  ],
  "hypotheses": ["Testable hypothesis"],
  "checks": [
    {
      "check_id": "check-1",
      "method_document_id": "METHOD_ID",
      "description": "What to check",
      "evidence_needed": "Evidence that would support or contradict it",
      "completion_rule": "When this check is complete",
      "fault_tree_item_ids": ["FAULT_ITEM_ID"]
    }
  ],
  "search_queries": [],
  "tool_calls": [
    {
      "call_id": "MCALL-SERVER-ISSUED",
      "tool_name": "search_log",
      "arguments": {
        "keywords": ["bounded signal"],
        "pattern_ids": [],
        "artifact_ids": [],
        "method_document_ids": ["METHOD_ID"],
        "top_k": 20
      },
      "method_document_ids": ["METHOD_ID"],
      "rationale": "Test the bounded check against current-case logs",
      "fault_tree_item_ids": ["FAULT_ITEM_ID"]
    }
  ],
  "evidence_gaps": ["Unresolved observation"],
  "fault_tree_assessments": [
    {
      "item_id": "FAULT_ITEM_ID",
      "method_document_id": "FAULT_TREE_METHOD_ID",
      "status": "INSUFFICIENT_EVIDENCE",
      "rationale": "What the current evidence permits",
      "evidence_ids": [],
      "next_action": "Evidence needed to resolve this item"
    }
  ],
  "continue_analysis": true,
  "stop_reason": "MORE_EVIDENCE_NEEDED"
}
```

Use only method, pattern, fault-tree, and evidence IDs returned for the current
run. A round may contain at most the number of queries and tool calls exposed by
the current budget. The server may normalize harmless formatting but rejects
unknown IDs, unread required methods, unsupported tools, or invalid coverage
bindings.

Before submitting a round, execute the method/evidence calls selected by the
CLI model's local check plan. Each tool response includes `call_id` and
`accepted_arguments`. In `planning.tool_calls`, use that exact `call_id` and
copy the complete `accepted_arguments` object verbatim into `arguments`; do not
reuse the original request object because the server may have normalized values
or supplied defaults. Use the planning-schema tool name corresponding to the
  receipt (for example, `search_log` for `debug_search_log`). A round referencing
  a call that has not already produced a server receipt is rejected.

Each `fault_tree_assessments[]` entry must include both `item_id` and the
owning `method_document_id` exactly as returned by the current run. Use the
field name `status` (not `assessment` or `conclusion`). Omitting either ID
causes the entry to be ignored during normalization and leaves that item
pending. `SUPPORTED` and `EXCLUDED` require current-run evidence IDs;
`INSUFFICIENT_EVIDENCE` may have none but must state the missing evidence in
`next_action`.

## Final diagnosis

Call `debug_finalize_diagnosis` with the live-schema `session_id` and
`expected_version` fields. Put a diagnosis object with this stable shape under
its `diagnosis` field:

```json
{
  "summary": "Concise evidence-grounded outcome",
  "confirmed_facts": [
    {"statement": "Direct observation", "evidence_ids": ["EVIDENCE_ID"]}
  ],
  "hypotheses": [
    {
      "rank": 1,
      "title": "Candidate cause",
      "description": "Mechanism and scope",
      "supporting_evidence": ["EVIDENCE_ID"],
      "contradicting_evidence": [],
      "confidence_score": 0.7,
      "confidence_level": "MEDIUM",
      "priority": "P1",
      "needs_human_review": true,
      "event_code": null
    }
  ],
  "recommended_actions": [
    {
      "priority": "P1",
      "action": "Bounded next action",
      "reason": "Why it follows from the evidence",
      "expected_result": "Observable result"
    }
  ],
  "missing_information": [],
  "suspected_modules": [],
  "limitations": [],
  "fault_tree_conclusions": [
    {
      "item_id": "FAULT_ITEM_ID",
      "method_document_id": "METHOD_ID",
      "status": "INSUFFICIENT_EVIDENCE",
      "conclusion": "What the current evidence permits",
      "evidence_ids": [],
      "next_action": "Evidence needed to resolve the branch"
    }
  ]
}
```

Allowed values:

- `confidence_level`: `LOW`, `MEDIUM`, `HIGH`. The server recomputes it from
  `confidence_score` and re-ranks hypotheses.
- `priority`: `P0`, `P1`, `P2`, `P3`, or `UNKNOWN`.
- Fault-tree `status`: `SUPPORTED`, `EXCLUDED`, or
  `INSUFFICIENT_EVIDENCE`. Copy the evidence-gated status from the run; the CLI
  model must not upgrade it.

Every confirmed fact needs at least one evidence ID. Every hypothesis needs at
least one supporting evidence ID. `SUPPORTED` and `EXCLUDED` fault-tree items
also require evidence. When the server requires complete fault-tree coverage,
include exactly one conclusion for every required item.

## Markdown knowledge-routing decisions

The live MCP schema is authoritative. After
`debug_get_knowledge_routing_context` returns the category allowlist and
document concurrency fields, call `debug_apply_knowledge_routing` with this
stable semantic shape:

```json
{
  "document_ids": ["DOC_ID_FROM_UPLOAD_JOB"],
  "consent_host_model_data": true
}
```

Set the literal consent field only after the user has approved the current
host model/provider to receive bounded masked excerpts. The context call does
not invoke a backend model. Build the apply request from its response:

```json
{
  "decisions": [
    {
      "document_id": "DOC_ID_FROM_CONTEXT",
      "expected_lock_version": 1,
      "content_sha256": "64_lowercase_hex_characters_from_context",
      "category_id": "ACTIVE_LEAF_CATEGORY_ID_FROM_CONTEXT",
      "device_type": "AP",
      "module": "smartlink",
      "confidence": 0.86,
      "rationale": "The bounded excerpt describes AP smartlink heartbeat faults."
    }
  ],
  "client_model_claim": "current CLI and model identifier",
  "confirm_draft_update": true
}
```

There must be exactly one decision for every requested document. Copy
`expected_lock_version` and `content_sha256` without modification. Allowed
`device_type` values are `GW`, `AP`, `GENERAL`, `OTHER`, or `null`; `module` may
also be `null`. A category must be one of the returned active leaves. The
server rejects duplicate, missing, stale, or invented entries atomically. A
successful response remains DRAFT-only and does not authorize publication.
