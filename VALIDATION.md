# Validation Record

## Latest: 0.5.1 bundled Skill and local administrator recovery (2026-09-09)

New installer-only checks: five passed in `backend/tests/test_installer_bundled_knowledge.py`.
They cover all six files, template/dependency binding, preservation of later human edits/deletions,
nonempty-corpus protection, interrupted initial-index recovery and invalid bundle/external-index deferral.
One test assertion initially used the wrong dependency-helper signature; only that failed case was rerun.
The separate administrator recovery checks and package verification are recorded in the
[installation repair record](docs/installer-skill-admin-fix-20260909.md).
Recovery: initial 3 passed (7.61s), then only 10 added cases passed (18.21s). Frontend type checking
and production bundle passed; a transient generated-declaration write error required only the failed Vite step again.
Repository harness 24/24 PASS, 198 Python / 36 Vue, 38 agent operations.
2026-09-10 package acceptance: 10,210 manifest files and 277 application/frontend files matched.
Bundled Python started a fresh prod/RBAC instance, created an administrator, published all six original
Skill documents, and retained them on restart. A synthetic expired bootstrap returned 401, and the
offline recovery credential authenticated as ADMIN. Running-server recovery was refused.
This used deterministic local hashing, no GGUF inference repetition or Chat requests; temporary credentials were revoked and removed.
No previous passing suites, real Chat/CLI model calls, Full/External or manual CI were rerun.
The actual deployed login error remains unconfirmed because this machine has no installed server data.

## Previous: 0.5.0 slow-model waiting and delivery (2026-09-09)

Only new waiting behavior and release assembly are in scope. Seven new checks in
`backend/tests/test_slow_model_timeouts.py` passed (1.34 seconds): legacy environment defaults,
operator overrides, and model profile timeout propagation into both SDK and HTTP transport.
No real model request was made; prior successful suites and API/CLI steps are not rerun.
Frontend production build passed. The installed Codex binary parsed the isolated generated MCP
configuration with `tool_timeout_sec=7200`; unrelated server/model settings were preserved.
Four CodeAgent environment scenarios passed, including restoration, and eight PowerShell scripts parsed.
Repository harness: 24/24 PASS, 197 Python / 36 Vue, 38 allowlisted operations.
Three existing E402 diagnostics in touched legacy modules are unchanged; new Python files have none.
New offline delivery verified: 10,200 server manifest entries; 276 application/frontend plus 22
companion source files byte-matched; 26 client manifest entries and ZIP CRC checked. Packaged Python
started against fresh isolated data, served the web entry and migrated the database to 0024.
No model inference or previous functional tests were run. EXE version is 0.5.0; hashes and delivery
scope are in [this iteration](docs/slow-model-timeouts-20260909.md). Physical upgrade and company
multi-machine acceptance are not covered. Existing CI was not manually dispatched.
Release v0.5.0 is published/latest; all seven remote asset sizes and SHA-256 values plus the tag commit
matched the delivery plan. Automatic release workflow 34341280002 was cancelled per the no-repeat request;
its Python-selection step failed and all application/model checks were skipped. No new CI pass is claimed.

## Previous: real API / Codex CLI acceptance and targeted repairs (2026-09-09)

The user explicitly requested real third-party Chat API and Codex CLI tests. Both use isolated
current-source servers, production RBAC identities, separate SQLite databases and synthetic data.
Base commit is `5f11eac`; these repairs are on the same `codex/expert-knowledge-iteration` branch.
See the [combined record](docs/expert-live-model-validation-20260909.md) and
[CLI evidence](docs/expert-cli-live-handoff-20260909.md) for retained evidence and limitations.

| New repair checks | Result | Coverage |
| --- | --- | --- |
| First publication with no graph state | 1 passed | Real SQLite and background JobRunner: refresh the state after claiming its generation, complete one approved publication |
| AI review JSON contract and bounded correction | 4 passed | Missing/wrong fields corrected once; repeated invalid output preserves original/history; concurrent human edit cannot be overwritten |
| Log-triage job case permissions | 7 passed | Owner/editor/viewer reads, owner cancellation, viewer write rejection, foreign/missing triage cannot borrow a supplied case ID |

These checks are in `test_assistant_live_publication.py`, `test_review_live_schema.py` and
`test_triage_live_job_access.py`. CLI used explicit `--model gpt-5.6-terra` twice; both real runs
completed with exit code 0. The engineer diagnosis persisted two planning rounds, five current-case
citations and an HTML report; expert routing remained inactive DRAFT, engineer routing was denied,
and backend Chat egress was zero. Five nonterminal CLI error events recovered, but their original
text was intentionally not retained, so no more specific attribution is claimed.

API requests specify `glm-5.2`. Two direct responses identify themselves as `glm-5.3`; the gateway's
internal model routing is not independently verified. Earlier successful reading, extraction and
review are reused when continuing from copied synthetic databases. The initial verifier missed
the mandatory triage call and selected the user message instead of the assistant reply; both
assertions were corrected. No historical suite, Full, External or manual CI was rerun.

Final API acceptance is **PARTIAL**, not PASS. Full-folder organization/publication, ordinary
extraction, two-turn review/publication, log triage, interactive chat, draft routing and restart
persistence passed. Comprehensive diagnosis stopped once on an upstream `InternalServerError`
(126947ms) and again on invalid Skill-reading output. Neither failure produced a model diagnosis
or its report. CLI full diagnosis/report PASS does not stand in for the failed API path.
Canonical API evidence is `artifacts/validation/expert-api-live-20260909-f/`, with c/d/e predecessor
artifacts retaining the successful real model work. All seven published document contents and
versions survived restart. API credential fields and active test tokens are zero after cleanup;
CLI credentials are also revoked. No new package, release, Full or green CI gate is claimed.
Repository harness passed 24/24 (196 Python / 36 Vue boundaries), changed Python sources passed
Ruff and AST checks, and 108 changed-source/evidence files had zero credential-pattern matches.

## Earlier: expert roles, model ownership and knowledge collaboration (2026-09-09)

Source branch: `codex/expert-knowledge-iteration`, based on `6523cca`. This is a local source and
integration delivery; the existing 0.4.0 release artifacts were not replaced. Unrelated local writing
was preserved. Only new or changed behavior was checked, with previously passed evidence reused;
no historical suite, Full, External or manual CI run was triggered.

| Checks | Result | Evidence and scope |
| --- | --- | --- |
| Model ownership, sharing, selected source, endpoint policy and migration | 48 new cases passed | [model handoff](docs/model-sharing-handoff-20260909.md); synthetic profiles and HTTP transport, no real model requests |
| Contributions, review AI, exact approval/outbox, private drafts, Markdown model binding and terminal recovery | 62 new cases plus 3 affected checks passed | [knowledge handoff](docs/knowledge-review-handoff-20260909.md); isolated SQLite and real HTTP route boundaries with synthetic identities |
| External backup, six-file reset, atomic publication and abrupt process exit/recovery | 52 distinct new scenarios passed | [reset handoff](docs/knowledge-reset-handoff-20260909.md); synthetic files, local hashing/graph and subprocess `os._exit(73)` before final commit |
| Assistant approval recovery and privilege exclusions | 12 new cases passed | `backend/tests/test_expert_publication_recovery.py`; rollback, idempotency, exact approval, stale workers and expert restrictions |
| Categories, published Skill boundaries and complete model reading receipts | 6 new cases passed | `backend/tests/test_skill_scope_iteration.py`; first built-in rename version defect fixed and the failed target rechecked |
| Assistant personal model binding and ordinary knowledge type | 5 new cases passed | `backend/tests/test_assistant_model_binding_iteration.py`; configuration/owner changes stop the request, no false READING status or private-profile disclosure |
| Changed assistant crash/concurrency expectations | 16 selected cases passed | Modified targets in `test_workbench_assistant.py` and `test_workbench_assistant_concurrency.py`; all other historical cases left unrun |
| Assembled REST category/RBAC integration and promoted expert login | 2 new cases passed | `test_expert_workbench_integration.py`, `test_expert_login_iteration.py`; new category reaches ordinary case creation, same login identity remains EXPERT |
| Final dispatcher, case review transaction and Windows client integration | 4 new cases passed across targeted runs | `artifacts/validation/expert-iteration-20260909/final-boundaries.xml`, `library-outbox-final.xml`; successful/rollback case submissions, terminal lease reconciliation, real PowerShell with 9 synthetic identity assertions |
| Frontend typecheck/build and browser scenarios | Build PASS; 26 distinct new browser cases passed | [frontend handoff](docs/frontend-expert-handoff-20260909.md); real production frontend with synthetic HTTP, including 390px layout, expert memory review and no unexpected API/JavaScript errors; one affected compatibility case rechecked |
| Workflow / OpenAPI / Skill references | 594 final contract assertions, 3 workflow checks and Skill validator passed | 68 declared operations, 48 schemas, unchanged 38 agent REST entrypoints and 18 MCP tools; startup-free export |
| Repository harness, lint and diff | 24/24 PASS; no new Ruff diagnostics or whitespace errors | 196 Python and 36 Vue architecture boundaries; 76 changed/new Python files clean after removing one unused import. Two old test files retain exactly 28 baseline Ruff diagnostics; checked against HEAD |
| Actual current-project data and application | Migration, import and restart PASS | [implementation record](docs/expert-knowledge-iteration-20260909.md); external consistent backup, 0016→0024 upgrade, six active Skills, complete dependencies, SQLite integrity and unchanged original business-record hashes |

