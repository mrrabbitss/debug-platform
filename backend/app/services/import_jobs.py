from pathlib import Path
from typing import Any

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads
from app.models import Artifact, KnowledgeDocument, Repository
from app.services.archive import extract_archive
from app.services.git_repository import (
    clone_git_bundle,
    find_git_worktree_root,
    repository_head,
    validate_git_worktree,
)
from app.services.jobs import JobCancelledError, JobContext
from app.services.knowledge import index_document
from app.services.storage import storage


def _mark_repository_import_failure(
    repository_id: str,
    *,
    cancelled: bool,
    error: str,
) -> None:
    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if not repository:
            return
        artifact = db.get(Artifact, repository.artifact_id)
        repository.status = "IMPORT_CANCELLED" if cancelled else "IMPORT_FAILED"
        metadata = json_loads(repository.index_metadata_json, {})
        metadata["import_error"] = error
        repository.index_metadata_json = json_dumps(metadata)
        if artifact:
            artifact.status = repository.status
        db.commit()


def import_repository_job(
    ctx: JobContext,
    repository_id: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        repository = db.get(Repository, repository_id)
        if not repository:
            raise ValueError("Repository not found")
        artifact = db.get(Artifact, repository.artifact_id)
        if not artifact:
            raise ValueError("Repository source artifact not found")
        source_path = storage.resolve_path(artifact.stored_path)
        uploaded_name = artifact.original_name
        repository.status = "IMPORTING"
        artifact.status = "IMPORTING"
        db.commit()

    try:
        ctx.update(5, "Preparing repository import")
        try:
            storage.remove_repository(repository_id)
        except (OSError, ValueError):
            pass
        destination = storage.repository_dir(repository_id)
        if uploaded_name.lower().endswith(".bundle"):
            ctx.update(15, "Cloning Git bundle")
            git_manifest = clone_git_bundle(source_path, destination)
            source_root = git_manifest.root
            file_count = git_manifest.file_count
            extracted_bytes = git_manifest.total_bytes
            branch = git_manifest.branch
            commit_hash = git_manifest.commit_hash
            import_format = "git_bundle"
        else:
            ctx.update(15, "Extracting repository archive")
            manifest = extract_archive(source_path, destination)
            detected_git_root = find_git_worktree_root(destination)
            if detected_git_root:
                validate_git_worktree(detected_git_root)
                branch, commit_hash = repository_head(detected_git_root)
                source_root = detected_git_root
            else:
                branch, commit_hash = None, None
                source_root = destination
            file_count = len(manifest.files)
            extracted_bytes = manifest.total_bytes
            import_format = "archive"
        ctx.raise_if_cancelled()
        ctx.update(90, "Publishing imported repository")
        result = {
            "repository_id": repository_id,
            "artifact_id": artifact.id,
            "files": file_count,
            "extracted_bytes": extracted_bytes,
            "import_format": import_format,
            "git_history_available": commit_hash is not None,
            "branch": branch,
            "commit_hash": commit_hash,
        }
        with SessionLocal() as db:
            repository = db.get(Repository, repository_id)
            artifact = db.get(Artifact, artifact.id)
            if not repository or not artifact:
                raise ValueError("Repository import target was deleted")
            repository.root_path = storage.storage_key(source_root)
            repository.branch = branch
            repository.commit_hash = commit_hash
            repository.status = "UPLOADED"
            repository.graph_status = "NOT_INDEXED"
            repository.commit_graph_status = (
                "NOT_INDEXED" if commit_hash else "UNAVAILABLE"
            )
            metadata = json_loads(repository.index_metadata_json, {})
            metadata.update({
                "manifest_file_count": file_count,
                "extracted_bytes": extracted_bytes,
                "import_format": import_format,
                "git_history_available": commit_hash is not None,
            })
            metadata.pop("import_error", None)
            repository.index_metadata_json = json_dumps(metadata)
            artifact.status = "EXTRACTED"
            artifact_metadata = json_loads(artifact.metadata_json, {})
            artifact_metadata.update(metadata)
            artifact.metadata_json = json_dumps(artifact_metadata)
            ctx.complete_in_transaction(
                db,
                result,
                message="Repository import completed",
            )
            db.commit()
        return result
    except Exception as exc:
        cancelled = isinstance(exc, JobCancelledError)
        _mark_repository_import_failure(
            repository_id,
            cancelled=cancelled,
            error=str(exc) or type(exc).__name__,
        )
        try:
            storage.remove_repository(repository_id)
        except (OSError, ValueError):
            pass
        raise


def _mark_knowledge_import_failure(
    document_id: str,
    artifact_id: str,
    *,
    cancelled: bool,
    error: str,
) -> None:
    with SessionLocal() as db:
        document = db.get(KnowledgeDocument, document_id)
        artifact = db.get(Artifact, artifact_id)
        status = "IMPORT_CANCELLED" if cancelled else "IMPORT_FAILED"
        if document:
            metadata = json_loads(document.metadata_json, {})
            metadata["import_status"] = status
            metadata["import_error"] = error
            document.metadata_json = json_dumps(metadata)
            document.active = False
        if artifact:
            artifact.status = status
        db.commit()


def import_knowledge_job(
    ctx: JobContext,
    document_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    with SessionLocal() as db:
        document = db.get(KnowledgeDocument, document_id)
        artifact = db.get(Artifact, artifact_id)
        if not document or not artifact:
            raise ValueError("Knowledge import target not found")
        source_path = storage.resolve_path(artifact.stored_path)
        metadata = json_loads(document.metadata_json, {})
        metadata["import_status"] = "IMPORTING"
        document.metadata_json = json_dumps(metadata)
        artifact.status = "IMPORTING"
        db.commit()

    try:
        ctx.update(10, "Reading knowledge document")
        content = Path(source_path).read_text(encoding="utf-8", errors="replace")
        ctx.raise_if_cancelled()
        with SessionLocal() as db:
            document = db.get(KnowledgeDocument, document_id)
            if not document:
                raise ValueError("Knowledge document was deleted")
            document.content = content
            metadata = json_loads(document.metadata_json, {})
            metadata["import_status"] = "INDEXING"
            metadata["source_bytes"] = artifact.size_bytes
            document.metadata_json = json_dumps(metadata)
            db.commit()
            ctx.update(35, "Chunking and embedding knowledge document")
            chunk_count = index_document(db, document)
            ctx.raise_if_cancelled()
            document = db.get(KnowledgeDocument, document_id)
            artifact = db.get(Artifact, artifact_id)
            if not document or not artifact:
                raise ValueError("Knowledge import target was deleted")
            metadata = json_loads(document.metadata_json, {})
            metadata["import_status"] = "INDEXED"
            metadata["chunk_count"] = chunk_count
            metadata.pop("import_error", None)
            document.metadata_json = json_dumps(metadata)
            document.active = True
            artifact.status = "INDEXED"
            result = {
                "document_id": document_id,
                "artifact_id": artifact_id,
                "chunks": chunk_count,
            }
            ctx.complete_in_transaction(
                db,
                result,
                message="Knowledge import completed",
            )
            db.commit()
        return result
    except Exception as exc:
        _mark_knowledge_import_failure(
            document_id,
            artifact_id,
            cancelled=isinstance(exc, JobCancelledError),
            error=str(exc) or type(exc).__name__,
        )
        raise
