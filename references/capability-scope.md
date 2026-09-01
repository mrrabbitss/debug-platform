# Capability scope

This parallel MVP vendors the main-branch backend snapshot from commit `181dae7b26863accd02e8206895d3cfb670739ac`, with Skill-specific portability and host-agent bridge patches.

## Preserved diagnostic slice

- safe archive and text handling, including extensionless collectDebuginfo;
- Huawei and generic event parsing;
- GW/AP artifact provenance and joint scope;
- diagnostic method and Pattern compilation, including bounded Markdown-only
  parsing of external diagnostic Skills and deterministic composition with the
  current base methods;
- run-scoped external Skill overlays, immutable content-addressed method
  generations, atomic multi-pack persistent activation, kernel-backed method
  state locking, and finite per-case backend bindings retained when an
  asynchronous job outcome is uncertain;
- conservative host-agent composed-method size preflight, with an explicit
  operator-selected override for a verified larger model context window;
- mandatory full local Pattern scanning and three evidence buckets;
- exact match occurrences with file and line locations;
- deterministic RAG/rule baseline and fallback;
- fault-tree item IDs, coverage accounting, and terminal status rules;
- evidence allowlist validation and human-readable Markdown/JSON output;
- bounded host-agent reasoning with hash-verified method bodies, authenticated/atomic read-only search state, evidence-to-node binding, and atomic result validation.

## Deliberately reduced or absent

- Late `result --mode host-agent` re-export uses a bounded 2,048-generation
  hash recovery scan when no exact selector survives in the backend analysis
  record; normal `run`/`diagnose` export uses its exact generation directly.
- Vue administrator/diagnosis workbench and VS Code extension;
- RBAC/user administration and production multi-tenant deployment;
- repository/commit/code graphs and static-analysis workflow;
- knowledge upload, curation, review, and publication workflows;
- analysis revision application/rejection;
- global Agent-run replay and the full 29-operation workflow contract;
- PostgreSQL/Qdrant/Docker production topology and frontend E2E;
- persisted platform-native host-agent AnalysisRun/AgentTrace sessions.
- trusted host-CLI runtime token, wall-clock, and semantic no-progress enforcement. The portable bridge preflights composed method size, enforces 1-20 monotonic rounds, four tool calls per round, query/result/dynamic-evidence limits, and final coverage, but the three host CLIs do not expose one common trusted token/time counter to the Skill. The method-size estimate is not a provider token meter and does not include all host overhead.
- isolation from a deliberately malicious process with arbitrary read/write access as the same OS user; the external HMAC key protects ordinary bundle integrity, not a fully compromised host account.

Host-agent results are validated and published inside the portable run bundle rather than written back as a new platform AnalysisRun. This is an MVP boundary, not full platform parity.

Do not describe this package as a complete copy of the main branch. A precise claim is: "portable GW/AP log-diagnosis core with deterministic local evidence processing and CLI-hosted model reasoning."
