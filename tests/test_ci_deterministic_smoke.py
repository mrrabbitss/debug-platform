from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_deterministic_smoke.py"
SPEC = importlib.util.spec_from_file_location("ci_deterministic_smoke", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class MethodGenerationSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / "state"
        self.generation_id = "gen-0123456789abcdefabcd"
        self.generation_dir = (
            self.state_dir / "method-packs" / "generations" / self.generation_id
        )
        self.generation_dir.mkdir(parents=True)
        legacy = self.state_dir / "methods"
        legacy.mkdir(parents=True)
        contents = {
            "故障树.md": "# Fault tree\n\nSynthetic diagnosis method.\n",
            "日志分析.md": "# Log analysis\n\nSynthetic log method.\n",
        }
        roles = {}
        for filename, content in contents.items():
            (self.generation_dir / filename).write_text(
                content, encoding="utf-8", newline="\n"
            )
            (legacy / filename).write_text(content, encoding="utf-8", newline="\n")
            roles[filename] = {
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()
            }
        manifest = {
            "schema": "gw-ap-debug-method-generation/v1",
            "id": self.generation_id,
            "scope": "persistent",
            "roles": roles,
        }
        (self.generation_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        pointer = {
            "schema": "gw-ap-debug-method-active/v1",
            "generation_id": self.generation_id,
            "path": f"generations/{self.generation_id}",
            "manifest_sha256": smoke.canonical_json_sha256(manifest),
        }
        pointer_path = self.state_dir / "method-packs" / "active.json"
        pointer_path.write_text(
            json.dumps(pointer, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_verifies_authenticated_immutable_generation_and_legacy_seeds(self) -> None:
        result = smoke.verify_method_generation(self.state_dir)

        self.assertEqual(result["generation_id"], self.generation_id)
        self.assertEqual(result["scope"], "persistent")
        self.assertEqual(set(result["roles"]), {"故障树.md", "日志分析.md"})
        self.assertEqual(result["legacy_seed_dir"], str(self.state_dir / "methods"))

    def test_rejects_role_content_tampering(self) -> None:
        (self.generation_dir / "日志分析.md").write_text(
            "tampered\n", encoding="utf-8", newline="\n"
        )

        with self.assertRaisesRegex(smoke.SmokeFailure, "role hash mismatch"):
            smoke.verify_method_generation(self.state_dir)

    def test_temporary_work_directory_retries_transient_windows_lock(self) -> None:
        real_rmtree = smoke.shutil.rmtree
        calls = 0

        def flaky_rmtree(path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                error = OSError("simulated sharing violation")
                error.winerror = 32
                raise error
            real_rmtree(path)

        with (
            mock.patch.object(smoke.shutil, "rmtree", side_effect=flaky_rmtree),
            mock.patch.object(smoke.time, "sleep"),
        ):
            with smoke.temporary_work_directory() as work_dir:
                (work_dir / "canary.txt").write_text("ok", encoding="utf-8")
                retained_path = work_dir

        self.assertEqual(calls, 2)
        self.assertFalse(retained_path.exists())


class RunScopedExternalSkillSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state_dir = root / "state"
        self.output_dir = root / "export"
        self.output_dir.mkdir(parents=True)
        control_root = self.state_dir / "method-packs"
        control_root.mkdir(parents=True)
        self.pointer = {
            "schema": "gw-ap-debug-method-active/v1",
            "generation_id": "gen-aaaaaaaaaaaaaaaaaaaa",
            "path": "generations/gen-aaaaaaaaaaaaaaaaaaaa",
            "manifest_sha256": "b" * 64,
        }
        (control_root / "active.json").write_text(
            json.dumps(self.pointer), encoding="utf-8"
        )
        contents = {
            "故障树.md": (
                "# Fault tree\n\n"
                f"Knowledge `{smoke.EXTERNAL_SKILL_CANARY}` requires peer evidence.\n"
            ),
            "日志分析.md": (
                "# Log analysis\n\n"
                f"Search `{smoke.EXTERNAL_LOG_CANARY}` in AP logs.\n"
            ),
        }
        role_hashes = {
            filename: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for filename, content in contents.items()
        }
        self.pack = {
            "id": "ci-external-ap-diagnosis-0123456789ab",
            "name": smoke.EXTERNAL_SKILL_NAME,
            "content_sha256": "c" * 64,
        }
        identity = {
            "scope": "run",
            "roles": role_hashes,
            "packs": [self.pack],
        }
        self.generation_id = "gen-" + smoke.canonical_json_sha256(identity)[:20]
        generation_dir = control_root / "generations" / self.generation_id
        generation_dir.mkdir(parents=True)
        for filename, content in contents.items():
            (generation_dir / filename).write_text(
                content, encoding="utf-8", newline="\n"
            )
        generation = {
            "schema": "gw-ap-debug-method-generation/v1",
            "id": self.generation_id,
            "scope": "run",
            "roles": {
                filename: {"sha256": digest}
                for filename, digest in role_hashes.items()
            },
            "packs": [self.pack],
        }
        (generation_dir / "manifest.json").write_text(
            json.dumps(generation, ensure_ascii=False), encoding="utf-8"
        )
        case_id = "CASE-ci-run-scope"
        export_manifest = {
            "case_id": case_id,
            "diagnostic_method_scope": "run",
            "persistent_active_unchanged": True,
            "method_generation_id": self.generation_id,
            "diagnostic_methods_dir": str(generation_dir),
            "imported_diagnostic_method_packs": [
                {**self.pack, "status": "RUN_SCOPED"}
            ],
        }
        (self.output_dir / "manifest.json").write_text(
            json.dumps(export_manifest), encoding="utf-8"
        )
        catalog = [
            {
                "id": f"LOCALDOC-{digest[:20]}",
                "content_sha256": digest,
            }
            for digest in role_hashes.values()
        ]
        (self.output_dir / "analysis.json").write_text(
            json.dumps({"diagnostic_planning": {"method_catalog": catalog}}),
            encoding="utf-8",
        )
        evidence = [
            {
                **catalog[index],
                "content_omitted": True,
            }
            for index, _content in enumerate(contents.values())
        ]
        (self.output_dir / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
        )
        triage_dir = self.output_dir / "triage"
        triage_dir.mkdir()
        (triage_dir / "TRIAGE-ci.json").write_text(
            json.dumps({
                "evidence": {
                    "METHOD_REQUIRED": {
                        "items": [{"message": smoke.EXTERNAL_LOG_CANARY}]
                    }
                }
            }),
            encoding="utf-8",
        )
        self.case_id = case_id

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_verifies_run_generation_canaries_and_state_isolation(self) -> None:
        result = smoke.verify_run_scoped_external_methods(
            self.state_dir,
            self.output_dir,
            persistent_pointer_before=self.pointer,
        )

        self.assertEqual(result["generation_id"], self.generation_id)
        self.assertEqual(result["method_catalog_hashes_verified"], 2)
        self.assertTrue(result["knowledge_canary_in_generation_verified"])
        self.assertTrue(result["log_canary_in_triage_output_verified"])
        self.assertTrue(result["case_binding_cleaned"])

    def test_rejects_leftover_case_binding(self) -> None:
        binding_name = hashlib.sha256(self.case_id.encode("utf-8")).hexdigest()[:24]
        binding = self.state_dir / "method-packs" / "bindings" / f"case-{binding_name}.json"
        binding.parent.mkdir(parents=True)
        binding.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(smoke.SmokeFailure, "left its case method binding"):
            smoke.verify_run_scoped_external_methods(
                self.state_dir,
                self.output_dir,
                persistent_pointer_before=self.pointer,
            )


class CliContractSmokeTests(unittest.TestCase):
    def test_check_cli_contract_requires_explicit_role_path_flags(self) -> None:
        repository_root = SCRIPT.parents[1]
        top_help = "bootstrap run triage diagnose result"
        run_help = " ".join((
            "--mode deterministic --bootstrap --state-dir --output-dir --ap-log --job-timeout",
            "--diagnostic-skill --diagnostic-skill-fault-tree",
            "--diagnostic-skill-scope --max-host-method-tokens",
        ))

        with mock.patch.object(
            smoke, "command_output", side_effect=[top_help, run_help]
        ):
            with self.assertRaisesRegex(
                smoke.SmokeFailure, "diagnostic-skill-log-analysis"
            ):
                smoke.check_cli_contract(repository_root)

    def test_external_skill_uses_explicit_frontmatter_role_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = smoke.write_external_diagnostic_skill(Path(raw) / "external")
            skill_text = (Path(result["skill_dir"]) / "SKILL.md").read_text(
                encoding="utf-8"
            )

        self.assertIn("gw_ap_debug_fault_tree: references/diagnosis.md", skill_text)
        self.assertIn("gw_ap_debug_log_analysis: references/logs.md", skill_text)


if __name__ == "__main__":
    unittest.main()
