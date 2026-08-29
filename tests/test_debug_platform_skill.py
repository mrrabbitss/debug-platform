from __future__ import annotations

import argparse
import contextlib
from io import StringIO
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "debug_platform_skill.py"
SPEC = importlib.util.spec_from_file_location("debug_platform_skill_under_test", SCRIPT)
assert SPEC and SPEC.loader
skill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = skill
SPEC.loader.exec_module(skill)


class FakeTriageClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, *, query: dict | None = None, **_kwargs):
        self.calls.append((method, path, query))
        if path.endswith("/occurrences"):
            offset = int((query or {}).get("offset", 0))
            all_items = [
                {
                    "id": f"HIT-{index}",
                    "source_file": "ap.log",
                    "line_start": index,
                    "line_end": index,
                    "timestamp": None,
                    "message": f"hit {index}",
                }
                for index in range(1, 4)
            ]
            # Return one item at a time to prove offset advances by actual batch size.
            batch = all_items[offset:offset + 1]
            return {"total": len(all_items), "offset": offset, "items": batch}
        bucket = str((query or {}).get("bucket"))
        if bucket == "LLM_RELEVANT":
            return {
                "total": 1,
                "items": [{"id": "MATCH-1", "evidence_id": "MATCH-1", "bucket": bucket}],
            }
        if bucket == "METHOD_REQUIRED":
            return {
                "total": 1,
                "items": [{"id": "MATCH-2", "evidence_id": "MATCH-2", "bucket": bucket}],
            }
        return {
            "total": 1,
            "items": [{"id": "EVENT-1", "evidence_id": "EVENT-1", "bucket": bucket}],
        }


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_host_bundle(
    root: Path,
    *,
    seed_trace: bool = True,
    deterministic_baseline: dict | None = None,
) -> tuple[Path, dict]:
    bundle = root / "bundle"
    bundle.mkdir()
    context = {
        "schema": skill.HOST_BUNDLE_SCHEMA,
        "skill_version": skill.SKILL_VERSION,
        "context_sha256": "",
        "host_session_nonce": "test-session",
        "case": {"id": "CASE-1", "title": "case"},
        "manifest": {"case_id": "CASE-1", "state_dir": str(root / "state")},
        "reasoning_model": {"mode": "host_cli_configured_model"},
        "deterministic_baseline": deterministic_baseline or {
            "summary": "baseline",
            "diagnostic_planning": {},
        },
        "diagnostic_methods": [
            {"id": "DOC-1", "title": "tree", "role": "FAULT_TREE", "content": "method"},
        ],
        "required_fault_tree_items": {
            "FTITEM-1": {
                "id": "FTITEM-1",
                "method_document_id": "DOC-1",
                "title": "check one",
            },
            "FTITEM-2": {
                "id": "FTITEM-2",
                "method_document_id": "DOC-1",
                "title": "check two",
            },
        },
        "initial_evidence_catalog": [],
        "limits": {
            "maximum_reasoning_rounds": 20,
            "maximum_tool_calls_per_round": 4,
            "maximum_query_characters": 500,
            "maximum_search_limit": 500,
            "maximum_dynamic_evidence_items": 100,
            "maximum_dynamic_evidence_bytes": 1024 * 1024,
            "maximum_hypothesis_log_searches": 1,
            "maximum_hypothesis_query_characters": 100,
            "maximum_hypothesis_search_limit": 20,
        },
        "files": {},
    }
    write_json(bundle / "evidence.json", [
        {
            "evidence_id": "EV-1",
            "source_type": "log_event",
            "source_file": "ap.log",
            "line_start": 10,
            "line_end": 10,
            "content": "link down",
        },
        {
            "evidence_id": "DOC-1",
            "source_type": "fault_tree",
            "role": "FAULT_TREE",
            "title": "tree",
            "content": "method",
        },
    ])
    write_json(bundle / "case.json", context["case"])
    write_json(bundle / "analysis.json", context["deterministic_baseline"])
    write_json(bundle / "analysis_record.json", {"id": "ANALYSIS-1", "status": "COMPLETED"})
    write_json(bundle / "manifest.json", context["manifest"])
    context["immutable_files"] = skill.immutable_bundle_hashes(bundle)
    context["context_sha256"] = skill._host_context_hash(context)
    write_json(bundle / "host-agent-context.json", context)
    skill.create_host_validation_key(context)
    key = skill.load_host_validation_key(context)
    skill.write_host_session_state(
        bundle, context, skill._new_host_session_state(context), key,
    )
    if seed_trace:
        skill.record_host_tool_call(
            bundle,
            tool_name="read_diagnostic_methods",
            arguments={"method_ids": ["DOC-1"], "all": True},
            fault_tree_item_ids=[],
            evidence_ids=["DOC-1"],
            round_number=1,
            returned=1,
        )
        skill.record_host_tool_call(
            bundle,
            tool_name="search_evidence",
            arguments={"query": "link"},
            fault_tree_item_ids=["FTITEM-1"],
            evidence_ids=["EV-1"],
            round_number=1,
            returned=1,
        )
        skill.record_host_tool_call(
            bundle,
            tool_name="search_evidence",
            arguments={"query": "link"},
            fault_tree_item_ids=["FTITEM-2"],
            evidence_ids=["EV-1"],
            round_number=2,
            returned=1,
        )
    return bundle, context


def reset_host_session(bundle: Path, context: dict) -> None:
    key = skill.load_host_validation_key(context)
    skill.write_host_session_state(
        bundle, context, skill._new_host_session_state(context), key,
    )


