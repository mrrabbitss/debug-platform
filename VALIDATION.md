# Validation Record

Last validated: 2026-08-05 on Windows 11.

## Current local regression result

- Unified `scripts\validate_all.bat Full`: all 18 stages passed. The run produced
  a machine-readable summary and per-step logs under the Git-ignored
  `artifacts\validation` directory.
- Backend tests: 140 passed and 1 external-service test skipped locally.
- Backend line coverage: 76.82%, above the enforced 75% quality gate.
- Golden Dataset: all 9 evaluators passed for parser output, document curation,
  Code Graph, Commit Graph, memory isolation, hybrid RAG and bounded Agentic Search.
- Browser E2E: 1 complete Edge scenario passed in an isolated runtime. It covered
  TXT, HTML, DOCX and PDF upload/preview, draft generation, conversational
  correction, human confirmation, trace inspection and safe replay. Browser
  console errors and warnings: zero.
- Repository Harness: all 13 contracts passed across 19 required files, 14
  Markdown files, dependency-update targets and 19 allowlisted Workflow operations.
- Architecture checks: 70 Python files and 11 Vue files passed file-size,
  dependency-boundary, required-module and complexity ratchets. The highest
  measured Python cyclomatic complexity was 47, below the limit of 50.
- Backend dependency consistency, lock synchronization, Ruff and Python
  compilation: passed.
- Vue TypeScript check, production Vite build and VS Code extension TypeScript
  compile: passed.
- Python, frontend and VS Code extension dependency audits: no known vulnerabilities.
- Fresh isolated SQLite schema upgraded through Alembic revisions 0001-0012.
- Isolated runtime smoke and `scripts\doctor.bat`: passed.

Final local run artifacts:
`artifacts\validation\20260805-130511-full\summary.json`.

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
