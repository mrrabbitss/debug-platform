# Validation record

> QA history only. Diagnostic agents must not use this file as case evidence,
> an expected answer, or a substitute for the current run's authenticated
> context and tool trace.

Date: 2026-09-01 (Asia/Shanghai)

Source baseline: Debug Platform `main` / `origin/main` commit
`181dae7b26863accd02e8206895d3cfb670739ac`.

## Current v0.6.0 release candidate

- Candidate version: `0.6.0`.
- External diagnostic Skills can provide explicit fault-tree and log-analysis
  role documents. Run-scoped imports are the default; persistent imports are
  explicit and are snapshotted into an immutable run generation before case
  execution.
- Each Skill `run`/`diagnose` execution binds its case to a content-addressed
  method generation. The backend verifies binding schema, runtime selector,
  expiry, generation identity, and content hashes; the runner verifies the
  owner token for renewal/release. A timed-out, unreachable, or interrupted job
  retains a finite renewable lease instead of deleting a generation whose
  outcome is unknown. The guaranteed client window is capped at 24 hours;
  longer backend queues are best effort. Standalone low-level commands are not
  claimed to provide this orchestration-level pinning.
- The published `skillonly` Git commit and tree IDs are the authoritative
  release fingerprint; the release file count is intentionally not used as a
  mutable identity check.
- Vendored runtime files: 137.
- After Git text/EOL normalization, 129 runtime files match the source commit,
  6 are deliberate Skill portability patches, and 2 bundled method files
  originate from the local source environment rather than a path in that
  commit.
- Source-mapping schema `gw-ap-debug-source-mapping/v2` pins the normalized
  source hash, normalized runtime hash, and canonical normalized-diff hash for
  all six patched files:
  `config.py`, `system.py`, `diagnostic_methods.py`, `log_triage.py`,
  `constraints.lock`, and `uv.lock`.
- Provenance tree SHA-256:
  `2c391be3b8f284727836d9607a8863f62e200fdd57c0ceaeede6ac91d3f1291c`.

## Automated validation

- `python -B -m unittest discover -s tests -v`: 83 passed.
- Skill Creator `quick_validate.py`: passed.
- `python -B scripts/check_provenance.py`: passed.
- `python -B scripts/check_source_mapping.py --source-repo
  D:\\GRXM\\debugplatform`: passed with 137 runtime files, 129
  normalized-identical files, 6 verified patches, 2 local method files, and 0
  failed or unexpected mappings.
- `python -B scripts/validate_release.py`: passed.
- `python -B scripts/ci_deterministic_smoke.py --check-only`: passed through
  both portable launchers.
- A real Windows 11 / Python 3.14 full smoke passed through the PowerShell
  launcher. It bootstrapped the runtime, imported a synthetic external Skill
  with explicit role paths, completed a deterministic diagnosis, verified both
  role hashes and canary matches, left the persistent pointer unchanged, and
  cleaned the case binding.
- Windows user-registration script: plan and isolated three-client registration
  passed without overwriting pre-existing paths. The current installer also
  recognizes a canonical checkout already located at Claude's real discovery
  directory; native Claude execution remains pending below.
- Windows launcher: automatic Python selection and explicit-interpreter
  `doctor --check package` passed under Windows PowerShell 5.1.
- POSIX registration script: Bash syntax check and plan passed locally; the
  Ubuntu CI job provides the authoritative symlink execution environment.
- POSIX launcher: Bash syntax and explicit-interpreter package doctor passed.
- Synthetic AP host-agent preparation through the Windows launcher passed. The
  exported manifest records the candidate `skill_version`; continuation instructions
  contain both platform launchers and no bare `python debug_platform_skill.py`
  command.
- `host-search-hypothesis-log --help`: passed and exposes no fault-tree
  binding option.
- Fresh dependency-only bootstrap on Python 3.14: passed with the locked
  backend environment and no editable source install.
- Fresh-checkout CI covers tests, provenance, exact source mapping, release
  envelope/frontmatter checks, wrapper checks, and one full Windows
  deterministic smoke. Skill Creator `quick_validate.py` remains the separate
  local format check recorded above.
