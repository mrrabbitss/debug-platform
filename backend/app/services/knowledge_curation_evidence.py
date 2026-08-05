from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.utils import json_dumps, json_loads, mask_sensitive, sha256_file
from app.models import KnowledgeCurationSession, KnowledgeCurationSourceFile
from app.services.curation_document_sandbox import prepare_curation_document_sandboxed
from app.services.curation_documents import DocumentExtractionError
from app.services.knowledge_curation_common import CurationError
from app.services.knowledge_methods import parse_markdown_sections
from app.services.storage import StorageService, storage
from app.services.text_files import open_text_lines


EVIDENCE_FILE_NAME = "evidence_for_model.md"
SOURCE_CITATION_PATTERN = re.compile(
    r"\[(?P<ref>SRC-\d+)(?::L(?P<start>\d+)(?:-L?(?P<end>\d+))?)?\]"
)
KEY_EVIDENCE_PATTERN = re.compile(
    r"(?i)(error|warn|fail|critical|exception|traceback|root\s*cause|fault|alarm|"
    r"故障|错误|异常|失败|告警|根因|原因|结论|解决|修复|方案|验证|回退)"
)
ROLE_PRIORITY = {
    "error": 0,
    "analysis": 1,
    "solution": 2,
    "log": 3,
    "context": 4,
}


def validate_curation_markdown(
    markdown: str,
    valid_source_refs: dict[str, int | None],
) -> dict[str, Any]:
    structure = parse_markdown_sections(markdown)
    citations = list(SOURCE_CITATION_PATTERN.finditer(markdown))
    line_citation_count = sum(1 for match in citations if match.group("start"))
    cited_refs = sorted({match.group("ref") for match in citations})
    invalid_refs = [
        source_ref for source_ref in cited_refs if source_ref not in valid_source_refs
    ]
    valid_refs = [
        source_ref for source_ref in cited_refs if source_ref in valid_source_refs
    ]
    invalid_line_citations: list[str] = []
    for match in citations:
        source_ref = match.group("ref")
        if source_ref not in valid_source_refs:
            continue
        start = int(match.group("start")) if match.group("start") else None
        end = int(match.group("end")) if match.group("end") else start
        line_count = valid_source_refs[source_ref]
        if start is None:
            continue
        if start < 1 or end is None or end < start or (line_count and end > line_count):
            invalid_line_citations.append(match.group(0))
    has_source_section = bool(re.search(
        r"(?im)^#{2,6}\s+.*(?:来源证据|证据来源|source evidence)",
        markdown,
    ))
    warnings: list[str] = []
    if structure["missing_sections"]:
        warnings.append("缺少必需章节：" + "、".join(structure["missing_sections"]))
    if not has_source_section:
        warnings.append("缺少“来源证据”章节")
    if not valid_refs:
        warnings.append("正文没有引用有效来源，至少需要一个 [SRC-xxxx:Lx-Ly] 引用")
    elif line_citation_count == 0:
        warnings.append("来源引用必须包含可核对的行号，例如 [SRC-0001:L10-L20]")
    if invalid_refs:
        warnings.append("存在无效来源引用：" + "、".join(invalid_refs))
    if invalid_line_citations:
        warnings.append("存在越界或无效行号引用：" + "、".join(invalid_line_citations))
    confirmable = bool(
        structure["complete"]
        and has_source_section
        and valid_refs
        and line_citation_count > 0
        and not invalid_refs
        and not invalid_line_citations
    )
    return {
        "format": "llm_curated_fault_case_v1",
        "structure": structure,
        "cited_source_refs": valid_refs,
        "invalid_source_refs": invalid_refs,
        "invalid_line_citations": invalid_line_citations,
        "citation_count": len(citations),
        "line_citation_count": line_citation_count,
        "has_source_section": has_source_section,
        "warnings": warnings,
        "confirmable": confirmable,
    }


def _sample_source_text(path: Path, max_chars: int) -> tuple[str, str, int] | None:
    opened = open_text_lines(path)
    if opened is None:
        return None
    encoding, lines = opened
    head: list[tuple[int, str]] = []
    tail: deque[tuple[int, str]] = deque(maxlen=50)
    matches: list[tuple[int, str]] = []
    complete: list[tuple[int, str]] = []
    complete_chars = 0
    complete_overflow = False
    line_count = 0
    for line_count, line in enumerate(lines, start=1):
        clipped = line[:4000]
        if line_count <= 80:
            head.append((line_count, clipped))
        tail.append((line_count, clipped))
        if len(matches) < 160 and KEY_EVIDENCE_PATTERN.search(clipped):
            matches.append((line_count, clipped))
        if not complete_overflow:
            complete_chars += len(clipped) + 16
            if complete_chars <= max_chars:
                complete.append((line_count, clipped))
            else:
                complete_overflow = True
                complete.clear()

    selected = complete if not complete_overflow else sorted(
        {line_number: text for line_number, text in [*head, *matches, *tail]}.items()
    )
    rendered: list[str] = []
    rendered_chars = 0
    previous_line = 0
    for line_number, text in selected:
        if previous_line and line_number > previous_line + 1:
            marker = f"... omitted lines {previous_line + 1}-{line_number - 1} ..."
            if rendered_chars + len(marker) + 1 > max_chars:
                break
            rendered.append(marker)
            rendered_chars += len(marker) + 1
        entry = f"L{line_number}: {text}"
        if rendered_chars + len(entry) + 1 > max_chars:
            break
        rendered.append(entry)
        rendered_chars += len(entry) + 1
        previous_line = line_number
    return "\n".join(rendered), encoding, line_count


