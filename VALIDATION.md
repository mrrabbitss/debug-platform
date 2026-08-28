# Validation Record

Last validated: 2026-08-28 on Windows 11.

## Current local regression result

- Unified `scripts\validate_all.bat Full`: all 18 stages passed. The run produced
  a machine-readable summary and per-step logs under the Git-ignored
  `artifacts\validation` directory.
- Backend tests: 233 passed and 1 external-service test skipped locally.
- Backend line coverage: 78.25%, above the enforced 75% quality gate.
- Golden Dataset: all 10 evaluators passed for parser output, document curation,
  the 36-case synthetic scenario-distribution contract, Code Graph, Commit Graph,
  memory isolation, hybrid RAG and bounded Agentic Search. The matrix is explicitly
  a distribution/schema gate; the core incident remains the full executable fixture.
- Browser E2E: 3 complete Edge scenarios passed in an isolated runtime. They
  covered TXT, HTML, DOCX and PDF upload/preview, draft generation,
  conversational correction, human confirmation, trace inspection, safe replay,
  encrypted Chat proxy configuration/clearing without credential exposure, and
  extensionless Huawei log upload, full-text three-bucket LLM triage, bounded
  two-to-twenty-round typed-tool diagnosis planning, explicit stop reason,
  recoverable case chat, GW/AP joint evidence scope and human-approved diagnosis/
  report revision. The diagnosis flow explicitly asserts non-zero total/input/output
  Token counts and the visible aggregate Agent budget, the called method/tool tables,
  Skill-derived match meanings, a
  collapsed repeated-match group, all occurrences at synthetic lines 5 and 13, and
  exact navigation from the second occurrence to line 13. User-facing diagnosis,
  chat, trace and report evidence is rendered as a filename plus line number rather
  than an opaque internal evidence ID; confirmed facts are rendered last.
  Browser console errors and warnings: zero.
- Repository Harness: all 18 contracts passed across 34 required files, 17
  tracked Markdown files, dependency-update targets and 29 allowlisted Workflow
  operations. The contracts now include the portable runtime, .NET-only package
  hashing, model-isolation, atomic managed-model publication and release-workflow
  checks.
- Architecture checks: 110 Python files and 16 Vue files passed file-size,
  dependency-boundary, required-module and complexity ratchets. The highest
  measured Python cyclomatic complexity was 50, at the enforced limit of 50.
- Backend dependency consistency, lock synchronization, Ruff and Python
  compilation: passed.
- Vue TypeScript check, production Vite build and VS Code extension TypeScript
  compile: passed.
- Python, frontend and VS Code extension dependency audits: no known vulnerabilities.
- Fresh isolated SQLite schema upgraded through Alembic revisions 0001-0016.
- Isolated runtime smoke and `scripts\doctor_local.bat`: passed.
- Startup, doctor and lock refresh scripts use the .NET SHA-256 implementation
  instead of depending on the optional `Get-FileHash` cmdlet. The obsolete local
  model download/install chain is absent and is enforced as a repository contract.
- Managed model downloads resolve the requested revision to an immutable upstream
  commit, resume into a staging generation, verify every file, and atomically switch
  the active-generation pointer only after the complete generation passes. Per-model
  in-process and cross-process locks serialize writers; failure and cancellation
  regression tests confirm that the previous active generation remains readable.
  Eleven focused downloader tests also cover Range resume, path traversal, proxy
  isolation, encrypted one-time proxy use and same-size file corruption detection.
- A complete portable package was built with an isolated 64-bit CPython 3.14
  runtime and the production frontend. Its self-check, SQLite migration/readiness,
  Vue root and deep-route fallback, and API liveness all passed. The 9,547-file
  integrity manifest matched the package exactly, contained zero `.pyc` files and
  confirmed that Torch and sentence-transformers were not bundled or imported from
  global/user Python locations.

Final local run artifacts:
`artifacts\validation\20260828-101217-full\summary.json` (18/18 stages passed in
418.39 seconds). The run used Windows PowerShell 5.1 and included Alembic 0016,
token-aware context/spill isolation, provider-observed usage and causal-depth budget
regressions, the 36-case Golden distribution gate, budget/context-panel build checks,
exact-hit navigation and human-readable evidence presentation. No persistent
system, project or model proxy setting was changed.

A read-only `hf-mirror.com` manifest probe additionally confirmed that both managed
defaults (`BAAI/bge-base-zh-v1.5` and `Qwen/Qwen3-Reranker-0.6B`) currently return
immutable 40-character commit revisions and LFS size metadata. No model weights or
company data were downloaded by this probe.

