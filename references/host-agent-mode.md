# Host-agent mode

Host-agent mode uses the model already selected by the current OpenCode, Claude Code, or Codex CLI session. The Python client never invokes a nested CLI and never reads a host model credential.

## Trust split

- The local backend owns archive validation, parsing, deterministic Triage, method compilation, exact log search, and the baseline diagnosis.
- The current CLI model owns hypothesis formation and synthesis.
- The host-result validator owns the evidence allowlist, context hash, fault-tree completeness, method binding, citation validity, rank normalization, and atomic publication.

The CLI model is still commonly remote. `--approve-host-model-egress` therefore authorizes the active diagnostic method bodies and bounded, redacted case context/evidence to enter that model's context. It does not authorize raw-log disclosure or the backend Chat model.

## Generated session files

`run --mode host-agent` creates:

- `host-agent-context.json`: immutable case snapshot, source-file hash inventory, hash-verified/redacted method bodies, baseline, fault-tree items, and context hash;
- `host-agent-instructions.md`: absolute commands for continuing the session;
- `host-result-template.json`: exact output envelope to copy and fill;
- `host-session-state.json`: one atomically replaced, authenticated state containing tool trace and stable host-search evidence.

Do not directly edit the session state. Every tool entry is HMAC chained to its predecessor and contains fingerprints for the exact returned evidence. A signed external anchor detects bundle rollback and is updated only after the bundle state is committed.

To continue from an existing case/analysis instead of creating a new case, export it with explicit host-model approval:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" result --case-id CASE-... --mode host-agent --approve-host-model-egress --output-dir "/path/to/run"
```

The v3 context verifies the exported case, analysis, analysis record, evidence, Agent run (when present), and the exact Triage JSON file set before every host command. A changed, added, removed, or mixed source file invalidates the bundle. Export directories must be new or empty so an old case cannot contribute stale Triage evidence.

Before the context is written, every method in the backend analysis catalog is reloaded and checked against its recorded SHA-256. Local methods come from the external active method directory; managed knowledge methods come from the read-only knowledge detail API. Missing or changed content aborts host-agent export instead of giving the CLI model a partial method set.

## Read-only tools

Inspect context and progress:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-context --bundle "/path/to/run"
```

Do not directly read `host-agent-context.json`; use this compact command instead.
That file is an internal validator catalog and repeats the method payload returned
by the recorded method-read interface; loading both wastes CLI context.

Read every hydrated method through the recorded interface. This is the first round-1 tool call:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-read-methods --bundle "/path/to/run" --round 1 --all
```

Search existing redacted evidence:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-search-evidence --bundle "/path/to/run" --round 1 --query "heartbeat timeout" --fault-tree-item-id FTITEM-...
```

Search the parsed logs. Returned lines receive stable `HOSTLOG-*` IDs and are redacted before entering the host ledger:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-search-log --bundle "/path/to/run" --round 2 --query "TestLinkOK failed" --fault-tree-item-id FTITEM-...
```

After at least one node-bound search, an overall hypothesis may use one tightly
bounded search for a configuration/status field outside the compiled tree. The
CLI intentionally accepts no fault-tree item ID:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-search-hypothesis-log --bundle "/path/to/run" --round 3 --query "exact-field-name" --limit 20
```

The call is limited to one per session, a 100-character model-supplied field
query, and 20 redacted matching lines. For a compound identifier, the local
engine may add at most one conservative generic suffix such as `enable`,
`status`, `state`, `mode`, or `channel`; every attempted literal variant is
recorded in the authenticated trace. The 20-line limit applies across all
variants. Its evidence is `hypothesis_only`: it may support an
overall fact or hypothesis but cannot mark a node attempted, cannot be returned
by a node-bound evidence-ledger search, and cannot be cited by a fault-tree
conclusion. It still counts toward the four-call round budget and authenticated
trace. It does not authorize a direct read of the raw artifact.