- Candidate and retained validated-run credential-shape scans: zero matches.

The regression suite additionally covers immutable method generations,
case-bound leases, kernel-backed writer locking, timeout/network/Ctrl+C cleanup,
content-addressed persistent snapshots, atomic multi-Skill activation, explicit
role-path selection, import traversal limits, same-lock host budget enforcement,
case-job lease bounds, cached-pack hash validation, late host export, and
runtime capability mismatch rejection.

### Current native CLI status

- Codex CLI `0.151.0-alpha.7.2` natively discovered the installed v0.6.0
  Skill from `C:\\Users\\23173\\.agents\\skills\\gw-ap-debug`. A built-in-model
  session read the candidate `SKILL.md`, executed only the documented Windows
  `doctor --check host-agent` launcher path, and returned
  `skill_version=0.6.0`, `package_ready=true`, and `host_agent_ready=true` with
  exit code 0. WebSocket sampling timed out before the CLI's HTTPS fallback
  completed successfully; no third-party model endpoint was used.
- Claude Code is not installed on this validation computer. The Windows-first
  registration, slash-command guidance, and wrapper contract are covered by
  tests, but native Claude model execution remains pending.
- OpenCode was intentionally not run for v0.6.0 at the repository owner's
  request because the available model quota is exhausted.

## Historical v0.5.0 composable diagnostic Skill validation

An isolated complete diagnostic Skill supplied explicit fault-tree and
log-analysis Markdown through frontmatter metadata. The fixture also linked an
inert execution canary outside the selected role documents.

- dry-run selected four contained Markdown files and both diagnostic roles;
- first import returned `IMPORTED`; the identical second import returned
  `UNCHANGED` and did not duplicate content;
- pack ID: `complete-wifi-diagnosis-821eef5c120e`;
- composed fault-tree SHA-256:
  `73d85d347a18c110ef58c93d409b242d8a1baaa607836e3f81c0d105d249d43b`;
- composed log-analysis SHA-256:
  `ad537310418b0d7b6aff76c709c4b0d9eeae00741d87a6b3dbf576ecd817c695`;
- each applicable external signature occurred exactly once in each composed
  role, while `IMPORTER_MUST_NOT_EXECUTE_CODE` occurred zero times;
- the unchanged runtime compiler produced four external Pattern forms and two
  additional fault-tree decisions, for 165 Patterns and 29 total tree items;
- traversal outside the imported Skill root, file/count/byte bounds,
  idempotence, removal/rebuild, CLI parsing, and automatic role classification
  are covered by the 49-test suite.

The Windows launcher initially exposed a real integration defect: PowerShell
abbreviated public `--skill` to the wrapper's internal `$SkillArguments`
parameter. Renaming the passthrough parameter to `$CliPassthrough` fixed the
collision. The actual Windows launcher then passed preview, import, repeated
import, list, doctor, bootstrap, and `run --diagnostic-skill` commands.

### Native Codex CLI acceptance

`codex-cli 0.150.0-alpha.12.2` natively discovered the exact candidate through
an isolated project `.agents/skills/gw-ap-debug` junction and read v0.5.0. Its
model session executed the Windows launcher to preview/import/list the method
pack, verify the active hashes/canaries, bootstrap a fresh Python 3.14 state,
and run a deterministic diagnosis with `--diagnostic-skill`.

- parse, Triage, and analysis jobs: all `COMPLETED`;
- imported method matches: four (template and fault-tree signatures for both
  synthetic external events), each with exact log locations;
- exported manifest: `skill_version=0.5.0`,
  `analysis_status=COMPLETED`, `execution_mode=deterministic`, and one recorded
  Markdown-only imported method pack;
- output bundle:
  `C:\Users\23173\AppData\Local\Temp\gw-ap-debug-v050-codex-20260830-01\codex-state\runs\20260829T173501Z-CASE-537eadf681f5473f`;