The separately built portable archive is
`artifacts\portable\debug-platform-windows-x64.zip` (112.18 MiB), SHA-256
`6d808e1ca8b5221294a6e030dda7f0b4cd0e4ffd4b477dec74cd7dd1c1190ac2`.
The packaged verifier and an independent archive pass validated all 9,547 manifest
entries by size and SHA-256. Generated validation and portable artifacts are
Git-ignored; release-tag CI rebuilds and publishes a clean artifact from the tagged
commit.

## Prior focused model validation

After removing the development-mode HTTP-only allowlist restriction, the focused
model-profile suite passed 16/16 and
`artifacts\validation\20260814-143609-fast\summary.json` passed all 9 Fast stages
in 26.70 seconds. The regression explicitly accepts a public HTTP model endpoint,
accepts a private HTTP endpoint when `MODEL_ALLOW_PRIVATE_ENDPOINTS=true`, and
retains blocking for unapproved private/loopback, cloud-metadata and production
endpoints.

The subsequent log-planning diagnostics suite passed 42/42. A real GLM-5.2
follow-up started from a provider configured with Thinking enabled; the production
purpose override sent `thinking.type=disabled` for `LLM LOG PLAN` and completed in
53.53 seconds without retry. It read 2 method documents and 157 compiled Patterns,
then returned 60 selected Patterns and 30 additional keywords using 30,756 Tokens.
Synthetic failure tests separately cover stable, content-safe timeout, authentication,
rate-limit, bad-request, connection, truncated-output and invalid-JSON diagnostics.

## Approved external GLM-5.2 result

On 2026-08-14 the approved GLM-5.2 endpoint was exercised with the Git-ignored
local `故障树.md` and `日志分析.md`, an isolated temporary database and synthetic
log evidence. The credential was obtained only inside the validation process and
was not written to source, reports or command output. The no-model preflight found
2 method documents, 157 compiled Patterns and 27 fault-tree nodes; 27/27 nodes had
an auditable retrieval entry and 26 had a direct recommended log Pattern.

All 9 Chat-model probes passed:

- Thinking-enabled gateway text and Thinking-disabled log planning;
- log planning read both methods, selected 59 Patterns and added 30 keywords;
- comprehensive typed-tool diagnosis with a hard 20-round limit;
- evidence-constrained final synthesis, asynchronous case answer and diagnosis/
  report revision;
- knowledge-folder generation, conversational correction and repository patch
  suggestion.

The comprehensive Agent independently passed twice. One run completed in 3 rounds;
the chained downstream run completed in 5 rounds with 19 total policy/model tool
calls. Both reached `attempted=27`, `concluded=27` and `complete=true`. In the chained
run the model correctly recognized that every log line was explicitly synthetic and
therefore returned 27 `INSUFFICIENT_EVIDENCE` conclusions rather than claiming a real
fault. Final synthesis and revision each preserved all 27 conclusions, and case chat
returned a non-empty answer with a citation. The chained planner used 991,321 Tokens;
the subsequent synthesis, chat and revision used 228,390, 222,200 and 338,341 Tokens.

A deliberate 16,384-output-token probe ended with `finish_reason=length`; the same
current fault tree completed with `max_tokens=65536`. The checked-in validator now
uses 65,536 for long diagnosis/revision structures and smaller caps for short probes.
Safe reports are Git-ignored under `artifacts\validation`; they contain status,
duration, Token counts, counts and hashes, not credentials, model response bodies or
private method bodies.

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
- Evidence IDs remain internal join/validation keys. Trace, diagnosis, chat and
  report serializers provide safe filename-line or document-title labels for every
  user-facing surface and scrub unresolved opaque IDs from model prose.
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
- Log planning, comprehensive diagnosis and case chat append live trace stages
  while their background jobs are running. Safe metadata includes method IDs,
  versions, planning rounds and stop reasons, but excludes method/log bodies.
- Structured Chat requests prefer JSON object mode. Log-planning validation can
  request one bounded correction, aggregates usage across attempts, and retains
  consumed Tokens even when the result falls back. Final diagnosis synthesis is
  included in the same live usage total; missing provider totals are derived from
  input plus output.
- Comprehensive diagnosis accumulates planning input/output/total Tokens, actual
  read-only tool calls and wall-clock duration across all rounds. A request that
  exhausts the remaining wall-clock allowance is cancelled; consecutive rounds are
  considered stagnant only when coverage, evidence, queries and unique tool calls
  all remain unchanged. Stable budget stops preserve incomplete coverage and use
  deterministic fallback.

## Agentic execution status

