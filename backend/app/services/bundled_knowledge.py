"""Initialize an empty installed server from the release owner's approved Skill ZIP.

The business documents are build inputs, not Python/source-control assets. Reuse
the durable, hash-bound import outbox; never reset an existing knowledge corpus.
"""
import json
import logging
import os
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.utils import json_dumps, json_loads
from app.models import AgentMemory, Job, KnowledgeDocument, KnowledgeDraft, KnowledgePublication, UserAccount
from app.workbench_models import WorkbenchRecord
from app.services import knowledge_reset as reset
from app.services.model_profiles import get_active_model_profile, MANAGED_LOCAL_PROVIDER

PACKAGE_ROOT = next((parent for parent in Path(__file__).resolve().parents
                     if (parent / "package-manifest.json").is_file() and (parent / "build-info.json").is_file()),
                    Path(__file__).resolve().parents[3])
BUNDLE_ID = "network-skill-v1"
OPERATION_ID = "installer-network-skill-v1"
RECORD_ID = "bundled-" + BUNDLE_ID
logger = logging.getLogger(__name__)


def read_packaged_bundle(package_root: Path = PACKAGE_ROOT):
    folder = package_root / "bundled-knowledge"
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("bundle_id") != BUNDLE_ID
            or manifest.get("archive") != "hilink-diag.zip"
            or manifest.get("approval_origin") != "INSTALLER_DEFAULT"):
        raise ValueError("Bundled knowledge manifest is invalid")
    source = folder / manifest["archive"]
    bundle = reset.read_bundle(source.resolve())
    expected = [{key: item[key] for key in ("path", "bytes", "source_sha256", "role")}
                for item in bundle["manifest"]]
    if manifest.get("source_sha256") != bundle["source_sha256"] or manifest.get("files") != expected:
        raise ValueError("Bundled knowledge failed its complete source verification")
    return source, manifest


def _remember(db, status, **values):
    row = db.get(WorkbenchRecord, RECORD_ID)
    if row is None:
        row = WorkbenchRecord(id=RECORD_ID, kind="bundled_knowledge")
        db.add(row)
    row.payload_json = json_dumps({"status": status, **values})
    db.commit()


def has_existing_knowledge(db):
    # Archived/deleted knowledge and old publication records also count: an
    # administrator's deliberate removal must never trigger a new default seed.
    return any(db.scalar(query.limit(1)) is not None for query in (
        select(KnowledgeDocument.id), select(KnowledgeDraft.id), select(KnowledgePublication.id),
        select(AgentMemory.id).where(AgentMemory.scope == "GLOBAL"),
        select(WorkbenchRecord.id).where(WorkbenchRecord.kind.in_(["template_default", reset.KIND])),
    ))


def initialize_packaged_knowledge(db, package_root: Path = PACKAGE_ROOT):
    """Only queue a verified empty-corpus import; model/index work is asynchronous."""
    try:
        packaged = read_packaged_bundle(package_root)
        if packaged is None:
            return
        source, manifest = packaged
        # The existing explicit maintenance preview can also use the shipped ZIP.
        os.environ.setdefault("KNOWLEDGE_RESET_SOURCE_ZIP", str(source))
        operation = db.get(WorkbenchRecord, reset.operation_key(OPERATION_ID))
        if operation is not None:
            return  # Includes interrupted, failed and completed attempts.
        marker = db.get(WorkbenchRecord, RECORD_ID)
        if marker and json_loads(marker.payload_json, {}).get("status") == "PRESERVED":
            return
        if has_existing_knowledge(db):
            _remember(db, "PRESERVED", source_sha256=manifest["source_sha256"])
            return
        actor = db.scalar(select(UserAccount.id).where(
            UserAccount.active.is_(True), UserAccount.role == "ADMIN").order_by(UserAccount.created_at))
        if actor is None:
            _remember(db, "WAITING_ADMIN")
            return
        profile = get_active_model_profile("embedding", db)
        if profile is None or (profile.mode == "api" and profile.provider != MANAGED_LOCAL_PROVIDER):
            _remember(db, "WAITING_LOCAL_INDEX")
            return
        data_root = get_settings().data_root
        preview = reset.preview_reset(db, operation_id=OPERATION_ID, data_root=data_root,
            source_zip=source, actor=actor)
        reset.confirm_reset(db, operation_id=OPERATION_ID, data_root=data_root, source_zip=source,
            actor=actor, expected_source_sha256=preview["source_sha256"], expected_preview_hash=preview["preview_hash"],
            confirmed=True, model_egress_approved=False, approval_origin="INSTALLER_DEFAULT")
        # confirm_reset already committed the durable marker and its outbox job.
    except (OSError, ValueError) as error:
        db.rollback()
        logger.warning("Bundled Skill initialization deferred (%s); existing knowledge retained", type(error).__name__)
        _remember(db, "FAILED", error_type=type(error).__name__)


def packaged_knowledge_status(db, package_root: Path = PACKAGE_ROOT):
    operation = db.get(WorkbenchRecord, reset.operation_key(OPERATION_ID))
    marker = db.get(WorkbenchRecord, RECORD_ID)
    if operation:
        value = json_loads(operation.payload_json, {})
        job = db.get(Job, value.get("job_id"))
        status = value.get("status", "APPROVED")
        if job and job.status in {"FAILED", "DEAD_LETTER", "CANCELLED"}:
            status = "FAILED"
        elif job and job.status == "QUEUED":
            status = "APPROVED"
        result = {"status": status, "operation_id": OPERATION_ID,
                  "job_id": job.id if job else None, "progress": job.progress if job else 0}
    elif marker:
        result = {"status": json_loads(marker.payload_json, {}).get("status", "FAILED")}
    else:
        result = {"status": "PENDING" if (package_root / "bundled-knowledge/manifest.json").is_file() else "NOT_BUNDLED"}
    messages = {
        "NOT_BUNDLED": "此运行目录未附带初始化 Skill 包。",
        "PENDING": "内置组网 Skill 等待初始化。",
        "APPROVED": "内置组网 Skill 已排队；索引完成后六个文件一起生效。",
        "BUILDING": "正在初始化内置组网 Skill 和索引，完成后会自动显示。",
        "PUBLISHED": "内置组网包已完整初始化；当前生效内容以人工维护的版本为准。",
        "PRESERVED": "已保留现有知识和 Skill。安装包内附完整组网文件夹，可在 AI 整理助手中按需合并导入。",
        "WAITING_ADMIN": "内置组网 Skill 等待服务器初始管理员创建完成；请重启服务器。",
        "WAITING_LOCAL_INDEX": "内置组网 Skill 尚未导入。请启用内置 Embedding 后重启，或在知识维护中确认使用外部模型导入。",
        "FAILED": "内置组网 Skill 初始化未完成，原有内容保留。请在知识维护中查看此操作并重试或重新预览导入。",
        "CANCELLED": "内置组网 Skill 初始化已取消；可在知识维护中重新预览导入。",
    }
    return {**result, "message": messages.get(result["status"], messages["FAILED"]),
            "folder": "bundled-knowledge/hilink-diag", "source_files": 6}
