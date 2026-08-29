# Composable diagnostic Skill knowledge

`gw-ap-debug` always keeps its built-in fault tree and log-analysis methods. It
can also parse the Markdown knowledge from another, more complete diagnostic
Skill before triage/diagnosis and compose that knowledge on top of the current
base methods.

The importer is deterministic and Markdown-only. It reads `SKILL.md` and local
Markdown files linked from it, classifies log-analysis and comprehensive-
diagnosis sections, records source files and SHA-256 values, and rebuilds the
two active method documents under the external state directory. It never runs
scripts, hooks, shell commands, MCP tools, or model calls from the imported
Skill.

## Fastest Windows 11 workflow

Preview what will be selected:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" import-skill-methods --skill "D:\skills\complete-network-diagnosis" --dry-run
```

Import it, then inspect the active registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" import-skill-methods --skill "D:\skills\complete-network-diagnosis"
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" list-method-packs
```

Or let a new run import/update the Skill immediately before local triage and
comprehensive diagnosis:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" run --mode host-agent --approve-host-model-egress --title "AP offline" --diagnostic-skill "D:\skills\complete-network-diagnosis" --gw-log "D:\logs\gw" --ap-log "D:\logs\ap"
```

`--diagnostic-skill` is repeatable. The `diagnose` command accepts the same
option for an existing case. Linux/macOS use `scripts/gw_ap_debug.sh` with the
same subcommands and arguments.

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

Only Markdown inside that Skill directory is accepted. The default envelope is
32 files, 2 MiB total, and 512 KiB per file; `--max-files`, `--max-bytes`, and
`--max-file-bytes` can narrow or deliberately enlarge it.

## Composition and provenance

On the first import, the current active methods are snapshotted as the base.
This preserves reviewed replacements that were installed before composition.
Every imported Skill becomes one external method pack with:

- a stable pack ID and content SHA-256;
- the selected relative Markdown paths and per-role hashes;
- generated fault-tree and/or log-analysis content;
- an explicit `MARKDOWN_ONLY_NO_IMPORTED_CODE_EXECUTION` policy.

The registry and cached packs live under `<state>/method-packs`; composed active
methods remain under `<state>/methods`. They are not written to the installed
Skill or the Git repository. Re-importing identical content is idempotent. A
new version with the same Skill `name` replaces that name's active pack instead
of appending a duplicate.

If an active composed method was manually edited, importing fails closed. After
reviewing the recorded base and packs, use `--force` to rebuild. With packs
installed, `sync-methods --force` updates the recorded base and then reapplies
all active packs.

Remove one active pack and rebuild from the retained base plus remaining packs:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" remove-method-pack --name "complete-network-diagnosis"
```

The cached source snapshot is retained for recovery/audit; it is no longer
included in active methods.

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