The production general-purpose Agentic Search path remains the deterministic,
explainable retrieval baseline. Comprehensive diagnosis is the narrow production
exception: it runs a two-to-twenty-round typed read-only tool Agent over that baseline.
Policy tools first list and read every applicable GW/AP/GENERAL method; each model
round may dynamically invoke at most four method, knowledge, log-evidence or evidence
lookup tools. Calls and their method, Pattern, fault-tree-node and evidence identifiers
are schema-validated before execution, then deduplicated and executed on the same
event loop, with content-safe failure diagnostics and deterministic fallback. The
coverage ledger rejects terminal conclusions for nodes that have not been attempted.
The reusable bounded runtime also provides:

- a typed Tool Registry and per-role allowlists;
- read-only-by-default tools and explicit approval for writes;
- step, hop, token, cost and wall-clock limits;
- retry, exponential backoff, circuit breaking, cancellation and explicit stop reasons;
- deterministic fallback when planning or a tool fails;
- privacy-preserving trajectory recording, scoring inputs and safe replay.

The bounded runtime is not silently enabled as the default general-purpose search
planner. Remaining P2 work is tracked in `CAPABILITIES.md`:
a cross-task DAG/approval/stagnation control plane, and automatic conversion of
human review feedback into a rule, test, document or evaluation case.

## Functional regression coverage

- Extensionless upload normalization to `.txt` and Huawei collectDebuginfo detection;
- streaming parse, sparse line index, arbitrary-line reads, event pagination,
  facets, timeline data and manifest-normalized event/triage-to-source navigation
  with exact-line highlighting;
- case-authorized LLM log planning that reads every applicable method, scans the
  complete extracted text locally, keeps command-output-only matches and presents
  LLM-relevant, method-required and other-event buckets with source lines; every
  relevant grouped match includes its Skill-derived meaning and exact occurrence
  count, expands on demand from a collapsed state, and supports per-occurrence
  source-file/line navigation;
- multi-round comprehensive diagnosis with live planning trace, five typed read-only
  tools, called-document/method visibility, validated method/Pattern/evidence IDs,
  a hard 20-round ceiling, aggregate Token/time/tool-call and no-progress budgets,
  explicit 27-node fault-tree coverage and diagnostic deterministic fallback;
- joint GW/AP diagnosis that retrieves both device domains plus GENERAL knowledge,
  retains primary-GW/secondary-AP artifact provenance and balances evidence across
  uploaded logs before synthesis;
- persistent asynchronous case chat with refresh recovery, cancellation, explicit
  failure state and no long-lived browser request; users can request a diagnosis/
  report revision, inspect the evidence-constrained DRAFT, and explicitly apply or
  reject it without overwriting the prior analysis version; confirmed facts are the
  final diagnosis/report section, and visible citations use filename-line labels;
- atomic parser, graph, vector and report publication with rollback-safe failures;
- layered knowledge taxonomy, editing, vector reindexing and hybrid retrieval;
- GW, AP, GENERAL (通用) and legacy OTHER knowledge applicability, with GENERAL
  documents eligible for both GW and AP retrieval;
- persistent LLM-assisted folder curation with TXT/MD/HTML/DOCX/PDF extraction,
  source citations, immutable revisions, correction chat and human-confirmed drafts;
- Code Graph, Commit Graph, three-class memory and Agentic Search integration;
- encrypted model credentials and per-profile Chat proxy URLs, endpoint policy,
  certificate-chain/hostname verification with proxy-mode revocation checking
  disabled, GLM-5.1/5.2 Thinking inherit/enabled/disabled and max-token options,
  egress audit and local/API
  model switching;
- idempotent, secret-safe Win11 opt-in that persists
  `MODEL_ALLOW_PRIVATE_ENDPOINTS=true` in the Git-ignored local `.env` without
  weakening the default configuration committed to the repository;
- case ownership/membership, role enforcement, token lifecycle and storage cleanup;
- backup verification, Docker/Compose definitions and runtime health/readiness checks.

## Environment-dependent checks

Docker is not installed on this validation computer, so `External`, local
PostgreSQL/Qdrant containers and local Docker image builds were not run here.
GitHub CI contains service-container and image-build jobs that provide those
checks after a push.

External GLM-5.2 Chat was not called by the unified Full command itself. It was called
separately on 2026-08-14 by the dedicated approved validator, and that current result
is recorded above. External Qwen Reranker, Qwen Chat, BGE and Embedding endpoints were
not called; a Chat-completions credential cannot validate those distinct model APIs.
Those adapter paths remain covered by the Fake OpenAI-compatible service and mocked
tests. Run each model-profile test action against the endpoint approved for the target
company environment before production use.

Use `scripts\validate_all.bat Fast` during development,
`scripts\validate_all.bat Full` before publishing, and
`scripts\validate_all.bat External` on a Docker-capable workstation.
