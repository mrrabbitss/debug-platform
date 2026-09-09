# Personal and shared Chat models: implementation handoff

Status: owned backend implementation complete, ready for parent integration. Files are edited directly
in the shared workspace; no commit, dependency changes, live data changes or release operations performed.

## Consumer contract

- `app.services.model_access.resolve_user_chat_profile(db, principal, profile_id=None)` returns an enabled
  accessible Chat `ModelProfile`, choosing explicit ID, then `pref-<principal.id>.chat_profile_id`, then
  the shared active default. Invalid, disabled or inaccessible personal selections raise an error;
  they never fall back silently.
- `require_model_profile(db, principal, profile_id, manage=False, require_enabled=False)` applies visibility
  before returning a profile. Even administrators cannot see or use another user's private profile.
- `model_profile_payload(db, principal, profile)` returns `owner_id`, `visibility`, `can_manage` in addition
  to existing fields, without credentials. `visible_model_clause(principal)` filters list queries.
- `chat_model_snapshot(db, principal, profile)` records an actor ID and opaque configuration fingerprint.
  `resolve_chat_model_snapshot(db, snapshot)` rechecks the live actor, ownership, enabled status and exact
  configuration/credential fingerprint in workers. A changed profile requires a fresh request; it is not
  silently replaced with another configuration or provider.
- `/system/model` and `/system/model/test` resolve the caller's personal choice, not the global default.
  `/system/models` accepts/returns `visibility: SHARED|PRIVATE`; creation binds the authenticated owner.
  `/system/models/{id}/activate` changes the shared default, so requires ADMIN/EXPERT and SHARED Chat
  (ADMIN for Embedding/Reranker). Personal selection remains `PUT /workbench/preferences`.
- Legacy VIEWER may select and test visible Chat profiles; case writes remain unchanged. Chat creation
  and edits are restricted to ENGINEER/EXPERT/ADMIN pending any explicit change to legacy VIEWER writes.

## Parent integration points

- Central middleware must allow model routes to reach the route/domain ownership checks.
- Add `from app import model_access_models as _model_access_models` to the model registry if relying on
  `Base.metadata` before importing the model services. Migration 0023 creates/backfills the table itself.
- Case creation/update should pass `principal` to `validate_case_options(db, values, principal=identity)`.
  Without a principal, only shared profiles can be accepted; the helper deliberately cannot infer a caller.
- Assistant/curation creation should resolve with the initiating principal, persist the Chat snapshot,
  and use `resolve_chat_model_snapshot` before worker requests. Never select arbitrary IDs with `db.get`.
- Diagnosis decorator derives the initiator from Request or `created_by`; standalone capture accepts
  `principal=`. Existing snapshots may use shared models only until a fresh authenticated snapshot exists.
- New authenticated diagnosis requests follow personal settings then the shared default; the old saved
  `Case.chat_profile_id` no longer overrides the user's unified setting. Already queued runs remain pinned.
  Unowned legacy contexts can retain an explicitly saved shared case selection.
- Revision workers prefer the current revision's AgentRun snapshot over its source analysis snapshot,
  so reviewing another user's report cannot consume the original author's private API.
- Parent owns assistant consumers, shared documentation/workflow updates and the new documentation index entry.

## Validation

Run from `backend` using `D:/GRXM/debugplatform/.venv/Scripts/python.exe`:

- `-m pytest tests/test_model_sharing.py -q --disable-warnings --maxfail=3`:
  46 passed, 2 failed in 40.19 seconds. One failure was an incomplete synthetic AgentRun fixture;
  after adding its required `input_summary_hash`, the focused rerun below passed.
- `-m pytest tests/test_model_sharing.py::test_revision_worker_uses_requesters_snapshot_not_source_authors_private_api -q --disable-warnings --tb=short`:
  1 passed in 2.68 seconds.