def valid_host_envelope(context: dict) -> dict:
    return {
        "schema": skill.HOST_RESULT_SCHEMA,
        "context_sha256": context["context_sha256"],
        "diagnosis": {
            "summary": "Evidence-grounded summary",
            "confirmed_facts": [{"statement": "Link went down", "evidence_ids": ["EV-1"]}],
            "hypotheses": [
                {
                    "title": "Primary hypothesis",
                    "description": "The observed link-down event is relevant.",
                    "supporting_evidence": ["EV-1"],
                    "contradicting_evidence": [],
                    "confidence_score": 0.8,
                    "priority": "P1",
                    "needs_human_review": True,
                },
                {
                    "title": "Secondary hypothesis",
                    "description": "A lower confidence alternative.",
                    "supporting_evidence": ["EV-1"],
                    "contradicting_evidence": [],
                    "confidence_score": 0.4,
                    "priority": "P2",
                    "needs_human_review": True,
                },
            ],
            "recommended_actions": [
                {
                    "priority": "P1",
                    "action": "Collect link counters",
                    "reason": "Confirm the physical path",
                    "expected_result": "Counters identify or exclude a link fault",
                },
            ],
            "missing_information": ["Peer counters"],
            "suspected_modules": ["ethernet"],
            "limitations": ["Synthetic fixture"],
            "fault_tree_conclusions": [
                {
                    "item_id": "FTITEM-1",
                    "method_document_id": "DOC-1",
                    "status": "SUPPORTED",
                    "conclusion": "The node is supported.",
                    "evidence_ids": ["EV-1"],
                    "next_action": "",
                },
                {
                    "item_id": "FTITEM-2",
                    "method_document_id": "DOC-1",
                    "status": "INSUFFICIENT_EVIDENCE",
                    "conclusion": "The node cannot yet be resolved.",
                    "evidence_ids": [],
                    "next_action": "Collect peer-side state.",
                },
            ],
        },
    }


class MarkdownAndEvidenceTests(unittest.TestCase):
    def test_markdown_uses_current_coverage_and_triage_schema(self) -> None:
        result = {
            "summary": "summary",
            "hypotheses": [],
            "confirmed_facts": [],
            "recommended_actions": [],
            "missing_information": [],
            "limitations": [],
            "diagnostic_planning": {
                "fault_tree_coverage": {
                    "total": 3,
                    "attempted": 2,
                    "concluded": 1,
                    "complete": False,
                    "items": [{"id": "FT-1", "title": "Readable node", "status": "PENDING"}],
                },
            },
            "fault_tree_conclusions": [
                {"item_id": "FT-1", "status": "INSUFFICIENT_EVIDENCE", "conclusion": "pending"},
            ],
        }
        triage = {
            "id": "TRIAGE-1",
            "artifact_id": "ART-1",
            "status": "COMPLETED",
            "summary": {
                "cluster_counts": {"LLM_RELEVANT": 2, "METHOD_REQUIRED": 1},
                "occurrence_counts": {"LLM_RELEVANT": 5, "METHOD_REQUIRED": 3},
                "other_events": 4,
            },
        }
        rendered = skill.markdown_diagnosis(
            {"id": "CASE-1", "title": "case"},
            {"id": "AN-1", "status": "COMPLETED"},
            result,
            [],
            None,
            [triage],
        )
        self.assertIn("Attempted: `2/3`; concluded: `1/3`", rendered)
        self.assertIn("Readable node", rendered)
        self.assertIn("clusters LLM=2, method=1", rendered)
        self.assertIn("occurrences LLM=5, method=3", rendered)
        self.assertNotIn("?/?", rendered)

    def test_evidence_label_tolerates_non_object_artifact_source(self) -> None:
        labels = skill.evidence_label_map([
            {"evidence_id": "A", "artifact_source": "bad", "title": "Fallback title"},
        ])
        self.assertEqual(labels["A"], "Fallback title")

    def test_occurrences_are_exported_only_for_match_buckets(self) -> None:
        client = FakeTriageClient()
        result = skill.fetch_all_triage_evidence(
            client,
            "CASE-1",
            "TRIAGE-1",
            max_per_bucket=10,
            max_occurrences_per_match=2,
        )
        self.assertEqual(result["LLM_RELEVANT"]["items"][0]["occurrences"]["returned"], 2)
        self.assertTrue(result["LLM_RELEVANT"]["items"][0]["occurrences"]["truncated"])
        occurrence_paths = [path for _method, path, _query in client.calls if path.endswith("/occurrences")]
        self.assertEqual(len(occurrence_paths), 4)
        self.assertFalse(any("EVENT-1" in path for path in occurrence_paths))


