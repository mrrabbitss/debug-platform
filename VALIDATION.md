# Validation Record

Validated in the generation environment:

- Backend dependency installation: passed
- Python compileall: passed
- Ruff static checks: passed
- Pytest: 21 passed
- Vue TypeScript build: passed
- VS Code extension TypeScript compile: passed
- VSIX package generation: passed
- API health check: passed
- Demo collectDebuginfo upload and parsing: passed
- Extensionless Huawei GW/AP collectDebuginfo content detection and `NOTICE` parsing: passed
- Extensionless upload name normalization to `.txt`: passed
- Zero-readable-file parse failure state and diagnostic message: passed
- Windows PowerShell/BAT offline log inspection on the extensionless demo file: passed
- Sparse NUL text acceptance, sanitization, archive handling, and PowerShell reporting: passed
- Fresh-process built-in parser registration and Huawei parser selection: passed
- Multi-profile Chat/Embedding/Reranker configuration and encrypted API-key redaction: passed
- SQLite embedding persistence and Qwen-compatible reranker request shape: passed
- Layered knowledge taxonomy, document detail/update, and vector reindex API integration: passed
- Browser interaction check for model tabs, BGE profile, category tree, filtering, and knowledge edit dialogs: passed
- Demo repository upload and symbol indexing: passed
- Rule + RAG diagnosis: passed
- HTML report generation: passed
- PDF report generation: passed
- DOCX report generation: passed

Demo diagnosis extracted 10 high-signal events and linked the failure to hostapd/configuration evidence and related C functions.

External Qwen/GLM calls were not executed because no API credentials were supplied. The OpenAI-compatible provider code path is included and can be tested after approved credentials are configured.