- the CLI's WebSocket requests timed out and automatically fell back to HTTPS;
  the test still exited 0.

This intentionally validates native Skill discovery plus the complete local
import/parse/Triage/deterministic-diagnosis path. It does not claim host-agent
finalization: synthesis was correctly `SKIPPED`, fault-tree state remained
0 attempted / 0 concluded / 29 total, and the stop reason was
`MOCK_PROVIDER_DETERMINISTIC_BASELINE`.

## Historical v0.3.4 host-agent validation

A fresh isolated run used the current Codex desktop model as the host reasoning
model and the exact v0.3.4 candidate snapshot. This validates the current
model-independent CLI tool contract and finalizer. It is not presented as a
native `codex` executable discovery test.

Synthetic input SHA-256:
`64a97abd4b8d6d539d657ea54bc49c80b4d7f169f7ece8a9af9439c0ef7dcab9`.

- extensionless `collectDebuginfo` parse/Triage/baseline export: passed;
- AP-only upload provenance inferred `case.device_type=AP` without an explicit
  device-type override;
- authenticated diagnostic methods read: 2/2;
- fault-tree nodes attempted and concluded: 27/27;
- authenticated host calls: 14 total;
- node-bound searches: 12 over rounds 1-4;
- hypothesis-only raw-log searches: 1 in round 5;
- `host-validation.json`: `accepted=true`;
- `manifest.json`: `host_agent.status=VALIDATED`;
- fault-tree status counts: 0 `SUPPORTED`, 0 `EXCLUDED`, and 27
  `INSUFFICIENT_EVIDENCE`;
- the hypothesis-only evidence ID appeared in zero fault-tree conclusions.

The current resolver accepted the compound model field `WlanEnable`, derived
the conservative signed variants `WlanEnable` and `Enable`, and returned only
the bounded redacted `collectDebuginfo.txt:L6` value `Enable :0`. The validated
diagnosis retained that value as an overall confirmed configuration fact and
high-priority hypothesis support. It separately retained the timestamped DC
radio initialization event and hostapd beacon-parameter error, did not treat
the collection command as a lifecycle event, and did not overclaim a final
root cause from the short AP-only fixture.

Retained isolated evidence:

- run root:
  `C:\Users\23173\AppData\Local\Temp\gw-ap-debug-glm-live-20260826-05\codex-run`;
- `host-validation.json` SHA-256:
  `9a49d62f1c333e4ba6f741226e1b2f4adfda89d24aaa8b7001037d04c3bf115f`;
- validated diagnosis JSON SHA-256:
  `5a18b2febc814df1a4304a9ab75c0ff346d1021fc17eceffcfbed4dccef90ad6`;
- validated Markdown SHA-256:
  `34827a414035199d7c5688ab17f37542aa30cd20e79b51c4a27ba13b2e91e729`;
- final manifest SHA-256:
  `7f7f412a3ef1d38475e66001074b39d68f505fee1a8d6f275a597b68469445f2`.

## OpenCode / GLM-5.2 history and current blocker

### v0.3.2 retained remote run

An isolated OpenCode run loaded the v0.3.2 Skill through the native `skill`
tool and completed one `opencode run` session with `bigmodel/glm-5.2` through
the OpenAI-compatible BigModel v4 endpoint.

- AP provenance inference: passed;
- diagnostic methods read: 2/2;
- fault-tree nodes attempted/concluded: 27/27;
- authenticated host calls: 14 total, including 13 node searches;
- final validation and manifest commit marker: passed;
- package QA history, web, subagent, nested CLI, environment inspection, and
  source-repository reads: zero;
- provider key presence in model-invoked shell: false, verified by a
  boolean-only plugin canary.

Retained root:
`C:\Users\23173\AppData\Local\Temp\gw-ap-debug-glm-live-20260825-03`.
Its hashes were:

- host validation:
  `e76f48b69b6f702fc6e85cff07c1594a76d590f243eeba53209855c38e084cec`;
- validated JSON:
  `0d43caf474fbb82fc41543f3b601be4253470ba737ba228ebfa26e78d6d5d877`;
