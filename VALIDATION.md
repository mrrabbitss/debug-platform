# Validation Record

Last validated: 2026-07-22 on Windows 11 with Python 3.14 and Node.js 24.

## Clean-install and build checks

- Backend dependency consistency (`pip check`): passed;
- Frontend reproducible install (`npm ci`): passed, 0 vulnerabilities;
- VS Code extension reproducible install (`npm ci`): passed, 0 vulnerabilities;
- Python compileall: passed;
- Ruff static checks: passed;
- Pytest: 54 passed;
- Vue TypeScript and production Vite build: passed;
- VS Code extension TypeScript compile: passed.

Pytest emits one upstream Starlette warning about the future `httpx2` test client. It does not affect the application runtime or current tests.

## Runtime checks

- Fresh isolated SQLite database upgraded through Alembic revisions 0001, 0002 and 0003;
- Isolated backend health check on an alternate loopback port: passed;
- Isolated frontend startup and `/api` proxy to that backend: passed;
- Case create/read API round trip through the frontend proxy: passed;
- In-app browser navigation: cases, case detail, event pagination, timeline, layered knowledge tree, knowledge edit dialog, model settings and model tabs passed;
- Browser console errors during the interactive checks: none;
- Windows environment doctor: executed successfully and correctly rejected an unrelated service occupying port 8000;
- Offline log inspector: sparse-NUL text test passed in under the 60-second regression limit.

The reusable command for the isolated runtime check is `scripts\runtime_smoke.bat`. It uses temporary storage and does not modify the normal project database.

## Functional regression coverage

- Extensionless upload normalization to `.txt`;
- Huawei collectDebuginfo content detection and parser registration;
- Exact 110,904-line streaming parse, batched database inserts and raw-line range reads;
- Server-side event pagination, facets, artifact IDs and timeline data;
- Parser failure and diagnosis failure state recovery;
- Persistent job restart recovery and duplicate active-job prevention;
- Fresh and legacy database migration paths;
- SQLite foreign keys, case cascade deletion and managed storage cleanup;
- Cross-case code retrieval and patch-suggestion isolation;
- Model endpoint protocol/private-network/metadata/production allowlist validation;
- LLM JSON schema, confidence and evidence-ID validation with deterministic fallback;
- Safe per-analysis model configuration snapshots without API keys;
- Multi-profile Chat, Embedding and Reranker switching and encrypted key redaction;
- Layered knowledge taxonomy, knowledge editing and vector reindex behavior.

External Qwen/GLM/BGE API calls were not made because approved credentials were not supplied. Their adapters and validation paths are covered with mocked request/response tests; use the model-profile “测试” action with an approved company endpoint before production use.
