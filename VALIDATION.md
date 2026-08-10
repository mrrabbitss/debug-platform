# Validation Record

Last unified Full validation: 2026-08-10 on Windows 11.

Latest focused Agent Runtime vNext validation: 2026-08-10 on Windows. The detailed
machine/human-readable record is in `AGENT_RUNTIME_VNEXT_VALIDATION.json` and
`AGENT_RUNTIME_VNEXT_VALIDATION.md`.

## CodeAgent/OpenCode compatibility follow-up

- Portable Skill packaging, isolated Runtime setup/start/stop and compatibility
  probe scripts: PASS under Windows PowerShell 5.1.
- Company target-machine proxy reproduction: CONFIRMED. With `HTTP_PROXY`,
  `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` all unset, HTTPX `trust_env=True`
  reached the HIS proxy and returned 504, while the same vNext Python with
  `trust_env=False` reached Uvicorn and returned `200 {"status":"ok"}`.
- Loopback proxy correction: PASS. `RuntimeClient` now disables environment/
  Windows system proxy discovery only for localhost, `.localhost`, IPv4
  loopback and `::1`; non-loopback endpoints preserve the previous policy.
  Generated MCP environments also include `NO_PROXY=127.0.0.1,localhost,::1`.
- The shareable dual-entry diagnostic collector: PASS. An isolated fixture with
  a root `nga` launcher and `bin/codeagent.exe` verified automatic discovery,
  separate probes, two direct 12-tool MCP protocol handshakes, a read-only
  `debug_status` Runtime data-plane call, loopback-only Runtime enforcement and
  removal of usernames, absolute repository paths and secret patterns from the
  shareable report. FULL status now requires the data-plane call to pass.
- The zero-argument company launcher contract: PASS under Windows PowerShell
  5.1. It fixes the tested `nga` + CodeArts Skill + OpenCode V1 project MCP
  profile, repairs missing/stale assets, checks the real RuntimeClient path and
  launches the TUI without changing main or the global company proxy.
- The generated OpenCode project configuration reached `FULL_SKILL_MCP` with
  OpenCode 1.18.15: project Skill discovered, Runtime healthy, MCP shown as
  connected, stdio `initialize`/`tools/list` handshake successful and all 12
  required `debug_*` tools present.
- Real OpenCode 1.18.15 + `opencode/deepseek-v4-flash-free` end-to-end
  diagnosis: PASS. The model loaded the Skill, invoked six diagnostic MCP tools
  and cited real `EVT-*` evidence in its conclusion.
- The same setup script emits either Huawei CodeArts native `mcp` or
  Claude-compatible `mcpServers` configuration and installs the project Skill
  under `.codeartsdoer/skills`. The proprietary target reported the Skill and
  MCP as connected before this correction; final authenticated-model tool-use
  acceptance must be repeated on that machine after pulling this fix.
- MCP uses stdio and has no TCP port. The separate GW/AP Runtime data plane uses
  loopback port 8766 by default; the agent application's own serve/UI port is
  not used for this integration.

## Agent Runtime vNext follow-up

- Focused Agent Runtime, CLI/MCP and startup tests: PASS, 39 passed.
- Complete backend regression: PASS, 179 passed / 1 skipped / 0 failed.
- Backend line coverage: 75.48%, above the enforced 75% quality gate.
- Repository Harness: PASS, 13/13 checks and 25 Workflow/OpenAPI operations.
- Architecture and PowerShell/JSON syntax gates: PASS.
- Native Windows multi-process Agent Runtime E2E: PASS with exit code 0.
- Real OpenCode 1.18.15 + free DeepSeek model + Skill + MCP diagnostic workflow:
  PASS using synthetic data. OpenCode called all six required diagnostic tools
  and cited real `EVT-*` evidence in its final reasoning.
- The default installer was not run and main was not modified. Side-by-side
  installer namespacing and external-result submission remain explicit limitations.
- Python lock verification now tolerates Git-for-Windows CRLF checkout without
  weakening content comparison. Current Python, frontend and extension production
  dependency audits passed with no known vulnerabilities.

## Current local regression result

- Unified `scripts\validate_all.bat Full`: all 19 stages passed. The run produced
  a machine-readable summary and per-step logs under the Git-ignored
  `artifacts\validation` directory.
- Backend tests: 179 passed and 1 external-service test skipped locally.
- Backend line coverage: 75.48%, above the enforced 75% quality gate.
- Golden Dataset: all 9 evaluators passed for parser output, document curation,
  Code Graph, Commit Graph, memory isolation, hybrid RAG and bounded Agentic Search.
- Browser E2E: 1 complete Edge scenario passed in an isolated runtime. It covered
  TXT, HTML, DOCX and PDF upload/preview, draft generation, conversational
  correction, human confirmation, trace inspection and safe replay. Browser
  console errors and warnings: zero.
- Repository Harness: all 13 contracts passed across 19 required files, 21
  Markdown files, dependency-update targets and 25 allowlisted Workflow operations.
- Architecture checks: 80 Python files and 11 Vue files passed file-size,
  dependency-boundary, required-module and complexity ratchets. The highest
  measured Python cyclomatic complexity was 47, below the limit of 50.