- These initial runs provided passing evidence for 47 of the 48 new cases. The parent subsequently
  fixed the central VIEWER `GET /system/model` allowance and confirmed the remaining
  `test_viewer_personal_selection_preserves_legacy_readonly_model_writes` passed on 2026-09-09.
  **All 48 model-sharing cases now have passing evidence** across these focused runs. The last result
  is parent-provided evidence, also recorded in `docs/expert-knowledge-iteration-20260909.md`; this
  documentation follow-up did not rerun the suite or claim one new 48-case batch execution.
- Ruff passed for all nine owned Python files (services, APIs, schemas, ownership model, migration, tests).
  Scoped `git diff --check` passed.
- Initial model-handoff `scripts/check_repo_harness.py`: 22/24 checks passed. The then-outstanding failures were four new
  handoff documents missing from `docs/README.md` and `knowledge_curation.py` at 904 lines (limit 890).
  These are in other agents'/parent's write scopes. `scripts/check_architecture.py` independently reports
  only that same curation file limit; all owned files satisfy the current limits (`system.py`: 637/650).

Covered: authenticated role/ownership checks, private guessed-ID protection across read/update/delete/test/
activation/proxy paths, shared default restrictions, personal preference persistence and invalidation,
global E/R administration, synthetic production intranet/localhost/IPv6 URLs, retained managed GGUF identity,
actual OpenAI-compatible HTTP client exercised with `httpx.MockTransport`, probe output/error redaction,
credential/config fingerprint checks, disabled/deleted profiles, deactivated owners, stale-session ownership
refresh, revised-diagnosis initiator isolation, 0022-to-0023 migration and restart-safe profile preferences.

No historical suites, Full validation, manual CI, real model requests, company endpoints or live databases
were used. PostgreSQL/remote gateway behavior is not newly validated in this handoff.

## Changed files and remaining integration

- `backend/app/services/model_profiles.py`: syntax-only external URL policy, managed-sidecar validation
  retained, shared-only activation/default lookup, seeded ownership and content-safe connection probe.
- `backend/app/services/model_access.py`: role/ownership policy, filtered payloads, selection and worker snapshots.
- `backend/app/model_access_models.py`: ownership/visibility table and optimistic version.
- `backend/app/migrations/versions/0023_model_sharing.py`: non-destructive shared ownership migration.
- `backend/app/api/system.py`: protected model routes, current-user `/system/model` and `/system/model/test`.
- `backend/app/schemas.py`: visibility/ownership output, forbidden owner/task injection, EXPERT role schema.
- `backend/app/api/workbench.py`: visible bootstrap choices and scoped durable preferences.
- `backend/app/services/workbench.py`: initiating-user selection and immutable per-request model binding.
- `backend/tests/test_model_sharing.py`: 48 focused new cases.
- This handoff document.

Parent may now take over both workbench files for category/Skill integration. Parent should also remove
or reword any legacy case-level model selector to reflect the unified personal setting for new requests.
The default activation endpoint remains a shared-default operation, not a personal preference setter.
Legacy `MODEL_ENDPOINT_ALLOWLIST`/`MODEL_ALLOW_PRIVATE_ENDPOINTS` settings remain parse-compatible but are
no longer consulted by API/proxy URL validation; remove obsolete deployment guidance in shared docs.

## Contract and operational-document sidecar handoff

The parent has taken over both workbench files and the assistant consumers. This follow-up changed only
the assigned contracts/Skill entrypoint and operational documentation; no backend/frontend code,
dependencies, real data, credentials, tests, or parent-owned source-of-truth documents were edited.

Changed files:

- `workflow/skill.yaml`: EXPERT and owner-scoped contributor permissions, personal/shared model policy,
  unrestricted valid external HTTP(S) endpoints, mandatory Skill management and category fallback rules;
  11 contribution routes declared under Web/REST-only endpoints.
- `workflow/mcp-tools.yaml`: ADMIN/EXPERT routing authorization and Web model/knowledge boundaries;
  retained historical validation explicitly distinguished from this policy revision.