The first final-boundary batch passed 3/4; only the test's expected HTTP status for the idempotent
library bridge was wrong (200 versus the existing 201 contract). Correcting that assertion and
rerunning that one target passed. There are no unresolved failures in the new targeted checks.
Additional affected single-case checks for full-folder assistant reading and legacy VIEWER model
selection also passed; they are not counted again as new cases above.

Actual data operations were limited to the current project's `backend/data/gw_ap_debug.db`, after
validating a consistent external backup and a trial migration on its copy. A single approved reset
retired four old active documents and published six supplied network Skills, including the report
format; historical references remain. Original-column hashes for 14 cases, 16 analyses, 28 artifacts
and 0 standalone reports did not change. The source ZIP, backups and business text remain outside
the source diff. A new process verified no old seeding, six complete Skill dependencies and the
network/default report choice. Read-only actual-browser checks covered management, review,
organizer and model settings; no Chat request was made.

The actual index used the existing local hashing profile. No company Chat API, real semantic
diagnosis, GGUF throughput, physical power loss, remote PostgreSQL/Qdrant, fresh-machine upgrade
or new installer was tested. Process-crash durability evidence is narrower than those checks.
No green CI/merge or release gate is claimed. Historical evidence below retains its original scope.
The final source diff contains no runtime database, models, ZIP/installer, real private key or model
credential; the unrelated untracked writing file remains untouched.

## Earlier: three-section workbench, 0.4.0 (2026-09-09)

Only new or changed behavior was checked. Historical passing suites, Full, External and manual CI
were not rerun, following the user's instruction. The package was built from `release` base
`46c3b1f47529ab17095d5e4e01ef8bb6ea59b671` plus the workbench changes; unrelated local writing
was preserved. The user-requested GitHub delivery records this implementation under `v0.4.0`.

| New checks | Latest result | Evidence |
| --- | --- | --- |
| Fixed knowledge/configuration, dependencies, migration and personal model choice | 10 distinct checks passed | `artifacts/validation/workbench-20260908/`; [checkpoint](docs/workbench-iteration-20260908.md) identifies the corrected fixture and final result |
| Main flows: owner submission, review conflicts, immutable reports, category/model routing and graph paths | 11 passed across targeted selections | `artifacts/validation/workbench-20260909/flows.xml`, `model-deleted.xml`, `bundle-category-integration.xml`, `graph-path-integration.xml`, `report-library-final.xml` |
| Transactional completion, stale-worker failure/cancel fencing and missing-context terminal status | 5 passed | `transactional-job.xml`, `stale-job-fence.xml`, `missing-context.xml` in the same directory |
| REST/MCP/domain access and workbench contract | 25 distinct checks passed | [permission handoff](docs/security-handoff-20260909.md) |
| Full-source assistant, exact plans, approval invalidation, atomic publication, concurrency and recovery | 56 distinct checks passed | [assistant handoff](docs/assistant-handoff-20260909.md); 55 passed in 181.08s, then four affected checks in 12.37s including one added case |
| Template/structure/evidence and HTML/DOCX/PDF export | 41 distinct checks passed | [report handoff](docs/report-handoff-20260909.md), `artifacts/validation/report-20260909/summary.json` |
| Frontend production build and new browser scenarios | Build PASS; 9 E2E passed in 15.7s | [frontend handoff](docs/frontend-handoff-20260909.md), `frontend-e2e.xml` |
| Repository harness / architecture | 24/24 PASS; 182 Python and 28 Vue files PASS | `repo-harness.json`, `architecture.json` under `artifacts/validation/workbench-20260909/` |
| New server payload and isolated packaged application | 10,181 manifest files verified; 257 backend/frontend source files identical; HTTP and restart PASS | `artifacts/lan/workbench-0.4.0/server-payload-build.log`, `artifacts/validation/workbench-20260909/package-runtime/result.json` |
| Client ZIP, manifest, guide and canonical Skill | 24 files PASS | `artifacts/validation/workbench-20260909/client-integrity.json` |

The new runtime verifier uses the packaged Python interpreter, a fresh synthetic SQLite database
and dynamic loopback port. It verified migrations through 0022, all three SPA routes, default true
egress with explicit false retained, mock diagnosis, HTML/DOCX/PDF creation and download hashes,
server-generated fixed report submission/confirmation, a no-egress PAUSED assistant and persistence
after stopping/restarting its own process. No external model calls or installed-server data were used.

Browser tests use the real production frontend with synthetic HTTP responses. Assistant index/Chat
tests use fakes. PDF's four representative pages were visually checked; Word content, fonts, repeated
headers and widths passed structural checks, but actual Word pagination was not rendered because
LibreOffice was unavailable. These results do not establish real-model semantic accuracy, remote
vector/PostgreSQL concurrency, TLS/physical-client acceptance, clean-machine installation, code
signing or a green CI merge gate. Existing historical GGUF evidence retains its original scope;
the new package revalidates pinned component hashes and native DLL loading without repeating it.

Final delivery: `artifacts/lan/workbench-0.4.0/delivery`; complete server EXE **920,949,842 bytes**,
SHA-256 `47b437e9ee743dd379ef0f780f759b77f05008853fa6bdb7b92aba809a143452`;
client ZIP **44,858 bytes**, SHA-256 `a569540f4ba4c1288cb063ac889d5f47473cad3b24c2204cf4d3965661971ab1`.
The exact files, upgrade procedure and remaining acceptance boundaries are recorded in the
[0.4.0 delivery note](deploy/releases/20260909/0.4.0/README.md).

GitHub delivery rechecked the existing binary sizes and SHA-256 values and the 257 application/
frontend source matches. No runtime database or server credentials were found in the payload.
The separately distributed server guide now explicitly documents same-Windows-user in-place
upgrade without uninstall, stopped-server backup and database-aware rollback. These are
installer/source checks, not a new physical-machine 0.3.3-to-0.4.0 upgrade acceptance run.

