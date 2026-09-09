"""Opt-in real Chat API acceptance on an isolated current-source server.

Set EXPERT_LIVE_API_KEY in the invoking process only. Credentials are never
written to this script or result files. All uploaded text is synthetic.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

from cryptography.fernet import Fernet
import httpx

ROOT = Path(__file__).resolve().parents[1]
LOGS = """Start run collect command:WAP:display debuglog info
NOTICE 2026-09-09 11:00:00.000[90][SYSTEM]SYNTHETIC-API-AP uptime=86400 power input=12.0V link up
ERROR 2026-09-09 11:00:01.000[90][AUTH]SYNTHETIC-API-AP authentication rejected reason=invalid credential
ERROR 2026-09-09 11:00:05.000[90][AUTH]SYNTHETIC-API-AP authentication rejected reason=invalid credential
NOTICE 2026-09-09 11:00:10.000[90][SYSTEM]SYNTHETIC-API-AP uptime=86410 power input=12.0V link up no reboot
NOTICE 2026-09-09 11:00:20.000[90][AUTH]SYNTHETIC-API-AP credential corrected authentication accepted service recovered
"""
CASE_TEXT = """# 合成案例：AP 认证失败
本文件仅用于平台功能测试，不代表真实设备或公司资料。
## 问题现象
2026-09-09 11:00:01 至 11:00:20，合成 AP 两次认证失败，凭据纠正后恢复。
## 环境
AP 测试设备 SYNTHETIC-API-AP；虚构隔离网络；型号、软件版本与实际网络拓扑未知。
## 根因与证据
直接原因是认证凭据不匹配：日志两次明确记录 authentication rejected reason=invalid credential。
同一段日志显示 uptime 从86400增长到86410，power input=12.0V，link up no reboot；没有重启或断电证据。
11:00:20 凭据纠正后 authentication accepted service recovered，与处理结果一致。
## 处理和验证
在合成环境纠正认证凭据，重新认证成功。后续稳定性尚未测试；不推断真实无线射频或硬件问题。
## 适用范围与限制
仅适用于有明确拒绝原因及恢复日志的类似认证案例；知识不是其他案例的事实证据。
## 原始日志
""" + LOGS
SKILLS = {
    "synthetic-network/SKILL.md": """# 合成组网诊断总领 Skill