class SafetyAndReadinessTests(unittest.TestCase):
    def test_host_agent_guidance_separates_commands_from_events_and_checks_state(self) -> None:
        skill_text = (SCRIPT.parents[1] / "SKILL.md").read_text(encoding="utf-8")
        script_text = SCRIPT.read_text(encoding="utf-8")
        reference_text = (
            SCRIPT.parents[1] / "references" / "host-agent-mode.md"
        ).read_text(encoding="utf-8")
        for text in (skill_text, script_text, reference_text):
            self.assertIn("Collection/inventory commands prove only", text)
            self.assertIn("start or restart", text)
            self.assertIn("host-search-hypothesis-log", text)
            self.assertIn("disabled, zero, negative, or conflicting values", text)
            self.assertRegex(text, r"raw[- ]artifact")
            self.assertIn("Do not directly read `host-agent-context.json`", text)

    def test_supported_python_range_matches_manifest(self) -> None:
        self.assertFalse(skill.python_version_supported((3, 10)))
        self.assertTrue(skill.python_version_supported((3, 11)))
        self.assertTrue(skill.python_version_supported((3, 14)))
        self.assertFalse(skill.python_version_supported((3, 15)))

    def test_case_device_type_is_derived_from_upload_provenance(self) -> None:
        ap = [skill.UploadSpec(Path("ap.log"), "AP", "SECONDARY")]
        gw = [skill.UploadSpec(Path("gw.log"), "GW", "PRIMARY")]
        mixed = ap + gw
        unknown = [skill.UploadSpec(Path("unknown.log"), "UNKNOWN", "UNKNOWN")]
        self.assertEqual(skill.resolve_case_device_type(None, ap), "AP")
        self.assertEqual(skill.resolve_case_device_type(None, gw), "GW")
        self.assertEqual(skill.resolve_case_device_type(None, mixed), "OTHER")
        self.assertEqual(skill.resolve_case_device_type(None, unknown), "OTHER")
        self.assertEqual(skill.resolve_case_device_type("GW", ap), "GW")

    def test_backend_bootstrap_uses_declared_dependencies_without_editable_install(self) -> None:
        runtime_root = SCRIPT.parents[1] / "runtime"
        dependencies = skill.backend_dependencies(runtime_root)
        self.assertIn("fastapi>=0.116,<1.0", dependencies)
        self.assertIn("uvicorn[standard]>=0.35,<1.0", dependencies)
        self.assertFalse(any("gw-ap-debug-backend" in item for item in dependencies))

    def test_backend_model_readiness_rejects_persisted_mock_profile(self) -> None:
        self.assertFalse(skill.backend_model_is_ready({
            "provider": "mock",
            "model": "rule-engine",
            "base_url_configured": False,
            "api_key_configured": False,
        }))
        self.assertTrue(skill.backend_model_is_ready({
            "provider": "openai_compatible",
            "model": "glm-example",
            "base_url_configured": True,
            "api_key_configured": True,
        }))

    def test_platform_url_security(self) -> None:
        skill.validate_platform_url("http://127.0.0.2:8000/api/v1")
        skill.validate_platform_url("https://192.0.2.1/api/v1")
        with self.assertRaises(skill.SkillError):
            skill.validate_platform_url("http://192.0.2.1/api/v1")
        with self.assertRaises(skill.SkillError):
            skill.validate_platform_url("https://example.com/api/v1", for_upload=True)
        skill.validate_platform_url(
            "https://example.com/api/v1", for_upload=True, remote_upload_approved=True,
        )
        for bad in (
            "https://user:pass@example.com/api/v1",
            "https://example.com/api/v1?q=1",
            "https://example.com/api/v1#fragment",
        ):
            with self.assertRaises(skill.SkillError):
                skill.validate_platform_url(bad)

    def test_auto_start_rejects_local_https_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.object(skill, "backend_is_healthy", return_value=False):
                with self.assertRaises(skill.SkillError):
                    skill.launch_backend(
                        Path(raw),
                        "https://127.0.0.1:8000/api/v1",
                        state_dir=Path(raw) / "state",
                    )

    def test_multipart_filename_sanitizer(self) -> None:
        rendered = skill.sanitize_multipart_filename('bad"\r\nX-Evil: yes\x00/\\.log')
        for forbidden in ('"', "\r", "\n", "\x00", "/", "\\"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(skill.sanitize_multipart_filename(""), "debug-log.bin")

    def test_directory_preflight_rejects_limits_before_packaging(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "one.log").write_bytes(b"1234")
            (root / "two.log").write_bytes(b"5678")
            with self.assertRaises(skill.SkillError):
                list(skill.iter_directory_files(root, max_files=1))
            with self.assertRaises(skill.SkillError):
                list(skill.iter_directory_files(root, max_total_bytes=7))
            with self.assertRaises(skill.SkillError):
                list(skill.iter_directory_files(root, max_single_file_bytes=3))

    def test_doctor_fails_when_package_and_backend_are_missing(self) -> None:
        args = argparse.Namespace(
            platform_root=None,
            state_dir=None,
            base_url=skill.DEFAULT_BASE_URL,
            check="host-agent",
            api_key=None,
            http_timeout=1,
            approve_remote_platform_upload=False,
        )
        output = StringIO()
        with (
            mock.patch.object(skill, "discover_platform_root", side_effect=skill.SkillError("missing")),
            mock.patch.object(skill, "backend_is_healthy", return_value=False),
            contextlib.redirect_stdout(output),
        ):
            code = skill.command_doctor(args)
        report = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(report["ok"])
        self.assertFalse(report["package_ready"])

    def test_external_method_sync_preserves_user_changes_until_forced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            state = base / "state"
            sources = {
                "故障树.md": base / "tree.md",
                "日志分析.md": base / "logs.md",
            }
            sources["故障树.md"].write_text("tree-v1", encoding="utf-8")
            sources["日志分析.md"].write_text("logs-v1", encoding="utf-8")
            methods_dir, first = skill.synchronize_methods(base, state, sources=sources)
            self.assertTrue(all(item["status"] == "COPIED" for item in first))
            target = methods_dir / "故障树.md"
            target.write_text("reviewed-user-version", encoding="utf-8")
            _methods_dir, preserved = skill.synchronize_methods(base, state, sources=sources)
            self.assertEqual(preserved[0]["status"], "DIFFERENT_PRESERVED")
            self.assertEqual(target.read_text(encoding="utf-8"), "reviewed-user-version")
            _methods_dir, forced = skill.synchronize_methods(base, state, sources=sources, force=True)
            self.assertEqual(forced[0]["status"], "COPIED")
            self.assertEqual(target.read_text(encoding="utf-8"), "tree-v1")

    def test_diagnostic_skill_metadata_maps_complete_role_documents(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = base / "complete-diagnostic"
            references = source / "references"
            scripts = source / "scripts"
            references.mkdir(parents=True)
            scripts.mkdir()
            (source / "SKILL.md").write_text(
                """---
name: complete-network-diagnosis
description: Complete log analysis and comprehensive diagnosis knowledge.
metadata:
  gw_ap_debug_fault_tree: references/diagnosis.md
  gw_ap_debug_log_analysis: references/logs.md
---
# Complete diagnosis

[Diagnosis](references/diagnosis.md "fault tree") and [logs](references/logs.md).
[Background](references/background.md "local reference").
""",
                encoding="utf-8",
            )
            (references / "diagnosis.md").write_text(
                "# 综合诊断\n\n| 判断点 | 证据 | 结论 |\n|---|---|---|\n| Link | `LINK_CANARY` | isolate transport |\n",
                encoding="utf-8",
            )
            (references / "logs.md").write_text(
                "# 日志分析\n\n| 日志关键字 | 含义 |\n|---|---|\n| `LOG_CANARY` | peer reset |\n",
                encoding="utf-8",
            )
            (references / "background.md").write_text(
                "# Background\n\nDevice inventory only.\n", encoding="utf-8"
            )
            (scripts / "must-not-run.py").write_text(
                "raise RuntimeError('imported code executed')\n", encoding="utf-8"
            )
            parsed = skill.parse_diagnostic_skill(source)
            self.assertEqual(parsed["execution_policy"], "MARKDOWN_ONLY_NO_IMPORTED_CODE_EXECUTION")
            self.assertIn("LINK_CANARY", parsed["roles"]["故障树.md"]["content"])
            self.assertIn("LOG_CANARY", parsed["roles"]["日志分析.md"]["content"])
            self.assertEqual(parsed["parsed_markdown_files"], 4)

    def test_diagnostic_skill_auto_classifies_role_sections(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "automatic"
            source.mkdir()
            (source / "SKILL.md").write_text(
                """---
name: automatic-diagnosis
description: Portable knowledge.
---
# Network troubleshooting

## 日志分析

Search `AUTO_LOG_CANARY` and correlate timestamps.

## 综合诊断与根因分析

| 判断点 | 日志证据 | 结论 |
|---|---|---|
| Peer state | `AUTO_TREE_CANARY` | decide link or protocol |
""",
                encoding="utf-8",
            )
            parsed = skill.parse_diagnostic_skill(source)
            self.assertIn("AUTO_LOG_CANARY", parsed["roles"]["日志分析.md"]["content"])
            self.assertIn("AUTO_TREE_CANARY", parsed["roles"]["故障树.md"]["content"])

    def test_diagnostic_skill_rejects_markdown_link_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = base / "escaped"
            source.mkdir()
            (base / "outside.md").write_text("# 日志分析\nsecret", encoding="utf-8")
            (source / "SKILL.md").write_text(
                """---
name: escaped-diagnosis
description: Must remain contained.
---
# 日志分析

[outside](../outside.md)
""",
                encoding="utf-8",
            )
            with self.assertRaises(skill.SkillError):
                skill.parse_diagnostic_skill(source)

    def test_diagnostic_skill_import_is_idempotent_and_composes_with_base(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = base / "pack"
            source.mkdir()
            (source / "SKILL.md").write_text(
                """---
name: idempotent-diagnosis
description: Log and fault-tree method pack.
---
# 日志分析与综合诊断

| 判断点 | 日志关键字 | 结论 |
|---|---|---|
| Canary | `PACK_CANARY` | imported knowledge works |
""",
                encoding="utf-8",
            )
            state = base / "state"
            runtime = SCRIPT.parents[1] / "runtime"
            first = skill.install_diagnostic_skill(runtime, state, source)
            second = skill.install_diagnostic_skill(runtime, state, source)
            self.assertEqual(first["status"], "IMPORTED")
            self.assertEqual(second["status"], "UNCHANGED")
            for filename in skill.METHOD_FILENAMES:
                active = (skill.resolve_methods_dir(state) / filename).read_text(encoding="utf-8")
                self.assertEqual(active.count("PACK_CANARY"), 1)
                self.assertIn("Imported Skill: idempotent-diagnosis", active)
            registry = skill.load_method_pack_registry(state)
            self.assertEqual(len(registry["packs"]), 1)
            self.assertEqual(registry["packs"][0]["name"], "idempotent-diagnosis")
            output = StringIO()
            with contextlib.redirect_stdout(output):
                code = skill.command_remove_method_pack(argparse.Namespace(
                    platform_root=str(runtime),
                    state_dir=str(state),
                    id=None,
                    name="idempotent-diagnosis",
                    force=False,
                ))
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["ok"])
            for filename in skill.METHOD_FILENAMES:
                active = (skill.resolve_methods_dir(state) / filename).read_text(encoding="utf-8")
                self.assertNotIn("PACK_CANARY", active)

    def test_diagnostic_skill_cli_supports_preview_and_pre_diagnosis_import(self) -> None:
        parser = skill.build_parser()
        preview = parser.parse_args([
            "import-skill-methods", "--skill", "C:/diagnostic-skill", "--dry-run"
        ])
        self.assertTrue(preview.dry_run)
        run = parser.parse_args([
            "run", "--mode", "deterministic", "--title", "case", "--log", "sample.log",
            "--diagnostic-skill", "C:/diagnostic-skill",
        ])
        self.assertEqual(run.diagnostic_skill, ["C:/diagnostic-skill"])

    def test_export_rejects_reused_nonempty_output_directory_before_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "existing"
            output.mkdir()
            (output / "stale.json").write_text("{}", encoding="utf-8")

            class NoApiCalls:
                def request(self, *_args, **_kwargs):
                    raise AssertionError("Output preflight must happen before API access")

            with self.assertRaises(skill.SkillError):
                skill.export_result_bundle(NoApiCalls(), "CASE-1", output_dir=output)

    def test_backend_launch_disables_source_bytecode_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = base / "runtime"
            (root / "backend").mkdir(parents=True)
            state = base / "state"
            python_path = state / "venv" / "Scripts" / "python.exe"
            python_path.parent.mkdir(parents=True)
            python_path.write_bytes(b"")
            process = mock.Mock()
            process.poll.return_value = None
            with (
                mock.patch.object(skill, "backend_is_healthy", return_value=False),
                mock.patch.object(skill, "synchronize_methods", return_value=(state / "methods", [])),
                mock.patch.object(skill, "venv_python", return_value=python_path),
                mock.patch.object(skill, "backend_environment_ready", return_value=True),
                mock.patch.object(skill, "wait_backend"),
                mock.patch.object(skill.subprocess, "Popen", return_value=process) as popen,
            ):
                skill.launch_backend(root, skill.DEFAULT_BASE_URL, state_dir=state)
            self.assertEqual(popen.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_backend_environment_readiness_requires_matching_lock_fingerprint(self) -> None:
        runtime_root = SCRIPT.parents[1] / "runtime"
        with tempfile.TemporaryDirectory() as raw:
            python_path = Path(raw) / "venv" / "Scripts" / "python.exe"
            python_path.parent.mkdir(parents=True)
            python_path.write_bytes(b"")
            completed = mock.Mock(returncode=0)
            with mock.patch.object(skill.subprocess, "run", return_value=completed):
                self.assertFalse(skill.backend_environment_ready(python_path, runtime_root))
                skill.atomic_write_json(
                    skill.backend_environment_fingerprint_path(python_path),
                    skill.backend_lock_fingerprint(runtime_root),
                )
                self.assertTrue(skill.backend_environment_ready(python_path, runtime_root))


class HostContractTests(unittest.TestCase):
    def test_host_methods_are_hydrated_by_hash_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            methods = base / "methods"
            methods.mkdir()
            content = "# Method\ncheck peer 192.0.2.20\n"
            (methods / "故障树.md").write_text(content, encoding="utf-8")
            content_hash = skill.hashlib.sha256(content.encode("utf-8")).hexdigest()
            result = {
                "diagnostic_planning": {
                    "method_catalog": [{
                        "id": f"LOCALDOC-{content_hash[:20]}",
                        "title": "tree",
                        "source_type": "fault_tree",
                        "role": "FAULT_TREE",
                        "version": 1,
                        "content_sha256": content_hash,
                    }],
                },
            }

            class NoNetwork:
                def request(self, *_args, **_kwargs):
                    raise AssertionError("Local method hydration must not use the knowledge API")

            with mock.patch.dict(skill.os.environ, {"GW_AP_DEBUG_METHODS_DIR": str(methods)}):
                hydrated = skill.hydrate_host_method_documents(
                    NoNetwork(), result, {"state_dir": str(base)},
                )
            self.assertEqual(len(hydrated), 1)
            self.assertIn("<IP>", hydrated[0]["content"])
            self.assertEqual(hydrated[0]["content_sha256"], content_hash)
            self.assertTrue(hydrated[0]["content_redacted"])
            self.assertEqual(hydrated[0]["content_origin"], "external_state_method")

            (methods / "故障树.md").write_text("changed", encoding="utf-8")
            with (
                mock.patch.dict(skill.os.environ, {"GW_AP_DEBUG_METHODS_DIR": str(methods)}),
                self.assertRaises(skill.SkillError),
            ):
                skill.hydrate_host_method_documents(NoNetwork(), result, {"state_dir": str(base)})

    def test_host_tool_budget_is_enforced_per_round(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, _context = make_host_bundle(Path(raw), seed_trace=False)
            for index in range(4):
                skill.record_host_tool_call(
                    bundle,
                    tool_name="search_evidence",
                    arguments={"query": f"term-{index}"},
                    fault_tree_item_ids=["FTITEM-1"],
                    evidence_ids=[],
                    round_number=1,
                    returned=0,
                )

    def test_hypothesis_log_search_requires_a_prior_node_search(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, _context = make_host_bundle(Path(raw), seed_trace=False)
            before = (bundle / "host-session-state.json").read_bytes()
            with self.assertRaises(skill.SkillError):
                skill.record_host_tool_call(
                    bundle,
                    tool_name="search_hypothesis_log",
                    arguments={
                        "query": "admin field",
                        "query_variants": ["admin field"],
                        "start_line": 1,
                        "limit": 20,
                    },
                    fault_tree_item_ids=[],
                    evidence_ids=[],
                    round_number=1,
                    returned=0,
                )
            self.assertEqual(before, (bundle / "host-session-state.json").read_bytes())

    def test_hypothesis_log_evidence_is_bounded_and_cannot_support_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            evidence = {
                "evidence_id": "HOSTLOG-HYPOTHESIS",
                "source_type": "host_log_search",
                "evidence_scope": "hypothesis_only",
                "source_file": "ap.log",
                "line_start": 6,
                "line_end": 6,
                "title": "ap.log:L6",
                "content": "Administrative field is zero",
            }
            skill.record_host_tool_call(
                bundle,
                tool_name="search_hypothesis_log",
                arguments={
                    "query": "admin field",
                    "query_variants": ["admin field"],
                    "start_line": 1,
                    "limit": 20,
                },
                fault_tree_item_ids=[],
                evidence_ids=[evidence["evidence_id"]],
                round_number=2,
                returned=1,
                host_evidence_items=[evidence],
            )
            progress = skill._host_progress(bundle, context)
            self.assertEqual(progress["fault_tree_attempted"], 2)
            self.assertEqual(progress["node_search_tool_calls"], 2)
            self.assertEqual(progress["hypothesis_search_tool_calls"], 1)
            self.assertEqual(progress["hypothesis_search_remaining"], 0)

            envelope = valid_host_envelope(context)
            envelope["diagnosis"]["confirmed_facts"][0]["evidence_ids"] = [
                evidence["evidence_id"]
            ]
            envelope["diagnosis"]["hypotheses"][0]["supporting_evidence"] = [
                evidence["evidence_id"]
            ]
            normalized, validation = skill.validate_host_result(bundle, envelope)
            self.assertEqual(
                normalized["confirmed_facts"][0]["evidence_ids"],
                [evidence["evidence_id"]],
            )
            self.assertEqual(validation["node_search_tool_calls"], 2)
            self.assertEqual(validation["hypothesis_search_tool_calls"], 1)

            node_claim = valid_host_envelope(context)
            node_claim["diagnosis"]["fault_tree_conclusions"][0].update({
                "status": "SUPPORTED",
                "evidence_ids": [evidence["evidence_id"]],
                "next_action": "",
            })
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, node_claim)

            key = skill.load_host_validation_key(context)
            state = skill.load_host_session_state(bundle, context, key)
            washed = skill.search_bundle_evidence(
                bundle,
                "Administrative",
                limit=20,
                exclude_scopes={"hypothesis_only"},
                host_state=state,
            )
            self.assertNotIn(evidence["evidence_id"], {
                item["evidence_id"] for item in washed
            })

            before = (bundle / "host-session-state.json").read_bytes()
            with self.assertRaises(skill.SkillError):
                skill.record_host_tool_call(
                    bundle,
                    tool_name="search_hypothesis_log",
                    arguments={
                        "query": "second field",
                        "query_variants": ["second field"],
                        "start_line": 1,
                        "limit": 20,
                    },
                    fault_tree_item_ids=[],
                    evidence_ids=[],
                    round_number=2,
                    returned=0,
                )
            self.assertEqual(before, (bundle / "host-session-state.json").read_bytes())

    def test_hypothesis_log_query_limits_fail_before_backend_start(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, _context = make_host_bundle(Path(raw))
            before = (bundle / "host-session-state.json").read_bytes()
            for query, limit in (("x" * 101, 20), ("field", 21)):
                args = argparse.Namespace(
                    bundle=str(bundle),
                    round=2,
                    query=query,
                    start_line=1,
                    limit=limit,
                    http_timeout=1.0,
                )
                with self.subTest(query_length=len(query), limit=limit):
                    with mock.patch.object(skill, "ensure_backend_for_command") as ensure:
                        with self.assertRaises(skill.SkillError):
                            skill.command_host_search_hypothesis_log(args)
                        ensure.assert_not_called()
                    self.assertEqual(
                        before, (bundle / "host-session-state.json").read_bytes()
                    )

    def test_hypothesis_query_variants_are_conservative_and_bounded(self) -> None:
        self.assertEqual(
            skill._host_hypothesis_query_variants("WlanEnable"),
            ["WlanEnable", "Enable"],
        )
        self.assertEqual(
            skill._host_hypothesis_query_variants("radio.admin_status"),
            ["radio.admin_status", "status"],
        )
        self.assertEqual(
            skill._host_hypothesis_query_variants("BeaconInterval"),
            ["BeaconInterval"],
        )
        self.assertEqual(
            skill._host_hypothesis_query_variants("admin status"),
            ["admin status"],
        )

    def test_hypothesis_log_collector_uses_signed_alias_variant_and_global_limit(self) -> None:
        context = {
            "context_sha256": "c" * 64,
            "case": {"id": "CASE-1"},
            "manifest": {"case_id": "CASE-1"},
        }
        calls: list[str] = []

        def search(_client, _case_id, query, *, start_line, limit):
            calls.append(query)
            if query == "WlanEnable":
                return []
            return [{
                "artifact_id": "ART-1",
                "source_device_type": "AP",
                "source_device_role": "SECONDARY",
                "path": "collectDebuginfo",
                "matches": [
                    {"line_number": 6, "text": "Enable    :0"},
                    {"line_number": 9, "text": "Enable    :1"},
                ],
            }]

        backend = skill.BackendProcess(started_by_skill=False)
        variants = skill._host_hypothesis_query_variants("WlanEnable")
        with (
            mock.patch.object(skill, "ensure_backend_for_command", return_value=(None, backend)),
            mock.patch.object(skill, "client_from_args", return_value=object()),
            mock.patch.object(skill, "search_case_logs", side_effect=search),
        ):
            results = skill._collect_host_log_search_evidence(
                context,
                query="WlanEnable",
                start_line=1,
                limit=1,
                http_timeout=1.0,
                evidence_scope="hypothesis_only",
                query_variants=variants,
            )
        self.assertEqual(calls, ["WlanEnable", "Enable"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["content"], "Enable    :0")
        self.assertEqual(results[0]["evidence_scope"], "hypothesis_only")

    def test_host_log_collector_masks_truncates_limits_and_scopes_results(self) -> None:
        context = {
            "context_sha256": "c" * 64,
            "case": {"id": "CASE-1"},
            "manifest": {"case_id": "CASE-1"},
        }
        groups = [{
            "artifact_id": "ART-1",
            "source_device_type": "AP",
            "source_device_role": "SECONDARY",
            "path": "ap.log",
            "matches": [
                {
                    "line_number": index,
                    "text": "api_key=topsecret " + ("x" * 5000),
                }
                for index in range(1, 26)
            ],
        }]
        backend = skill.BackendProcess(started_by_skill=False)
        with (
            mock.patch.object(skill, "ensure_backend_for_command", return_value=(None, backend)),
            mock.patch.object(skill, "client_from_args", return_value=object()),
            mock.patch.object(skill, "search_case_logs", return_value=groups),
        ):
            hypothesis = skill._collect_host_log_search_evidence(
                context,
                query="field",
                start_line=1,
                limit=20,
                http_timeout=1.0,
                evidence_scope="hypothesis_only",
            )
            node = skill._collect_host_log_search_evidence(
                context,
                query="field",
                start_line=1,
                limit=20,
                http_timeout=1.0,
                evidence_scope=None,
            )
        self.assertEqual(len(hypothesis), 20)
        self.assertEqual(hypothesis[0]["evidence_scope"], "hypothesis_only")
        self.assertNotIn("topsecret", hypothesis[0]["content"])
        self.assertIn("api_key=<MASKED>", hypothesis[0]["content"])
        self.assertLessEqual(len(hypothesis[0]["content"]), 4001)
        self.assertNotIn("evidence_scope", node[0])
        self.assertNotEqual(hypothesis[0]["evidence_id"], node[0]["evidence_id"])

    def test_hypothesis_log_parser_has_no_fault_tree_binding_option(self) -> None:
        parser = skill.build_parser()
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([
                "host-search-hypothesis-log",
                "--bundle", "run",
                "--round", "2",
                "--query", "field",
                "--fault-tree-item-id", "FTITEM-1",
            ])

    def test_host_read_methods_command_records_exact_authenticated_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw), seed_trace=False)
            args = argparse.Namespace(
                bundle=str(bundle), round=1, all=True, method_id=None,
            )
            output = StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(skill.command_host_read_methods(args), 0)
            rendered = json.loads(output.getvalue())
            self.assertEqual(rendered["diagnostic_methods"][0]["id"], "DOC-1")
            key = skill.load_host_validation_key(context)
            state = skill.load_host_session_state(bundle, context, key)
            trace = skill.verify_host_session_state(bundle, context, state, key)
            self.assertEqual(trace[0]["tool_name"], "read_diagnostic_methods")
            self.assertEqual(
                trace[0]["evidence_fingerprints"]["DOC-1"],
                skill.canonical_json_sha256(context["diagnostic_methods"][0]),
            )

    def test_forged_legacy_and_dynamic_host_evidence_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            write_json(bundle / "host-evidence.json", [{
                "evidence_id": "FORGED-1", "source_type": "host_log_search", "content": "fake",
            }])
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            state = json.loads((bundle / "host-session-state.json").read_text(encoding="utf-8"))
            state["evidence"].append({
                "evidence_id": "HOSTLOG-FORGED", "source_type": "host_log_search", "content": "fake",
            })
            write_json(bundle / "host-session-state.json", state)
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))

    def test_extra_triage_after_context_creation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            (bundle / "triage").mkdir()
            write_json(bundle / "triage" / "extra.json", {
                "evidence": {"LLM_RELEVANT": {"items": [{"id": "FORGED-TRIAGE"}]}},
            })
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))

    def test_context_rehash_trace_tampering_and_state_rollback_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            changed = json.loads((bundle / "host-agent-context.json").read_text(encoding="utf-8"))
            changed["limits"]["maximum_tool_calls_per_round"] = 99
            changed["context_sha256"] = skill._host_context_hash(changed)
            write_json(bundle / "host-agent-context.json", changed)
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            state = json.loads((bundle / "host-session-state.json").read_text(encoding="utf-8"))
            state["trace"][0]["arguments"]["all"] = False
            write_json(bundle / "host-session-state.json", state)
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw), seed_trace=False)
            old_state = (bundle / "host-session-state.json").read_bytes()
            skill.record_host_tool_call(
                bundle,
                tool_name="read_diagnostic_methods",
                arguments={"all": True},
                fault_tree_item_ids=[],
                evidence_ids=["DOC-1"],
                round_number=1,
                returned=1,
            )
            (bundle / "host-session-state.json").write_bytes(old_state)
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))

    def test_supported_conclusion_must_use_evidence_returned_for_same_item(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw), seed_trace=False)
            skill.record_host_tool_call(
                bundle,
                tool_name="read_diagnostic_methods",
                arguments={"all": True},
                fault_tree_item_ids=[],
                evidence_ids=["DOC-1"],
                round_number=1,
                returned=1,
            )
            skill.record_host_tool_call(
                bundle,
                tool_name="search_evidence",
                arguments={"query": "link"},
                fault_tree_item_ids=["FTITEM-1"],
                evidence_ids=["EV-1"],
                round_number=1,
                returned=1,
            )
            skill.record_host_tool_call(
                bundle,
                tool_name="search_evidence",
                arguments={"query": "missing"},
                fault_tree_item_ids=["FTITEM-2"],
                evidence_ids=[],
                round_number=2,
                returned=0,
            )
            envelope = valid_host_envelope(context)
            second = envelope["diagnosis"]["fault_tree_conclusions"][1]
            second.update({
                "status": "SUPPORTED", "evidence_ids": ["EV-1"], "next_action": "",
            })
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, envelope)

    def test_hypothesis_requires_support_and_event_code_type_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            no_support = valid_host_envelope(context)
            no_support["diagnosis"]["hypotheses"][0]["supporting_evidence"] = []
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, no_support)
            bad_event = valid_host_envelope(context)
            bad_event["diagnosis"]["hypotheses"][0]["event_code"] = 42
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, bad_event)

    def test_budget_failure_happens_before_backend_search_and_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, _context = make_host_bundle(Path(raw), seed_trace=False)
            for index in range(4):
                skill.record_host_tool_call(
                    bundle,
                    tool_name="search_evidence",
                    arguments={"query": f"term-{index}"},
                    fault_tree_item_ids=["FTITEM-1"],
                    evidence_ids=[],
                    round_number=1,
                    returned=0,
                )
            before = (bundle / "host-session-state.json").read_bytes()
            args = argparse.Namespace(
                bundle=str(bundle), round=1, fault_tree_item_id=["FTITEM-1"],
                query="link", start_line=1, limit=10, http_timeout=1.0,
            )
            with mock.patch.object(skill, "ensure_backend_for_command") as ensure:
                with self.assertRaises(skill.SkillError):
                    skill.command_host_search_log(args)
                ensure.assert_not_called()
            self.assertEqual(before, (bundle / "host-session-state.json").read_bytes())

    def test_finalized_session_is_idempotent_but_rejects_new_tools_and_output_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            draft = bundle / "host-diagnosis.json"
            write_json(draft, valid_host_envelope(context))
            first = skill.finalize_host_result(bundle, draft)
            self.assertTrue(first["ok"])
            second = skill.finalize_host_result(bundle, draft)
            self.assertTrue(second["already_finalized"])
            with self.assertRaises(skill.SkillError):
                skill.record_host_tool_call(
                    bundle,
                    tool_name="get_evidence",
                    arguments={"evidence_ids": ["EV-1"]},
                    fault_tree_item_ids=["FTITEM-1"],
                    evidence_ids=["EV-1"],
                    round_number=2,
                    returned=1,
                )
            (bundle / "host-diagnosis.md").write_text("tampered", encoding="utf-8")
            with self.assertRaises(skill.SkillError):
                skill.finalize_host_result(bundle, draft)
            with self.assertRaises(skill.SkillError):
                skill.record_host_tool_call(
                    bundle,
                    tool_name="search_evidence",
                    arguments={"query": "over-budget"},
                    fault_tree_item_ids=["FTITEM-1"],
                    evidence_ids=[],
                    round_number=1,
                    returned=0,
                )

    def test_valid_result_is_normalized_and_ranked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            normalized, validation = skill.validate_host_result(bundle, valid_host_envelope(context))
            self.assertTrue(validation["accepted"])
            self.assertEqual(normalized["hypotheses"][0]["rank"], 1)
            self.assertEqual(normalized["hypotheses"][0]["confidence_level"], "HIGH")
            self.assertEqual(normalized["hypotheses"][1]["confidence_level"], "LOW")

    def test_context_hash_and_unknown_evidence_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            bad_hash = valid_host_envelope(context)
            bad_hash["context_sha256"] = "0" * 64
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, bad_hash)
            unknown = valid_host_envelope(context)
            unknown["diagnosis"]["confirmed_facts"][0]["evidence_ids"] = ["EV-FAKE"]
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, unknown)

    def test_immutable_source_evidence_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            evidence = json.loads((bundle / "evidence.json").read_text(encoding="utf-8"))
            evidence[0]["content"] = "tampered line"
            write_json(bundle / "evidence.json", evidence)
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))

    def test_method_document_cannot_be_used_as_case_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            envelope = valid_host_envelope(context)
            envelope["diagnosis"]["hypotheses"][0]["supporting_evidence"] = ["DOC-1"]
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, envelope)

    def test_fault_tree_completeness_binding_and_attempts_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            missing = valid_host_envelope(context)
            missing["diagnosis"]["fault_tree_conclusions"].pop()
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, missing)
            wrong = valid_host_envelope(context)
            wrong["diagnosis"]["fault_tree_conclusions"][0]["method_document_id"] = "DOC-X"
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, wrong)
            state = skill._new_host_session_state(context)
            key = skill.load_host_validation_key(context)
            skill.write_host_session_state(bundle, context, state, key)
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, valid_host_envelope(context))

    def test_supported_requires_evidence_and_insufficient_requires_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            no_evidence = valid_host_envelope(context)
            no_evidence["diagnosis"]["fault_tree_conclusions"][0]["evidence_ids"] = []
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, no_evidence)
            no_action = valid_host_envelope(context)
            no_action["diagnosis"]["fault_tree_conclusions"][1]["next_action"] = ""
            with self.assertRaises(skill.SkillError):
                skill.validate_host_result(bundle, no_action)

    def test_model_narrative_cannot_override_authoritative_host_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(
                Path(raw),
                deterministic_baseline={
                    "summary": "baseline",
                    "analysis_engine": "provider-control-state-42",
                    "diagnostic_planning": {},
                },
            )
            cases = (
                "Observed link failure. Execution mode host-agent; synthesis mode skipped.",
                "观察到链路故障。执行模式 host-agent，综合模式 skipped，停止原因 baseline。",
                "Observed link failure. MOCK_PROVIDER_DETERMINISTIC_BASELINE",
                "Observed link failure under provider-control-state-42.",
                "Observed link failure under provider control state 42.",
            )
            for summary in cases:
                with self.subTest(summary=summary):
                    envelope = valid_host_envelope(context)
                    envelope["diagnosis"]["summary"] = summary
                    with self.assertRaisesRegex(skill.SkillError, "host-finalize supplies"):
                        skill.validate_host_result(bundle, envelope)

            non_summary = valid_host_envelope(context)
            non_summary["diagnosis"]["hypotheses"][0]["description"] = (
                "The evidence is relevant. Agent status: COMPLETED."
            )
            with self.assertRaisesRegex(skill.SkillError, "host-finalize supplies"):
                skill.validate_host_result(bundle, non_summary)

            separator_bypass = valid_host_envelope(context)
            separator_bypass["diagnosis"]["limitations"] = [
                "synthesis_status=SKIPPED; agent_status=COMPLETED",
            ]
            with self.assertRaisesRegex(skill.SkillError, "host-finalize supplies"):
                skill.validate_host_result(bundle, separator_bypass)

            benign = valid_host_envelope(context)
            benign["diagnosis"]["summary"] = (
                "The AP device operating mode is bridge mode and the observed link went down."
            )
            normalized, validation = skill.validate_host_result(bundle, benign)
            self.assertIn("bridge mode", normalized["summary"])
            self.assertTrue(validation["accepted"])

    def test_opaque_ids_are_allowed_only_in_structured_citation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            for field, value in (
                ("summary", "The link failed at EVT-deadbeef."),
                ("description", "CHK-1234 advises collecting counters."),
            ):
                with self.subTest(field=field):
                    envelope = valid_host_envelope(context)
                    if field == "summary":
                        envelope["diagnosis"]["summary"] = value
                    else:
                        envelope["diagnosis"]["hypotheses"][0][field] = value
                    with self.assertRaisesRegex(skill.SkillError, "opaque internal ID"):
                        skill.validate_host_result(bundle, envelope)

            readable = valid_host_envelope(context)
            readable["diagnosis"]["summary"] = "The link failed at ap.log:L10."
            normalized, validation = skill.validate_host_result(bundle, readable)
            self.assertEqual(normalized["summary"], "The link failed at ap.log:L10.")
            self.assertTrue(validation["accepted"])

    def test_invalid_finalize_does_not_overwrite_existing_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(Path(raw))
            output = bundle / "host-diagnosis.validated.json"
            output.write_text("sentinel", encoding="utf-8")
            invalid = valid_host_envelope(context)
            invalid["context_sha256"] = "wrong"
            draft = bundle / "host-diagnosis.json"
            write_json(draft, invalid)
            with self.assertRaises(skill.SkillError):
                skill.finalize_host_result(bundle, draft)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel")

    def test_finalization_publishes_validated_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bundle, context = make_host_bundle(
                Path(raw),
                deterministic_baseline={
                    "summary": "baseline",
                    "diagnostic_planning": {
                        "planner_mode": "legacy-planner",
                        "rounds": [{"provider": "mock"}],
                        "tool_calls": [{"name": "backend-only"}],
                    },
                },
            )
            draft = bundle / "host-diagnosis.json"
            write_json(draft, valid_host_envelope(context))
            result = skill.finalize_host_result(bundle, draft)
            self.assertTrue(result["ok"])
            validated = json.loads((bundle / "host-diagnosis.validated.json").read_text(encoding="utf-8"))
            self.assertEqual(validated["synthesis_status"]["mode"], "HOST_CLI_EVIDENCE_VALIDATED")
            planning = validated["diagnostic_planning"]
            self.assertEqual(planning["planner_mode"], "host_cli_read_only_tools")
            self.assertNotIn("rounds", planning)
            self.assertNotIn("tool_calls", planning)
            self.assertEqual(planning["host_tool_summary"]["tool_calls"], 3)
            coverage_rounds = {
                item["id"]: item["last_round"]
                for item in planning["fault_tree_coverage"]["items"]
            }
            self.assertEqual(coverage_rounds, {"FTITEM-1": 1, "FTITEM-2": 2})
            validation = json.loads((bundle / "host-validation.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["fault_tree_concluded"], 2)
            self.assertTrue(validation["fault_tree_complete"])
            self.assertEqual(validation["fault_tree_status_counts"]["INSUFFICIENT_EVIDENCE"], 1)
            self.assertEqual(
                validation["fault_tree_item_rounds"],
                {"FTITEM-1": 1, "FTITEM-2": 2},
            )
            markdown = (bundle / "host-diagnosis.md").read_text(encoding="utf-8")
            self.assertIn("Final synthesis: `HOST_CLI_EVIDENCE_VALIDATED`", markdown)
            self.assertIn("Agent stop reason: `HOST_AGENT_VALIDATED`", markdown)


if __name__ == "__main__":
    unittest.main()