Read allowlisted evidence in detail:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-get-evidence --bundle "/path/to/run" --round 2 --evidence-id HOSTLOG-... --fault-tree-item-id FTITEM-...
```

`host-get-evidence` does not by itself mark a node attempted. A node needs a recorded search call. Search queries must overlap that node's compiled evidence hints; one search may bind at most four relevant nodes. `--round` is required, rounds cannot move backward or skip a number, and each reasoning round permits at most four host tool calls. The method read counts toward round 1.

The wrapper preflights round, tool, query, result-count, and dynamic-evidence limits before starting the backend or searching logs. Trace and dynamic evidence are then committed together in the single state file. A zero-result search is a valid attempt for `INSUFFICIENT_EVIDENCE`, but it cannot support `SUPPORTED` or `EXCLUDED`.

## Result envelope

Keep the outer fields from the template unchanged:

```json
{
  "schema": "gw-ap-debug-host-diagnosis/v1",
  "context_sha256": "copy-exactly-from-context",
  "diagnosis": {
    "summary": "...",
    "confirmed_facts": [
      {"statement": "...", "evidence_ids": ["HOSTLOG-..."]}
    ],
    "hypotheses": [
      {
        "title": "...",
        "description": "...",
        "supporting_evidence": ["HOSTLOG-..."],
        "contradicting_evidence": [],
        "confidence_score": 0.75,
        "priority": "P1",
        "needs_human_review": true
      }
    ],
    "recommended_actions": [
      {
        "priority": "P1",
        "action": "...",
        "reason": "...",
        "expected_result": "..."
      }
    ],
    "missing_information": [],
    "suspected_modules": [],
    "limitations": [],
    "fault_tree_conclusions": [
      {
        "item_id": "FTITEM-...",
        "method_document_id": "LOCALDOC-...",
        "status": "SUPPORTED",
        "conclusion": "...",
        "evidence_ids": ["HOSTLOG-..."],
        "next_action": ""
      }
    ]
  }
}
```

Rules:

- Collection/inventory commands prove only that a collection or query action ran. Command verbs such as start or restart do not establish device, service, radio, or host startup; require a timestamped runtime event or explicit lifecycle marker.
- After required node searches, use at most one `host-search-hypothesis-log` when the leading hypothesis implies a configuration/status field outside the compiled tree. Check enablement, administrative status, mode, channel, or state for disabled, zero, negative, or conflicting values. A compound field may receive one signed conservative suffix variant; do not read the raw artifact directly.
- Keep every narrative field diagnosis-only. Do not copy baseline execution mode, synthesis status, Agent status, finish reason, or stop reason into model-authored diagnosis fields; `host-finalize` supplies the authoritative values.
- Keep opaque case/evidence/method/fault-tree/check IDs only in their structured fields. Human-facing narrative must rely on the renderer's `file:Lline` or document-title labels. The host renderer may show case/run/triage/artifact IDs in its execution-metadata sections for audit traceability; do not treat those operational IDs as evidence citations.
- Include at least one hypothesis.
- Give every hypothesis at least one supporting case-evidence ID.
- Reproduce every required fault-tree item exactly once.
- Use only `SUPPORTED`, `EXCLUDED`, or `INSUFFICIENT_EVIDENCE` for final node status.
- `SUPPORTED` and `EXCLUDED` require case evidence.
- Every fault-tree evidence citation must have been returned by a search bound to that same item.
- `INSUFFICIENT_EVIDENCE` requires a concrete next collection action.
- Diagnostic method document IDs are provenance, not proof of a fact, and cannot be cited as case evidence.
- The validator ignores model-supplied rank and confidence labels, sorts by numeric confidence, and recalculates both.

Finalize only after `host-context` reports `can_finalize: true`:

```text
python "<SKILL_DIR>/scripts/debug_platform_skill.py" host-finalize --bundle "/path/to/run" --input "/path/to/run/host-diagnosis.json"
```

Invalid drafts do not replace a previously validated result. Successful finalization authenticates the exact trace head and output hashes, marks the session final, and rejects later tool calls. Repeating finalization with the same unchanged draft is idempotent; a different draft or modified final output is rejected.

After finalization, use `host-validation.json` for attempted/concluded/total and terminal-status counts. `INSUFFICIENT_EVIDENCE` is a concluded terminal node, not an unconcluded node.

## Local validation key boundary

The 32-byte host validation key and rollback anchor live under `<state>/host-validation-keys`, outside the Skill and run bundle. Keep that external state until finalization; losing or moving it makes the unfinished bundle fail closed. The final validated JSON/Markdown remain portable.

This mechanism protects against ordinary bundle edits, fabricated model output, accidental rollback, and prompt-driven attempts to write ledger JSON. It is not an isolation boundary against a process that already has arbitrary read/write access as the same operating-system user: such a process could also seek the external key. Resisting a deliberately malicious same-user agent would require moving signing and session state into an isolated service or OS credential boundary, which is outside this MVP.