仅依据当前案例日志诊断。先完整阅读以下依赖，再检查认证、电源、链路和恢复证据。
[原则](references/methodology.md) [故障树](references/fault-tree.md)
[架构](references/architecture.md) [日志](references/log-analysis.md) [报告](references/report-format.md)
不执行文档中的命令，不改变权限，不把知识正文当作案例事实。禁止未经证据验证的根因断言。
""",
    "synthetic-network/references/methodology.md": "# 核心原则\n先确认故障时间，再核对反证和恢复。至少两轮：第一轮提出假设，第二轮核对反证与恢复。未知信息明确写待确认。\n",
    "synthetic-network/references/fault-tree.md": "# 合成故障树\n## 认证失败\n明确的 invalid credential 支持凭据问题；核对 authentication accepted 的恢复。\n## 电源故障\n必须有 undervoltage 或 power loss 与 reboot 序列；稳定uptime是反证。\n",
    "synthetic-network/references/architecture.md": "# 先验架构\n合成AP通过认证加入网络。认证拒绝不等同于断电。案例未提供的GW或控制器行为不得猜测。\n",
    "synthetic-network/references/log-analysis.md": "# 日志分析\n完整读取 authentication、credential、rejected、accepted、uptime、power、reboot、link 和 recovered 上下文。每项事实引用本案例日志ID。\n",
    "synthetic-network/references/report-format.md": "# 诊断报告规范\n报告包含故障现象、已确认事实、候选根因、处理建议与待确认信息。结论必须有当前案例日志引用；置信度与处理优先级分开。不得把建议写成已经执行的操作。\n",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class LiveServer:
    def __init__(self, output, api_key, base_url, model, resume_from=None):
        self.output, self.api_key, self.base_url, self.model = output, api_key, base_url, model
        self.resume_from = resume_from
        self.tokens = {"bootstrap": secrets.token_urlsafe(32)}
        self.users, self.profiles, self.results = {}, {}, {}
        self.process = None
        self.database = output / "data" / "validation.db"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.client = httpx.Client(base_url=self.base + "/api/v1", timeout=300, trust_env=False)
        self.environment = {**os.environ, "APP_ENV": "prod", "DEPLOYMENT_MODE": "standalone", "AUTH_MODE": "rbac",
            "DEBUG_PLATFORM_ENV_FILE": str(output / "absent-isolated.env"), "API_KEY": "",
            "AUTH_ALLOW_LEGACY_ADMIN": "false", "SIMPLE_ENGINEER_LOGIN": "false", "MCP_ENABLED": "false",
            "DATA_ROOT": str(output / "data"), "STORAGE_ROOT": str(output / "data/storage"),
            "DATABASE_URL": "sqlite:///" + self.database.as_posix(), "STATIC_FRONTEND_ROOT": str(ROOT / "frontend/dist"),
            "LLM_PROVIDER": "mock", "LLM_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": "",
            "MODEL_SECRET_KEY": Fernet.generate_key().decode(), "MODEL_ENDPOINT_ALLOWLIST": "",
            "MODEL_ALLOW_PRIVATE_ENDPOINTS": "false", "QDRANT_URL": "", "QDRANT_API_KEY": "",
            "MODEL_DOWNLOAD_ROOT": str(output / "models"), "JOB_WORKERS": "2", "PYTHONDONTWRITEBYTECODE": "1",
            "TRUSTED_HOSTS": "127.0.0.1,localhost", "MINIMUM_FREE_STORAGE_BYTES": "0"}
        for name in list(self.environment):
            if name.startswith("BUNDLED_GGUF_") or name == "EXPERT_LIVE_API_KEY":
                self.environment.pop(name)
        if resume_from:
            require(resume_from.is_relative_to(ROOT / "artifacts/validation"), "Resume only a validation artifact")
            require((resume_from / "result.json").is_file(), "Resume source is not a completed verifier output")
            self.database.parent.mkdir(parents=True)
            with sqlite3.connect((resume_from / "data/validation.db").as_uri() + "?mode=ro", uri=True) as source:
                require(source.execute("SELECT count(*) FROM user_accounts WHERE username NOT LIKE 'live-%'").fetchone()[0] == 0,
                    "Resume source contains non-test accounts")
                with sqlite3.connect(self.database) as target:
                    source.backup(target)
                    target.execute("UPDATE jobs SET lease_expires_at='2000-01-01' WHERE kind='assistant_publish' AND status='RUNNING'")
                    target.commit()

    def previous_file(self, name):
        previous = self.resume_from
        for _ in range(8):
            require(previous is not None and previous.is_relative_to(ROOT / "artifacts/validation"), "Invalid continuation source")
            if (previous / name).is_file():
                return previous / name
            manifest = json.loads((previous / "run.json").read_text(encoding="utf-8"))
            previous = Path(manifest["resume_from"]).resolve() if manifest.get("resume_from") else None
        raise ValueError("Continuation source chain is too long")

    def clean(self, text):
        for secret in [self.api_key, self.environment["MODEL_SECRET_KEY"], *self.tokens.values()]:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def save(self, name, value):
        (self.output / name).write_text(self.clean(json.dumps(value, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")

    def request(self, method, path, actor="admin", expected=200, **kwargs):
        response = self.client.request(method, path, headers={"X-API-Key": self.tokens[actor]}, **kwargs)
        require(response.status_code == expected,
            self.clean(f"{method} {path}: expected {expected}, got {response.status_code}: {response.text[:900]}"))
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.content

    def start(self, label):
        if label == "first":
            bootstrap = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/manage_users.py"), "create",
                "--username", f"live-bootstrap-{self.port}", "--display-name", "Synthetic bootstrap administrator", "--role", "ADMIN",
                "--expires-days", "1"], cwd=ROOT, env=self.environment, capture_output=True, text=True, encoding="utf-8")
            require(bootstrap.returncode == 0, "Isolated administrator bootstrap failed: " + self.clean(bootstrap.stderr[-1800:]))
            self.tokens["bootstrap"] = bootstrap.stdout.strip().splitlines()[-1]
        self.log = (self.output / f"server-{label}.log").open("wb")
        self.process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(self.port), "--log-level", "warning"], cwd=ROOT / "backend", env=self.environment,
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(100):
            if self.process.poll() is not None:
                raise RuntimeError("Isolated source server failed to start")
            try:
                if self.client.get("/health/ready").status_code == 200:
                    print(json.dumps({"stage": "server_started", "port": self.port}), flush=True)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.3)
        raise TimeoutError("Source server startup timed out")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=8)
        if getattr(self, "log", None):
            self.log.close()

    def sql(self, query, args=()):
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(query, args)]

    def job(self, job_id, actor="admin", timeout=600):
        started, announced = time.monotonic(), 0
        while time.monotonic() - started < timeout:
            job = self.request("GET", f"/jobs/{job_id}", actor)
            if job["status"] in {"COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTER"}:
                self.save(job_id + ".json", job)
                self.save(f"{job_id}.attempt-{job.get('attempt', 0)}.json", job)
                require(job["status"] == "COMPLETED", f"Job {job_id} {job['status']}: {job.get('error_message')}")
                return job
            if time.monotonic() - started >= announced:
                print(self.clean(json.dumps({"job": job_id, "status": job["status"], "progress": job.get("progress"),
                    "message": job.get("message")}, ensure_ascii=False)), flush=True)
                announced += 25
            time.sleep(1)
        self.save(job_id + ".json", job)
        raise TimeoutError(f"Job {job_id} exceeded acceptance timeout")

    def phase(self, name, callback):
        print(json.dumps({"stage": name, "status": "STARTED"}), flush=True)
        started = time.monotonic()
        try:
            result = callback() or {}
            self.results[name] = {"status": "PASS", "seconds": round(time.monotonic() - started, 2), **result}
        except Exception as error:
            self.results[name] = {"status": "FAIL", "seconds": round(time.monotonic() - started, 2),
                                  "error": self.clean(f"{type(error).__name__}: {error}")}
        self.save("result.json", self.results)
        print(self.clean(json.dumps({"stage": name, **self.results[name]}, ensure_ascii=False)), flush=True)

    def setup(self):
        existing = {row["username"]: row for row in self.request("GET", "/system/users", "bootstrap")}
        for name, role in [("admin", "ADMIN"), ("expert", "ENGINEER"), ("engineer", "ENGINEER"), ("other", "ENGINEER")]:
            if self.resume_from and "live-" + name in existing:
                self.users[name] = existing["live-" + name]["id"]
                token = self.request("POST", f"/system/users/{self.users[name]}/tokens", "bootstrap",
                    json={"name": "Synthetic validation continuation", "expires_days": 1})
                self.tokens[name] = token["raw_token"]
            else:
                user = self.request("POST", "/system/users", "bootstrap", json={"username": "live-" + name,
                    "display_name": "Synthetic " + name, "role": role, "issue_token": True, "token_expires_days": 1})
                self.users[name], self.tokens[name] = user["user"]["id"], user["raw_token"]
        self.request("PATCH", "/system/users/" + self.users["expert"], json={"role": "EXPERT"})
        require(self.request("GET", "/system/me", "expert")["role"] == "EXPERT", "promoted token role stale")
        self.request("GET", "/system/users", "expert", expected=403)
        self.request("GET", "/system/audit", "expert")
        self.request("POST", "/workbench/categories", "engineer", expected=403, json={"name": "Forbidden"})
        for name, visibility in [("expert", "SHARED"), ("engineer", "PRIVATE")]:
            profile = self.request("POST", "/system/models", name, json={"name": "Live synthetic " + name,
                "task_type": "chat", "mode": "api", "provider": "openai_compatible", "model_name": self.model,
                "base_url": self.base_url, "api_key": self.api_key, "visibility": visibility,
                "config": {"thinking_mode": "disabled", "max_tokens": 8192, "temperature": .1,
                           "timeout_seconds": 180, "max_retries": 1}})
            self.profiles[name] = profile["id"]
            self.request("PUT", "/workbench/preferences", name, json={"chat_profile_id": profile["id"]})
        self.request("POST", f"/system/models/{self.profiles['expert']}/activate", "expert")
        visible = self.request("GET", "/system/models", "other")
        require(self.profiles["expert"] in {row["id"] for row in visible}, "shared model unavailable")
        require(self.profiles["engineer"] not in {row["id"] for row in visible}, "private model leaked")
        self.request("POST", f"/system/models/{self.profiles['engineer']}/test", "expert", expected=404)
        tested = self.request("POST", f"/system/models/{self.profiles['expert']}/test", "expert")
        require(tested.get("ok") is True, "real model connection failed: " + str(tested))
        return {"model_requested": self.model, "roles": ["ADMIN", "EXPERT", "ENGINEER"], "endpoint_allowlist": "empty", "private_isolation": True}

    def assistant(self):
        if self.resume_from:
            saved = json.loads(self.previous_file("assistant-review.json").read_text(encoding="utf-8"))
            session = self.request("GET", f"/workbench/assistant/{saved['id']}", "expert")
            self.assistant_id = session["id"]
            if session["status"] == "PUBLISH_FAILED":
                session = self.request("POST", f"/workbench/assistant/{session['id']}/retry", "expert", json={"version": session["version"]})
            require(session["status"] in {"APPROVED", "BUILDING", "PUBLISHED"}, "Saved approval was not recoverable")
            self.job(session["job_id"], "expert", timeout=180)
            return self.published_skills(session["id"], reused_reading=True)
        request_text = ("这是合成验收。完整阅读全部6份文件，保持每份原文，分别新增6个诊断Skill，全部归入组网问题network，"
            "content_kind=SKILL。SKILL.md及methodology用于diagnosis，fault-tree用于fault_tree，architecture用于prior_knowledge，"
            "log-analysis用于log_analysis，report-format用于report_template。保留相对路径和依赖，不合并文件，不发布，先给出完整精确清单。")
        session = self.request("POST", "/workbench/assistant", "expert",
            files=[("files", (Path(path).name, content.encode(), "text/markdown")) for path, content in SKILLS.items()],
            data={"paths": json.dumps(list(SKILLS)), "message": request_text, "mode": "edit"})
        self.assistant_id = session["id"]
        self.job(session["job_id"], "expert")
        session = self.request("GET", f"/workbench/assistant/{session['id']}", "expert")
        self.save("assistant-review.json", session)
        require(session["status"] == "REVIEW", "assistant did not produce an approvable review")
        require(len(session.get("plan", [])) == 6, "assistant did not propose all six files independently")
        approved = self.request("POST", f"/workbench/assistant/{session['id']}/confirm", "expert",
            json={"version": session["version"], "review_digest": session["review_digest"]})
        self.job(approved["job_id"], "expert")
        return self.published_skills(session["id"])

    def published_skills(self, session_id, reused_reading=False):
        docs = self.request("GET", "/workbench/knowledge", "engineer")
        self.skills = [doc for doc in docs if doc["content_kind"] == "SKILL"]
        require(len(self.skills) == 6, "six approved Skills not visible")
        require(sorted(doc["content"] for doc in self.skills) == sorted(SKILLS.values()),
            "Published Skill source content changed")
        return {"session_id": session_id, "published_skills": len(self.skills),
            "roles": sorted(doc["role"] for doc in self.skills), "reused_real_reading": reused_reading}

    def review(self):
        if self.resume_from:
            existing = self.sql("SELECT id FROM knowledge_contributions WHERE status='PUBLISHED' AND source_curation_id IS NULL")
            if existing:
                row = self.request("GET", f"/knowledge-contributions/{existing[0]['id']}", "expert")
                require(len(row["messages"]) >= 4 and "REVIEW_ROUND_TWO" in row["candidate"]["content"], "Prior multi-turn review was not preserved")
                self.contribution_id = row["id"]
                return {"contribution_id": row["id"], "ai_turns": 2, "contribution_status": row["status"],
                    "original_preserved": bool(row["original"]), "reused_real_review": True}
        row = self.request("POST", "/knowledge-contributions", "engineer", expected=201,
            json={"operation": "CREATE", "content_kind": "KNOWLEDGE", "title": "Synthetic verified case Wiki",
                  "content": CASE_TEXT, "metadata": {"problem_categories": ["network"]}})
        self.request("GET", f"/knowledge-contributions/{row['id']}", "expert", expected=404)
        row = self.request("POST", f"/knowledge-contributions/{row['id']}/submit", "engineer", json={"expected_version": row["version"]})
        for instruction in ["保留原稿全部事实，补充适用边界。不要捏造观察，结尾注明 REVIEW_ROUND_ONE。",
                            "再明确区分处理建议与已经完成的验证，保留第一轮修改，结尾补充 REVIEW_ROUND_TWO。"]:
            previous = row["version"]
            row = self.request("POST", f"/knowledge-contributions/{row['id']}/review-chat", "expert",
                json={"expected_version": previous, "instruction": instruction})
            require(row["version"] > previous, "AI review version did not change")
        require(len(row["messages"]) >= 4, "two-turn review history missing")
        require("REVIEW_ROUND_TWO" in row["candidate"]["content"], "second review instruction missing")
        self.save("review-candidate.json", row)
        row = self.request("POST", f"/knowledge-contributions/{row['id']}/review", "expert", json={
            "expected_version": row["version"], "expected_content_hash": row["content_hash"], "action": "APPROVE"})
        self.job(row["publication_job_id"], "expert")
        row = self.request("GET", f"/knowledge-contributions/{row['id']}", "engineer")
        require(row["status"] == "PUBLISHED", "approved Wiki not published")
        self.contribution_id = row["id"]
        return {"contribution_id": row["id"], "ai_turns": 2, "contribution_status": row["status"], "original_preserved": bool(row["original"])}

    def curation(self):
        session = self.request("POST", "/knowledge-curations", "engineer", expected=202,
            files=[("files", ("case.md", CASE_TEXT.encode(), "text/markdown"))],
            data={"relative_paths_json": '["synthetic-case/case.md"]', "title_hint": "合成认证失败案例提炼"})
        self.save("curation-created.json", session)
        job = session.get("job") or {}
        sid = session.get("id") or session.get("session", {}).get("id")
        self.job(job.get("id") or session.get("job_id"), "engineer")
        session = self.request("GET", f"/knowledge-curations/{sid}", "engineer")
        self.save("curation-review.json", session)
        require(session.get("validation", {}).get("confirmable") is True, "real extraction needs corrections: " + str(session.get("validation")))
        confirmed = self.request("POST", f"/knowledge-curations/{sid}/confirm", "engineer",
            json={"expected_draft_version": session["draft_version"]})
        row = confirmed["contribution"]
        require(row["content_kind"] == "KNOWLEDGE" and row["status"] == "DRAFT", "extraction bypassed draft review")
        submitted = self.request("POST", f"/knowledge-contributions/{row['id']}/submit", "engineer", json={"expected_version": row["version"]})
        return {"session_id": sid, "contribution_status": submitted["status"], "draft_characters": len(session["draft_markdown"])}

    def diagnosis(self):
        require(len(getattr(self, "skills", [])) == 6, "Skill publication must pass before diagnosis")
        reusable = self.sql("SELECT t.case_id FROM log_triage_runs t JOIN cases c ON c.id=t.case_id "
            "JOIN artifacts a ON a.id=t.artifact_id WHERE t.status='COMPLETED' AND c.owner_id=? "
            "AND c.title='Synthetic API authentication incident' AND a.sha256=? ORDER BY t.created_at DESC LIMIT 1",
            (self.users["engineer"], hashlib.sha256(LOGS.encode()).hexdigest())) if self.resume_from else []
        if reusable:
            case = self.request("GET", f"/cases/{reusable[0]['case_id']}", "engineer")
        else:
            case = self.request("POST", "/cases", "engineer", json={"title": "Synthetic API authentication incident",
                "description": "组网认证失败，排查凭据、电源和恢复证据；所有资料均为合成功能验收。", "device_type": "AP", "problem_category": "network"})
            artifact = self.request("POST", f"/cases/{case['id']}/artifacts", "engineer",
                files={"file": ("synthetic-api.txt", LOGS.encode(), "text/plain")}, data={"kind": "debug_log"})
            parse = self.request("POST", f"/cases/{case['id']}/artifacts/{artifact['id']}/parse", "engineer", json={})
            self.job(parse["id"], "engineer")
            triage = self.request("POST", f"/cases/{case['id']}/artifacts/{artifact['id']}/triage", "engineer", expected=202)
            self.job(triage["job"]["id"], "engineer")
        self.case_id = case["id"]
        triage = self.request("GET", f"/cases/{case['id']}/log-triage", "engineer")
        self.save("log-triage.json", triage)
        require(triage["status"] == "COMPLETED", "Real log planning did not complete")
        require(triage["summary"].get("planner_status") == "ACCEPTED", "Log planning used a fallback")
        analysis_job = self.request("POST", f"/cases/{case['id']}/analyses", "engineer", json={})
        try:
            self.job(analysis_job["id"], "engineer")
        except AssertionError:
            failed = self.request("GET", f"/jobs/{analysis_job['id']}", "engineer")
            if "Skill全文阅读请求失败" not in str(failed.get("error_message")):
                raise
            print(json.dumps({"stage": "diagnosis", "action": "one_retry_of_same_job_after_read_failure"}), flush=True)
            self.request("POST", f"/jobs/{analysis_job['id']}/retry", "engineer")
            self.job(analysis_job["id"], "engineer")
        analysis = self.request("GET", f"/cases/{case['id']}/analyses", "engineer")[0]
        self.analysis_id = analysis["id"]
        self.save("analysis.json", analysis)
        result = json.loads(analysis["result_json"])
        planning = result.get("diagnostic_planning", {})
        require(analysis["provider"] == "openai_compatible", "diagnosis silently used fallback")
        require(planning.get("method_coverage", {}).get("model_reading", {}).get("complete") is True, "Skills not fully delivered to real model")
        require(len(planning.get("rounds", [])) >= 2, "two planning rounds not completed")
        require(planning.get("planner_accepted") is True, "Real diagnostic planning was not accepted")
        citations = json.loads(analysis["evidence_json"])
        require(any(item.get("source_type") in {"log", "log_event", "log_evidence"} for item in citations),
            "Diagnosis lacks direct current-case log evidence")
        require("credential" in json.dumps(result, ensure_ascii=False).lower() or "凭据" in json.dumps(result, ensure_ascii=False), "credential cause absent")
        report = self.request("POST", f"/cases/{case['id']}/analyses/{analysis['id']}/reports/html", "engineer")
        html = self.request("GET", f"/reports/{report['report_id']}/download", "engineer")
        require(hashlib.sha256(html).hexdigest() == report["sha256"], "report hash mismatch")
        (self.output / "synthetic-api-report.html").write_bytes(html)
        submitted = self.request("POST", "/workbench/library", "engineer", json={"title": "合成诊断结果入库", "content": CASE_TEXT,
            "problem_category": "network", "case_id": case["id"], "analysis_id": analysis["id"]})
        require(bool(submitted.get("contribution_id")), "case conclusion not atomically submitted")
        return {"case_id": case["id"], "analysis_id": analysis["id"], "provider": analysis["provider"],
            "planning_rounds": len(planning["rounds"]), "skill_reading": planning["method_coverage"]["model_reading"]["document_count"],
            "report_sha256": report["sha256"], "citation_count": len(citations)}

    def chat(self):
        submission = self.request("POST", f"/cases/{self.case_id}/chat", "engineer", expected=202,
            json={"question": "根据本案例日志，认证失败与断电哪种更有证据？请引用具体日志并说明尚待验证的信息。"})
        self.job(submission["job"]["id"], "engineer")
        messages = self.request("GET", f"/cases/{self.case_id}/conversations", "engineer")
        self.save("case-chat.json", messages)
        answer = next(row for row in messages if row["role"] == "assistant" and row["job_id"] == submission["job"]["id"])
        require(answer["status"] == "COMPLETED" and bool(answer["content"]), "interactive answer missing")
        require(len(answer.get("citations", [])) > 0, "Interactive answer has no evidence references")
        return {"message_id": answer["id"], "citations": len(answer.get("citations", [])), "answer_characters": len(answer["content"])}

    def routing(self):
        if self.resume_from:
            imported = json.loads(self.previous_file("routing-created.json").read_text(encoding="utf-8"))
        else:
            imported = self.request("POST", "/knowledge-routing/import", "expert", expected=202,
                files=[("files", ("routing.md", CASE_TEXT.encode(), "text/markdown"))], data={"relative_paths_json": '["routing.md"]'})
        self.save("routing-created.json", imported)
        items = imported.get("items") or imported.get("documents") or []
        require(bool(items), "routing response has no items")
        for item in items:
            self.job((item.get("job") or {})["id"], "expert")
            document = self.sql("SELECT active,review_status FROM knowledge_documents WHERE id=?", (item["document_id"],))[0]
            require(document["active"] == 0 and document["review_status"] == "DRAFT", "Routing bypassed human approval")
        return {"documents": len(items), "draft_only": True, "reused_real_routing": bool(self.resume_from)}

    def restart(self):
        before = self.request("GET", "/workbench/knowledge", "expert")
        require(len(before) >= 7, "Restart test requires six published Skills and a reviewed Wiki")
        self.stop()
        self.start("restart")
        after = self.request("GET", "/workbench/knowledge", "expert")
        require(sorted((x["id"], x["version"], x["content"]) for x in before) == sorted((x["id"], x["version"], x["content"]) for x in after), "published knowledge changed on restart")
        return {"published_documents": len(after), "database_integrity": self.sql("PRAGMA quick_check")[0]}

    def cleanup(self):
        for actor, profile in self.profiles.items():
            try:
                self.request("PATCH", f"/system/models/{profile}", actor, json={"clear_api_key": True, "enabled": False})
            except Exception:
                pass
        self.stop()
        if self.database.exists():
            with sqlite3.connect(self.database) as db:
                db.execute("UPDATE model_profiles SET api_key_ciphertext=NULL, proxy_url_ciphertext=NULL")
                db.execute("UPDATE access_tokens SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL")
                db.commit()
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.save("cleanup.json", {"model_keys_remaining": self.sql("SELECT count(*) n FROM model_profiles WHERE api_key_ciphertext IS NOT NULL")[0]["n"],
                "live_test_tokens_remaining": self.sql("SELECT count(*) n FROM access_tokens WHERE revoked_at IS NULL")[0]["n"],
                "server_stopped": self.process is None or self.process.poll() is not None})
        for log in self.output.glob("*.log"):
            log.write_text(self.clean(log.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="glm-5.2")
    parser.add_argument("--resume-from", type=Path, help="Copy a prior synthetic run to reuse successful reading and routing")
    parser.add_argument("--phases", nargs="+", default=["setup", "assistant", "review", "curation", "diagnosis", "chat", "routing", "restart"],
        choices=["setup", "assistant", "review", "curation", "diagnosis", "chat", "routing", "restart"])
    args = parser.parse_args()
    secret = os.environ.pop("EXPERT_LIVE_API_KEY", "")
    require(bool(secret), "EXPERT_LIVE_API_KEY is required")
    require(not args.output.exists(), "Use a new isolated output directory")
    output = args.output.resolve()
    output.mkdir(parents=True)
    server = LiveServer(output, secret, args.base_url, args.model, args.resume_from.resolve() if args.resume_from else None)
    server.save("run.json", {"synthetic_only": True, "company_data_used": False, "requested_model": args.model,
        "base_url": args.base_url, "resume_from": str(server.resume_from) if server.resume_from else None, "phases": args.phases})
    try:
        server.start("first")
        for name in args.phases:
            server.phase(name, getattr(server, name))
            if name == "setup" and server.results[name]["status"] != "PASS":
                break
        audits = server.sql("SELECT outcome,details_json FROM audit_events WHERE action='model.egress'")
        values = [{**json.loads(row["details_json"]), "outcome": row["outcome"]} for row in audits]
        require(bool(values), "No real model-egress audit was recorded")
        server.save("model-egress-summary.json", {"calls": len(values), "purposes": dict(Counter(x.get("purpose") for x in values)),
            "outcomes": dict(Counter(x.get("outcome") for x in values)), "records": values})
    finally:
        server.cleanup()
        server.save("result.json", server.results)
    return 0 if server.results and all(row["status"] == "PASS" for row in server.results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
