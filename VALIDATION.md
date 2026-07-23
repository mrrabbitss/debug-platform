# Validation Record

Last validated: 2026-07-23 on Windows 11 with Python 3.14 and Node.js 24.

## Current regression result

- Backend dependency consistency (`pip check`): passed;
- Ruff static checks across backend, tests and Python utility scripts: passed;
- Python compileall: passed;
- Pytest: 86 passed, 1 external-service test skipped locally;
- Vue TypeScript check and production Vite build: passed;
- VS Code extension TypeScript compile: passed;
- Python, frontend and extension dependency audit: 0 known vulnerabilities after applying current fixes;
- Isolated Win11 runtime smoke: passed.
- Hugging Face access probe: mirror API and verified curl fallback passed; the
  local `hf` CLI reproduced `LocalEntryNotFoundError` as expected.

The test environment includes Starlette's supported `httpx2` test client; the suite completes without deprecation warnings.

## Runtime and browser checks

- Fresh isolated SQLite database upgraded through Alembic revisions 0001-0006;
- Backend liveness/readiness, frontend `/api` proxy and case create/read round trip passed on alternate loopback ports;
- “安全与审计” rendered local identity, database/storage/job status and redacted audit rows;
- User creation dialog rendered role, initial-token and expiry controls;
- Isolated user CLI create/list/deactivate flow passed with one-time token output suppressed from the record;
- Case detail rendered `OWNER`, member management, edit controls and extensionless-log upload guidance;
- Browser console warnings/errors during the current interactive checks: none;
- All isolated backend/frontend test processes were stopped after validation.

The reusable command is `scripts\runtime_smoke.bat`. It uses temporary storage and does not modify the normal project database.

## Functional regression coverage

- Extensionless upload normalization to `.txt` and Huawei collectDebuginfo content detection;
- Exact 110,904-line streaming parse, sparse line index, arbitrary-line reads and raw keyword search;
- Event pagination, facets, timeline data and event-to-source navigation;
- Atomic parse generations: failed reparse retains the last published events;
- Persistent jobs, restart recovery, cooperative cancellation, retry and unsafe-cancellation rejection;
- Fresh/legacy migrations, SQLite foreign keys, cascade deletion and managed storage cleanup;
- Model endpoint SSRF/allowlist checks, evidence-ID validation and deterministic fallback;
- Chat/Embedding/Reranker profile switching, encrypted key redaction and content-free model-egress audit;
- Project-relative BGE/Qwen model paths, BGE query instruction handling and local-model installer layout;
- Layered knowledge taxonomy, document editing and vector reindex behavior;
- Hashed personal tokens, role enforcement, case ownership/membership and admin safety checks;
- VS Code SecretStorage credentials, extensionless-log selection and secret-file exclusions for workspace archives;
- Liveness/readiness and operational status without disclosing filesystem paths;
- Backup round trip, manifest/hash verification, tamper rejection and rollback retention;
- Docker/Compose definitions for PostgreSQL and Qdrant, plus CI workflow structure.

## Environment-dependent checks

Docker is not installed on this validation computer, so PostgreSQL/Qdrant containers and Docker image builds were not executed locally. `.github/workflows/ci.yml` contains a dedicated service-container test that starts PostgreSQL and Qdrant, runs all migrations, starts the FastAPI application, performs a case API round trip and performs a Qdrant vector write/query/delete round trip. The same workflow builds both Docker images.

External Qwen/GLM/BGE endpoints were not called because approved credentials were not supplied. Their adapters and validation paths use mocked responses in the local suite; use the model-profile “测试” action with an approved company endpoint before production use.

The optional `backend[local-models]` dependency set resolved successfully on Python 3.14, including current Windows wheels for PyTorch, Sentence Transformers, Transformers and the pinned Hugging Face Hub 0.36.2. Both approved model manifests and their pinned revisions were read from `hf-mirror.com`; `config.json` was downloaded and validated through the new curl path for both BGE and Qwen. The multi-gigabyte weights were intentionally not downloaded on this validation computer. The installer directory creation, CLI preflight, automatic fallback, path safety, resume/integrity checks and application-adapter probes are covered by automated tests; running `scripts\install_local_models.bat` in the target company network performs the final real weight download and load test.
