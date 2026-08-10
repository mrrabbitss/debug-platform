# Diagnosis workflow

## Normal external-agent flow

`status → create case → ingest/parse → attach/index workspace (optional) → evidence bundle → drill-down → final reasoning → code edit/test → optional Web/report`

Use `debug_diagnose` only when a persisted deterministic platform analysis/report is useful. With `AGENT_MODE=external`, that operation skips platform Chat-LLM synthesis.

## When to use the Web UI

Use `/ui/` for large-log navigation, event/timeline browsing, Code/Commit/Domain Graph visualization, Agent Trace, knowledge governance, retrieval evaluation, model configuration and report inspection. Routine reasoning stays in the coding-agent CLI.