- validated Markdown:
  `c8c46bf2f8a769d2f8a8b85838c53263ae315d8f4307d895f38ba9fe0489e34d`;
- manifest:
  `cddf1a55c9f70129065ca1edbba796cb3cadd369f7ac9eba8118b61596e0522f`.

That run passed the mechanical contract but failed the stricter diagnosis
quality review: the raw fixture contained `Enable :0`, but the model had no
tree-bound query capable of discovering it and incorrectly stated that no
disabled configuration value was visible. This finding led to the scoped
hypothesis-only interface in v0.3.3.

### v0.3.3 remote discovery result

The exact v0.3.3 OpenCode run loaded the Skill natively, read both methods, and
searched all 27 nodes. GLM-5.2 selected `WlanEnable` for the new supplemental
search, but literal matching returned no result because the fixture field is
the shorter `Enable`. The session then exhausted its practical context before
writing the draft. This demonstrated that the one-call safety boundary worked
but that relying on an exact model spelling was not reliable.

v0.3.4 therefore adds a local, deterministic, authenticated, maximum-one-alias
resolver for generic compound-field suffixes. The resolver does not place a
fixture answer in the prompt, cannot bind a fault-tree node, and retains the
same 20-line total result cap.

### v0.3.4 remote rerun blocker

Two new v0.3.4 `opencode run` attempts were made against an exact isolated
snapshot. Both stopped before the first model event or Skill tool call. A
separate minimal request containing no diagnostic data returned HTTP 429 with
BigModel business error `1113`: `余额不足或无可用资源包,请充值。`

Consequently, v0.3.4 has not yet received a fresh native OpenCode + GLM-5.2
end-to-end acceptance result. This is an external account-resource blocker,
not a passed remote test and not evidence of a Skill execution failure. Rerun
the retained isolated v0.3.4 harness after replenishing the account or
providing another authorized key.

No key was written into the candidate, OpenCode configuration, probe script,
logs, or retained run artifacts. OpenCode shell tools receive the provider key
removed by the project plugin.

## Historical CLI discovery status

- OpenCode project Skills use `.opencode/skills`; the native load was verified
  by retained remote runs.
- Claude Code: executable is not installed on this validation machine. The
  primary `/gw-ap-debug` command, personal `~/.claude/skills` junction, live
  discovery guidance, and argument flow are based on the current official
  Claude Code Skills contract; native Claude model execution remains pending on
  a machine with Claude Code installed.
- Codex CLI: `codex-cli 0.150.0-alpha.12.2` launches on this machine. On
  2026-08-28 an isolated project-scope native run discovered v0.3.5 from
  `.agents/skills/gw-ap-debug`, used the Skill, and executed `doctor --check
  package` with exit code 0, `package_ready=true`, and
  `host_agent_ready=true`. The connection initially timed out over WebSocket
  and completed after the CLI's automatic HTTPS fallback. The current Codex
  desktop model separately completed the full authenticated host loop. The
  v0.5.0 acceptance above adds a native `codex exec` discovery and deterministic
  end-to-end diagnosis; native host-agent finalization remains unrecorded.

## Capability conclusion

The candidate preserves a useful parallel MVP of the main repository's local
GW/AP log-diagnosis slice: safe artifact ingestion, AP/GW provenance, parsing,
Triage, active diagnostic methods, deterministic retrieval/rules, fault-tree
coverage, bounded host-model reasoning, evidence validation, and atomic final
publication. It does not claim parity with the main platform's administrator
UI, VS Code client, RBAC/multitenancy, repository graphs and static analysis,
knowledge governance/publication, analysis revisions, global trace replay,
PostgreSQL/Qdrant/Docker deployment topology, or frontend end-to-end tests.

## Distribution boundary

Distribution status is
`PUBLIC_RELEASE_WITH_BUNDLED_METHODS_BY_OWNER_DIRECTION`. The repository owner
directed the complete method-bearing MVP to the public `skillonly` branch on
2026-08-28; downstream redistribution requires its own audience decision.