- `.agents/skills/gw-ap-debug/SKILL.md`: role and UI entrypoints, reviewed ordinary contributions versus
  direct Skill management, category/Skill fallback, no endpoint whitelist prerequisite, and reuse of
  already-given host-provider authorization. Current-CLI reasoning remains mandatory.
- `deploy/windows-portable/README.txt`, `docs/windows-portable-deployment.md`: current HTTP(S) API/
  proxy policy, private/shared Chat selection and ADMIN-only retrieval/GGUF configuration.
- `docs/model-and-knowledge-configuration.md`, `docs/llm-knowledge-curation.md`: removed mandatory
  allowlist/private opt-in script instructions; corrected model ownership, ordinary AI extraction,
  proposal review, and private/pinned model guidance.
- `docs/project-architecture-and-evolution.md`: narrowly replaced obsolete host/IP blocking claims
  and marked the 2026-08-11 private-opt-in step as historical and superseded.
- This handoff appendix records the sidecar evidence and remaining parent integration locations.

Operation sets remain unchanged: 38 agent REST entrypoints and 18 MCP tools. All names, REST
methods/paths, and MCP access/write-effect/case-scope declarations match HEAD. Contribution routes
are only documented Web/REST operations, not new agent entrypoints or MCP operations. Existing
contract versions remain 0.9.0 (REST) and 0.3.0 (MCP); both carry policy revision
`expert-model-knowledge-20260909`.

Focused validation from the repository root with `.venv/Scripts/python.exe`:

- Ephemeral Python/YAML contract assertions: **118 passed**. Checked unique YAML mapping keys,
  unchanged operation sets and versions against HEAD, exact 18-tool runtime registry matching,
  contributor/reviewer/VIEWER role declarations, model policy fields, all **38 agent + 25 REST-only**
  paths against a startup-free FastAPI router, and all Skill Markdown reference targets. This checks
  declarations and route existence, not end-to-end authorization behavior.
- `scripts/check_repo_harness.py` imported and **only `check_workflow_contract` invoked**:
  **3/3 PASS** (`workflow-api-contract`, `workflow-runtime-openapi`, `workflow-safety-contract`).
  No other harness groups or historical regressions were rerun.
- `C:/Users/23173/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/gw-ap-debug`:
  **Skill is valid!**
- Scoped `git diff --check`: **PASS**. Reviewed deprecated-policy matches in the changed guides:
  remaining endpoint-variable mentions explain removal/compatibility; no mandatory whitelist or
  private-opt-in instructions remain. Evidence, tool, and category allowlists are unchanged.

Initial sidecar follow-up items (current resolution recorded below):

1. OpenAPI role/schema synchronization: completed in the follow-up below, including explicit role
   parity checks that the initial harness subset did not perform.
2. MCP registry descriptions: the three routing/section-reading descriptions now include EXPERT.
   A normalized AST comparison confirms this follow-up changed only those strings in the registry.
3. Both Skill references now agree with EXPERT routing, ordinary knowledge proposals/AI extraction,
   privileged direct Skill management, no endpoint whitelist prerequisite, and the unchanged tool set.
4. `backend/app/services/knowledge_routing.py::resolve_routing_model` and its Web import worker
   still resolve a supplied ID/global default directly. `backend/app/api/knowledge_routing.py`
   invokes that resolver without a principal and persists only the profile ID. Wire the existing
   `resolve_user_chat_profile` / `chat_model_snapshot` / `resolve_chat_model_snapshot` helpers into
   this remaining Web consumer to enforce private-profile isolation and queued-request binding.
   The parent assigned this consumer integration to Ohm. This sidecar did not edit those application
   files or claim that path newly verified.

No Full validation, historical regression suites, manual CI, releases, or real model requests were run.

## OpenAPI, reference, and login-description follow-up

The parent extended this agent's scope to the remaining OpenAPI/Skill reference/MCP descriptions and
then to the expert-login explanation. Application ownership and live-data work remain with the parent
and Ohm. This follow-up changes only:

