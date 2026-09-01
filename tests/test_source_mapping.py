from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_source_mapping.py"
SPEC = importlib.util.spec_from_file_location("check_source_mapping", SCRIPT)
assert SPEC and SPEC.loader
source_mapping = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_mapping)


class SourceMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source_repo = self.base / "source"
        self.skill_root = self.base / "skill"
        self.source_repo.mkdir()
        self.skill_root.mkdir()
        self._git("init")
        self._git("config", "user.email", "source-mapping@example.invalid")
        self._git("config", "user.name", "Source Mapping Test")
        self._git("config", "core.autocrlf", "false")

        source_files = {
            ".env.example": b"VALUE=source\n",
            "backend/Dockerfile": b"FROM scratch\n",
            "backend/tests/test_sample.py": b"def test_sample():\n    pass\n",
            "backend/app/identical.py": b"VALUE = 1\n",
        }
        for runtime_path in source_mapping.DECLARED_PORTABILITY_PATCHES:
            source_files[runtime_path] = b"SOURCE = True\n"
        for relative, data in source_files.items():
            self._write(self.source_repo / relative, data)
        self._git("add", ".")
        self._git("commit", "-m", "source")
        self.commit = self._git("rev-parse", "HEAD").stdout.strip()

        runtime_files = {
            ".env.example": b"VALUE=source\r\n",
            "backend/app/identical.py": b"VALUE = 1\n",
            "日志分析.md": "# 日志分析\n".encode("utf-8"),
            "故障树.md": "# 故障树\n".encode("utf-8"),
        }
        for runtime_path in source_mapping.DECLARED_PORTABILITY_PATCHES:
            runtime_files[runtime_path] = b"SKILL_ONLY = True\n"
        for relative, data in runtime_files.items():
            self._write(self.skill_root / "runtime" / relative, data)
        self.original_expected_fingerprints = (
            source_mapping.EXPECTED_PORTABILITY_PATCH_FINGERPRINTS
        )
        source_mapping.EXPECTED_PORTABILITY_PATCH_FINGERPRINTS = {
            runtime_path: source_mapping.portability_patch_fingerprints(
                b"SOURCE = True\n", b"SKILL_ONLY = True\n"
            )
            for runtime_path in source_mapping.DECLARED_PORTABILITY_PATCHES
        }
        self._write_manifest(self.commit)

    def tearDown(self) -> None:
        source_mapping.EXPECTED_PORTABILITY_PATCH_FINGERPRINTS = (
            self.original_expected_fingerprints
        )
        self.temp.cleanup()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.source_repo), *arguments],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_manifest(self, commit: str) -> None:
        declared_patches = sorted(
            set(source_mapping.DECLARED_PORTABILITY_PATCHES.values())
            | source_mapping.NON_RUNTIME_PATCHES
        )
        manifest = {
            "source": {
                "repository": "source",
                "branch": "main",
                "commit": commit,
            },
            "runtime": {
                "local_patches": declared_patches,
            },
        }
        (self.skill_root / "skill-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_passes_with_normalized_files_declared_patches_and_local_methods(self) -> None:
        report = source_mapping.build_report(self.skill_root, self.source_repo)

        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["ok"])
        self.assertTrue(report["verifiable"])
        patch_path_count = len(source_mapping.DECLARED_PORTABILITY_PATCHES)
        self.assertEqual(report["summary"]["runtime_files"], 4 + patch_path_count)
        self.assertEqual(report["summary"]["source_mapped_files"], 2 + patch_path_count)
        self.assertEqual(report["summary"]["normalized-identical"], 2)
        self.assertEqual(
            report["summary"]["declared-portability-patch"], patch_path_count
        )
        self.assertEqual(
            report["summary"]["declared-portability-patch-verified"],
            patch_path_count,
        )
        self.assertEqual(
            report["summary"]["declared-portability-patch-failed"], 0
        )
        self.assertEqual(report["summary"]["local-method"], 2)
        self.assertEqual(report["summary"]["source_excluded_files"], 2)
        self.assertFalse(report["errors"])

        generation_patch = next(
            row
            for row in report["declared_patches"]
            if row["name"].startswith("external method root with immutable generations")
        )
        self.assertEqual(generation_patch["status"], "VERIFIED")
        self.assertEqual(
            generation_patch["runtime_paths"],
            [
                "backend/app/api/system.py",
                "backend/app/services/diagnostic_methods.py",
            ],
        )
        config_patch = next(
            row
            for row in report["files"]
            if row["runtime_path"] == "backend/app/core/config.py"
        )
        self.assertEqual(config_patch["verification_status"], "VERIFIED")
        self.assertTrue(all(config_patch["fingerprint_matches"].values()))
        self.assertEqual(
            {
                field: config_patch[field]
                for field in source_mapping.PATCH_FINGERPRINT_FIELDS
            },
            config_patch["expected_fingerprints"],
        )

    def test_fails_when_a_declared_patch_drifts_from_its_pinned_delta(self) -> None:
        runtime_path = "backend/app/core/config.py"
        self._write(
            self.skill_root / "runtime" / runtime_path,
            b"SKILL_ONLY = False\n",
        )

        report = source_mapping.build_report(self.skill_root, self.source_repo)

        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["summary"]["declared-portability-patch-failed"], 1
        )
        entry = next(
            row for row in report["files"] if row["runtime_path"] == runtime_path
        )
        self.assertEqual(entry["verification_status"], "HASH_MISMATCH")
        self.assertTrue(entry["fingerprint_matches"]["source_normalized_sha256"])
        self.assertFalse(entry["fingerprint_matches"]["runtime_normalized_sha256"])
        self.assertFalse(entry["fingerprint_matches"]["normalized_diff_sha256"])
        self.assertTrue(
            any(
                "declared portability patch fingerprint mismatch "
                "(runtime_normalized_sha256)" in item
                for item in report["errors"]
            )
        )

    def test_missing_pinned_patch_fingerprint_fails_closed(self) -> None:
        missing_path = "backend/app/core/config.py"
        source_mapping.EXPECTED_PORTABILITY_PATCH_FINGERPRINTS = {
            path: value
            for path, value in source_mapping.EXPECTED_PORTABILITY_PATCH_FINGERPRINTS.items()
            if path != missing_path
        }

        report = source_mapping.build_report(self.skill_root, self.source_repo)

        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["verifiable"])
        self.assertIn(
            f"declared portability patch has no pinned fingerprint: {missing_path}",
            report["errors"],
        )

    def test_fails_for_an_undeclared_runtime_difference(self) -> None:
        self._write(
            self.skill_root / "runtime" / "backend" / "app" / "identical.py",
            b"VALUE = 2\n",
        )

        report = source_mapping.build_report(self.skill_root, self.source_repo)

        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["unexpected"], 1)
        self.assertTrue(
            any("undeclared runtime difference" in item for item in report["errors"])
        )

    def test_missing_source_commit_is_skipped_and_returns_three(self) -> None:
        self._write_manifest("f" * 40)
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = source_mapping.main(
                [
                    "--skill-root",
                    str(self.skill_root),
                    "--source-repo",
                    str(self.source_repo),
                ]
            )

        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(report["status"], "SKIPPED")
        self.assertIsNone(report["ok"])
        self.assertFalse(report["verifiable"])
        self.assertIn("no network fetch was attempted", report["skip_reason"])

    def test_duplicate_manifest_patch_name_fails_closed(self) -> None:
        manifest_path = self.skill_root / "skill-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime"]["local_patches"].append(
            manifest["runtime"]["local_patches"][0]
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = source_mapping.build_report(self.skill_root, self.source_repo)

        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "manifest runtime.local_patches contains duplicates", report["errors"]
        )


if __name__ == "__main__":
    unittest.main()
