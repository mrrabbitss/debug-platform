"""New standalone updater compatibility against an old delivered Python/app.

All mutable data is synthetic and confined to one temporary directory. Model
indexing uses local hashing; application payload files are read-only inputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


SEED = r'''
import os, sys, json
from pathlib import Path
package, root = map(Path, sys.argv[1:3])
sys.path.insert(0, str(package))
sys.path.insert(0, str(package / "app/backend"))
from portable_server_config import ServerConfig
config = ServerConfig(public_url="https://127.0.0.1:25443", data_root=str(root), server_id="GWAP-" + "1" * 32, backend_port=25444, minimum_free_gib=1)
(root / "config").mkdir(parents=True)
(root / "data/storage").mkdir(parents=True)
(root / "config/server.json").write_text(json.dumps(config.payload()), encoding="utf-8")
(root / "config/server.env").write_text("MODEL_DISABLE_IN_PROCESS_LOCAL=true\n", encoding="utf-8")
config.apply_environment()
import server_admin
server_admin._prepare_existing_server_environment(config)
from app.core.db import Base, engine, SessionLocal
from app.core.utils import json_dumps
from app.models import Case, KnowledgeDocument, KnowledgeChunk, ModelProfile, UserAccount
from app.workbench_models import WorkbenchRecord
Base.metadata.create_all(engine)
with SessionLocal() as db:
    db.add(UserAccount(id="admin", username="admin", display_name="Synthetic admin", role="ADMIN", active=True))
    db.add(ModelProfile(id="embedding", name="Synthetic local", task_type="embedding", mode="builtin", provider="hashing", model_name="synthetic", enabled=True, is_active=True, config_json="{}"))
    db.add(Case(id="case", title="Synthetic case retained"))
    db.add(KnowledgeDocument(id="old", title="Synthetic wiki", content="Preserve old wiki", active=True, review_status="ACTIVE", confidentiality="INTERNAL"))
    db.flush()
    db.add(KnowledgeChunk(id="old-chunk", document_id="old", document_version=1, chunk_index=0, content="Preserve old wiki"))
    db.add(WorkbenchRecord(id="bundled-network-skill-v1", kind="bundled_knowledge", payload_json=json_dumps({"status":"PRESERVED"})))
    db.commit()
'''

VERIFY = r'''
import sys, json
from pathlib import Path
package, root = map(Path, sys.argv[1:3])
sys.path.insert(0, str(package))
from portable_server_config import load_server_config
config = load_server_config(root / "config/server.json")
config.apply_environment()
import server_admin
server_admin._prepare_existing_server_environment(config)
from sqlalchemy import select
from app.core.db import SessionLocal
from app.models import Case, KnowledgeDocument, KnowledgeEmbedding, ModelProfile, Job
from app.services import bundled_knowledge, knowledge_reset
from app.services.skill_dependencies import bundle_dependencies
from app.services.jobs import job_runner
with SessionLocal() as db:
    docs = list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))))
    assert len(docs) == 7
    assert db.get(Case, "case").title == "Synthetic case retained"
    assert db.get(KnowledgeDocument, "old").content == "Preserve old wiki"
    assert bundled_knowledge.packaged_knowledge_status(db)["status"] == "PUBLISHED"
    profile = db.get(ModelProfile, "embedding")
    assert db.scalar(select(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id == "old-chunk", KnowledgeEmbedding.generation_id == profile.active_embedding_generation_id))
    bundle = knowledge_reset.read_bundle(Path(sys.argv[3]))
    for item in bundle["files"]:
        assert next(d.content for d in docs if d.title == item["path"]) == item["content"]
    root_skill = next(d for d in docs if d.title.endswith("/SKILL.md"))
    children, missing = bundle_dependencies(root_skill, docs)
    assert len(children) == 5 and not missing
    job = db.scalar(select(Job))
    assert job.kind == "offline_network_skill_update" and job.status == "COMPLETED" and job.attempt == 2
    assert knowledge_reset.KIND == "knowledge_reset"
    try:
        job_runner._resolve_handler(job, None, ())
    except ValueError as error:
        assert "No handler registered" in str(error)
    else:
        raise AssertionError("Old server unexpectedly dispatches offline job")
print("OLD_APP_RESTART_VERIFIED")
'''

CRASH = r'''
import os, sys
from pathlib import Path
updater, package, data = map(Path, sys.argv[1:4])
sys.path.insert(0, str(updater))
import update_network_skill as entry
import argparse
args = argparse.Namespace(package=package, data_root=data, allow_configured_embedding_api=False)
# Crash at the first private index build, after the real approval/backup commit.
import builtins
original_import = builtins.__import__
def hooked(name, globals=None, locals=None, fromlist=(), level=0):
    module = original_import(name, globals, locals, fromlist, level)
    if name == "offline_skill_update":
        module.reset.index_embeddings = lambda *a, **k: os._exit(91)
    return module
builtins.__import__ = hooked
entry.update(args, entry.verify_files())
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    runtime = package / "runtime/python/python.exe"
    before = {str(p.relative_to(package)): (p.stat().st_size, p.stat().st_mtime_ns)
              for p in package.rglob("*") if p.is_file()}
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    with tempfile.TemporaryDirectory(prefix="gwap-offline-update-") as temporary:
        root = Path(temporary)
        updater, data = root / "updater", root / "server"
        with zipfile.ZipFile(args.updater) as archive:
            archive.extractall(updater)
        def child(code, arguments, expected=0):
            result = subprocess.run([str(runtime), "-B", "-s", "-c", code, *map(str, arguments)],
                cwd=root, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180)
            assert result.returncode == expected, result.stdout + result.stderr
            return result.stdout + result.stderr
        child(SEED, [package, data])
        # Lock refusal exercises the actual bootstrap before the database work.
        import msvcrt
        with (data / "script-runner.lock").open("w+b") as lock:
            lock.write(b"0")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            code = "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
            cli = [updater / "update_network_skill.py", "--package", package, "--data-root", data]
            refused = child(code, cli, expected=1)
            assert "Stop it" in refused, refused
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        child(CRASH, [updater, package, data], expected=91)
        completed = child(code, cli)
        assert "PUBLISHED" in completed
        # Separate old-app process after updater exit verifies durable results.
        child(VERIFY, [package, data, updater / "hilink-diag.zip"])
        batch = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(updater / "Update-Network-Skill.bat")],
            cwd=updater, env={**env, "GWAP_SERVER_PACKAGE_ROOT": str(package), "GWAP_SERVER_DATA_ROOT": str(data)},
            input="\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180)
        assert batch.returncode == 0, batch.stdout + batch.stderr
        unchanged = batch.stdout
        assert "UNCHANGED" in unchanged
    after = {str(p.relative_to(package)): (p.stat().st_size, p.stat().st_mtime_ns)
             for p in package.rglob("*") if p.is_file()}
    assert before == after, "Installed package was modified"
    evidence = {"status": "PASS", "old_version": json.loads((package / "build-info.json").read_text(encoding="utf-8-sig"))["package_version"],
        "updater_sha256": hashlib.sha256(args.updater.read_bytes()).hexdigest(),
        "stopped_server_lock_refusal": True, "forced_process_exit_after_approval": 91,
        "resume_same_approval_attempt": 2, "six_original_files_verified": True,
        "old_app_restart_status": "PUBLISHED", "old_case_wiki_vectors_preserved": True,
        "second_run": "UNCHANGED", "batch_entry_verified": True, "program_files_unchanged": len(before),
        "old_reset_dispatcher_excluded": True, "embedding": "local hashing", "diagnostic_model_calls": 0}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
