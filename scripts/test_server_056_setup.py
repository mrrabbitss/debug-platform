"""Execute the exact EXE against synthetic data and held directory handles.

Uses this development Windows account only; never reads company server data.
The prior application fixture is created only if the legacy path is absent.
"""
import argparse
import ctypes
from contextlib import closing
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time

from test_offline_knowledge_package import SEED

KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
KERNEL.CreateFileW.restype = wintypes.HANDLE
KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID_HANDLE = ctypes.c_void_p(-1).value


def lock_directory(path):
    handle = KERNEL.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02000000, None)
    if handle == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def remove_owned(path, parent):
    resolved, parent = path.resolve(), parent.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise ValueError("Test cleanup escaped its explicitly owned root")
    shutil.rmtree(resolved)


def snapshot(path):
    return {str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in path.rglob("*") if p.is_file()}


def check_knowledge(data, package, *, old=False):
    database = data / "data/gw_ap_debug.db"
    with closing(sqlite3.connect(database)) as db:
        docs = db.execute("select id,title,content,metadata_json from knowledge_documents where active=1 and review_status='ACTIVE'").fetchall()
        skills = [d for d in docs if json.loads(d[3]).get("content_kind") == "SKILL"]
        assert len(skills) == 6, len(skills)
        # Five child files match original bytes; the root retains the full source
        # plus only the existing, explicitly documented platform adapter.
        for _, title, content, _ in skills:
            original = (package / "bundled-knowledge" / title).read_bytes().decode("utf-8-sig")
            if title.endswith("/SKILL.md"):
                for line in original.splitlines():
                    assert line in content
            else:
                assert content == original, title
        if old:
            assert db.execute("select content from knowledge_documents where id='old'").fetchone()[0] == "Preserve old wiki"
            assert db.execute("select title from cases where id='case'").fetchone()[0] == "Synthetic case retained"
            assert db.execute("select active from knowledge_documents where id='old-root'").fetchone()[0] == 0
            assert db.execute("select count(*) from jobs where kind='offline_network_skill_update' and status='COMPLETED'").fetchone()[0] == 1
        else:
            assert db.execute("select count(*) from user_accounts where role='ADMIN' and active=1").fetchone()[0] == 1
        return len(skills)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--old-package", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--reuse-running-refusal", action="store_true",
                        help="Reuse this exact EXE's already-passed running-lock scenario")
    parser.add_argument("--reuse-upgrade-check", action="store_true")
    args = parser.parse_args()
    args.installer, args.old_package = args.installer.resolve(strict=True), args.old_package.resolve(strict=True)
    home = Path(os.environ["LOCALAPPDATA"]) / "Programs/GWAPDebugServer"
    legacy, releases = home / "app", home / "releases"
    if legacy.exists():
        raise ValueError("Development account already has an app installation; test refuses to overwrite it")
    home.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.old_package, legacy)
    before = snapshot(legacy)
    owned_releases = []
    handles = []
    try:
        with tempfile.TemporaryDirectory(prefix="gwap-056-setup-", ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            data = root / "old-data"
            runtime = args.old_package / "runtime/python/python.exe"
            seed = SEED.replace("Base.metadata.create_all(engine)",
                "from app.core.migrations import run_database_migrations\nrun_database_migrations()")
            seed = seed.replace('mode="builtin", provider="hashing", model_name="synthetic",',
                'mode="api", provider="llama_cpp_local", model_name="BAAI/bge-base-zh-v1.5", base_url="http://127.0.0.1:25445/v1",')
            seed += '\nwith SessionLocal() as db:\n    db.add(KnowledgeDocument(id="old-root", title="hilink-diag/SKILL.md", content="Previous edited root", active=True, review_status="ACTIVE", metadata_json=json_dumps({"content_kind":"SKILL", "problem_categories":["network"]})))\n    db.commit()\n'
            subprocess.run([str(runtime), "-B", "-s", "-c", seed, str(args.old_package), str(data)],
                           cwd=root, check=True, capture_output=True, timeout=120)
            def install(target_data, hold_new=False, expected_code=0):
                label = target_data.name + '-' + str(expected_code)
                existing = set(releases.iterdir()) if releases.exists() else set()
                process = subprocess.Popen([str(args.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
                    "/LOG=" + str(root / (label + "-setup.log"))],
                    env={**os.environ, "GWAP_SERVER_DATA_ROOT": str(target_data)}, creationflags=subprocess.CREATE_NO_WINDOW)
                started, new_handle = time.monotonic(), None
                while process.poll() is None:
                    new = set(releases.iterdir()) - existing if releases.exists() else set()
                    if hold_new and new and new_handle is None:
                        new_handle = lock_directory(next(iter(new)))
                        handles.append(new_handle)
                    if time.monotonic() - started > 600:
                        process.terminate()
                        raise TimeoutError("Focused EXE installation exceeded 10 minutes")
                    time.sleep(0.2)
                new = set(releases.iterdir()) - existing if releases.exists() else set()
                owned_releases.extend(new)
                evidence_log = args.evidence.parent / (label + "-setup.log")
                evidence_log.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / (label + "-setup.log"), evidence_log)
                assert process.returncode == expected_code, f"Unexpected Setup exit {process.returncode}; inspect {evidence_log}"
                if expected_code:
                    assert not new
                    return None
                assert len(new) == 1
                if hold_new:
                    assert new_handle is not None
                print("EXE_INSTALL_OK " + target_data.name, flush=True)
                return next(iter(new))
            import msvcrt
            if args.reuse_running_refusal:
                prior = (args.evidence.parent / "old-data-7-setup.log").read_text(encoding="utf-8-sig")
                assert "The interactive server or a backup is running" in prior
                assert str(args.installer) in prior
            else:
                with (data / "script-runner.lock").open("w+b") as runner_lock:
                    runner_lock.write(b"0")
                    runner_lock.flush()
                    runner_lock.seek(0)
                    msvcrt.locking(runner_lock.fileno(), msvcrt.LK_NBLCK, 1)
                    install(data, expected_code=7)
                    msvcrt.locking(runner_lock.fileno(), msvcrt.LK_UNLCK, 1)
            assert snapshot(legacy) == before
            upgrade_proof = args.evidence.parent / "upgrade-verification.json"
            exe_sha = hashlib.sha256(args.installer.read_bytes()).hexdigest()
            if args.reuse_upgrade_check:
                prior_upgrade = json.loads(upgrade_proof.read_text(encoding="utf-8"))
                assert prior_upgrade["status"] == "PASS" and prior_upgrade["installer_sha256"] == exe_sha
            else:
                handles.append(lock_directory(legacy))
                upgraded = install(data, hold_new=True)
                assert snapshot(legacy) == before
                check_knowledge(data, upgraded, old=True)
                assert list((data / "knowledge-update-backups").glob("**/before.sqlite3"))
                upgrade_proof.write_text(json.dumps({"status": "PASS", "installer_sha256": exe_sha,
                    "old_program_files_unchanged": len(before), "skills": 6,
                    "old_case_wiki_preserved": True, "previous_root_archived": True,
                    "database_backup": True, "old_new_directory_handles": True}), encoding="utf-8")
            # Release only test-owned handles; no arbitrary process is stopped.
            for handle in handles:
                KERNEL.CloseHandle(handle)
            handles.clear()
            fresh_data = root / "fresh-data"
            fresh = install(fresh_data)
            assert not (fresh_data / "data/gw_ap_debug.db").exists()
            # Start the actual newly installed server with a synthetic loopback
            # address; normal first-start path must populate the six files.
            started = subprocess.run([str(fresh / "runtime/python/python.exe"), "-B", "-s",
                str(fresh / "scripts/run_lan_server.py"), "--package", str(fresh), "--data-root", str(fresh_data),
                "--public-url", "https://127.0.0.1:25453", "--backend-port", "25454", "--run-seconds", "180"],
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=480)
            for log in fresh_data.rglob("*.log"):
                destination = args.evidence.parent / "fresh-start-logs" / log.relative_to(fresh_data)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(log, destination)
            assert started.returncode == 0, started.stdout + started.stderr
            assert "READY:" in started.stdout
            check_knowledge(fresh_data, fresh)
            assert (fresh_data / "config/bootstrap-token.txt").is_file()
            assert snapshot(legacy) == before
            proof = {"status": "PASS", "installer_sha256": hashlib.sha256(args.installer.read_bytes()).hexdigest(),
                "actual_exe_runs": 3, "running_server_refused_exit_code": 7, "upgrade_old_and_new_directory_handles_held": True,
                "running_refusal_reused_same_exe": args.reuse_running_refusal,
                "upgrade_reused_same_exe": args.reuse_upgrade_check,
                "old_program_files_unchanged": len(before), "upgrade_published_before_finish": 6,
                "old_case_wiki_preserved": True, "previous_root_archived": True, "database_backup": True,
                "fresh_install_and_actual_server_start": True, "fresh_skill_files": 6, "initial_admin_created": True,
                "model": "actual bundled GGUF", "diagnostic_model_calls": 0, "company_installation_claimed": False}
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(proof, indent=2), encoding="utf-8")
            print(json.dumps(proof), flush=True)
    finally:
        for handle in handles:
            KERNEL.CloseHandle(handle)
        # Uninstall only the last test-created release, then remove only the
        # explicitly owned test app/releases; business roots are temporary.
        uninstaller = home / "uninstall/unins000.exe"
        if owned_releases and uninstaller.exists():
            subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=120)
        for release in owned_releases:
            if release.exists():
                remove_owned(release, releases)
        if legacy.exists():
            remove_owned(legacy, home)


if __name__ == "__main__":
    main()
