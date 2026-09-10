"""Stopped-server bundle maintenance. Never dispatch an old knowledge_reset job."""
from datetime import timedelta
import os
from uuid import uuid4

from sqlalchemy import select

from app.core.utils import json_loads, utcnow
from app.models import Job, ModelProfile, UserAccount
from app.services import jobs, knowledge_reset as reset
from app.services.model_profiles import MANAGED_LOCAL_PROVIDER, get_active_model_profile
from app.workbench_models import WorkbenchRecord

KIND = "offline_network_skill_update"


def isolate_job_kind():
    # An older server must never resume this with its whole-corpus reset handler.
    # This assignment affects only this process; installed files stay untouched.
    reset.KIND = KIND
    reset.MUTATING_JOBS = {*reset.MUTATING_JOBS, KIND}


class ConsoleContext(jobs.JobContext):
    def update(self, progress, message):
        super().update(progress, message)
        if getattr(self, "last_progress", None) == progress:
            return
        self.last_progress = progress
        stages = "构建向量索引" if progress < 70 else "构建知识关系并准备发布"
        print(f"[{progress}%] {stages}（阶段估算，尚未发布）", flush=True)


def choose_operation(db, source_hash):
    pending = []
    for row in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == KIND)):
        value = json_loads(row.payload_json, {})
        if value.get("status") != "PUBLISHED":
            pending.append(value)
    if len(pending) > 1 or (pending and pending[0].get("source_sha256") != source_hash):
        raise reset.ResetError("存在另一份来源的未完成更新；未修改知识，请保留日志核对。")
    return pending[0] if pending else None


def managed_indexer(index):
    """Use the temporary sidecar address without changing the approved DB profile."""
    def call(db, profile, *args, **kwargs):
        if profile.provider == MANAGED_LOCAL_PROVIDER:
            transient = ModelProfile(**{c.name: getattr(profile, c.name) for c in ModelProfile.__table__.columns})
            transient.base_url = os.environ["BUNDLED_GGUF_EMBEDDING_URL"]
            transient.api_key_ciphertext = None
            transient.model_name = os.environ["BUNDLED_GGUF_EMBEDDING_MODEL"]
            return index(db, transient, *args, **kwargs)
        return index(db, profile, *args, **kwargs)
    return call


def run_update(*, data_root, source_zip, archive_root, manager_factory, allow_external=False):
    """Caller must hold the installed server's stop/backup lock throughout."""
    isolate_job_kind()
    bundle = reset.read_bundle(source_zip)
    with reset.SessionLocal() as db:
        pending = choose_operation(db, bundle["source_sha256"])
        if pending:
            actor = pending["approved_by"]
            plan = {**pending["approved_plan"], "preview_hash": pending["preview_hash"]}
            reset.require_manager(db, actor)
            if allow_external and not pending["model_egress_approved"]:
                raise reset.ResetError("重试沿用原审批的模型授权，不能扩大出站范围。")
        else:
            account = db.scalar(select(UserAccount).where(UserAccount.active.is_(True),
                UserAccount.role == "ADMIN").order_by(UserAccount.created_at, UserAccount.id))
            if not account:
                raise reset.ResetError("现有数据库未找到有效管理员；未创建账号或更改密钥。")
            actor = account.id
            plan = reset.preview_reset(db, operation_id="offline-network-" + uuid4().hex,
                data_root=data_root, source_zip=source_zip, actor=actor, preserve_existing=True, replace_bundle=True)
            if not plan["can_confirm"]:
                print("[100%] 六份组网 Skill 已完整生效，无需重复更新。", flush=True)
                return {"status": "UNCHANGED", "files": 6}
        profile = get_active_model_profile("embedding", db)
        if not profile:
            raise reset.ResetError("没有已启用的 Embedding 配置；未修改知识。")
        managed = profile.provider == MANAGED_LOCAL_PROVIDER
        if profile.mode == "api" and not managed and not (allow_external or (pending and pending["model_egress_approved"])):
            raise reset.ResetError("当前使用外部 Embedding API。需显式使用 --allow-configured-embedding-api 后重试；不会更换模型。")
    print("[5%] 更新范围：指定组网包的六份文件；其他知识、草稿、案例、报告保留。", flush=True)
    for item in plan["inspection"]:
        print("  " + item["path"] + " : " + item["disposition"], flush=True)
    manager = manager_factory() if managed else None
    original_index = reset.index_embeddings
    try:
        if manager:
            print("[8%] 正在启动现有本地 Embedding，首次加载可能需要等待。", flush=True)
            manager.start(require_all=True)
            reset.index_embeddings = managed_indexer(original_index)
        with reset.SessionLocal() as db:
            if pending:
                row, job = reset.read_operation(db, plan["operation_id"])[0], db.get(Job, pending["job_id"])
            else:
                print("[10%] 备份数据库，持久保存六文件更新清单。", flush=True)
                row, job = reset.confirm_reset(db, operation_id=plan["operation_id"], data_root=data_root,
                    source_zip=source_zip, actor=actor, preserve_existing=True, replace_bundle=True,
                    confirmed=True, model_egress_approved=allow_external, archive_root=archive_root,
                    expected_source_sha256=plan["source_sha256"], expected_preview_hash=plan["preview_hash"],
                    approval_origin="OFFLINE_BUNDLE_UPDATE")
            value = json_loads(row.payload_json, {})
            if not job or job.kind != KIND:
                raise reset.ResetError("离线更新任务类型不匹配；未启动发布。")
            print("备份位置：" + value["backup"]["path"], flush=True)
            job.status, job.attempt, job.lease_owner = "RUNNING", job.attempt + 1, "offline-" + uuid4().hex
            job.lease_expires_at = utcnow() + timedelta(hours=12)
            job.deadline_at = None
            job.error_message = None
            db.commit()
            context = ConsoleContext(job.id, lease_owner=job.lease_owner, lease_seconds=43200)
        try:
            result = reset.reset_job(context, plan["operation_id"])
        except BaseException:
            with reset.SessionLocal() as db:
                job = db.get(Job, context.job_id)
                if job and job.status != "COMPLETED":
                    job.status, job.lease_owner, job.lease_expires_at = "FAILED", None, None
                    db.commit()
            raise
        print("[100%] 六份组网 Skill 与索引已一起生效。现在可以启动原服务器并刷新网页。", flush=True)
        return {"status": "PUBLISHED", **result, "backup": value["backup"]["path"]}
    finally:
        reset.index_embeddings = original_index
        if manager:
            manager.stop()
