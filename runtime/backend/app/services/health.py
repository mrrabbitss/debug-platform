import tempfile
from pathlib import Path
from shutil import disk_usage
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.utils import utcnow
from app.models import Artifact, Case, Job, KnowledgeDocument


def probe_database(db: Session) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "dialect": db.get_bind().dialect.name}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"ok": False, "error_type": type(exc).__name__}


def probe_storage(storage_root: Path) -> dict[str, Any]:
    try:
        if not storage_root.is_dir():
            return {"ok": False, "error_type": "StorageDirectoryMissing"}
        with tempfile.NamedTemporaryFile(prefix=".readiness-", dir=storage_root, delete=True):
            pass
        usage = disk_usage(storage_root)
        return {
            "ok": True,
            "free_bytes": usage.free,
            "total_bytes": usage.total,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_type": type(exc).__name__}


def readiness_report(db: Session, storage_root: Path) -> dict[str, Any]:
    checks = {
        "database": probe_database(db),
        "storage": probe_storage(storage_root),
    }
    ready = all(bool(check["ok"]) for check in checks.values())
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "checks": checks,
        "checked_at": utcnow().isoformat(),
    }


def system_status_report(db: Session, storage_root: Path, job_workers: int) -> dict[str, Any]:
    readiness = readiness_report(db, storage_root)
    job_counts = {
        status: int(count)
        for status, count in db.execute(
            select(Job.status, func.count(Job.id)).group_by(Job.status)
        ).all()
    }
    entity_counts = {
        "cases": int(db.scalar(select(func.count(Case.id))) or 0),
        "artifacts": int(db.scalar(select(func.count(Artifact.id))) or 0),
        "knowledge_documents": int(db.scalar(select(func.count(KnowledgeDocument.id))) or 0),
    }
    artifact_bytes = int(db.scalar(select(func.coalesce(func.sum(Artifact.size_bytes), 0))) or 0)
    return {
        "status": readiness["status"],
        "checked_at": readiness["checked_at"],
        "checks": readiness["checks"],
        "database": {
            "dialect": db.get_bind().dialect.name,
        },
        "storage": {
            "artifact_bytes": artifact_bytes,
            "free_bytes": readiness["checks"]["storage"].get("free_bytes"),
            "total_bytes": readiness["checks"]["storage"].get("total_bytes"),
        },
        "jobs": {
            "configured_workers": job_workers,
            "counts": job_counts,
        },
        "entities": entity_counts,
    }