- Backend dependency consistency, lock synchronization, Ruff and Python
  compilation: passed.
- Vue TypeScript check, production Vite build and VS Code extension TypeScript
  compile: passed.
- Python, frontend and VS Code extension production dependency audits: no known
  vulnerabilities. The lock refresh includes `pypdf 6.15.0`, `Mako 1.4.1` and a
  non-vulnerable `nanoid` resolution.
- Fresh isolated SQLite schema upgraded through Alembic revisions 0001-0012.
- Native Agent Skill/MCP/CLI E2E, isolated frontend/backend runtime smoke and
  `scripts\doctor_local.bat`: passed.

Final local run artifacts:
`artifacts\validation\20260810-154515-full\summary.json`.

The GitHub CI result is intentionally not recorded as a local fact here. After
each push, the pull request checks are the authoritative Linux, Windows,
PostgreSQL/Qdrant and Docker validation record.

## Golden quality gates

The synthetic, redistributable corpus is stored in
`sample_data\golden_incident`. Its manifest pins file hashes and expected
answers. The gate rejects regressions in:

- Huawei collectDebuginfo event type, timestamp and source-line extraction;
- curated Markdown sections, evidence citations and forbidden hallucinations;
- Code Graph call, inheritance and reference edges;
- Commit Graph query-to-commit-to-file-to-symbol paths;
- reusable memory selection and cross-case contamination;
- RAG Recall@K, MRR, NDCG and citation accuracy;
- Agentic Search module selection, hop/step budgets and explicit stop reasons;
- typed bounded-agent fallback, approval and resource-budget behavior.

Run it independently with `scripts\run_golden_evals.bat`. Golden thresholds in
`harness\quality_gates.json` and test failures are CI-blocking.

## Runtime, observability and browser checks

- Agent runs and trace events persist `run_id`, `case_id`, stage/tool, model and
  configuration identity, Prompt version, redacted input/output hashes, token
  counts, cost, latency, retry count, evidence IDs, stop reason and approval state.
- Trace storage excludes unprocessed company-log bodies. The frontend trace
  viewer exposes only redacted metadata and supports a read-only replay path.
- Background jobs use atomic leases, heartbeat, idempotency keys, deadlines,
  bounded retries with backoff, dead-letter state and resource quotas.
- DOCX, PDF and HTML extraction runs in a separate constrained process with a
  wall-clock timeout and platform-specific CPU/memory controls where supported.
- The reusable E2E command starts an isolated Fake OpenAI-compatible service,
  backend, frontend, temporary database and storage directories, then tears all
  processes down. Run it with `scripts\run_browser_e2e.bat`.
- Fake model fixtures cover chat, embedding, reranking, timeout, rate-limit,
  malformed-response and interrupted-response behavior without external keys.

## Agentic execution status

The production Agentic Search path remains the deterministic, explainable
retrieval baseline. The repository now also contains a bounded typed-agent
runtime with:

- a typed Tool Registry and per-role allowlists;
- read-only-by-default tools and explicit approval for writes;
- step, hop, token, cost and wall-clock limits;
- retry, exponential backoff, circuit breaking, cancellation and explicit stop reasons;
- deterministic fallback when planning or a tool fails;
- privacy-preserving trajectory recording, scoring inputs and safe replay.

The bounded runtime is beta infrastructure and is not silently enabled as the
default production planner. Remaining P2 work is tracked in `CAPABILITIES.md`:
a cross-task DAG/approval/stagnation control plane, and automatic conversion of
human review feedback into a rule, test, document or evaluation case.

## Functional regression coverage

- Extensionless upload normalization to `.txt` and Huawei collectDebuginfo detection;
- streaming parse, sparse line index, arbitrary-line reads, event pagination,
  facets, timeline data and event-to-source navigation;
- atomic parser, graph, vector and report publication with rollback-safe failures;
- layered knowledge taxonomy, editing, vector reindexing and hybrid retrieval;
- persistent LLM-assisted folder curation with TXT/MD/HTML/DOCX/PDF extraction,
  source citations, immutable revisions, correction chat and human-confirmed drafts;
- Code Graph, Commit Graph, three-class memory and Agentic Search integration;
- encrypted model credentials, endpoint policy, egress audit and local/API model switching;
- case ownership/membership, role enforcement, token lifecycle and storage cleanup;
- backup verification, Docker/Compose definitions and runtime health/readiness checks.

## Environment-dependent checks

Docker is not installed on this validation computer, so `External`, local
PostgreSQL/Qdrant containers and local Docker image builds were not run here.
GitHub CI contains service-container and image-build jobs that provide those
checks after a push.

External Qwen, GLM and BGE endpoints were not called because approved credentials
were not supplied. Adapter paths are covered by the Fake OpenAI-compatible
service and mocked tests. Run the model-profile test action against an approved
company endpoint before production use.

Use `scripts\validate_all.bat Fast` during development,
`scripts\validate_all.bat Full` before publishing, and
`scripts\validate_all.bat External` on a Docker-capable workstation.
