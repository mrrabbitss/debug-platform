"""Exercise real packaged GGUF indexing and retrieval using reviewed synthetic knowledge.

This is a small end-to-end sanity check, not an upstream-equivalence Golden evaluation.
It uses only a newly created temporary database, never an existing user's backend.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


EMBEDDING_ID = "MODEL-embedding-bundled-gguf"
RERANKER_ID = "MODEL-reranker-bundled-gguf"
QUERY = "AP 频繁离线，UDM 进程异常和 Advertise 心跳发送失败，应该如何排查？"
FIXTURES = (
    {
        "title": "[SYNTHETIC RETRIEVAL CHECK] AP UDM 频繁离线排查",
        "content": (
            "AP 频繁离线的合成验证案例：AP 的 UDM 进程异常退出，监听端口失效，"
            "随后 Advertise 心跳发送失败。GW 等待心跳超过超时阈值后判定 AP 离线并删除拓扑。"
            "排查应关联 AP 进程退出、监听端口、Advertise 发送错误与 GW 心跳超时的时间线。"
            "若端口链路始终 Up，不应仅凭离线事件判定物理断线。修复 UDM 后验证心跳和拓扑恢复。"
        ),
    },
    {
        "title": "[SYNTHETIC RETRIEVAL CHECK] AP 外壳与包装规格",
        "content": (
            "AP 设备外壳为白色，包装盒长宽高为合成示例尺寸，附件为纸质安装说明。"
            "本文只描述颜色、重量和运输包装，不涉及网络诊断、进程或心跳机制。"
        ),
    },
)


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


class API:
    def __init__(self, port: int):
        require(1024 <= port <= 65535, "Invalid isolated loopback port")
        self.base = f"http://127.0.0.1:{port}/api/v1"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, payload: Any = None, *, post: bool = False) -> Any:
        require(path.startswith("/") and not path.startswith("//"), "Invalid API path")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.base + path, data=body, method="POST" if post or body is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Response bodies and model errors are not copied into verification summaries.
            raise VerificationError(f"API {path} returned HTTP {exc.code}") from None


def isolated_environment() -> dict[str, str]:
    environment = dict(os.environ)
    overrides = {
        "APP_ENV": "test", "AUTH_MODE": "local", "API_KEY": "",
        "AUTH_ALLOW_LEGACY_ADMIN": "true", "MCP_ENABLED": "true",
        "MCP_BEARER_TOKEN": "", "DEBUGPLATFORM_MCP_TOKEN": "",
        "MCP_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*",
        "MCP_ALLOWED_ORIGINS": "http://127.0.0.1:*,http://localhost:*",
        "LLM_PROVIDER": "mock", "LLM_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": "",
        "QDRANT_URL": "", "QDRANT_API_KEY": "", "MODEL_SECRET_KEY": "",
        "MODEL_ENDPOINT_ALLOWLIST": "", "MODEL_ALLOW_PRIVATE_ENDPOINTS": "false",
        "MODEL_DISABLE_IN_PROCESS_LOCAL": "true", "API_PREFIX": "/api/v1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
    }
    names = {name.casefold() for name in overrides}
    for name in tuple(environment):
        if name.casefold() in names or name.startswith("BUNDLED_GGUF_"):
            environment.pop(name)
    environment.update(overrides)
    return environment


def wait_ready(api: API, process: subprocess.Popen, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(process.poll() is None, "Packaged launcher exited before readiness")
        try:
            health = api.request("/health/ready")
            if health.get("ready") is True:
                return
        except (OSError, ValueError, VerificationError):
            pass
        time.sleep(0.5)
    raise VerificationError("Packaged launcher readiness timed out")


def wait_job(api: API, job_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = api.request(f"/jobs/{job_id}")
        if job["status"] == "COMPLETED":
            return job
        require(job["status"] not in {"FAILED", "CANCELLED", "DEAD_LETTER"},
                f"Synthetic validation job ended with {job['status']}")
        time.sleep(0.5)
    raise VerificationError("Synthetic validation job timed out")


def publish_fixture(api: API, fixture: dict) -> str:
    document = api.request("/knowledge", {
        **fixture, "source_type": "document", "device_type": "AP",
        "confidentiality": "PUBLIC", "module": "synthetic_retrieval_check",
        "metadata": {"synthetic": True, "verification_fixture": "packaged_gguf_retrieval_v1"},
    })
    require(document["review_status"] == "DRAFT" and document["active"] is False,
            "Knowledge creation bypassed the DRAFT review gate")
    document_id = document["id"]
    for action, expected in (("submit", "IN_REVIEW"), ("approve", "ACTIVE")):
        document = api.request(f"/knowledge/{document_id}/review/{action}", {
            "expected_lock_version": document["lock_version"],
            "comment": "Reviewed deterministic synthetic verification fixture; no company data.",
        })
        require(document["review_status"] == expected, "Knowledge review transition failed")
    require(document["active"] is True, "Reviewed synthetic knowledge was not published")
    return document_id


def read_index_snapshot(database: Path, document_ids: list[str], generation: str) -> dict:
    """Read persisted evidence only; all document/index mutations use public APIs."""
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        profile = db.execute(
            "SELECT provider, active_embedding_generation_id FROM model_profiles WHERE id = ?",
            (EMBEDDING_ID,),
        ).fetchone()
        require(profile == ("llama_cpp_local", generation), "Index generation/profile mismatch")
        rows = db.execute(
            "SELECT e.chunk_id, c.document_id, e.dimension, e.vector_json "
            "FROM knowledge_embeddings e JOIN knowledge_chunks c ON c.id = e.chunk_id "
            "WHERE e.profile_id = ? AND e.generation_id = ?",
            (EMBEDDING_ID, generation),
        ).fetchall()
        require(bool(rows), "No real indexed vectors were persisted")
        for _chunk_id, _document_id, dimension, vector_json in rows:
            vector = json.loads(vector_json)
            require(dimension == 768 and len(vector) == 768, "Persisted embedding is not 768-D")
            require(all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector),
                    "Persisted embedding contains non-finite values")
            norm = math.sqrt(sum(float(value) ** 2 for value in vector))
            require(abs(norm - 1.0) < 0.03, "Persisted embedding is not L2-normalized")
        evidence_by_document = {
            document_id: [row[0] for row in rows if row[1] == document_id]
            for document_id in document_ids
        }
        require(all(evidence_by_document.values()), "Synthetic document index is incomplete")
        return {
            "profile_id": EMBEDDING_ID, "generation_id": generation,
            "vector_count": len(rows), "dimension": 768, "finite_and_normalized": True,
            "evidence_by_document": evidence_by_document,
        }


def validate_evaluation(run: dict, related_ids: list[str], generation: str) -> dict:
    require(run.get("status") == "COMPLETED", "Retrieval evaluation did not complete")
    profiles = run["config"]["model_profiles"]
    for task, expected_id in (("embedding", EMBEDDING_ID), ("reranker", RERANKER_ID)):
        require(profiles[task]["id"] == expected_id and profiles[task]["provider"] == "llama_cpp_local",
                "Evaluation silently used a fallback retrieval model")
    require(profiles["embedding"]["embedding_generation_id"] == generation,
            "Evaluation did not use the rebuilt index generation")
    require(run["config"]["memory_writes"] is False, "Evaluation unexpectedly writes memory")
    require(len(run["results"]) == 1, "Unexpected synthetic evaluation result count")
    result = run["results"][0]
    traces = result["trace"]
    require(not any(item.get("status") == "FAILED" for item in traces),
            "Retrieval completed with an internal fallback")
    stages = {item["stage"]: item for item in traces}
    for stage in ("dense_embedding", "reranker"):
        require(stages.get(stage, {}).get("status") == "COMPLETED",
                f"Real {stage} did not participate")
        require(stages[stage].get("candidate_count", 0) >= 2,
                f"Real {stage} did not compare multiple candidates")
    retrieved = result["retrieved"]
    require(bool(retrieved) and retrieved[0]["evidence_id"] in related_ids,
            "Relevant synthetic AP knowledge did not rank first")
    for score_name in ("dense_score", "reranker_score"):
        score = retrieved[0].get(score_name)
        require(isinstance(score, (int, float)) and math.isfinite(score),
                f"Top result lacks a finite {score_name}")
    require(run["metrics"].get("recall_at_k") == 1.0 and run["metrics"].get("mrr") == 1.0,
            "Synthetic retrieval ranking sanity threshold failed")
    return {
        "evaluation_id": run["id"],
        "metrics": {key: run["metrics"].get(key) for key in (
            "case_count", "recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
        )},
        "related_first": True, "returned": len(retrieved),
        "dense_candidates": stages["dense_embedding"]["candidate_count"],
        "reranker_candidates": stages["reranker"]["candidate_count"],
        "top_dense_score": retrieved[0]["dense_score"],
        "top_reranker_score": retrieved[0]["reranker_score"],
    }


def read_model_audit(database: Path) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT outcome, resource_id, details_json FROM audit_events WHERE action = 'model.egress'"
        ).fetchall()
    counts = {"embedding": 0, "reranker": 0, "chat": 0}
    for outcome, profile_id, details_json in rows:
        details = json.loads(details_json)
        task = details.get("task_type")
        if task == "chat":
            counts["chat"] += 1
            continue
        require(task in {"embedding", "reranker"}, "Unexpected model task in synthetic run")
        origin = urllib.parse.urlsplit(details.get("destination_origin") or "")
        require(origin.hostname == "127.0.0.1" and origin.scheme == "http",
                "Retrieval contacted a non-loopback model endpoint")
        require(profile_id == (EMBEDDING_ID if task == "embedding" else RERANKER_ID)
                and details.get("provider") == "llama_cpp_local" and outcome == "SUCCESS",
                "Retrieval audit contains a failed or unexpected model call")
        require(details.get("content_recorded") is False, "Model audit contains content")
        counts[task] += 1
    require(counts["chat"] == 0, "Unexpected backend Chat model egress")
    require(counts["embedding"] > 0 and counts["reranker"] > 0,
            "Missing real Embedding/Reranker audit receipts")
    return {"backend_chat_calls": 0, "loopback_only": True, "successful_calls": counts}


def stop_managed_process(process: subprocess.Popen) -> bool:
    if process.stdin and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        return process.wait(timeout=30) == 0
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return False


def verify_package(package: Path, work: Path, timeout: int) -> dict:
    python = package / "runtime" / "python" / "python.exe"
    launcher = package / "portable_launcher.py"
    require(python.is_file() and launcher.is_file(), "Package lacks its Python runtime or launcher")
    component_path = package / "model-components.json"
    require(component_path.is_file(), "A complete GGUF package is required, not Core-only")
    components = json.loads(component_path.read_text(encoding="utf-8-sig"))["components"]
    require({item["task_type"] for item in components} == {"embedding", "reranker"},
            "Both GGUF retrieval components must be installed")
    data_root = work / "data"
    env_path = work / ".env"
    env_path.write_text("APP_ENV=test\nAUTH_MODE=local\nAPI_KEY=\nLLM_PROVIDER=mock\n", encoding="utf-8")
    environment = isolated_environment()
    arguments = [str(python), "-u", "-B", "-s", str(launcher), "--no-browser",
                 "--data-root", str(data_root), "--env-file", str(env_path)]
    with (work / "package.log").open("wb") as log:
        check = subprocess.run(
            [*arguments, "--check"], cwd=package, env=environment,
            stdout=log, stderr=log, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        require(check.returncode == 0, "Packaged integrity/self-check failed; see package.log")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        process = subprocess.Popen(
            [*arguments, "--port", str(port), "--managed-stdin", "--model-start-timeout", str(timeout)],
            cwd=package, env=environment, stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            api = API(port)
            wait_ready(api, process, timeout * 2 + 60)
            profiles = api.request("/system/models")
            for profile_id in (EMBEDDING_ID, RERANKER_ID):
                matches = [item for item in profiles if item["id"] == profile_id]
                require(len(matches) == 1 and matches[0]["is_active"] is True
                        and matches[0]["provider"] == "llama_cpp_local",
                        "Real GGUF profile was not activated; fallback is not a pass")
            document_ids = [publish_fixture(api, fixture) for fixture in FIXTURES]
            case = api.request("/cases", {
                "title": "Synthetic packaged GGUF retrieval verification", "device_type": "AP",
                "description": "Synthetic AP UDM/Advertise failure; no company data.",
            })
            job = api.request("/knowledge/reindex", post=True)
            completed = wait_job(api, job["id"], timeout)
            reindex = json.loads(completed["result_json"])
            require(reindex.get("profile_id") == EMBEDDING_ID, "Reindex used a fallback model")
            status = api.request("/system/retrieval")["embedding"]
            require(status["complete"] is True and status["generation_id"] == reindex["generation_id"],
                    "Published retrieval index is incomplete or stale")
            database = data_root / "gw_ap_debug.db"
            indexed = read_index_snapshot(database, document_ids, reindex["generation_id"])
            related_ids = indexed.pop("evidence_by_document")[document_ids[0]]
            dataset = api.request("/evaluation/datasets", {
                "name": "Synthetic packaged GGUF sanity v1",
                "description": "Small live retrieval check; not upstream-equivalence Golden.",
            })
            api.request(f"/evaluation/datasets/{dataset['id']}/cases", {
                "case_id": case["id"], "query": QUERY, "expected_evidence_ids": related_ids,
                "modules": ["knowledge"], "top_k": 10, "max_hops": 0,
            })
            started = api.request(f"/evaluation/datasets/{dataset['id']}/runs", post=True)
            wait_job(api, started["job"]["id"], timeout)
            run = api.request(f"/evaluation/runs/{started['run']['id']}")
            evaluation = validate_evaluation(run, related_ids, reindex["generation_id"])
            audit = read_model_audit(database)
        finally:
            graceful = stop_managed_process(process)
    require(graceful, "Packaged launcher did not shut down cleanly through managed stdin")
    return {
        "status": "PASS", "scope": "real_packaged_gguf_retrieval_sanity",
        "upstream_equivalence_verified": False, "synthetic_only": True, "content_recorded": False,
        "stored_vector_ann_recall_verified": False,
        "documents_published_through_review": len(document_ids),
        "review_path": ["DRAFT", "IN_REVIEW", "ACTIVE"],
        "reindex_job_status": completed["status"], "index": indexed,
        "evaluation": evaluation, "model_audit": audit, "managed_shutdown": "clean",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Write a content-safe JSON result")
    parser.add_argument("--timeout", type=int, default=600, help="Per-model/job limit, 30..1800 seconds")
    parser.add_argument("--keep-workdir", action="store_true", help="Retain only this run's synthetic DB/logs")
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout must be between 30 and 1800")
    work = Path(tempfile.mkdtemp(prefix="gwap-packaged-retrieval-"))
    started = time.monotonic()
    try:
        summary = verify_package(args.package_root.resolve(), work, args.timeout)
    except (VerificationError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        summary = {
            "status": "FAIL", "scope": "real_packaged_gguf_retrieval_sanity",
            "error": str(exc) if isinstance(exc, VerificationError) else type(exc).__name__,
            "content_recorded": False, "upstream_equivalence_verified": False,
        }
    finally:
        if not args.keep_workdir:
            # This exact directory was allocated by mkdtemp above; no user data is removed.
            shutil.rmtree(work)
    summary["duration_seconds"] = round(time.monotonic() - started, 3)
    if args.keep_workdir:
        summary["synthetic_workdir"] = str(work)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
