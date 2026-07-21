# Validation Record

Validated in the generation environment:

- Backend dependency installation: passed
- Python compileall: passed
- Ruff static checks: passed
- Pytest: 8 passed
- Vue TypeScript build: passed
- VS Code extension TypeScript compile: passed
- VSIX package generation: passed
- API health check: passed
- Demo collectDebuginfo upload and parsing: passed
- Extensionless Huawei GW/AP collectDebuginfo content detection and `NOTICE` parsing: passed
- Demo repository upload and symbol indexing: passed
- Rule + RAG diagnosis: passed
- HTML report generation: passed
- PDF report generation: passed
- DOCX report generation: passed

Demo diagnosis extracted 10 high-signal events and linked the failure to hostapd/configuration evidence and related C functions.

External Qwen/GLM calls were not executed because no API credentials were supplied. The OpenAI-compatible provider code path is included and can be tested after approved credentials are configured.
