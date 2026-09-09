"""Explicit, human-approved replacement of one SQLite knowledge corpus.

No startup hook, SQL DDL, archive extraction, Chat call, or AI reading receipt.
The original ZIP and consistent SQLite backup live outside source control.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import posixpath
import re
import sqlite3
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (AgentMemory, AuditEvent, Job, KnowledgeAccess, KnowledgeChunk,
    KnowledgeDocument, KnowledgeDraft, KnowledgeEmbedding, KnowledgeGraphState,
    KnowledgePublication, ModelProfile, UserAccount)
from app.workbench_models import WorkbenchRecord
from app.services.jobs import JobCancelledError, JobLeaseLostError
from app.services.knowledge import chunk_document
from app.services.knowledge_access import bind_owner, knowledge_kind
from app.services.knowledge_governance import create_document_revision
from app.services.knowledge_release_graph import stage_graph
from app.services.model_profiles import get_active_model_profile, MANAGED_LOCAL_PROVIDER
from app.services.retrieval_models import index_embeddings

KIND = "knowledge_reset"
ROLES = {"SKILL.md": "diagnosis", "diagnostic-methodology.md": "diagnosis",
    "fault-tree.md": "fault_tree", "log-analysis.md": "log_analysis",
    "hilink-architecture.md": "prior_knowledge", "report-format.md": "report_template"}
SOURCE_TYPES = {"diagnosis": "analysis_method", "fault_tree": "fault_tree",
    "log_analysis": "analysis_skill", "prior_knowledge": "diagnostic_rule", "report_template": "document"}
MAX_ZIP = 16 * 1024 * 1024
MAX_FILE = 1024 * 1024
MUTATING_JOBS = {KIND, "publish_knowledge_revision", "publish_knowledge_contribution",
    "assistant_publish", "import_knowledge", "reindex_knowledge", "rebuild_domain_graph",
    "route_markdown_knowledge"}
ADAPTER = (
    "\n> 平台运行适配（人工确认导入）：本文中使用当前 CLI 会话模型的要求，"
    "在网页平台映射为当前任务已固定的诊断模型配置。知识与 Skill 的修改遵循平台角色权限，"
    "须由管理员或专家确认具体差异后发布。以下源文保留；文中命令仅作为知识，不自动执行。\n\n"
)


class ResetError(ValueError):
    """An operator-readable error with no document contents or model secrets."""


def digest(value) -> str:
    data = value if isinstance(value, bytes) else (value if isinstance(value, str) else
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def operation_key(operation_id: str) -> str:
    if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operation_id):
        raise ResetError("operation_id must contain 1-64 letters, digits, underscores or hyphens")
    return "KRST-" + digest(operation_id)


def _path(name: str) -> str:
    parts = name.split("/")
    if (not name or len(name) > 512 or "\\" in name or ":" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in parts)
            or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts)):
        raise ResetError("ZIP contains an unsafe relative path")
    return name


def _references(path, content, names):
    links = [a or b for a, b in re.findall(r'\]\(\s*(?:<([^>\n]+)>|([^\s)>]+))', content)]
    links += [a or b for a, b in re.findall(r'^\s*\[[^\]]+\]:\s*(?:<([^>\n]+)>|([^\s>]+))', content, re.M)]
    code_links = re.findall(r'`([^`\n]+\.md(?:#[^`\n]*)?)`', content)
    links += code_links
    result = []
    for link in dict.fromkeys(links):
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc or not parsed.path.lower().endswith(".md"):
            continue  # Never fetch URLs or treat quoted source/code paths as commands.
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(parsed.path)))
        # The approved root uses bare filenames in prose as well as proper links.
        # Only an unambiguous code-span basename may use this bundle-local alias.
        if target not in names and link in code_links and "/" not in parsed.path and "\\" not in parsed.path:
            matches = [name for name in names if PurePosixPath(name).name == unquote(parsed.path)]
            if len(matches) == 1:
                target = matches[0]
        if target not in names:
            raise ResetError("A local Markdown reference does not resolve inside the six-file bundle")
        result.append({"reference": link, "path": target, "fragment": parsed.fragment})
    return result


def read_bundle(source_zip: Path | str) -> dict:
    source = Path(source_zip)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise ResetError("Source ZIP must be an existing absolute regular file")
    with source.open("rb") as handle:
        raw = handle.read(MAX_ZIP + 1)
    if len(raw) > MAX_ZIP:
        raise ResetError("ZIP exceeds 16 MiB; no source was truncated")
    files, seen, total = [], set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > 16:
                raise ResetError("Expected one root Skill and five reference files")
            for info in archive.infolist():
                name = _path(info.filename.rstrip("/") if info.is_dir() else info.filename)
                if name.casefold() in seen:
                    raise ResetError("ZIP contains duplicate paths")
                seen.add(name.casefold())
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
                    raise ResetError("ZIP links, devices and encrypted entries are unsupported")
                if info.is_dir():
                    continue
                total += info.file_size
                if info.file_size > MAX_FILE or total > 6 * MAX_FILE:
                    raise ResetError("A Skill source exceeds the bounded import size")
                data = archive.read(info)
                content = data.decode("utf-8-sig")
                if not content.strip() or "\x00" in content:
                    raise ResetError("Every Skill source must be non-empty UTF-8 Markdown")
                files.append({"path": name, "original_content": content, "source_sha256": digest(data), "bytes": len(data)})
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError):
        raise ResetError("ZIP could not be fully verified as UTF-8 Markdown") from None
    roots = [f for f in files if PurePosixPath(f["path"]).name == "SKILL.md"]
    if len(files) != 6 or len(roots) != 1:
        raise ResetError("Expected exactly SKILL.md plus five reference Markdown files")
    prefix = roots[0]["path"][:-len("SKILL.md")]
    expected = {prefix + "SKILL.md", *(prefix + "references/" + n for n in ROLES if n != "SKILL.md")}
    if {f["path"] for f in files} != expected:
        raise ResetError("The approved six-file Skill layout does not match this ZIP")
    for item in files:
        item["role"] = ROLES[PurePosixPath(item["path"]).name]
        item["references"] = _references(item["path"], item["original_content"], expected)
        item["content"] = item["original_content"]
        if item is roots[0]:
            lines = item["content"].splitlines(keepends=True)
            insertion = 0
            if lines and lines[0].strip() == "---":
                closes = [i for i in range(1, len(lines)) if lines[i].strip() == "---"]
                if not closes:
                    raise ResetError("Root Skill front matter is not closed")
                insertion = closes[0] + 1
            lines.insert(insertion, ADAPTER)
            item["content"] = "".join(lines)
        item["sha256"] = digest(item["content"])
        item["adaptation_diff"] = "".join(difflib.unified_diff(
            item["original_content"].splitlines(keepends=True), item["content"].splitlines(keepends=True),
            fromfile=item["path"] + " (source)", tofile=item["path"] + " (platform)", n=0))
    files.sort(key=lambda item: (item["path"] != roots[0]["path"], item["path"]))
    return {"source_sha256": digest(raw), "raw": raw, "files": files,
        "manifest": [{k: f[k] for k in ("path", "bytes", "source_sha256", "sha256", "role", "references", "adaptation_diff")}
                     for f in files]}


def _bound_manifest(bundle, operation_id):
    identifiers = {f["path"]: "KDOC-" + digest([operation_id, f["path"]])[:32] for f in bundle["files"]}
    result = []
    for item in bundle["manifest"]:
        refs = [{**ref, "document_id": identifiers[ref["path"]], "document_ids": [identifiers[ref["path"]]]}
                for ref in item["references"]]
        result.append({**item, "document_id": identifiers[item["path"]], "document_ids": [identifiers[item["path"]]],
            "references": refs, "resolved_references": refs})
    return result


def require_manager(db, actor: str) -> None:
    account = db.get(UserAccount, actor) if actor else None
    if account and account.active and account.role in {"ADMIN", "EXPERT"}:
        return
    settings = get_settings()
    if actor == "local-development" and settings.auth_mode == "local":
        return
    if actor == "legacy-api-key" and (settings.auth_mode in {"local", "api_key"} or settings.auth_allow_legacy_admin):
        return
    raise ResetError("An active administrator or expert must approve and execute this operation")


def verified_target(db, data_root: Path | str) -> dict:
    root = Path(data_root)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ResetError("An explicit existing absolute data root is required")
    root = root.resolve(strict=True)
    settings = get_settings()
    if root != Path(settings.data_root).resolve(strict=True) or db.get_bind().dialect.name != "sqlite":
        raise ResetError("The supplied data root must match the configured SQLite instance")
    databases = db.connection().exec_driver_sql("PRAGMA database_list").fetchall()
    path = next((Path(row[2]) for row in databases if row[1] == "main" and row[2]), None)
    if path is None or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ResetError("The connected database must be an existing regular SQLite file inside the verified root")
    path = path.resolve(strict=True)
    if path == root:
        raise ResetError("Database and data root must be distinct")
    info = path.stat()
    return {"data_root": str(root), "database_path": str(path), "database_identity": [info.st_dev, info.st_ino]}


def _fingerprint(row):
    return digest({col.name: str(getattr(row, col.name)) for col in row.__table__.columns})


def _corpus(db, operation_id=None):
    documents = list(db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.id)))
    if operation_id:
        operation = db.get(WorkbenchRecord, operation_key(operation_id))
        plan = json_loads(operation.payload_json, {}).get("approved_plan", {}) if operation else {}
        reserved_ids = {f["document_id"] for f in plan.get("manifest", [])}
        documents = [d for d in documents if not (d.id in reserved_ids and not d.active and not d.content
            and d.review_status == "DRAFT" and json_loads(d.metadata_json, {}).get("reset_reservation") == operation_id)]
    drafts = list(db.scalars(select(KnowledgeDraft).order_by(KnowledgeDraft.id)))
    memories = list(db.scalars(select(AgentMemory).where(AgentMemory.scope == "GLOBAL").order_by(AgentMemory.id)))
    templates = list(db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "template_default")
        .order_by(WorkbenchRecord.id)))
    profile = get_active_model_profile("embedding", db)
    graph = db.get(KnowledgeGraphState, "domain")
    return {"documents": documents, "drafts": drafts, "memories": memories, "templates": templates,
        "signature": digest({"documents": [(d.id, _fingerprint(d)) for d in documents],
            "drafts": [(d.id, _fingerprint(d)) for d in drafts],
            "memories": [(d.id, _fingerprint(d)) for d in memories],
            "templates": [(d.id, _fingerprint(d)) for d in templates]}),
        "profile_id": profile.id if profile else None, "profile_hash": _fingerprint(profile) if profile else None,
        "old_vector": profile.active_embedding_generation_id if profile else None,
        "old_graph": graph.active_generation_id if graph else None}


def _assert_idle(db, own_job=None):
    query = select(func.count()).select_from(Job).where(Job.kind.in_(MUTATING_JOBS),
        Job.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]))
    if own_job:
        query = query.where(Job.id != own_job)
    if db.scalar(query):
        raise ResetError("Another knowledge mutation is queued or running; finish it before resetting")


def _summary(snapshot):
    return {key: snapshot[key] for key in ("signature", "profile_id", "profile_hash", "old_vector", "old_graph")}


def preview_reset(db, *, operation_id: str, data_root: Path | str, source_zip: Path | str, actor: str) -> dict:
    """Read only. Returned hashes bind all six sources and the entire prior corpus."""
    operation_key(operation_id)
    require_manager(db, actor)
    target = verified_target(db, data_root)
    bundle, snapshot = read_bundle(source_zip), _corpus(db)
    counts = {"knowledge_and_skills": len(snapshot["documents"]),
        "currently_active": sum(d.active and d.review_status == "ACTIVE" for d in snapshot["documents"]),
        "skills": sum(knowledge_kind(d) == "SKILL" for d in snapshot["documents"]),
        "drafts": len(snapshot["drafts"]), "global_memories": len(snapshot["memories"]),
        "preserved_chunks": db.scalar(select(func.count()).select_from(KnowledgeChunk)),
        "preserved_publications": db.scalar(select(func.count()).select_from(KnowledgePublication))}
    plan = {"schema_version": 1, "operation_id": operation_id, "target": target,
        "source_sha256": bundle["source_sha256"], "manifest": _bound_manifest(bundle, operation_id), "counts": counts,
        "baseline": _summary(snapshot), "approval_type": "HUMAN_DIRECT_IMPORT",
        "content_kind": "SKILL", "problem_categories": ["network"],
        "preserved": ["cases", "reports", "users", "models", "configuration", "audit", "immutable_references"],
        "report_template_path": next(f["path"] for f in bundle["files"] if f["role"] == "report_template")}
    return {**plan, "preview_hash": digest(plan)}


def _archive_directory(target, archive_root):
    root = Path(archive_root or os.environ.get("KNOWLEDGE_RESET_ARCHIVE_ROOT") or
        Path(target["data_root"]) / "knowledge-reset-archives")
    if not root.is_absolute():
        raise ResetError("Archive root must be absolute")
    for parent in (root, *root.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ResetError("Archive directories may not traverse symbolic links or junctions")
        if (parent / ".git").exists():
            raise ResetError("Set KNOWLEDGE_RESET_ARCHIVE_ROOT to a directory outside Git")
    root.mkdir(parents=True, exist_ok=True)
    result = Path(tempfile.mkdtemp(prefix="knowledge-reset-", dir=root)).resolve()
    if not result.is_relative_to(root.resolve()) or result == Path(target["database_path"]):
        raise ResetError("Invalid managed archive directory")
    return result


def _sync_file(path):
    # Windows CRT _commit requires a writable descriptor even after SQLite closed.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def consistent_backup(database_path: Path, destination: Path) -> dict:
    """SQLite online backup includes committed WAL pages; never copy a live .db."""
    if destination.exists():
        raise ResetError("Backup destination already exists")
    started = time.monotonic()
    def progress(status, remaining, total):
        if time.monotonic() - started > 120:
            raise ResetError("Consistent backup timed out; no knowledge was changed")
    source = sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True, timeout=30)
    backup = sqlite3.connect(str(destination), timeout=30)
    try:
        source.backup(backup, pages=256, progress=progress, sleep=0.05)
        if backup.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ResetError("SQLite backup integrity check failed")
        if backup.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ResetError("SQLite backup contains invalid foreign keys")
    finally:
        backup.close()
        source.close()
    _sync_file(destination)
    with destination.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"path": str(destination), "sha256": sha, "bytes": destination.stat().st_size}


def _write_private(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _transaction(db):
    if db.new or db.dirty or db.deleted:
        raise ResetError("Reset requires a dedicated clean database session")
    db.rollback()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _existing(db, operation_id, source_hash, preview_hash):
    row = db.get(WorkbenchRecord, operation_key(operation_id))
    if not row:
        return None
    value = json_loads(row.payload_json, {})
    if (row.kind != KIND or value.get("source_sha256") != source_hash or value.get("preview_hash") != preview_hash):
        raise ResetError("Operation id is already bound to a different source or approval")
    return row, db.get(Job, value["job_id"])


def confirm_reset(db, *, operation_id: str, data_root: Path | str, source_zip: Path | str,
        actor: str, expected_source_sha256: str, expected_preview_hash: str,
        confirmed: bool, model_egress_approved: bool, archive_root: Path | str | None = None):
    """Back up first, then commit approval and outbox job together. Never dispatch here."""
    if confirmed is not True:
        raise ResetError("Explicit confirmation of the complete preview is required")
    require_manager(db, actor)
    target = verified_target(db, data_root)
    existing = _existing(db, operation_id, expected_source_sha256, expected_preview_hash)
    if existing:
        return existing
    _assert_idle(db)
    preview = preview_reset(db, operation_id=operation_id, data_root=data_root, source_zip=source_zip, actor=actor)
    if preview["source_sha256"] != expected_source_sha256 or preview["preview_hash"] != expected_preview_hash:
        raise ResetError("Source, corpus or target changed; review a fresh preview")
    bundle = read_bundle(source_zip)
    if bundle["source_sha256"] != expected_source_sha256:
        raise ResetError("Source ZIP changed during confirmation")
    if not preview["baseline"]["profile_id"]:
        raise ResetError("Configure an active Embedding profile before publication")
    profile = db.get(ModelProfile, preview["baseline"]["profile_id"])
    if profile.mode == "api" and profile.provider != MANAGED_LOCAL_PROVIDER and model_egress_approved is not True:
        raise ResetError("Embedding content egress requires explicit consent")
    db.rollback()  # No write transaction is held during the online backup.
    directory = _archive_directory(target, archive_root)
    backup = consistent_backup(Path(target["database_path"]), directory / "before.sqlite3")
    _write_private(directory / "source.zip", bundle["raw"])
    _write_private(directory / "preview.json", json_dumps(preview).encode("utf-8"))
    _sync_directory(directory)
    _sync_directory(directory.parent)
    _transaction(db)
    try:
        require_manager(db, actor)
        if verified_target(db, data_root) != target:
            raise ResetError("The database file changed during backup")
        existing = _existing(db, operation_id, expected_source_sha256, expected_preview_hash)
        if existing:
            db.rollback()
            return existing
        _assert_idle(db)
        if _summary(_corpus(db)) != preview["baseline"]:
            raise ResetError("Knowledge changed while backing up; review a fresh preview")
        graph = db.get(KnowledgeGraphState, "domain")
        if graph and graph.building_generation_id:
            raise ResetError("Another graph generation is building")
        job = Job(id=new_id("JOB"), kind=KIND, input_json=json_dumps({"operation_id": operation_id}),
            idempotency_key=operation_key(operation_id), status="QUEUED", max_attempts=3, timeout_seconds=3600)
        plan = {key: val for key, val in preview.items() if key != "preview_hash"}
        value = {"status": "APPROVED", "operation_id": operation_id, "source_sha256": expected_source_sha256,
            "preview_hash": expected_preview_hash, "approved_plan": plan, "approved_by": actor,
            "approved_at": utcnow().isoformat(), "approval_type": "HUMAN_DIRECT_IMPORT",
            "model_egress_approved": model_egress_approved, "archive_zip": str(directory / "source.zip"),
            "backup": backup, "job_id": job.id}
        row = WorkbenchRecord(id=operation_key(operation_id), kind=KIND, owner_id=actor, payload_json=json_dumps(value))
        db.add_all([row, job, AuditEvent(id=new_id("AUD"), actor_id=actor, actor_type="user",
            action="knowledge.reset.approved", resource_type=KIND, resource_id=row.id,
            details_json=json_dumps({"operation_id": operation_id, "source_sha256": expected_source_sha256,
                "preview_hash": expected_preview_hash, "backup_sha256": backup["sha256"], "content_recorded": False}))])
        db.commit()
        return row, job
    except Exception:
        db.rollback()
        raise


def read_operation(db, operation_id):
    row = db.get(WorkbenchRecord, operation_key(operation_id))
    if not row or row.kind != KIND:
        raise ResetError("Knowledge reset operation not found")
    return row, json_loads(row.payload_json, {})


def operation_payload(row):
    value = json_loads(row.payload_json, {})
    return {"id": row.id, "version": row.version, **value}


class ResetFence:
    def __init__(self, ctx, operation_id):
        self.ctx, self.operation_id = ctx, operation_id
        self.attempt, self.graph_id = None, None

    def check(self, db, *, building=True):
        row, value = read_operation(db, self.operation_id)
        job = db.get(Job, value["job_id"])
        if job and job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
            raise JobCancelledError("Knowledge reset cancellation requested")
        if (not job or job.id != self.ctx.job_id or job.status != "RUNNING" or not self.ctx.lease_owner
                or job.lease_owner != self.ctx.lease_owner
                or (self.attempt is not None and job.attempt != self.attempt)):
            raise JobLeaseLostError("Knowledge reset worker no longer owns its lease")
        if db.scalar(select(Job.id).where(Job.id == job.id, Job.lease_expires_at <= utcnow())):
            raise JobLeaseLostError("Knowledge reset lease expired")
        require_manager(db, value["approved_by"])
        if (value["approval_type"] != "HUMAN_DIRECT_IMPORT" or not value.get("approved_at")
                or digest(value["approved_plan"]) != value["preview_hash"]):
            raise ResetError("The human-approved reset manifest is no longer valid")
        if verified_target(db, value["approved_plan"]["target"]["data_root"]) != value["approved_plan"]["target"]:
            raise ResetError("The confirmed database identity no longer matches")
        if building:
            graph = db.get(KnowledgeGraphState, "domain")
            if (value.get("status") != "BUILDING" or value.get("worker_attempt") != self.attempt
                    or value.get("building_generation_id") != self.graph_id
                    or not graph or graph.building_generation_id != self.graph_id):
                raise JobLeaseLostError("A later reset worker owns the private generation")
            profile = get_active_model_profile("embedding", db)
            if not profile or _fingerprint(profile) != value["approved_plan"]["baseline"]["profile_hash"]:
                raise ResetError("The approved Embedding profile changed")
        return row, value, job

    def raise_if_cancelled(self):
        self.ctx.raise_if_cancelled()
        with SessionLocal() as db:
            self.check(db)

    def update(self, progress, message):
        self.raise_if_cancelled()
        self.ctx.update(progress, message)


def _prepare(fence):
    fence.ctx.raise_if_cancelled()
    with SessionLocal() as db:
        _transaction(db)
        row, value, job = fence.check(db, building=False)
        if value["status"] == "PUBLISHED":
            raise ResetError("A completed reset cannot run again")
        _assert_idle(db, job.id)
        snapshot = _corpus(db, fence.operation_id)
        if _summary(snapshot) != value["approved_plan"]["baseline"]:
            raise ResetError("The approved old corpus or model changed; a new preview is required")
        bundle = read_bundle(value["archive_zip"])
        if (bundle["source_sha256"] != value["source_sha256"]
                or _bound_manifest(bundle, fence.operation_id) != value["approved_plan"]["manifest"]):
            raise ResetError("The retained source archive failed verification")
        with Path(value["backup"]["path"]).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != value["backup"]["sha256"]:
                raise ResetError("The consistent pre-reset backup failed verification")
        graph = db.get(KnowledgeGraphState, "domain")
        if not graph:
            graph = KnowledgeGraphState(id="domain", status="NOT_BUILT")
            db.add(graph)
        if graph.building_generation_id and graph.building_generation_id != value.get("building_generation_id"):
            raise ResetError("Another publisher owns the graph generation")
        fence.attempt, fence.graph_id = job.attempt, new_id("KGEN")
        vector_id = new_id("EGEN")
        graph.building_generation_id, graph.status = fence.graph_id, "BUILDING"
        value.update(status="BUILDING", worker_attempt=job.attempt, building_generation_id=fence.graph_id,
            vector_generation_id=vector_id, error=None)
        row.payload_json = json_dumps(value)
        candidates, by_document = [], {}
        for item in bundle["files"]:
            key = "KDOC-" + digest([fence.operation_id, item["path"]])[:32]
            document = db.get(KnowledgeDocument, key)
            if document and (document.active or document.content or document.review_status != "DRAFT"
                    or json_loads(document.metadata_json, {}).get("reset_reservation") != fence.operation_id):
                raise ResetError("An imported document id is already occupied")
            if not document:
                db.add(KnowledgeDocument(id=key, title=item["path"], content="", active=False, review_status="DRAFT",
                    metadata_json=json_dumps({"reset_reservation": fence.operation_id})))
        db.flush()
        for item in bundle["files"]:
            key = "KDOC-" + digest([fence.operation_id, item["path"]])[:32]
            metadata = {"content_kind": "SKILL", "problem_categories": ["network"], "knowledge_role": item["role"],
                "bundle_id": row.id, "bundle_manifest": value["approved_plan"]["manifest"], "source_paths": [item["path"]],
                "reset_operation_id": fence.operation_id, "source_zip_sha256": bundle["source_sha256"],
                "original_sha256": item["source_sha256"], "approval_type": "HUMAN_DIRECT_IMPORT"}
            candidate = KnowledgeDocument(id=key, title=item["path"], source_type=SOURCE_TYPES[item["role"]],
                content=item["content"], version=1, lock_version=1, active=True, review_status="ACTIVE",
                confidentiality="INTERNAL", trust_level="HIGH", metadata_json=json_dumps(metadata))
            candidates.append(candidate)
            by_document[key] = []
            for index, (heading, content) in enumerate(chunk_document(candidate.content)):
                values = {"id": new_id("CHK"), "document_id": key, "chunk_index": index,
                    "heading": heading, "content": content, "token_estimate": max(1, len(content) // 3),
                    "metadata_json": json_dumps({"title": candidate.title, "source_type": candidate.source_type,
                        "reset_generation_id": fence.graph_id})}
                db.add(KnowledgeChunk(**values, document_version=0))
                by_document[key].append(KnowledgeChunk(**values, document_version=1))
        db.commit()
        return snapshot, value, candidates, by_document, vector_id


def _retire(db, snapshot, value):
    """Keep immutable bodies and references. Only the live projection is retired."""
    operation_id, reviewer = value["operation_id"], value["approved_by"]
    for document in snapshot["documents"]:
        revision = create_document_revision(db, document, created_by=reviewer,
            change_summary="Snapshot preserved before explicitly approved knowledge reset")
        if document.active and document.review_status == "ACTIVE":
            db.add(KnowledgePublication(id=new_id("KPUB"), document_id=document.id, document_version=document.version,
                published_by=reviewer, manifest_json=json_dumps({"schema_version": 1,
                    "reset_retirement": operation_id, "revision_id": revision.id,
                    "document_id": document.id, "document_version": document.version,
                    "content_hash": digest(document.content), "embedding_profile_id": snapshot["profile_id"],
                    "embedding_generation_id": snapshot["old_vector"], "graph_generation_id": snapshot["old_graph"]})))
        document.active, document.review_status = False, "ARCHIVED"
        document.lock_version += 1
        document.metadata_json = json_dumps({**json_loads(document.metadata_json, {}), "retired_by_reset": operation_id})
    for draft in snapshot["drafts"]:
        if draft.status not in {"PUBLISHED", "ARCHIVED", "REJECTED"}:
            draft.status, draft.building_generation_id = "ARCHIVED", None
    for memory in snapshot["memories"]:
        memory.review_status = "ARCHIVED"
        memory.review_comment = "Retired by explicitly approved knowledge reset: " + operation_id
    for template in snapshot["templates"]:
        previous = json_loads(template.payload_json, {})
        template.payload_json = json_dumps({"document_id": None, "retired_document_id": previous.get("document_id"),
            "reset_operation_id": operation_id})


def _publish(fence, approved, candidates, by_document, vector_id, graph_metadata):
    fence.raise_if_cancelled()
    with SessionLocal() as db:
        _transaction(db)
        row, value, job = fence.check(db)
        _assert_idle(db, job.id)
        snapshot = _corpus(db, fence.operation_id)
        if _summary(snapshot) != approved["approved_plan"]["baseline"]:
            raise ResetError("Knowledge or Embedding configuration changed during build")
        expected_chunks = sum(len(group) for group in by_document.values())
        actual = db.scalar(select(func.count()).select_from(KnowledgeEmbedding).where(
            KnowledgeEmbedding.profile_id == snapshot["profile_id"], KnowledgeEmbedding.generation_id == vector_id))
        if not expected_chunks or actual != expected_chunks:
            raise ResetError("The complete private vector generation is required before publication")
        _retire(db, snapshot, value)
        published = []
        for candidate in candidates:
            document = db.get(KnowledgeDocument, candidate.id)
            if not document or document.active or json_loads(document.metadata_json, {}).get("reset_reservation") != fence.operation_id:
                raise ResetError("A reserved import target changed during build")
            chunk_ids = [c.id for c in by_document[document.id]]
            moved = db.execute(update(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids),
                KnowledgeChunk.document_version == 0).values(document_version=1))
            if moved.rowcount != len(chunk_ids):
                raise ResetError("Private chunks changed during build")
            for key in ("title", "content", "source_type", "version", "active", "review_status", "confidentiality", "trust_level"):
                setattr(document, key, getattr(candidate, key))
            document.lock_version += 1
            document.metadata_json = json_dumps({**json_loads(candidate.metadata_json, {}),
                "embedding_generation_id": vector_id, "embedding_profile_id": snapshot["profile_id"],
                "embedding_status": "INDEXED", "embedding_vector_count": len(chunk_ids)})
            document.reviewed_by, document.reviewed_at, document.published_at = value["approved_by"], utcnow(), utcnow()
            bind_owner(db, document.id, value["approved_by"])
            access = db.get(KnowledgeAccess, document.id)
            if access:
                access.publisher_id = value["approved_by"]
            db.flush()
            revision = create_document_revision(db, document, created_by=value["approved_by"],
                change_summary="Human-approved complete Skill ZIP import; no AI reading attestation")
            db.add(KnowledgePublication(id=new_id("KPUB"), document_id=document.id, document_version=1,
                published_by=value["approved_by"], manifest_json=json_dumps({"schema_version": 1,
                    "document_id": document.id, "document_version": 1, "revision_id": revision.id,
                    "content_hash": digest(document.content), "approval_type": "HUMAN_DIRECT_IMPORT",
                    "reset_operation_id": fence.operation_id, "preview_hash": value["preview_hash"],
                    "source_zip_sha256": value["source_sha256"], "embedding_profile_id": snapshot["profile_id"],
                    "embedding_generation_id": vector_id, "graph_generation_id": fence.graph_id,
                    "document_versions": {d.id: 1 for d in candidates},
                    "chunk_ids": {key: [c.id for c in group] for key, group in by_document.items()}})))
            published.append(document.id)
            if json_loads(document.metadata_json, {})["knowledge_role"] == "report_template":
                template = db.get(WorkbenchRecord, "template-network")
                if not template:
                    template = WorkbenchRecord(id="template-network", kind="template_default", owner_id=value["approved_by"])
                    db.add(template)
                template.payload_json = json_dumps({"document_id": document.id, "reset_operation_id": fence.operation_id})
        profile = db.get(ModelProfile, snapshot["profile_id"])
        profile.active_embedding_generation_id = vector_id
        graph = db.get(KnowledgeGraphState, "domain")
        graph.active_generation_id, graph.building_generation_id, graph.status = fence.graph_id, None, "READY"
        graph.metadata_json, graph.error_message = json_dumps(graph_metadata), None
        result = {"operation_id": fence.operation_id, "documents": published, "retired_documents": len(snapshot["documents"]),
            "embedding_generation_id": vector_id, "graph_generation_id": fence.graph_id, "source_sha256": value["source_sha256"]}
        value.update(status="PUBLISHED", result=result, published_at=utcnow().isoformat(), building_generation_id=None)
        row.payload_json = json_dumps(value)
        db.add(AuditEvent(id=new_id("AUD"), actor_id=value["approved_by"], actor_type="user",
            action="knowledge.reset.published", resource_type=KIND, resource_id=row.id,
            details_json=json_dumps({"operation_id": fence.operation_id, "preview_hash": value["preview_hash"],
                "documents": len(published), "content_recorded": False})))
        db.flush()
        fence.ctx.complete_in_transaction(db, result, "Approved Skill corpus and both indexes published atomically")
        db.commit()
        return result


def _failed(fence, error):
    if isinstance(error, JobLeaseLostError):
        return
    with SessionLocal() as db:
        _transaction(db)
        row, value = read_operation(db, fence.operation_id)
        job = db.get(Job, value["job_id"])
        if (value["status"] == "PUBLISHED" or not job or job.lease_owner != fence.ctx.lease_owner
                or (fence.attempt is not None and job.attempt != fence.attempt)):
            return
        graph = db.get(KnowledgeGraphState, "domain")
        if graph and value.get("building_generation_id") and graph.building_generation_id == value["building_generation_id"]:
            graph.building_generation_id = None
            graph.status = "READY" if graph.active_generation_id else "NOT_BUILT"
        value.update(status="CANCELLED" if isinstance(error, JobCancelledError) else "FAILED",
            building_generation_id=None, error="Reset did not publish; old corpus, backup and human approval are retained")
        row.payload_json = json_dumps(value)
        db.commit()


def recover_abandoned_resets(db) -> int:
    """Parent dispatcher hook: release terminal workers' latches, keep approval.

    QUEUED/RUNNING operations retain their generation ownership for reset_job to
    resume. This helper neither queues work nor commits its caller's transaction.
    """
    recovered = 0
    for row in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == KIND)):
        value = json_loads(row.payload_json, {})
        if value.get("status") != "BUILDING":
            continue
        changed = db.execute(update(WorkbenchRecord).where(WorkbenchRecord.id == row.id,
            WorkbenchRecord.version == row.version).values(version=row.version)
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            continue
        status = db.scalar(select(Job.status).where(Job.id == value.get("job_id")))
        if status in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
            continue
        graph = db.get(KnowledgeGraphState, "domain")
        if graph and value.get("building_generation_id") and graph.building_generation_id == value["building_generation_id"]:
            graph.building_generation_id = None
            graph.status = "READY" if graph.active_generation_id else "NOT_BUILT"
        value.update(status="CANCELLED" if status == "CANCELLED" else "FAILED", building_generation_id=None,
            error="Terminal reset worker released; old corpus and approval retained")
        row.payload_json = json_dumps(value)
        recovered += 1
    return recovered


def reset_job(ctx, operation_id: str):
    """Retry only this approved operation; a fresh attempt uses private generations."""
    fence = ResetFence(ctx, operation_id)
    try:
        snapshot, value, candidates, by_document, vector_id = _prepare(fence)
        fence.update(15, "Building private indexes for all six human-approved Skill files")
        with SessionLocal() as db:
            profile = db.get(ModelProfile, snapshot["profile_id"])
            if not profile or _fingerprint(profile) != snapshot["profile_hash"]:
                raise ResetError("The approved Embedding profile changed before indexing")
            if profile.mode == "api" and profile.provider != MANAGED_LOCAL_PROVIDER and not value["model_egress_approved"]:
                raise ResetError("Embedding egress was not approved")
            count = index_embeddings(db, profile, [c for group in by_document.values() for c in group],
                generation_id=vector_id, activate_if_missing=False,
                progress=lambda done, total: fence.update(15 + int(50 * done / max(1, total)), "Building private vectors"))
        if count != sum(len(group) for group in by_document.values()):
            raise ResetError("Embedding did not cover every imported chunk")
        fence.update(70, "Building a private graph; original knowledge remains active")
        graph = stage_graph(SessionLocal, candidates, by_document, fence.graph_id, fence)
        return _publish(fence, value, candidates, by_document, vector_id, graph)
    except (JobCancelledError, JobLeaseLostError) as error:
        _failed(fence, error)
        raise
    except Exception as error:
        _failed(fence, error)
        raise ResetError("Knowledge reset did not publish; the existing corpus is unchanged") from None
