# Composable diagnostic Skill knowledge

`gw-ap-debug` always keeps its built-in fault tree and log-analysis methods. It
can also parse the Markdown knowledge from another, more complete diagnostic
Skill before triage/diagnosis and compose that knowledge on top of the current
base methods.

The importer is deterministic and Markdown-only. It reads `SKILL.md` and local
Markdown files linked from it, classifies log-analysis and comprehensive-
diagnosis sections, records source files and SHA-256 values, and composes the
two method documents into an immutable generation under the external state
directory. It never runs scripts, hooks, shell commands, MCP tools, or model
calls from the imported Skill.

## Fastest Windows 11 workflow

Preview what will be selected:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" import-skill-methods --skill "D:\skills\complete-network-diagnosis" --dry-run
```

Use it for one diagnosis without changing persistent knowledge:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" run --mode host-agent --approve-host-model-egress --title "AP offline" --diagnostic-skill "D:\skills\complete-network-diagnosis" --gw-log "D:\logs\gw" --ap-log "D:\logs\ap"
```

`--diagnostic-skill-scope run` is the default. It overlays the supplied Skill
for this case and leaves the persistent active generation unchanged. The live
binding is removed after every method-using backend job is confirmed terminal.
If a wait times out or its network outcome is uncertain, the runner renews the
finite binding instead of deleting it while a queued worker may still start.

Install knowledge persistently only when that is the intended state change:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" import-skill-methods --skill "D:\skills\complete-network-diagnosis"
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" list-method-packs
```

Equivalently, add `--diagnostic-skill-scope persistent` to `run` or `diagnose`
only when that persistent state change was explicitly requested. Multiple
packs in one request are fully parsed first and published with one active-
pointer update, so a later parse failure cannot partially install earlier
packs. `--diagnostic-skill` is repeatable, and `diagnose --mode host-agent`
accepts it for an existing case and creates the host bundle before releasing a
known-finished binding. Linux/macOS use `scripts/gw_ap_debug.sh` with the same
subcommands and arguments.

## Imported Skill format

The importer accepts a directory containing `SKILL.md`, or the `SKILL.md` path
itself. A normal Agent Skill often needs no changes: use clear headings such as
`日志分析`, `Log analysis`, `综合诊断`, `故障树`, `Root cause`, or `Diagnosis`, and
link supporting Markdown from `SKILL.md`.

For deterministic role selection, add these optional metadata keys:

```yaml
---
name: complete-network-diagnosis
description: Complete network log analysis and root-cause diagnosis knowledge.
metadata:
  gw_ap_debug_fault_tree: references/comprehensive-diagnosis.md
  gw_ap_debug_log_analysis: references/log-analysis.md
---
```

Each value is relative to the imported Skill root. Multiple files can be
separated with `;`, or supplied explicitly with repeatable CLI arguments:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" import-skill-methods --skill "D:\skills\complete-network-diagnosis" --fault-tree "references\tree.md" --log-analysis "references\patterns.md"
```

