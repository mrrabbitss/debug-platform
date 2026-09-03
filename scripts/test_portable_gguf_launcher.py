from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = PROJECT_ROOT / "deploy" / "windows-portable" / "portable_launcher.py"


def _load_launcher():
    module_spec = importlib.util.spec_from_file_location(
        "portable_launcher_contract_test",
        LAUNCHER_PATH,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError("Unable to load portable launcher")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


class BundledModelContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.launcher = _load_launcher()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.package_root = Path(self.temporary.name).resolve()
        (self.package_root / "runtime" / "llama").mkdir(parents=True)
        (self.package_root / "models" / "embedding").mkdir(parents=True)
        (self.package_root / "models" / "reranker").mkdir(parents=True)
        (self.package_root / "runtime" / "llama" / "llama-server.exe").write_bytes(b"exe")
        (self.package_root / "models" / "embedding" / "bge.gguf").write_bytes(b"gguf")
        (self.package_root / "models" / "reranker" / "qwen.gguf").write_bytes(b"gguf")
        self.old_root = self.launcher.PACKAGE_ROOT
        self.old_manifest = self.launcher.MODEL_COMPONENTS_PATH
        self.launcher.PACKAGE_ROOT = self.package_root
        self.launcher.MODEL_COMPONENTS_PATH = self.package_root / "model-components.json"

    def tearDown(self) -> None:
        self.launcher.PACKAGE_ROOT = self.old_root
        self.launcher.MODEL_COMPONENTS_PATH = self.old_manifest
        self.temporary.cleanup()

    def _payload(self) -> dict:
        executable = "runtime/llama/llama-server.exe"
        return {
            "schema_version": 1,
            "bundle_id": "test",
            "components": [
                {
                    "id": "bge",
                    "task_type": "embedding",
                    "executable": executable,
                    "model": "models/embedding/bge.gguf",
                    "model_name": "BAAI/bge-base-zh-v1.5",
                    "base_path": "/v1",
                    "health_path": "/health",
                    "server_arguments": ["--embedding", "--pooling", "cls"],
                },
                {
                    "id": "qwen",
                    "task_type": "reranker",
                    "executable": executable,
                    "model": "models/reranker/qwen.gguf",
                    "model_name": "Qwen/Qwen3-Reranker-0.6B",
                    "base_path": "/v1",
                    "health_path": "/health",
                    "server_arguments": ["--reranking", "--pooling", "rank"],
                },
            ],
        }

    def _write_payload(self, payload: dict) -> None:
        self.launcher.MODEL_COMPONENTS_PATH.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def test_valid_contract_resolves_two_package_local_components(self) -> None:
        self._write_payload(self._payload())
        specs = self.launcher.load_bundled_model_specs()
        self.assertEqual([item.task_type for item in specs], ["embedding", "reranker"])
        self.assertTrue(all(item.executable.is_file() for item in specs))
        self.assertTrue(all(item.model.is_file() for item in specs))

    def test_contract_cannot_override_loopback_or_random_api_key(self) -> None:
        payload = self._payload()
        payload["components"][0]["server_arguments"] += ["--host", "0.0.0.0"]
        self._write_payload(payload)
        with self.assertRaisesRegex(
            self.launcher.PortableLayoutError,
            "Unsafe server argument",
        ):
            self.launcher.load_bundled_model_specs()

    def test_contract_rejects_duplicate_task_processes(self) -> None:
        payload = self._payload()
        payload["components"][1]["task_type"] = "embedding"
        self._write_payload(payload)
        with self.assertRaisesRegex(
            self.launcher.PortableLayoutError,
            "Invalid local model component contract",
        ):
            self.launcher.load_bundled_model_specs()

    def test_ready_sidecars_are_exported_only_through_ephemeral_environment(self) -> None:
        self._write_payload(self._payload())
        specs = self.launcher.load_bundled_model_specs()
        manager = self.launcher.BundledModelManager(
            specs,
            data_root=self.package_root / "state",
            timeout_seconds=10,
        )
        manager.ready = {
            "embedding": types.SimpleNamespace(
                base_url="http://127.0.0.1:41001/v1",
                spec=specs[0],
            ),
            "reranker": types.SimpleNamespace(
                base_url="http://127.0.0.1:41002/v1",
                spec=specs[1],
            ),
        }
        with mock.patch.dict(os.environ, {}, clear=False):
            manager.apply_environment()
            self.assertEqual(
                os.environ["BUNDLED_GGUF_EMBEDDING_URL"],
                "http://127.0.0.1:41001/v1",
            )
            self.assertEqual(
                os.environ["BUNDLED_GGUF_RERANKER_URL"],
                "http://127.0.0.1:41002/v1",
            )
            self.assertGreater(len(os.environ["BUNDLED_GGUF_API_KEY"]), 32)

    def test_sidecar_command_uses_key_file_instead_of_plaintext_key(self) -> None:
        self._write_payload(self._payload())
        spec = self.launcher.load_bundled_model_specs()[0]
        key_file = self.package_root / "state" / "key.txt"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("secret-value", encoding="ascii")
        process_job = mock.Mock()
        process_job.assign.return_value = True
        fake_process = mock.Mock(pid=12345)
        with mock.patch.object(
            self.launcher.subprocess,
            "Popen",
            return_value=fake_process,
        ) as popen:
            sidecar = self.launcher.BundledModelProcess(
                spec,
                api_key="secret-value",
                api_key_file=key_file,
                log_root=self.package_root / "state" / "logs",
                process_job=process_job,
            )
            sidecar.start()
            sidecar._close_log()
        arguments = popen.call_args.args[0]
        self.assertIn("--api-key-file", arguments)
        self.assertIn(str(key_file), arguments)
        self.assertNotIn("secret-value", arguments)
        environment = popen.call_args.kwargs["env"]
        self.assertIn("127.0.0.1", environment["NO_PROXY"].split(","))
        self.assertIn("localhost", environment["NO_PROXY"].split(","))
        self.assertIn("127.0.0.1", environment["no_proxy"].split(","))
        self.assertIn("localhost", environment["no_proxy"].split(","))

    def test_sidecar_environment_preserves_existing_proxy_bypass(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"NO_PROXY": "intranet.example,localhost"},
            clear=True,
        ):
            environment = self.launcher._sidecar_environment()

        self.assertEqual(
            environment["NO_PROXY"],
            "intranet.example,localhost,127.0.0.1",
        )
        self.assertEqual(
            environment["no_proxy"],
            "intranet.example,localhost,127.0.0.1",
        )

    def test_sidecar_health_uses_proxy_free_loopback_opener(self) -> None:
        self._write_payload(self._payload())
        spec = self.launcher.load_bundled_model_specs()[0]
        key_file = self.package_root / "state" / "key.txt"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("secret-value", encoding="ascii")
        process = self.launcher.BundledModelProcess(
            spec,
            api_key="secret-value",
            api_key_file=key_file,
            log_root=self.package_root / "state" / "logs",
            process_job=mock.Mock(),
        )
        process.process = mock.Mock()
        process.process.poll.return_value = None

        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with (
            mock.patch.object(
                self.launcher._LOOPBACK_HTTP_OPENER,
                "open",
                return_value=response,
            ) as proxy_free_open,
            mock.patch.object(
                self.launcher.urllib.request,
                "urlopen",
                side_effect=AssertionError("system proxy-aware urlopen must not be used"),
            ),
        ):
            ready, detail = process.wait_until_ready(1)

        self.assertTrue(ready, detail)
        proxy_free_open.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_job_object_kills_attached_process_when_closed(self) -> None:
        job = self.launcher.WindowsKillOnCloseJob()
        self.assertTrue(job.active)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            self.assertTrue(job.assign(process.pid))
            job.close()
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