The user-requested GitHub Release `v0.4.0` was published on 2026-09-09 at source commit
`bd9c9dab340d3c982971a72294d3bab913fc7745`. All seven attachment sizes and GitHub SHA-256
digests match local files. The release event automatically started
[Windows Full GGUF Installer](https://github.com/mrrabbitss/debug-platform/actions/runs/34299969071),
which failed at Python setup because Python 3.12.12 x64 was unavailable for Windows 2022.
No application build or validation step ran in that job, and it was not manually retried.
The uploaded local artifacts remain the delivery evidence; this is not a green CI result.

## Earlier: trusted LAN personal-code login, 0.3.3 (2026-09-08)

- New identity API checks in an isolated database PASS: strict lowercase-letter/eight-ASCII-digit
  format, stable identity on repeated sign-in, fixed ENGINEER role, rejection of administrator
  and disabled accounts, administrator-route denial, and once-only browser handoff. Handoff
  credentials are not accepted as ordinary REST/MCP access tokens.
  Evidence: `artifacts/lan/simple-login-0.3.3/identity-check.json`.
- Frontend typecheck/production build PASS. The first build hit a transient write error on the
  generated `components.d.ts`; the repeat completed. Windows PowerShell parsed the new connector
  scripts and compiled the certificate helper; no certificate was installed in this computer's trust store.
- Built the current backend/frontend and packaged cached complete GGUF E/R with regression/model
  smoke disabled. Server payload integrity and exact backend source/runner/guide comparison PASS.
- Server EXE: **920,864,664 bytes**, SHA-256
  `395080f4a8f6f4c31a352f9dc131c847c0acb5adc8b518900018b82a294175de`.
  Client package `7894265020d4f9d9`: **24 files / 44,249 ZIP bytes**, SHA-256
  `84b650d4063c26cebead518c26ac83ddd6c446822c01003b7ad48c435e73a4d9`.
  Both files and separate guides are in `artifacts/lan/simple-login-0.3.3/delivery`.
- Internal deployment address is confined to ignored local profiles and dedicated artifacts.
  No historical regression suite or CI was run. Actual company-server certificate provisioning,
  Windows certificate-store installation and physical client-to-server login remain target-machine acceptance.

## Previous: complete offline server installer (2026-09-08)

- Inno Setup compilation completed successfully: `GWAP-Debug-Server-Setup-0.3.2-x64.exe`,
  **920,859,824 bytes**, SHA-256 `05e231fdf135c3efb5d7640e426bcb06e75638feb16f3160c81436eea1b0a074`.
  EXE, checksum and guide: `artifacts/installer/server-release-0.3.2-final`.
  Assembled payload has **10,149 manifest entries**; backend source and bundled runner/guide
  comparison is recorded in that directory's `source-comparison.json`.
- Removed `setup_server.bat`, `scripts/setup_lan_server.ps1` and their dedicated test file
  at the user's request. Server deployment now uses a separately delivered offline EXE.
- Rebuilt the current Core/frontend and assembled the pinned cached GGUF Embedding/Reranker,
  Python runtime and HTTPS gateway. Package manifests, model/cache sizes and SHA-256 hashes,
  and app-local Microsoft runtime signatures were checked during assembly.
- Installer source reuses the existing `install_local.ps1` atomic publisher with `Full`
  components, separate server application identity and external business data. Start/backup
  wrappers invoke the existing LAN runner with explicit package/data locations.
- No regression suites, repeated model inference, installed-server acceptance or CI run were
  performed for this change, per the user's instruction. Prior acceptance records below retain
  their original scope; packaging integrity does not establish a new clean-machine acceptance.

## Previous: required mirror restored to server setup (2026-09-08)

- Server setup now passes `--hf-endpoint https://hf-mirror.com` to the existing model
  preparation script. No model pins, checksums or cache paths changed. The downloader
  has no automatic retry against the original Hugging Face origin.
- Focused setup regression: **9 tests PASS**, including an assertion on the actual
  model-preparation subprocess arguments.
- Real mirror download of the pinned **190-byte** Embedding pooling configuration and its
  SHA-256 check PASS. Evidence: `artifacts/validation/mirror-fix-20260908/result.json`.
  This checks a small public file from this computer, not complete weights or the company server's network.
- Previous commit `7ea106f` CI run `34118911828` completed successfully; this is baseline
  evidence only, not CI verification of the mirror fix.
- Full run `20260908-092529-full` was interrupted at the user's request during backend
  regression. It is not a passing Full result. No further regression or CI run was requested;
  the user asked that previously passed regression suites not be repeated automatically.

## Previous: one-command server preparation (2026-09-07)

- Full run `20260907-193858-full` passed its first **10 stages**, including backend
  **478 passed / 1 skipped**, coverage **80.48%**, and frontend typecheck/build. It stopped
  at the Python dependency audit because the PyPI TLS connection closed unexpectedly.
- The failed audit and all seven remaining Full stages were run with their canonical commands:
  **8/8 PASS** in `artifacts/validation/20260907-193858-full-followup/summary.json`, including
  dependency audits, extension compilation, doctor, runtime smoke and **7 browser E2E tests**.
  All 18 Full checks passed across these two runs; the original failed summary is retained.
- Root `setup_server.bat` and `scripts/setup_lan_server.ps1`: **9 focused tests PASS**,
  covering interpreter selection, read-only planning, existing-directory protection, failures in
  each build stage, final verification failure and publication of verified output only.
- Actual batch entrypoint dry-run and reuse of the existing complete server directory PASS;
  the latter ran bundled Python layout and manifest validation against the real server package.
- Build-stage sequencing uses synthetic subprocess replacements in regression tests. A fresh
  download/conversion/build through the new wrapper was not rerun on this machine, which has
  Python 3.14 but no registered Python 3.12. The four underlying build commands are unchanged.
- Prior release CI run `34114208853` failed the Windows large-log parser test with
  `JobLeaseLostError`; its other jobs passed. That run does not establish green CI for this change.

## Previous: script server and quick client package (2026-09-07 18:43 CST)

- `scripts/run_lan_server.py` / `scripts/start_lan_server.bat`: initial setup, saved-data restart,
  real HTTPS readiness, LAN RBAC admin access, public certificate export and exclusion of backup while
  running PASS. Evidence: `artifacts/lan/script-server-smoke-20260907/smoke-result.json`.
- Stopped-server backup returned `Backup verified`; matching archive retained under that isolated
  server's `backups` directory. No service, scheduled task, firewall rule or system trust was installed.
- Focused client/config/runner tests: **26 passed** across the two targeted selections.
- Client package `cf4dafdaa87fb4e3`, **20 files / 40,046 ZIP bytes**, includes the concise user guide.
  Final handoff files: `artifacts/lan/quickstart-20260907` (client ZIP, two separate Markdown guides, SHA256).
- The final ZIP was extracted and installed into an isolated directory; manifest checks, guide byte equality,
  content scan and remote-only launcher dry-run PASS. No desktop shortcut or user trust store was changed by tests.
- Full regression `20260907-183320-full`: **18/18 stages PASS**, 591.53 seconds;
  backend **469 passed / 1 skipped**, coverage **80.48%**, Edge E2E **7 passed**.
  Dependency audits, repository harness, Golden gates, runtime smoke and local doctor passed.
  External Docker/PostgreSQL/Qdrant and GitHub CI were not run; company CodeAgent still needs target-machine acceptance.

## Earlier increment: two real CLI clients over HTTPS (2026-09-07)

- `scripts/verify_multiclient_cli.py`: final successful full run
  `artifacts/lan/multi-cli-20260907-d/result.json`, 355.64 seconds; prior complete run `multi-cli-20260907-c`
  also passed in 262.71 seconds. Two authenticated Codex CLI
  processes each completed two planning rounds and persisted one diagnosis plus one HTML report.
- Client A was terminated after method reading and resumed the same durable host session in a new
  CLI process while client B remained alive. Both final exit codes were zero. Cross-principal host-run
  reads through new MCP sessions were rejected.
- Actual packaged local retrieval audit: **10 Embedding / 2 Reranker / 0 backend Chat** calls, all E/R successful.
- Post-shutdown read-only verification in final `result.json`: published method, both cases and analyses
  retained; 6 confirmed facts use current-case logs, both sets of 5 citations valid, report SHA256 values match.
  Test credentials revoked and owned server processes shut down. One model-stream network retry recovered.
- Verifier, host session/diagnosis/runtime and MCP transport/auth/integration/registry regression:
  **39 passed** in 19.84 seconds; repository harness **24/24 PASS**.
  This increment changes validation tooling and documentation, not packaged product runtime code.
  The most recent product Full remains the 16:04 CST record below; CI was not run.
- Physical multi-machine networking, company CodeAgent, ten concurrent model jobs, and disk failure are
  outside this result. Setup investigations and reproduction steps are recorded in
  [local multi-client validation](docs/local-multiclient-cli-validation.md).

## Previous Full: personal knowledge and pilot candidate (2026-09-07 16:04 CST)

- `scripts\validate_all.bat Full`: PASS, **18/18** stages, run
  `artifacts/validation/20260907-155428-full/summary.json` (571.21 seconds).
- Backend **461 passed / 1 skipped**, coverage **80.48%**; Edge browser **7 passed**.
  Includes migration 0021, independent authors, immutable Web/CLI views, publisher/admin approvals,
  initial publication, crashed publication recovery, full server restore, human attestation and Markdown coverage.
- Python/npm production dependency audits, VS Code compilation, local doctor, runtime smoke,
  repository harness and Golden quality/time gates PASS. Existing SQLite ResourceWarning and Node
  color/annotation warnings remain; Docker/PostgreSQL/Qdrant external acceptance and GitHub CI were not run.
- Earlier run `20260907-153651-full` exposed two stale test assumptions (17 tools and no test principal),
  corrected and included in the passing run. `20260907-154646-full` exceeded the unchanged 5-second graph
  budget during concurrent package assembly; the final isolated Full passed without relaxing the gate.
- `artifacts/lan/transport-pilot-b-20260907/result.json`: actual Caddy HTTPS, engineer REST/MCP,
  GW/AP uploads, origin rejection and token revocation PASS; 10 distinct principals, 40 simultaneous
  case reads/writes, zero failures, P95 0.142 s. Not a ten-model-job load test. Initial smoke used the wrong
  REST token header; corrected to the established X-API-Key contract before this successful run.
- `artifacts/lan/retrieval-pilot-20260907.json`: actual packaged GGUF E/R PASS, 14 normalized 768-D vectors,
  relevant document first, 9 embedding / 1 reranking / 0 Chat calls. Does not establish upstream model equivalence.
- `artifacts/lan/knowledge-evolution-cli-pilot-20260907.json`: real Codex CLI consumed synthetic retrieved
  personal corrections; same/similar/different-cause/insufficient-evidence answers and citations **4/4 PASS**.
  No company data or backend Chat. This is answer synthesis after retrieval, not full interactive MCP acceptance.
- Core and GGUF 0.3.1 package smoke PASS; server directory `artifacts/lan/server-pilot-20260907`,
  light client `artifacts/lan/client-pilot-20260907/GWAP-Client-e365cda6ba051b60.zip`.
  `artifacts/lan/pilot-source-package-comparison.json`: 236 backend/frontend/Skill files matched.
- Server PowerShell syntax and complete backup/restore tests PASS. Windows service installation,
  scheduled-task execution, actual upgrade rollback, corporate network clients and off-machine restore
  remain target-machine acceptance work. See [pilot handoff](docs/pilot-handoff-20260907.md).

## LAN and knowledge iteration work in progress (2026-09-07)

- Baseline Full: `artifacts/validation/20260907-093626-full/summary.json`, 18/18 stages PASS,
  backend 416 passed / 1 skipped, coverage 80.44%, browser E2E 5 passed.
- Existing launcher/portable focused regression: 19 passed.
- LAN profile, model gates and access-control regression: 27 passed; Windows connector tests: 2 passed;
  later LAN/Caddy/client public-certificate regression: 24 passed.
- Fresh portable `artifacts/portable/lan-m1b-20260907/debug-platform-windows-x64`: manifest, isolated
  runtime, backend readiness, Vue route, bundled recorded AP-offline demo PASS. Building from a venv
  exposed and fixed the copied-Python-redirector defect (`sys._base_executable` is now used).
- Real HTTPS/RBAC/MCP: `artifacts/lan/transport-m1-20260907/result.json` PASS. Actual Caddy TLS,
  engineer discovery, GW/AP multipart uploads, Vue route, MCP session/status, Origin rejection,
  REST and live-session token revocation verified. No Windows services installed, no machine/user
  certificate-store changes, no GGUF or generative model calls in this transport smoke.
- Memory/migration/knowledge-governance regression: 10 passed; new candidate publication, expiry,
  real-resolution and recurrence regressions: 3 passed. Frontend typecheck/build PASS.
- Publication/migration tests: 9 passed; publication + existing governance/method tests: 12 passed.
  Edge publication E2E: 1 passed (12.0s), covering edit-to-proposal, old content still active,
  human submission/approval, persistent background publication and a matching manifest.
- Real four-process Windows credential contention: 20 consecutive passes after resolving the
  stable parent directory for mutex identity, rather than the concurrently replaced token file.
- Fresh Core `artifacts/portable/lan-m3b-20260907/debug-platform-windows-x64` self-check, backend,
  Vue route and recorded demo passed. GGUF ZIP 0.3.0 under `artifacts/installer/lan-m3-20260907`
  passed real E/R and packaged app smoke; no Setup.exe was produced in this invocation.
- LAN package `artifacts/lan/server-m3-20260907`: real HTTPS/RBAC/MCP transport PASS in
  `artifacts/lan/transport-m3-20260907/result.json`; real packaged retrieval PASS in
  `artifacts/lan/retrieval-m3-20260907.json`: 14 finite normalized 768-D vectors, relevant result
  first, 9 local embedding calls / 1 local reranker call / 0 backend Chat calls, clean shutdown.
  This is synthetic sanity coverage, not upstream equivalence or complete diagnostic quality acceptance.
- Full `20260907-113656-full` stopped at the unsynchronized workflow OpenAPI contract (fixed).
  Full `20260907-113802-full`: 450 passed / 2 failed / 1 skipped, coverage 80.59%; failures were
  the health-test module patch target and real Windows credential contention described above.
  Post-fix Full `20260907-115000-full`: **18/18 stages PASS**, backend **452 passed / 1 skipped**,
  coverage **80.59%**, six Edge browser E2E scenarios passed (33.5s), including the new knowledge
  publication workflow. Remaining warnings are SQLite connection ResourceWarnings, VueUse PURE
  annotations, Node color settings, and extension development-dependency notices (production audit clean).
  The subsequent SQLite connection-close fix in the standalone retrieval verifier passed its targeted
  verifier/client tests (16 passed) and the real packaged retrieval run above; no application behavior changed.
- The above are incremental results, **not** final M0–M5 acceptance. Package directories are tested
  intermediate snapshots, not a final release (the latest Web historical-rollback form change is source-only).
  Real CLI reasoning, final synchronized packages, Windows service recovery, full server restore/upgrade,
  knowledge access scopes and the remaining knowledge evolution work still require completion.

## WebSkillMcp 0.2.0 additive MCP and component installer (2026-09-03)

Current source adds MCP session configuration without `--strict-mcp-config`;
the launcher never writes the user's `.cac` or other global CLI configuration.
The portable CodeAgent entry uses bundled Python, the package Skill, a separate
`data/workspace`, and a ConnectOnly call to the shared PowerShell entry. A
CurrentUser DPAPI credential permits unkeyed-local Web/CLI coexistence without
changing `.env`, configured API-key/RBAC policies or model/proxy settings.

- Final-source Full `20260903-155255-full`: `18/18` stages PASSED in `675.96s`;
  backend `416 passed, 1 skipped, 13 warnings` in `531.68s`, coverage `80.44%`.
  All five Edge/Playwright E2E scenarios pass (`33.1s`), including comprehensive
  Web diagnosis, multi-MD routing and Chat proxy configuration. All warnings are
  SQLite unclosed-connection ResourceWarnings, not failed assertions. This run
  includes the final Peek pipe, portable coordinator and new artifact-verifier
  regression tests. Ruff, diff checking and the `24/24` Harness also pass.
- Earlier Full `20260903-151923-full`: `18/18` stages PASSED in `567.91s`; backend
  `399 passed, 1 skipped, 2 warnings`, coverage `80.44%`; all five browser E2E
  scenarios passed, including comprehensive Web diagnosis and multi-MD routing.
  The warnings were SQLite connection ResourceWarnings, not failed assertions.
- Source launcher additive/ConnectOnly checks, portable coordinator tests,
  component projection/install tests and real Windows DPAPI/concurrency checks
  passed before assembly. Final external-workspace and artifact acceptance
  checks are recorded below.
- Rebuilt Core with Python `3.12.13`; the frontend was freshly built with Node
  `24.11.1` in the initial candidate and reused unchanged for the final launcher
  reassembly. The final GGUF build explicitly used `-SkipPortableBuild` with that
  newly verified Core, not a historical 0.1.0 package. No GGUF/runtime/API smoke
  gates were skipped. Local toolchain versions differ from CI's pinned Python
  `3.12.12` / Node `22.22.0`; this is not a clean CI release build.
- Both real GGUF inference checks and the assembled package's model activation,
  E/R APIs, recorded demo, source-line jumps and Web route smoke passed. The
  final Inno Setup compile completed in `226.281s`.

Final tested MVP artifacts under `artifacts/installer/webskillmcp-0.2.0-verified/`:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `GWAP-Debug-Platform-Setup-0.2.0-x64.exe` | 901991777 | `0564efa59184318a04c7d41ea37e3415626550ca41b25014232908be0207bed9` |
| `debug-platform-offline-gguf-0.2.0-windows-x64.zip` | 936413385 | `4e747fd348b2d1c247377fbe98c6bed320bdf69902b8ef5aa3a1e026cfa5dfcc` |
| `debug-platform-offline-gguf-0.2.0-windows-x64.provenance.json` | 15294 | `b6a5042d8dfc803c788630c7a194ed5004003a6fb3473b447482f4d4a414dabd` |

- Final package manifest SHA-256:
  `2b549c96301660bca40bf54d70f9ede1548e8f6d7a70d308b17423c65b4be3d5`.
- `20260903-verified-core-codeagent`: `2/2 PASS`, `41.58s`;
  `20260903-verified-gguf-codeagent`: `2/2 PASS`, `45.79s`. Both use the original
  packaged product with a simulated CLI, not a diagnostic wrapper. Owned-backend
  exit code `23`, Web-first reuse, unchanged shared DPAPI token, preserved existing
  Web process, external workspace output, synthetic user settings/model/proxy
  environment preservation, and cleanup of all owned ports/session/key files pass.
  The GGUF run enables both real `llama_cpp_local` providers. Chat calls are zero.
- `real-retrieval-validation.json`: `PASS`, `39.622s`. Two synthetic documents
  pass DRAFT -> IN_REVIEW -> ACTIVE through real governance APIs. Reindex completes
  with `14 x 768` finite, normalized persistent vectors; five candidates are really
  embedded and reranked, with the related document first. The single-case Recall@10,
  MRR and NDCG@10 are `1.0` (Precision@10 `0.1`); audit records nine successful local
  Embedding calls, one Reranker call and no Chat calls. Shutdown is clean. This is
  small-corpus retrieval sanity, not an upstream-equivalence Golden or proof of
  ANN recall against the stored vector generation.
- Earlier component matrix `component-verification-20260903-153136-957417c3`:
  `5/5 PASS`, `288.619s` (Core -> Auto/Core -> E -> R -> Full).
  `setup-verification-20260903-153808-97ae1046`: `PASS`, `745.298s`; real Setup
  `/TYPE=core/embedding/reranker/full` installations/upgrades each exit `0`, then
  uninstall removes only the owned application/uninstaller/shortcuts/registry.
  A 128-byte synthetic business-data marker is preserved. These Setup results bind
  to the earlier `a3e180...` EXE, not the final `0564ef...` EXE. Installer selection
  scripts are byte-identical; final-payload installation is checked separately.
- Final-payload matrix `component-verification-20260903-155643-46f4d482`:
  `5/5 PASS`, `319.855s`, bound to the final `2b549c...` manifest above. Core,
  Auto/Core, Embedding, Reranker and Full have the expected actual files and pass
  installed-package self-check; external data is preserved, with no staging/backup
  residue. This run uses an isolated Chinese/space/bracket path and does not change
  global shortcuts, registry or the default application installation.

The earlier `webskillmcp-0.2.0-final/` candidate is superseded and must not be
distributed: managed startup stalled during NumPy native imports while another
thread performed a blocking stdin read. Proxy-free and proxied Core controls both
reproduced it. Non-blocking `PeekNamedPipe` fixed the original-product tests above;
`20260903-core-peek-shutdown` also records readiness, graceful exit `0` and closed
port in `12.03s`. The final child uses explicit Python `-u`. A separate PowerShell 7
header-array cast in the portable smoke verifier was corrected and the full
package smoke rerun successfully.

Setup remains `NotSigned`; independent clean Win11/CPU/enterprise-policy matrices,
upstream-equivalence Golden, real private CodeAgent inference/Ctrl+C/window-close,
interactive wizard GUI coverage and release CI are not claimed. RBAC retains the
configured legacy-admin policy; these tests do not prove personal-token enforcement.
No OpenCode or Chat-model quota was used. This turn does not publish the artifacts
or commit/push the source branch.

## WebSkillMcp publication preflight (2026-09-03)

The handoff source branch is `WebSkillMcp`. README and the deployment guide now
use the actual repository and branch, with a direct codeagent launcher path that
does not require the manual user-level MCP installation steps.

- Publication inspection found that Git would normalize the byte-pinned recorded
  JSON snapshot from CRLF to LF. The synthetic demo directory now has a `-text`
  attribute; all six staged blobs exactly match the verified working-file bytes.
  No sample, recorded snapshot or public method content was regenerated.
- A clean export of the final staged source using `core.autocrlf=true`, without
  this computer's `.env`, database or private methods, passed all `34` exporter,
  demo import, bundled-method and Skill-contract checks in `7.90s`. This validates
  the source-checkout boundary; it does not emulate a clean computer's installed
  Python dependencies or prove the real codeagent client.
- The maintenance-only snapshot exporter no longer embeds private example
  identifiers or a local default run ID. It requires `--run-id` and accepts an
  optional runtime `--redaction-map`; unmapped private device examples are rejected
  without echoing their values. Its focused exporter/import regression passed
  `23/23` checks, and the existing demo snapshot remains unchanged.
- Final pre-publication Fast run `20260903-112222-fast` passed all `9/9` stages in
  `30.05s`, including static checks, Harness, golden checks, focused regression and
  production frontend build. The most recent complete Web/backend Full run is
  `20260903-105852-full` below; it preceded these publication-only fixes.

Runtime credentials, databases, model weights and generated packages remain
excluded. The unrelated community-post draft is retained locally and not part of
the handoff. This is a source-branch handoff, not a rebuilt installer or a claim
that remote CI / real codeagent acceptance has already completed.

## Windows codeagent one-click source launcher (2026-09-03)

The source-tree `start_codeagent.bat` now prepares or reuses the backend, verifies
REST and MCP, and starts a Claude-compatible `codeagent` with session-only MCP
configuration and the current repository Skill. It does not change the existing
Web/host-model architecture, global CLI model settings, or installed user Skills.

- `python -m pytest backend/tests/test_codeagent_launcher.py -q --maxfail=1`
  completed with `10 passed in 68.40s` using Windows PowerShell 5.1, simulated CLI
  entrypoints, isolated SQLite databases and real backend REST/MCP handshakes.
- Coverage includes process-local simulated Program Files discovery, explicit
  `.ps1`/`.cmd` paths containing Chinese characters, spaces and brackets, saved
  path/URL and DPAPI token reuse on a second launch, endpoint-bound token rotation,
  source relocation and current-Skill resolution, model-setting preservation,
  no-model `-Check`, CLI exit-code propagation, owned-backend cleanup, foreign-port
  rejection and preservation of an existing API-key-protected backend.
- A final compatibility review identified Windows PowerShell 5.1 treating native
  stderr warnings as terminating errors during help/bootstrap output capture.
  The launcher now uses the actual native exit code: the additional regression
  preserves success for stderr plus exit `0`, failure for exit `17`, and restores
  the caller's error policy. Simulated `.cmd --help` also emits a warning while
  completing the real-backend launcher tests.
- After that compatibility fix, `scripts\validate_all.bat Full` passed all
  `18/18` stages in `artifacts\validation\20260903-105852-full` (474.20 seconds).
  Backend regression reported `340 passed, 1 skipped, 1 warning` with 80.44%
  coverage; the warning was an SQLite connection ResourceWarning. All five
  Edge/Playwright scenarios passed, including Web comprehensive diagnosis and
  multi-Markdown knowledge routing. Frontend and extension builds, production
  dependency audits, Windows doctor, runtime smoke and repository contracts also
  passed. This supersedes the pre-fix launcher Full run `20260903-104836-full`.
- The root BAT `-DryRun` completed without creating launcher state or starting
  processes. Ruff passed and repository Harness passed all `24/24` checks,
  including the five new required launcher/test files (`51` required files total).

No actual `codeagent`/Claude executable was available on PATH or in the checked
Program Files/LocalAppData locations. These checks prove launcher orchestration
with a simulated client, not the private fork's real model/Skill end-to-end
compatibility. Its required CLI flags are checked before startup. This change did
not consume model quota, rebuild an installer or publish an artifact; real
codeagent and clean-computer acceptance remain pending.

## Markdown routing and current-source Web/Codex validation (2026-09-03)

The current source extends the MCP registry from 15 to 17 tools with
`debug_get_knowledge_routing_context` and `debug_apply_knowledge_routing`, and adds
`POST /api/v1/knowledge-routing/import` for one-to-many Markdown staging. Both the Web
platform-model route and the real Codex host-model route completed successfully while
preserving one inactive `DRAFT` per input file and the existing human publication gate.

- `scripts\validate_all.bat Full` passed all 18 stages from the final source in
  `artifacts\validation\20260903-014606-full` in 426.05 seconds. The backend completed
  with `330 passed, 1 skipped` and 80.44% coverage; the production frontend build,
  dependency audits, VS Code extension build, Windows doctor, repository/architecture
  contracts and isolated backend/frontend runtime smoke all passed.
- The same Full run executed five Edge/Playwright scenarios. All five passed, including
  the existing recorded AP-offline demo, Web comprehensive diagnosis, knowledge
  curation, model-profile proxy handling, and the new two-file Markdown-routing case.
  The routing scenario classified the files into different governed categories and
  verified that both documents remained `review_status=DRAFT` and `active=false`.
- The final Full run includes the routing dialog's model-loading/file-selection race
  guard, model-change consent reset and structured error rendering, plus the service
  guard that returns every accepted reclassification to an inactive `DRAFT`.
- A real Codex CLI host-model run uploaded and routed batch
  `KRBATCH-28b4b098d8984ae2`. Its two documents were classified as
  `history.fault_trees` and `diagnosis.protocol_rules`; both remained inactive DRAFTs.
  The host path reported zero backend Chat calls and zero model-egress audit events,
  proving that the current Codex session model, rather than a platform Chat Profile,
  owned classification.
- A real Codex CLI comprehensive diagnosis against the current source completed durable
  session `HASESS-6bbdc379f6a04e7f` in four accepted planning rounds. All 27 fault-tree
  nodes reached terminal states: 20 `SUPPORTED`, three `EXCLUDED` and four
  `INSUFFICIENT_EVIDENCE`. The analysis retained 119 evidence records and generated
  report `RPT-b6fa95b301634af7`; backend model calls remained zero.

These results close the current-source Web and Codex CLI functional acceptance for
Markdown routing and comprehensive diagnosis. They do not claim a Claude Code result:
Claude CLI is not installed on this validation host. OpenCode was intentionally not run
at the user's request. Remote HTTPS/Personal Token deployment, a clean-computer package
matrix and real CLI negative-path acceptance remain separate deployment-hardening work;
they do not invalidate the successful local Codex paths recorded above.

## Skill + MCP Option A foundation validation (2026-09-02)

The host-reasoning foundation, unchanged Web regression, a real local Codex
Skill-to-MCP status smoke, and a subsequent complete local Codex synthetic-case
diagnosis now have evidence. The remote deployment and real Claude Code workflow
are still `IN_PROGRESS`; the combined capability is not reported as available.

- The focused protocol command covering `test_mcp_transport.py`,
  `test_mcp_auth_and_config.py`, `test_mcp_app_integration.py`,
  `test_debugplatform_mcp_registry.py`, `test_host_agent_sessions.py`,
  `test_host_diagnostic_runtime.py`, `test_host_diagnosis.py`,
  `test_agent_skill_contract.py` and the fresh-database migration case completed
  with `38 passed in 24.53s`.
- Those tests cover standard Streamable HTTP initialization/list/call, the real
  registry's 15-tool `tools/list` and `debug_status`, exact main-app `/mcp`
  mounting, shared Bearer/API Key/database Personal Token resolution, safe
  authorization/argument failures, case scoping, durable session
  snapshots/CAS/budgets, evidence and tool-receipt state, receipt-default hash
  validation, snapshot-drift rejection, and finalization into the existing
  immutable `AnalysisRun`. The finalization regression replaces
  `get_llm_provider` with a failing stub and records `backend_chat_calls=0`.
- Ruff passed for the focused MCP, HostAgentSession, host-diagnosis and Skill
  contract modules. All three generated Skill packages passed
  `quick_validate.py`; their non-symlink mirror and thin-package contract passed
  `5/5` checks, including the separate MCP YAML contract and its link from
  `workflow/skill.yaml`.
- Windows PowerShell 5.1 successfully ran Skill mirror `-Check`, the Claude +
  Codex MCP installer dry-run, and the REST large-file upload helper dry-run.
  These dry-runs contain no live token and do not prove a server connection.
- Repository Harness passed all `24/24` checks after linking the MCP contract
  without changing the existing REST/OpenAPI version contract.
- `workflow/mcp-tools.yaml` freezes the 15 intended `debug_*` names and their
  read/write and case scopes. The typed business registry and focused protocol
  path are tested, while the overall contract remains `IN_PROGRESS` until the
  remote HTTPS/RBAC, Claude Code and real CLI negative-path acceptance completes.
- `scripts\validate_all.bat Full` passed all 18 steps in run
  `20260902-113145-full` (`593.65s`). The backend result was `318 passed,
  1 skipped`, total coverage was `80.34%`, the frontend and VS Code extension
  built, both production dependency audits passed, Windows doctor and isolated
  runtime smoke passed, and all four Edge/Playwright browser cases passed. The
  machine first encountered a transient npm `ENOTEMPTY` while replacing
  `frontend\node_modules\lodash`; a clean `npm ci` recovered it before this
  successful run, so no product test failed in the recorded Full run.
- A real `codex-cli 0.151.0-alpha.7.2` process ran with `--ephemeral`,
  `--ignore-user-config`, a temporary SQLite database, a temporary local Bearer
  token and runtime-only MCP overrides. It loaded `$gw-ap-debug`, called only
  `gw-ap-debug/debug_status` over Streamable HTTP, and exited `0` after returning
  `inference_owner=host_cli`, `backend_chat_allowed=false` and
  `backend_chat_calls=0`. The model connection retried WebSocket timeouts and
  successfully fell back to HTTPS; the MCP call itself completed. This proves a
  local native-model Skill/MCP status path, not a complete diagnostic-case E2E.

Still pending before this capability can become `AVAILABLE`: mount the real
server behind its intended HTTPS reverse proxy; verify issued Personal Tokens,
roles and case permissions against that remote deployment; pass the real Claude
Code synthetic-case E2E; and exercise cancellation, invalid evidence and
snapshot drift through a real CLI (these boundaries already have service/MCP
automation). The unchanged Web Full/E2E regression is locally green but must
also remain green in the intended deployment environment.

## Current bundled-public-method Codex E2E and Core package (2026-09-02)

Codex CLI 0.152.1 using `gpt-5.6-luna` completed a fresh end-to-end diagnosis
against the current source tree, a new SQLite/data root, an empty knowledge base
and the demo-only public method bundle. The Skill was also loaded in a separate
real CLI status smoke because the full run's `approval_policy=never` blocked its
safe PowerShell file read; using `--approve-for-me` allowed the read and the
observed session remained `sandbox: read-only`.

- The full run strictly matched `DEMO-HOST-METHOD-LOG-ANALYSIS-V1` and
  `DEMO-HOST-METHOD-FAULT-TREE-V1` to their pinned SHA-256 values. The runtime
  exposed exactly two methods, 59 compiled patterns, 27 fault-tree nodes and
  root labels `场景1`/`场景2`/`场景3`.
- The one durable session completed after six accepted planning rounds. All
  27 nodes were attempted and concluded: 21 `SUPPORTED`, one `EXCLUDED`, five
  `INSUFFICIENT_EVIDENCE`, and zero `PENDING`. The three roots respectively
  excluded physical-link failure, supported AP-side UDM process/protocol failure,
  and retained network transport as insufficient. Persisted `last_round` values
  were non-zero, including round 6 for the final no-hit link-detection node.
- The immutable `AnalysisRun` used provider `host_cli`, engine `host_cli_mcp`,
  no model profile and 115 GW/AP evidence records; 15 distinct current-run
  evidence IDs were cited by the final diagnosis. Its `AgentRun` completed with
  `HOST_AGENT_COMPLETED`, backend token usage `0/0/0`, `backend_chat_calls=0`
  and zero `model.egress` audit events.
- The report SHA-256
  `8a28ea5740acf1bbdeec241877715bb448ae38104528f88124e0a2b3e7e66e71`
  matched its stored file. It contained locatable GW and AP filename/line
  references and no `EVT-`, `HASESS-`, `ARUN-`, `RUN-` or `RPT-` identifier.
- The CLI exited 0 and reported 338,812 total tokens. This is materially lower
  than the earlier run that repeatedly loaded full host history and is consistent
  with the compact run-read guidance, without attributing the difference to a
  single variable.
- A second CLI smoke successfully read `.agents/skills/gw-ap-debug/SKILL.md`,
  returned the required `debug_status` host-only flags and exited 0 with 11,154
  tokens.
- The Core portable package was rebuilt from the current dirty validation tree,
  including the canonical Skill, both installers and all six pinned demo files.
  Its 10,040-file integrity check, isolated Python check, dynamic-port installer
  dry-run, demo import/idempotency, backend readiness, frontend deep-route and API
  smoke passed. The 119,176,048-byte ZIP SHA-256 is
  `70e20e3825bda219081989ed7acf1699a4de75c57930febc94cc53123d845e54`.
  Because `source_dirty=true`, it is a local MVP validation artifact rather than
  a signed or commit-attested Release.

Local proof is stored under
`artifacts/validation/20260902-ap-offline-host-cli/bundled-proof.json`; the rebuilt
ZIP is under `artifacts/portable/skill-mcp-20260902/`. Both paths are Git-ignored.
The package smoke and current-source real Codex E2E use the same backend and exact
method hashes, but a second full Codex run launched from the ZIP was not repeated.
Final-tree Fast validation run `20260902-173238-fast` passed all 9/9 stages in
31.09 seconds, including repository harness contracts, the focused backend
regression and the production frontend build. Subsequent edits only synchronized
this validation narrative and the Git-ignored proof metadata.

## Complete local Codex AP-offline Skill/MCP validation (2026-09-02)

A real Codex CLI process using `gpt-5.6-luna`, the repository `$gw-ap-debug`
Skill, runtime-only MCP configuration, a temporary Bearer credential and an
isolated SQLite/data root completed the synthetic GW/AP case through the real
Streamable HTTP `/mcp` server. Connection retries resumed the same durable
`HostAgentSession`; no duplicate diagnosis session was created.

- The final host run was `COMPLETED` after five consecutive planning rounds.
  All 27 fault-tree nodes were attempted and concluded: 22 `SUPPORTED`, one
  `EXCLUDED`, four `INSUFFICIENT_EVIDENCE`, and zero `PENDING`.
- The root-cause branches had the required semantics: physical-link failure was
  excluded, AP-side UDM process/protocol failure was supported, and the
  packet-capture-dependent network-transport branch remained insufficient.
- The immutable `AnalysisRun` used provider `host_cli`, engine `host_cli_mcp`
  and no backend model profile. The linked `AgentRun` completed with
  `HOST_AGENT_COMPLETED` and persisted backend token usage `0/0/0`.
- The analysis retained 126 current-run evidence records with both
  `GW_PRIMARY` and `AP_SECONDARY`. The HTML report contained locatable lines
  from both synthetic source files; its downloaded SHA-256 matched the database
  and a broad scan found no internal evidence/session/analysis/report IDs.
- The isolated audit log contained no `model.egress` event and the planning
  record reported `backend_chat_calls=0`. Thus the Codex CLI model performed
  reasoning while the server only handled data, constraints and persistence.
- The CLI reported 816,313 input tokens plus 7,112,192 cached input tokens and
  5,194 output tokens. Review traced the excess to repeated full host-history
  reads during reconnect recovery. The Skill now defaults to compact
  `debug_get_host_run` reads and bounded `debug_get_evidence` calls.
- The MCP planning/finalization inputs now advertise the full nested Pydantic
  schema. The Skill schema explicitly requires `item_id`,
  `method_document_id`, `status`, `rationale`, `evidence_ids` and `next_action`,
  preventing malformed assessment objects from being silently ignored.
- Coverage entries now persist the planning round that last updated each
  conclusion instead of leaving `last_round=0`; a focused regression covers the
  behavior.

The read-only verification record for this temporary run is stored locally at
`artifacts/validation/20260902-ap-offline-host-cli/proof.json`. Runtime IDs and
the temporary credential are not part of the repository release.

The demo is now self-contained for clean-PC Host CLI replay. Two newly authored,
public synthetic methods are pinned by manifest and SHA-256 and are selected only
when the reserved demo case ID, exact GW/AP fixture hashes, roles, parse state and
dataset metadata all match. Runtime validation requires one method per role, 59
compiled patterns, 27 fault-tree nodes (13 flow, 11 decision, three root) and the
three root labels. Ordinary cases continue using the existing governed knowledge
and optional local-method loader. The focused demo/Host/MCP/Skill/portable suite
passed `40 passed` after these changes. The subsequent Full run
`20260902-155454-full` passed all 18 steps in `445.4s`: the backend completed
with `327 passed, 1 skipped` and `80.40%` coverage, the frontend and VS Code
extension built, production dependency audits passed, Windows doctor and the
isolated runtime smoke passed, and all four Edge/Playwright browser cases passed.

Historical evidence boundary: this earlier Codex CLI E2E used the pre-bundle local
governed method snapshot. The later current-tree acceptance recorded in the section
above closes the real Codex/public-method source path and rebuilds the Core package;
the remaining acceptance boundary is a duplicate full CLI run launched directly
from that ZIP, an independent clean-computer matrix, real Claude Code full-case
execution, remote HTTPS/Personal Token authorization, real CLI negative paths and
OpenCode installation/E2E coverage.

## Built-in recorded GLM-5.2 AP-offline demo validation (2026-09-02)

The repository now combines two pure-synthetic AP frequent-offline log fixtures
with a sanitized snapshot exported from the previously successful third-party
`wawapii.com` run configured for `glm-5.2`. The model plans, final synthesis,
fault-tree conclusions, evidence mapping, trace and usage are recorded outputs;
they are not a hand-authored deterministic diagnosis. Importing the snapshot is
idempotent and performs no new model egress.

- The allowlisted exporter omits credentials, encrypted key fields, raw prompts,
  memory bodies and private method bodies. It replaces private example device
  identifiers, rebases database/event IDs during import and pins the source
  result, evidence and both synthetic logs by SHA-256. The committed snapshot
  SHA-256 is
  `844e0ced48d69586b5393871e0f369be77c88ebf46786f4050dff09d78c71a29`;
  the source result and source evidence hashes are respectively
  `b5af75574ec34e585cede40a2d43190aaaaec0bf683853cbfd7dfc7d22be0164`
  and `d922e8b12bfbd9bedc13f0e3a9b839c4d909bb57652c530c53a0235fced3cea3`.
- Backend service/API regression verifies 2 artifacts, 74 parsed events,
  persistent idempotency, AP 27 match groups/62 exact hits, GW 39 groups/87
  hits, all repeated occurrence jumps, 152 evidence-catalog entries, 2 real LLM
  planning rounds and 27/27 fault-tree conclusions. The recorded diagnosis
  stopped with `DIAGNOSIS_CONVERGED_ROOT_CAUSE_IDENTIFIED` after 305,655 input,
  15,798 output and 321,453 total Tokens in 146,082 ms with zero retries.
- The 2026-09-02 `scripts\validate_all.bat Full` run at
  `artifacts/validation/20260902-113229-full` passed all 18 stages: backend
  `318 passed, 1 skipped`, 80.34% coverage, Harness 24/24, Golden 10/10,
  production frontend build, Python/npm audits and 4 Edge Playwright scenarios.
  The demo E2E verifies one-click import, authentic-recorded-result disclosure,
  triage expansion, exact source jumps, diagnosis/fault-tree rendering, report
  preview and zero browser console errors.
- `pypdf` was raised to 6.16.2 after the dependency audit found three issues in
  6.15.0; the final Python production audit reports zero known vulnerabilities.
- The complete GGUF staging package passed real BGE/Qwen inference, managed
  Profile activation and the recorded-demo checks. The rebuilt ZIP is
  937,032,215 bytes (`3fd4ab0699ea8778f01816133f98b728391d04e064ceed9bed87e129200f028e`);
  Setup is 902,853,773 bytes
  (`d6736ce0d2943b378f253d92e4c75b643cdf2453802664e8dadeb3783d23ddaa`);
  provenance is 26,631 bytes
  (`1a97f0f5a229d3a395d72b4608bc77258299d93e241efb8fca7482d70f4fdf7f`).
- The actual unsigned Setup passed silent first install at the fixed per-user app
  root. Its installed tree repeated integrity, real local BGE/Qwen, managed
  Profiles, frontend/API, recorded model usage, diagnosis and source-jump checks.
  Silent uninstall then removed the app tree, uninstaller and Start Menu group
  without targeting business data. The project Setup remains `NotSigned`.

Last validated: 2026-09-02 on Windows 11. The persisted demonstration proves the
historical model run against these fixed synthetic inputs; it does not assert that
a user's current endpoint, network or private knowledge base is available.

## Full-GGUF E/R experimental validation draft

On 2026-09-01 the repository-pinned `llama.cpp b10729` runtime (commit
`458681e1d5d4a29a1463c4732e03226cf384b997`) and the two Git-ignored model
artifacts were exercised on the current Windows 11 computer through
`scripts\model-runtime\smoke_runtime.py`. The check started two real
`llama-server` processes on dynamic loopback ports, used an ephemeral bearer-key
file, sent only synthetic Chinese diagnostic text and removed the processes and
temporary directory afterward.

- The locally converted BGE F16 artifact was 204,756,128 bytes with SHA-256
  `677d0074629b28c96860d258d04c5197996d3f422cba84badadd64090e085f35`.
  `/v1/embeddings` returned three finite, L2-normalized 768-dimensional vectors;
  the AP-offline diagnostic candidate scored `0.5609`, above the unrelated
  packaging candidate at `0.1655`.
- The pinned Qwen3 Reranker 0.6B Q8_0 artifact was 639,153,184 bytes with SHA-256
  `22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48`.
  `/v1/rerank` placed the diagnostic candidate first with `0.9997`, above the
  unrelated candidate at `0.0001`.
- The command ended with `Full GGUF E/R runtime smoke passed`. This is a real
  native HTTP inference check, not a mocked Provider test.
- The cache now includes the exact ten Microsoft VC143 x64 release CRT files
  extracted app-local from an immutable official VSIX. Source size/SHA and every
  DLL size/SHA passed validation; all ten source DLLs had valid Microsoft
  Authenticode signatures. Live-module enumeration confirmed both sidecars loaded
  `MSVCP140.dll`, `VCRUNTIME140.dll` and `VCRUNTIME140_1.dll` from the packaged
  `runtime/llama` directory rather than a machine-global runtime.
- The managed Profile recovery regression passed three phases: healthy bundled
  Profile, transient sidecar fallback, then automatic restoration after recovery.
  A separate regression confirmed an explicit user selection cancels restoration.
- Loopback model traffic is isolated from corporate proxies: launcher health
  checks use a proxy-free opener, sidecar environments preserve existing proxy
  settings while appending `127.0.0.1,localhost` to both `NO_PROXY` forms, and
  only managed GGUF E/R clients force `trust_env=False`. External model Profiles
  retain their configured/default proxy behavior.
- The pinned asset manifest passed its standard-library validator. The final
  focused release/portable/Profile suite passed `44/44`; Ruff passed; Repository
  Harness passed all `24/24` checks, including explicit workflow triggers,
  fixed toolchain/Action revisions, asset-lock rebinding, Git weight exclusion,
  artifact/provenance requirements and the Inno uninstaller boundary.
- An assembled experimental package was then built with version `0.1.0`, reusing
  the already built frontend and intentionally omitting Inno Setup for this run:
  `scripts/build_windows_gguf_installer.ps1 ... -Version 0.1.0
  -SkipFrontendBuild -SkipSetupExe`. The package self-check passed, and the
  assembled application started with a temporary fresh data root. Both bundled
  Profiles activated automatically. A real platform API probe returned two
  768-dimensional Embedding vectors, while the Reranker returned the relevant
  document at `index=0`.
- The experimental ZIP is 934,065,370 bytes with SHA-256
  `3cbb5fd72f80bfeed429fd05abf2cdb252535abb266ca62df3839075fb5b15f6`.
  Because this run used `-SkipFrontendBuild` and `-SkipSetupExe`, it proves the
  assembled Core/sidecar/Profile/API path but is not a clean release build.
- Inno Setup 6.7.1 was subsequently obtained through an immutable GitHub Release
  download. The downloader's Authenticode status was `Valid`, signed by
  `Pyrsys B.V.`. Its portable compiler produced
  `GWAP-Debug-Platform-Setup-0.1.0-x64.exe` in 213.469 seconds. The candidate is
  899,941,808 bytes with SHA-256
  `08499a54aabc7bf7e7e4f0e5b0256b1b79d3d60126e70bcfd7341b62c669fb1a`;
  Windows reports `FileVersion=0.1.0` and
  `ProductName=GWAP Debug Platform`. The generated project installer itself is
  currently `NotSigned`; the valid Pyrsys signature applies to the Inno Setup
  downloader, not to this project output.
- A subsequent high-risk upgrade review found that this first Setup candidate
  merged files directly into `{app}`. Such an upgrade can leave files that a
  newer package no longer contains, after which the immutable package manifest
  rejects the tree. The tracked Inno source now extracts the complete payload to
  `{tmp}\GWAPDebugPlatformPayload`, places `package-manifest.json` last, and uses
  its `[Files]` `AfterInstall` callback to invoke
  `install_local.ps1 -NoLaunch -NoShortcuts`. The callback waits for PowerShell
  and raises a fatal error on launch failure or any non-zero exit. The existing
  staging/backup publisher therefore removes obsolete files and restores the
  previous tree on a publication failure. Inno alone owns shortcuts and deletes
  only the exact `{app}` tree on uninstall; the runtime data root is not an
  uninstall target.
- The shared Setup/ZIP publisher now also uses a PowerShell 5.1-compatible named
  mutex. The contract covers bounded waiting, abandoned-mutex ownership and
  release in `finally`. When the destination is absent, recovery is allowed only
  for exactly one regular managed backup after its manifest shape and required
  runtime files pass basic checks; multiple backups are an explicit failure.
- The revised script passed a focused Inno Setup 6.7.1 compilation using a tiny
  synthetic payload, the GGUF release contract tests and Repository Harness.
  The 899,941,808-byte Setup hash above predates this correction and is retained
  only as historical compiler evidence; it is not the artifact for the revised
  atomic installer source.
- The final build then ran without any `Skip*` option using CPython 3.12.13 x64,
  a fresh `npm ci`/Vue production build, the pinned component cache and Inno
  Setup 6.7.1. Real E/R inference, app-local CRT module checks, package integrity,
  managed Profile activation and platform HTTP smoke all passed before packaging.
  The resulting ZIP is 934,791,944 bytes with SHA-256
  `5c3a1a358872835f14ee636ab2eedd9074c14d96ddc022fbee004e7ca5a63128`.
  Its component provenance is 26,631 bytes with SHA-256
  `9d050058cf3c124f1c3a83b3613ae6717b0dbe6cd770a3d6b49c655cf8889eed`.
- The revised `GWAP-Debug-Platform-Setup-0.1.0-x64.exe` was compiled in
  193.406 seconds. It is 900,673,904 bytes with SHA-256
  `d010b7b6779b466c401df3ebdcd18a8636b38d355f92fe5d48dc5eb246adf29f`;
  Windows reports `FileVersion=0.1.0`, `ProductName=GWAP Debug Platform` and
  `CompanyName=mrrabbitss`. The project installer remains `NotSigned`.
- The ZIP publisher passed first install and a second atomic upgrade into an
  isolated workspace directory, followed by real bundled E/R and platform API
  smoke. A failure-state simulation renamed the installed app to a single valid
  `backup-<32 hex>` directory; the next run verified and restored it, then
  completed the upgrade without leaving a backup or staging directory.
- The actual Setup passed silent first install and silent overwrite upgrade at
  `%LOCALAPPDATA%\Programs\GWAPDebugPlatform`. Both installed generations passed
  integrity, bundled E/R/Profile, readiness, frontend fallback and API liveness
  checks. Silent uninstall removed the exact app and uninstaller trees; runtime
  data was outside the uninstall target.

This record does **not** make the bundle release-ready. At the time of this
draft, the following gates remain open:

- a full knowledge-index and hybrid-RAG quality/failure-injection run has not
  yet been recorded with both packaged Profiles;
- the generated Setup.exe has not been release-code-signed or validated as a
  trusted signed project artifact;
- the ZIP/Setup pair still needs the same install, cold-start, upgrade, recovery
  and uninstall matrix on a separate clean Win11 x64 CPU-only computer without
  a development environment/cache;
- BGE cosine/Recall parity and Qwen ordering/NDCG parity against their pinned
  upstream implementations have not passed a dedicated Golden comparison;
- the BGE generated hash is locked in the asset manifest, but its verification
  status has not been promoted from `hash_locked` to `quality_verified`; the
  bundle therefore remains `experimental_unverified`;
- the new manual full-installer workflow and strict published-Release path have
  not both completed successfully in GitHub Actions.

The published-Release workflow intentionally calls `--strict-release`; with the
current experimental manifest it must fail. The final ZIP and unsigned Setup
recorded above remain experimental validation artifacts until every gate above
is completed and this record is updated with the signed Setup hash and
clean-machine evidence.

## Last complete Full regression result

On 2026-09-02, `scripts\validate_all.bat Full` passed all 18 stages in 445.4
seconds. Its Git-ignored machine-readable summary is
`artifacts\validation\20260902-155454-full\summary.json`.

- Unified `scripts\validate_all.bat Full`: all 18 stages passed. The run produced
  a machine-readable summary and per-step logs under the Git-ignored
  `artifacts\validation` directory.
- Backend tests: 327 passed and 1 external-service test skipped locally.
- Backend line coverage: 80.40%, above the enforced 75% quality gate.
- Golden Dataset: all 10 evaluators passed for parser output, document curation,
  the 36-case synthetic scenario-distribution contract, Code Graph, Commit Graph,
  memory isolation, hybrid RAG and bounded Agentic Search. The matrix is explicitly
  a distribution/schema gate; the core incident remains the full executable fixture.
- Browser E2E: 4 complete Edge scenarios passed in an isolated runtime. They
  covered the persistent no-model AP-offline demo plus TXT, HTML, DOCX and PDF
  upload/preview, draft generation,
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
- Repository Harness: all 24 contracts passed across 46 required files, 20
  tracked Markdown files, dependency-update targets and 29 allowlisted Workflow
  operations. The contracts include the portable runtime, .NET-only package
  hashing, model-isolation, atomic managed-model publication, full-GGUF supply
  chain, installer boundaries and release-workflow checks.
- Architecture checks: 136 Python files and 16 Vue files passed file-size,
  dependency-boundary, required-module and complexity ratchets. The highest
  measured Python cyclomatic complexity was 50, at the enforced limit of 50.
- Backend dependency consistency, lock synchronization, Ruff and Python
  compilation: passed.
- Vue TypeScript check, production Vite build and VS Code extension TypeScript
  compile: passed.
- Python, frontend and VS Code extension dependency audits: no known vulnerabilities.
- Fresh isolated SQLite schema upgraded through Alembic revisions 0001-0017.
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

The current complete run used Windows PowerShell 5.1 and included Alembic 0017,
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

## Approved third-party live workflow result

On 2026-08-31 a user-approved OpenAI-compatible third-party endpoint was exercised
through an encrypted local Chat profile requesting `glm-5.2`. The credential and
endpoint were not written to source, tracked documentation, validation artifacts or
command output. The synthetic frequent-offline demonstration completed in the real
platform rather than through a direct model-only probe.

The compatibility probe and platform profile both sent `model=glm-5.2`, but the
third-party response metadata identified the served model as `glm-5.3`. Therefore
this run proves that the requested route and full platform workflow work; it does not
prove that the gateway pinned the upstream model to GLM-5.2. Strict model-version
attestation requires confirmation from the gateway provider or use of an endpoint
whose model identity is contractually pinned.

- Both AP and GW log plans were accepted with structured Thinking disabled, no
  deterministic planner fallback and all two required method documents read. AP
  matched 32 of 36 parsed events; GW matched 36 of 38.
- After the precision gate, the AP three-tier view contained 15 LLM-relevant groups
  (44 occurrences), 12 method-required groups (18 occurrences) and 4 other events.
  GW contained 18 LLM-relevant groups (50 occurrences), 21 method-required groups
  (37 occurrences) and 2 other events. A broad model-selected rule was rejected for
  each file instead of being promoted into the highest relevance tier.
- The final current-code typed read-only diagnosis Agent completed in 2 of 20
  allowed rounds, stopped with `DIAGNOSIS_CONVERGED_ROOT_CAUSE_IDENTIFIED`, and
  attempted and concluded all 27 fault-tree nodes: 21 `SUPPORTED`, 1 `EXCLUDED`
  and 5 `INSUFFICIENT_EVIDENCE`. Its first round executed all four validated model
  calls plus one separately budgeted policy evidence read. That read returned the
  exact same 14 IDs as its causal source search, proving it neither displaced the
  fourth model call nor invented evidence.
- Final model synthesis passed evidence validation. Provider-reported aggregate
  usage was 305,655 input Tokens, 15,798 output Tokens and 321,453 total Tokens;
  the recorded diagnosis duration was 146,082 ms, with zero failed trajectory
  events and no deterministic fault-tree fallback. A separate case-chat call through
  the same profile also returned a non-empty evidence-backed answer with non-zero
  usage.
- In-app browser verification found zero console warnings or errors. One repeated AP
  result expanded to all three exact lines 21, 30 and 39; its second occurrence
  navigated to line 30. The final report used filename-line labels, exposed no
  internal evidence IDs, and rendered confirmed facts last.

Focused planner, triage, fault-tree evidence-gating and model-snapshot regression
completed with 51/51 tests passing. The final `scripts\validate_all.bat Fast` run
passed all 9 stages in 26.86 seconds, including
Ruff, compilation, all 18 Repository Harness contracts, architecture boundaries,
Golden evaluators, fast backend regression, Vue type checking and the production
build. Its Git-ignored summary is
`artifacts\validation\20260831-213112-fast\summary.json`.

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

## AP frequent-offline synthetic demonstration

On 2026-08-29 the configured GLM-5.2 Chat profile was exercised against the two
Git-ignored local method documents and the checked-in synthetic GW/AP demo logs. Both
log-triage plans were accepted by the model without planner fallback, fixed structured
Thinking to `disabled`, and proved that all 2 required methods were read. The AP scan
matched 32 of 36 parsed events; the GW scan matched 36 of 38. Both retained all exact
occurrences and the Skill-derived meaning and source section for every grouped result.

The typed read-only diagnosis Agent completed in 2 of the allowed 20 rounds. It made
8 model-directed tool calls plus the policy tools, consumed 120,163 provider-reported
planning Tokens (164,753 including bounded tool-output accounting), and stopped with
`ALL_FAULT_TREE_ITEMS_ASSESSED`. All 27 nodes were attempted and concluded: 21
`SUPPORTED`, 1 `EXCLUDED`, and 5 `INSUFFICIENT_EVIDENCE`. The root semantics were
checked independently: physical-link root excluded, UDM root supported, and network-
transport root left insufficient. The UDN/AP-MAC node was supported only by a local,
content-safe derived comparison; zero memory/knowledge IDs were accepted as case
evidence. The final model synthesis encountered an upstream `MODEL_RATE_LIMITED` and
correctly retained the evidence-constrained deterministic report instead of returning
an empty or ungrounded diagnosis.

The exact successful GLM keyword plans were then replayed through the new precision
policy: the AP plan retained 27 of 30 supplemental keywords and the GW plan retained
29 of 30, rejecting broad terms including `Start` and `FAILED`. A subsequent fresh
endpoint attempt remained rate-limited, so it is not reported as a successful online
rerun. The final no-model seed replay (`CASE-1a59016cc27c48b3`) passed method
preflight, all 27 fault-tree nodes, the same three root statuses and four expected
causal hypotheses. Its guard now rejects healthy UDM ready/heartbeat-success lines as
failure evidence, calculates heartbeat timeout from the reported current, last-event
and timeout values, keeps link-detection, physical-link and network-transport failures
separate, and checks numeric parent/topology boundaries. The generated report showed
both GW and AP filename-line operands for the local UDN/MAC comparison and exposed no
internal evidence IDs. Manual in-app-browser verification found zero console errors,
confirmed triage-to-source navigation to the AP file at line 21, and rendered local
derived evidence as the GW filename at lines 21 and 14 with no visible `LDE-*` key.
The root `故障树.md` and `日志分析.md` remain untracked and were not copied into the
repository or validation artifacts.

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
# 2026-09-08 0.3.3 Embedding 超长输入定点修复

- 故障：知识按 1800 字符分块，超过内置 BGE 512-token 上限时，llama.cpp 返回 physical batch size 错误。
- 新增 4 项定点检查通过：Unicode 原文逐字完整、特殊 token 边界、输入顺序/尾部参与加权/归一化、无法切分输入明确失败。未运行既有回归套件。
- 随原 0.3.3 包的真实 CPU BGE 模型复现 541-token 错误；修复后 541、2252、7-token 输入均返回有限、归一化的 768 维向量。短输入与独立原调用 cosine=0.9999997325（批内数值差异最大约 0.000204）。记录：`artifacts/lan/embedding-hotfix/result.json`。
- 小补丁在隔离目录验证首次安装、重复安装、模拟清单写入失败回滚和运行锁拒绝；数据哨兵保持原样。
- `GWAP-Server-Embedding-Fix-0.3.3.zip`：13,632 字节；SHA-256 `c000c6581836b28f9d796a23bb0376a851415ce3218bf1fa3a54bdf1baa073e1`。
- 限制：长输入采用完整分窗后 token 加权向量，不代表长文检索质量评测通过；公司服务器应用补丁与实际知识上传尚待用户验证。未重跑 Full，未手动触发 CI。