For `run` and `diagnose`, use the scoped equivalents when the source Skill has
ambiguous headings or no mapping metadata:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" run --mode host-agent --approve-host-model-egress --title "AP offline" --diagnostic-skill "D:\skills\complete-network-diagnosis" --diagnostic-skill-fault-tree "references\tree.md" --diagnostic-skill-log-analysis "references\patterns.md" --log "D:\logs\device.log"
```

Both role options are repeatable, but they are accepted only when exactly one
`--diagnostic-skill` is supplied, so every relative path has an unambiguous
Skill root. For multiple Skills, declare each Skill's role paths in its own
frontmatter metadata instead; a multi-Skill command combined with either
explicit role option fails closed. The same rule applies to both `run` scope
and `persistent` scope.

Only Markdown inside that Skill directory is accepted. The default envelope is
32 files, 2 MiB total, and 512 KiB per file; `--max-files`, `--max-bytes`, and
`--max-file-bytes` can narrow or deliberately enlarge it. When both roles have
explicit metadata or CLI paths, the importer reads only `SKILL.md` and those
role documents instead of recursively loading unrelated linked Markdown.

## Scope, generations, and provenance

On the first composition, the current methods are snapshotted as the base. This
preserves reviewed replacements that were installed before composition. Every
parsed Skill carries:

- a stable pack ID and content SHA-256;
- the selected relative Markdown paths and per-role hashes;
- generated fault-tree and/or log-analysis content;
- an explicit `MARKDOWN_ONLY_NO_IMPORTED_CODE_EXECUTION` policy.

Persistent registry data and cached packs live under `<state>/method-packs`.
Every composition writes or reuses a content-addressed immutable directory
under `<state>/method-packs/generations`; each generation manifest pins both
method hashes, its scope, selected packs, and size estimate. A persistent
change commits `<state>/method-packs/active.json` with an atomic replace. The
backend validates the pointer, generation manifest, and both method hashes
before reading them.

A run-scoped composition does not update the registry or active pointer. After
the case is created, the runner creates a finite per-case binding under
`<state>/method-packs/bindings`. The backend resolves that binding before the
persistent active pointer, so concurrent cases can use different reviewed
method generations without changing one another. The runner releases its
binding after analysis/export when every method-using job outcome is known. An
uncertain asynchronous outcome retains a renewed finite lease; the generation
stays immutable for provenance and later hash-based host export.

Runs that use only persistent knowledge are pinned the same way: the runner
creates or reuses a content-addressed run-scope snapshot of the selected
persistent generation and binds the case to it. A concurrent persistent import
therefore cannot change methods halfway through Triage or analysis. The export
records both the case snapshot ID and its persistent source-generation ID.

Re-importing identical persistent content is idempotent even when its reviewed
source directory moved to another machine. A new version with the
same Skill `name` replaces that name's persistent pack instead of appending a
duplicate. A run-scoped pack with the same name overlays the persistent version
for that run only. Method-pack changes are guarded by a kernel-backed exclusive
byte-range lock, so process death releases ownership without an unlink/recreate
stale-lock race. None of these files are written to the installed Skill or Git
repository.

If a generation body, manifest, pointer, or binding does not match its recorded
identity or hash, loading fails closed. With persistent packs installed,
`sync-methods --force` updates the recorded base and then publishes a new
generation with all persistent packs reapplied; existing generations are not
edited in place.

Remove one persistent pack and rebuild from the retained base plus remaining
packs:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" remove-method-pack --name "complete-network-diagnosis"
```

The cached source snapshot and older immutable generations are retained for
recovery/audit; the removed pack is no longer included by the active pointer.

## Host context budget

Dry-run and import results report selected-file and knowledge-size metadata.
For every host-agent `run` or `diagnose`, including runs that use only
persistent methods, the runner estimates the total composed fault-tree and
log-analysis payload before creating the case. The tokenizer-free estimator
uses UTF-8 byte count as a deliberately conservative upper-bound proxy. This
is a portable preflight, not the host provider's tokenizer.

The default ceiling is 120,000 estimated method tokens. If it is exceeded, the
command stops before diagnosis. Prefer narrowing the imported Skill with
`gw_ap_debug_fault_tree` / `gw_ap_debug_log_analysis` metadata or explicit role
paths. Raise `--max-host-method-tokens` only after checking that the currently
selected Claude Code, Codex CLI, or OpenCode CLI model has enough context for
the methods plus case evidence, instructions, and output. The preflight does
not claim a trusted host token counter or reserve the provider's exact context
overhead.

## Diagnostic behavior

Imported log tables, inline patterns, and relevant sections feed the same local
Pattern compiler and mandatory scan used by built-in knowledge. Imported fault
tables and flow descriptions feed the same fault-tree item compiler and
coverage validator. The resulting method bodies are hash-pinned in host-agent
bundles and must be read through `host-read-methods` before conclusions can be
finalized.

Importing a Skill extends method knowledge only. It does not grant its scripts
or instructions permission to operate the computer, does not publish knowledge
to the main platform, and does not bypass evidence IDs, fault-tree coverage, or
final result validation.
