"""New actual managed-sidecar updater path, isolated synthetic database only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from test_offline_knowledge_package import SEED, VERIFY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    runtime = package / "runtime/python/python.exe"
    with tempfile.TemporaryDirectory(prefix="gwap-offline-sidecar-") as temporary:
        root = Path(temporary)
        updater, data = root / "updater", root / "server"
        with zipfile.ZipFile(args.updater) as archive:
            archive.extractall(updater)
        def child(code, arguments):
            result = subprocess.run([str(runtime), "-B", "-s", "-c", code, *map(str, arguments)],
                cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=600)
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout
        seed = SEED.replace('mode="builtin", provider="hashing", model_name="synthetic",',
            'mode="api", provider="llama_cpp_local", model_name="BAAI/bge-base-zh-v1.5", base_url="http://127.0.0.1:25445/v1",')
        child(seed, [package, data])
        # Actual double-click entry: new loopback address must replace the stale
        # saved address only for this process. No global profile edit is allowed.
        run = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(updater / "Update-Network-Skill.bat")],
            cwd=updater, env={**os.environ, "GWAP_SERVER_PACKAGE_ROOT": str(package), "GWAP_SERVER_DATA_ROOT": str(data)},
            input="\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600)
        assert run.returncode == 0 and "PUBLISHED" in run.stdout, run.stdout + run.stderr
        verify = VERIFY.replace("job.attempt == 2", "job.attempt == 1").replace(
            'assert knowledge_reset.KIND == "knowledge_reset"',
            'assert profile.base_url == "http://127.0.0.1:25445/v1"\n    assert knowledge_reset.KIND == "knowledge_reset"')
        child(verify, [package, data, updater / "hilink-diag.zip"])
        assert not list((data / "data/logs/local-models").glob("api-key-*.txt"))
        logs = list((data / "data/logs/local-models").glob("*.log"))
        assert logs and not any("reranker" in p.name.lower() for p in logs)
    evidence = {"status": "PASS", "old_version": "0.5.3", "embedding": "actual bundled BGE GGUF CPU",
        "entry": "Update-Network-Skill.bat", "saved_endpoint_unchanged": True,
        "six_original_files_and_old_wiki_verified": True, "restart_status": "PUBLISHED",
        "temporary_model_key_removed": True, "reranker_started": False, "diagnostic_model_calls": 0}
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
