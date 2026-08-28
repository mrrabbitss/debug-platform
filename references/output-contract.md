# Comprehensive diagnosis output contract

The portable diagnosis should preserve the Debug Platform's structured result rather than inventing a new result schema.

Recommended presentation order:

1. Summary and execution status.
2. Ranked hypotheses / root-cause candidates.
3. Fault-tree coverage and conclusions.
4. Recommended actions.
5. Missing information and limitations.
6. Confirmed facts with human-readable evidence locations.

A `SUPPORTED` or high-confidence hypothesis is not automatically a confirmed root cause unless the underlying evidence and final synthesis support that claim. `INSUFFICIENT_EVIDENCE` is a valid terminal diagnostic conclusion.

## Common bundle files

- `manifest.json`: schema/version, case, artifacts, jobs, execution mode, and validation state;
- `case.json`: redacted case snapshot;
- `analysis.json`: redacted deterministic or backend analysis;
- `analysis_record.json`: platform AnalysisRun metadata;
- `evidence.json`: persisted redacted evidence with provenance;
- `agent_run.json`: content-safe runtime and stop metadata;
- `triage/*.json`: bucket pages plus bounded exact occurrences for every match;
- `diagnosis.md`: baseline human-readable report;
- `report.html`: optional local platform report preview.

Host-agent mode also adds:

- `host-agent-context.json` with immutable source-file hashes and hash-verified/redacted method bodies, and `host-agent-instructions.md`;
- `host-result-template.json`;
- authenticated `host-session-state.json` containing the tool trace and dynamic host evidence; its key/rollback anchor stay in external state and are not exported into the bundle;
- `host-diagnosis.validated.json`, `host-validation.json`, and `host-diagnosis.md` after successful finalization.

Only `host-diagnosis.md` backed by `host-validation.json` with `accepted: true` and the final `manifest.json` commit marker `host_agent.status: VALIDATED` is a host-model validated report. `diagnosis.md` remains the deterministic/backend baseline.

Model-authored narrative and evidence citations use readable source locations and document titles. Structured JSON bindings retain authenticated internal IDs, and host-owned execution sections may display case/run/triage/artifact IDs for audit traceability; those operational identifiers are not diagnosis evidence.

Execution engine, final synthesis mode, Agent status, finish reason, and stop reason are finalizer-owned metadata. Model-authored diagnosis fields must not copy those values from `analysis.json` or `agent_run.json`; this prevents a valid host-agent report from repeating stale deterministic-baseline status in its narrative.

Fault-tree coverage uses `total`, `attempted`, `concluded`, `complete`, and `status_counts`. Every `SUPPORTED`, `EXCLUDED`, or `INSUFFICIENT_EVIDENCE` terminal item counts as concluded. `host-validation.json` publishes the same authoritative totals and status counts for CLI reporting, plus `fault_tree_item_rounds` derived from the authenticated tool trace. Each finalized planning item carries the corresponding `last_round`; the model cannot supply or rewrite it. Search metrics distinguish node-bound searches from the optional single `hypothesis_only` raw-log search; the latter cannot change node coverage or be cited by a node. That supplemental interface accepts one model-supplied field, may derive at most one conservative generic suffix from a compound identifier, records every literal variant in its authenticated trace, and returns at most 20 redacted lines across all variants. Triage summaries distinguish cluster counts, exact occurrence counts, and unmatched event counts.

The top-level validated `diagnostic_planning` object is rebuilt from the authenticated host trace. Any backend/deterministic planner rounds, tools, usage, and stop metadata remain only under `deterministic_baseline` as historical context and are not mixed into the authoritative host plan.