def build_evidence_bundle(
    db: Session,
    session: KnowledgeCurationSession,
    *,
    storage_service: StorageService = storage,
) -> tuple[str, dict[str, Any]]:
    settings = get_settings()
    sources = list(db.scalars(
        select(KnowledgeCurationSourceFile).where(
            KnowledgeCurationSourceFile.session_id == session.id
        )
    ).all())
    sources.sort(key=lambda source: (
        ROLE_PRIORITY.get(source.source_role, 99),
        source.relative_path.casefold(),
    ))
    remaining = settings.curation_max_prompt_chars
    evidence_parts: list[str] = []
    selected_refs: list[str] = []
    skipped_refs: list[str] = []
    extracted_refs: list[str] = []
    for index, source in enumerate(sources):
        source_path = storage_service.resolve_path(source.stored_path)
        if not source_path.is_file():
            raise CurationError(
                f"Source file is missing from local storage: {source.source_ref}"
            )
        if not hmac.compare_digest(sha256_file(source_path), source.sha256):
            raise CurationError(
                f"Source file failed its integrity check: {source.source_ref}"
            )
        extracted_path = (
            storage_service.curation_dir(session.id) / "extracted" / f"{source.id}.txt"
        )
        source.extracted_text_path = None
        source.extracted_text_sha256 = None
        source.extraction_method = None
        source.extraction_truncated = False
        source.page_count = None
        try:
            prepared = prepare_curation_document_sandboxed(
                source_path,
                source.relative_path,
                extracted_path,
            )
        except DocumentExtractionError as exc:
            source.included = False
            source.skip_reason = exc.code
            source.text_encoding = None
            source.line_count = None
            skipped_refs.append(source.source_ref)
            continue
        if prepared is not None:
            source_path = prepared.path
            source.extracted_text_path = storage_service.storage_key(prepared.path)
            source.extracted_text_sha256 = prepared.sha256
            source.extraction_method = prepared.method
            source.extraction_truncated = prepared.truncated
            source.page_count = prepared.page_count
            extracted_refs.append(source.source_ref)
        else:
            source.extraction_method = "plain_text"
        if remaining < 1000:
            source.included = False
            source.skip_reason = "prompt_budget_exhausted"
            if prepared is not None:
                source.text_encoding = "utf-8"
                source.line_count = prepared.line_count
            skipped_refs.append(source.source_ref)
            continue
        remaining_sources = max(1, len(sources) - index)
        per_file_budget = min(24_000, max(1200, remaining // remaining_sources))
        sampled = _sample_source_text(source_path, per_file_budget)
        if sampled is None:
            source.included = False
            source.skip_reason = "binary_or_unsupported_text_encoding"
            source.text_encoding = None
            source.line_count = None
            skipped_refs.append(source.source_ref)
            continue
        excerpt, encoding, line_count = sampled
        source.included = True
        source.skip_reason = None
        source.text_encoding = encoding
        source.line_count = line_count
        if not excerpt.strip():
            source.included = False
            source.skip_reason = "empty_text_file"
            skipped_refs.append(source.source_ref)
            continue
        header = (
            f"## {source.source_ref} | {source.relative_path} | "
            f"role={source.source_role} | extraction={source.extraction_method} | "
            f"pages={source.page_count or '-'} | truncated={source.extraction_truncated} | "
            f"raw_sha256={source.sha256}\n"
        )
        part = header + mask_sensitive(excerpt)
        if len(part) > remaining:
            part = part[:remaining]
        evidence_parts.append(part)
        selected_refs.append(source.source_ref)
        remaining -= len(part) + 2

    bundle = "\n\n".join(evidence_parts)
    evidence_path = storage_service.curation_dir(session.id) / EVIDENCE_FILE_NAME
    temporary_path = evidence_path.with_suffix(".tmp")
    temporary_path.write_text(bundle, encoding="utf-8")
    os.replace(temporary_path, evidence_path)
    manifest = json_loads(session.source_manifest_json, {})
    manifest.update({
        "selected_source_refs": selected_refs,
        "skipped_source_refs": skipped_refs,
        "document_extracted_source_refs": extracted_refs,
        "evidence_chars": len(bundle),
        "evidence_sha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest(),
        "evidence_storage_key": storage_service.storage_key(evidence_path),
        "sensitive_masking": True,
        "prompt_limit_chars": settings.curation_max_prompt_chars,
    })
    session.source_manifest_json = json_dumps(manifest)
    db.commit()
    if not selected_refs:
        raise CurationError("No readable text files were found in the selected folder")
    return bundle, manifest


def source_refs(db: Session, session_id: str) -> dict[str, int | None]:
    return {
        source_ref: line_count
        for source_ref, line_count in db.execute(
            select(
                KnowledgeCurationSourceFile.source_ref,
                KnowledgeCurationSourceFile.line_count,
            ).where(
                KnowledgeCurationSourceFile.session_id == session_id,
                KnowledgeCurationSourceFile.included.is_(True),
            )
        ).all()
    }
