"""Content-safe parent adapter for the isolated document parser process."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.core.config import BACKEND_ROOT, get_settings
from app.core.utils import sha256_file
from app.services.curation_documents import (
    HTML_SUFFIXES,
    LEGACY_WORD_SUFFIXES,
    PDF_SUFFIXES,
    WORD_SUFFIXES,
    DocumentExtractionError,
    PreparedCurationDocument,
)
from app.services.process_sandbox import SandboxError, SandboxTimeout, run_sandboxed


DOCUMENT_SUFFIXES = HTML_SUFFIXES | LEGACY_WORD_SUFFIXES | PDF_SUFFIXES | WORD_SUFFIXES


def prepare_curation_document_sandboxed(
    source_path: Path,
    relative_path: str,
    destination: Path,
) -> PreparedCurationDocument | None:
    if Path(relative_path).suffix.casefold() not in DOCUMENT_SUFFIXES:
        return None
    settings = get_settings()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        result = run_sandboxed(
            [
                sys.executable,
                "-m",
                "app.services.curation_document_worker",
                "--source",
                str(source_path.resolve()),
                "--relative-path",
                relative_path,
                "--destination",
                str(destination.resolve()),
            ],
            cwd=BACKEND_ROOT,
            timeout_seconds=settings.curation_document_timeout_seconds,
            memory_bytes=settings.curation_document_memory_bytes,
            cpu_seconds=settings.curation_document_cpu_seconds,
            env=os.environ,
        )
    except SandboxTimeout as exc:
        destination.unlink(missing_ok=True)
        raise DocumentExtractionError(
            "document_extraction_timeout",
            "Document extraction exceeded its time limit",
        ) from exc
    except SandboxError as exc:
        destination.unlink(missing_ok=True)
        raise DocumentExtractionError(
            "document_sandbox_failed",
            "Document extraction sandbox could not be started",
        ) from exc

    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        destination.unlink(missing_ok=True)
        raise DocumentExtractionError(
            "document_worker_invalid_response",
            "Document extraction worker returned an invalid response",
        ) from exc
    if not payload.get("ok"):
        destination.unlink(missing_ok=True)
        raise DocumentExtractionError(
            str(payload.get("code") or "document_extraction_failed")[:128],
            str(payload.get("message") or "Document extraction failed")[:1000],
        )
    item = payload.get("result")
    if item is None:
        return None
    if result.returncode != 0 or not destination.is_file():
        destination.unlink(missing_ok=True)
        raise DocumentExtractionError(
            "document_worker_failed",
            "Document extraction worker did not publish an output file",
        )
    digest = sha256_file(destination)
    if digest != item.get("sha256"):
        destination.unlink(missing_ok=True)
        raise DocumentExtractionError(
            "document_worker_integrity_failed",
            "Document extraction output failed its integrity check",
        )
    return PreparedCurationDocument(
        path=destination,
        method=str(item["method"]),
        sha256=digest,
        line_count=int(item["line_count"]),
        page_count=(int(item["page_count"]) if item.get("page_count") is not None else None),
        truncated=bool(item.get("truncated")),
    )