- `workflow/openapi.yaml`: generated request/response/parameter schemas from the current FastAPI
  router, with role/callability/scope metadata from `workflow/skill.yaml`, current policy notes, and
  preserved existing operation IDs. It contains **68 operations and 48 referenced schemas**.
- `workflow/skill.yaml`: added the parent's existing category rename/deactivation and knowledge-reset
  preview/confirm/status routes as **five REST-only declarations**. Agent entrypoints remain **38**;
  REST-only declarations are now **30**. No login endpoint or MCP operation was added.
- `.agents/skills/gw-ap-debug/references/knowledge-routing.md` and `references/tool-reference.md`:
  synchronized roles, proposal/Skill boundaries, existing authorization reuse, external HTTP(S) policy,
  and the complete existing **18-tool** catalog. Routing reference also explains expert login continuity.
- `backend/app/mcp/debugplatform_registry.py`: **only three description strings** changed; tool names,
  handlers, input models, annotations, and runtime authorization logic were not changed by this follow-up.
- `docs/分机使用指南.md`, `docs/服务器使用指南.md`, and `deploy/windows-client/README.md`:
  active ADMIN-appointed experts retain their personal code and browser-ticket login with the actual
  current role. New self-enrollment creates ENGINEER only; ADMIN and disabled accounts remain refused
  by the self-service paths. Existing 0.4.0 instructions are explicitly distinguished from the new server
  behavior. No released installer or client archive was rebuilt.
- This handoff records the updated 48-case model evidence and follow-up validation.

No standalone OpenAPI exporter was found in the tracked repository/scripts. The export reused the
existing `backend/tests/test_workbench_access_contract.py::_contracts` approach: construct a bare
FastAPI application, include `app.api.routes.router` at `/api/v1` with `verify_api_key`, call `openapi()`,
select the operations declared in workflow, and recursively export their referenced components. The
one-shot export disabled SQLAlchemy `Engine.connect` and `socket.create_connection`; no application
startup, database access, or model/network call was used.

New focused validation:

- **594 contract assertions passed**: unique YAML keys; role and policy-extension parity; exact current
  request, parameter, response, and 48 component schemas; resolvable references; stable existing
  operation IDs; complete current workbench/contribution REST coverage; 38 agent/30 REST-only
  boundaries; privileged review/reset/category permissions; VIEWER preference compatibility; runtime
  MCP descriptions and exact 18-tool reference-table coverage. This is contract inspection, not a rerun
  of historical behavior suites.
- Normalized AST comparison before/after the registry edit: **PASS**; only the three allowed description
  arguments differ. Runtime registry listing used a synthetic principal and a forbidden database factory.
- Skill creator's `quick_validate.py`: **Skill is valid!** Scoped `git diff --check`: **PASS**.
- Workflow-only harness rerun after export: **3/3 PASS** for declared API paths, runtime route matching,
  and safety declarations; no other harness groups were invoked. Login descriptions, local links, and
  whitespace checks passed for all four updated login-reference documents and this handoff.

Login integration note for the parent: at inspection time,
`deploy/windows-client/client_onboarding.ps1:58` still required returned role `ENGINEER`, so reconfiguring
or onboarding an already-promoted EXPERT can yield `The server returned an unexpected identity` despite
the fixed server. The guides explain this older-client limitation; the script was not edited in this
documentation scope. The parent's `test_expert_login_iteration.py` validation remains parent-owned.

Final parent resolution: both cached-token reuse and onboarding response validation in
`client_onboarding.ps1` now accept ENGINEER/EXPERT. A real PowerShell subprocess with synthetic HTTP
and credential functions passed 9 assertions for reuse, appointed expert login and rejection of
unexpected roles, mismatched codes or empty tokens. No real credentials or endpoints were used.
The remaining Markdown model consumer was completed by Ohm; see the knowledge-review handoff.

The parent also reports actual configured development database migration **0016 -> 0024** and the
six-file reset/import completed, with business ZIP/archive outside Git and no Chat egress. Those actions
were not repeated or newly validated here. Platform startup smoke remains with the parent.
