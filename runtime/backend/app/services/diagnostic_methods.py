from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Case, KnowledgeDocument
from app.services.diagnostic_scope import knowledge_matches_joint_diagnostic_scope
from app.services.text_files import read_text_file


DIAGNOSTIC_SOURCE_TYPES = frozenset({
    "fault_tree",
    "analysis_method",
    "analysis_skill",
    "log_rule",
    "diagnostic_rule",
    "fault_case",
    "historical_bug",
})
LOG_METHOD_SOURCE_TYPES = frozenset({
    "analysis_method",
    "analysis_skill",
    "log_rule",
    "diagnostic_rule",
    "fault_tree",
})
_METHOD_GENERATION_SCHEMA = "gw-ap-debug-method-generation/v1"
_METHOD_ACTIVE_POINTER_SCHEMA = "gw-ap-debug-method-active/v1"
_METHOD_BINDING_SCHEMA = "gw-ap-debug-method-binding/v1"
_METHOD_FILENAMES = ("故障树.md", "日志分析.md")
_INLINE_CODE = re.compile(r"`([^`\r\n]{2,500})`")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_PLACEHOLDER = re.compile(r"%(?:[-+0-9.*hlLzjt]*[diuoxXfFeEgGaAcsp])")
_LOG_HINTS = (
    "日志",
    "关键字",
    "关键词",
    "格式",
    "特征",
    "必查",
    "pattern",
    "keyword",
    "signature",
    "log",
)
_MEANING_HINTS = (
    "含义",
    "说明",
    "意义",
    "解释",
    "判断",
    "结论",
    "用途",
    "结果",
    "meaning",
    "description",
)
_LOCAL_METHOD_FILES = {
    "故障树.md": ("fault_tree", "FAULT_TREE"),
    "日志分析.md": ("analysis_skill", "LOG_ANALYSIS_METHOD"),
}


@dataclass(frozen=True)
class DiagnosticMethodDocument:
    id: str
    title: str
    source_type: str
    version: int
    device_type: str | None
    module: str | None
    content: str
    content_sha256: str
    role: str

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = asdict(self)
        snapshot.pop("content", None)
        return snapshot


@dataclass(frozen=True)
class DiagnosticPattern:
    id: str
    text: str
    match_kind: str
    regex: str
    document_id: str
    document_title: str
    document_version: int
    source_type: str
    heading: str
    line_start: int
    reason: str
    meaning: str

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = asdict(self)
        snapshot.pop("regex", None)
        return snapshot


def _document_role(source_type: str) -> str:
    if source_type == "fault_tree":
        return "FAULT_TREE"
    if source_type in LOG_METHOD_SOURCE_TYPES:
        return "LOG_ANALYSIS_METHOD"
    return "REFERENCE_CASE"


def _canonical_json_sha256(payload: Any) -> str:
    rendered = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _method_generation_identity(
    scope: str,
    role_hashes: dict[str, str],
    packs: Any,
) -> dict[str, Any]:
    if scope not in {"persistent", "run"}:
        raise ValueError("Diagnostic method generation scope is invalid")
    if set(role_hashes) != set(_METHOD_FILENAMES) or any(
        not re.fullmatch(r"[0-9a-f]{64}", str(role_hashes.get(filename) or ""))
        for filename in _METHOD_FILENAMES
    ):
        raise ValueError("Diagnostic method generation role identity is invalid")
    if not isinstance(packs, list):
        raise ValueError("Diagnostic method generation packs are invalid")
    projected: list[dict[str, str]] = []
    for pack in packs:
        if not isinstance(pack, dict):
            raise ValueError("Diagnostic method generation pack identity is invalid")
        item = {
            "id": str(pack.get("id") or ""),
            "name": str(pack.get("name") or ""),
            "content_sha256": str(pack.get("content_sha256") or ""),
        }
        if (
            not item["id"]
            or not item["name"]
            or not re.fullmatch(r"[0-9a-f]{64}", item["content_sha256"])
        ):
            raise ValueError("Diagnostic method generation pack identity is incomplete")
        projected.append(item)
    projected.sort(
        key=lambda item: (
            item["name"].casefold(),
            item["id"],
            item["content_sha256"],
        )
    )
    return {"scope": scope, "roles": dict(role_hashes), "packs": projected}


def _read_method_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Diagnostic method control file is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Diagnostic method control file must be an object: {path}")
    return payload


def _validated_generation_root(
    control_root: Path,
    selector: dict[str, Any],
    *,
    expected_scope: str,
) -> Path:
    generation_id = str(selector.get("generation_id") or "")
    relative = Path(str(selector.get("path") or ""))
    if not re.fullmatch(r"gen-[0-9a-f]{20}", generation_id):
        raise ValueError("Diagnostic method generation ID is invalid")
    if relative.is_absolute() or relative.as_posix() != f"generations/{generation_id}":
        raise ValueError("Diagnostic method generation path is invalid")
    generation_root = (control_root / relative).resolve()
    generations_root = (control_root / "generations").resolve()
    try:
        generation_root.relative_to(generations_root)
    except ValueError as exc:
        raise ValueError("Diagnostic method generation escapes its control root") from exc
    manifest_path = generation_root / "manifest.json"
    manifest = _read_method_json(manifest_path)
    if (
        manifest.get("schema") != _METHOD_GENERATION_SCHEMA
        or manifest.get("id") != generation_id
        or manifest.get("scope") != expected_scope
        or not hmac.compare_digest(
            str(selector.get("manifest_sha256") or ""),
            _canonical_json_sha256(manifest),
        )
    ):
        raise ValueError("Diagnostic method generation manifest validation failed")
    roles = manifest.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(_METHOD_FILENAMES):
        raise ValueError("Diagnostic method generation role set is invalid")
    role_hashes = {
        filename: str((roles.get(filename) or {}).get("sha256") or "")
        for filename in _METHOD_FILENAMES
    }
    identity = _method_generation_identity(
        str(manifest.get("scope") or ""),
        role_hashes,
        manifest.get("packs"),
    )
    expected_generation_id = f"gen-{_canonical_json_sha256(identity)[:20]}"
    if manifest.get("packs") != identity["packs"] or not hmac.compare_digest(
        generation_id,
        expected_generation_id,
    ):
        raise ValueError("Diagnostic method generation content identity is invalid")
    registry = manifest.get("registry")
    if expected_scope == "run":
        if registry is not None:
            raise ValueError("Run-scoped diagnostic method generation contains a registry")
    else:
        if not isinstance(registry, dict) or registry.get("schema") != "gw-ap-debug-method-pack-registry/v2":
            raise ValueError("Persistent diagnostic method generation registry is invalid")
        if registry.get("active") != role_hashes or registry.get("active_generation") != {
            "id": generation_id,
            "path": f"generations/{generation_id}",
        }:
            raise ValueError("Persistent diagnostic method generation registry selector is invalid")
        registry_identity = _method_generation_identity(
            expected_scope,
            role_hashes,
            registry.get("packs"),
        )
        if registry_identity["packs"] != identity["packs"]:
            raise ValueError("Persistent diagnostic method generation pack registry is invalid")
    for filename in _METHOD_FILENAMES:
        path = generation_root / filename
        decoded = read_text_file(path)
        if decoded is None:
            raise ValueError(f"Diagnostic method generation role is unreadable: {path}")
        content = decoded.replace("\r\n", "\n").replace("\r", "\n")
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expected = str((roles.get(filename) or {}).get("sha256") or "")
        if not expected or not hmac.compare_digest(actual, expected):
            raise ValueError(f"Diagnostic method generation role hash mismatch: {path}")
    return generation_root


def resolve_case_method_root(configured_root: Path, case: Case) -> Path:
    """Resolve one immutable method pair for the case, then fall back to v0.5 files."""
    control_root = configured_root.expanduser().resolve()
    binding_name = hashlib.sha256(str(case.id).encode("utf-8")).hexdigest()[:24]
    binding_path = control_root / "bindings" / f"case-{binding_name}.json"
    if binding_path.is_file():
        binding = _read_method_json(binding_path)
        if binding.get("schema") != _METHOD_BINDING_SCHEMA or binding.get("case_id") != case.id:
            raise ValueError("Diagnostic method case binding is invalid")
        expires = binding.get("expires_at_epoch")
        if (
            isinstance(expires, bool)
            or not isinstance(expires, (int, float))
            or not math.isfinite(float(expires))
        ):
            raise ValueError("Diagnostic method case binding expiry is invalid")
        if float(expires) > time.time():
            return _validated_generation_root(
                control_root, binding, expected_scope="run",
            )
    active_path = control_root / "active.json"
    if active_path.is_file():
        active = _read_method_json(active_path)
        if active.get("schema") != _METHOD_ACTIVE_POINTER_SCHEMA:
            raise ValueError("Diagnostic method active pointer is invalid")
        return _validated_generation_root(
            control_root, active, expected_scope="persistent",
        )
    return control_root


def load_applicable_diagnostic_methods(
    db: Session,
    case: Case,
) -> list[DiagnosticMethodDocument]:
    rows = list(db.scalars(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.active.is_(True),
            KnowledgeDocument.review_status == "ACTIVE",
            KnowledgeDocument.source_type.in_(DIAGNOSTIC_SOURCE_TYPES),
        )
        .order_by(KnowledgeDocument.source_type, KnowledgeDocument.title, KnowledgeDocument.id)
    ).all())
    result: list[DiagnosticMethodDocument] = []
    for row in rows:
        # A managed WLAN is one diagnostic system: an AP symptom may originate
        # from its primary GW and a GW symptom may originate from an AP. Keep
        # ordinary knowledge search device-scoped, but force diagnostic method
        # coverage across both sides plus shared knowledge.
        if not knowledge_matches_joint_diagnostic_scope(row.device_type):
            continue
        content = row.content.replace("\r\n", "\n").replace("\r", "\n")
        result.append(DiagnosticMethodDocument(
            id=row.id,
            title=row.title,
            source_type=row.source_type,
            version=row.version,
            device_type=row.device_type,
            module=row.module,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            role=_document_role(row.source_type),
        ))
    known_hashes = {document.content_sha256 for document in result}
    configured_method_root = os.environ.get("DIAGNOSTIC_METHODS_ROOT")
    repository_root = (
        Path(configured_method_root).expanduser().resolve()
        if configured_method_root
        else Path(__file__).resolve().parents[3]
    )
    repository_root = resolve_case_method_root(repository_root, case)
    for filename, (source_type, role) in _LOCAL_METHOD_FILES.items():
        path = repository_root / filename
        if not path.is_file():
            continue
        decoded = read_text_file(path)
        if decoded is None:
            continue
        content = decoded.replace("\r\n", "\n").replace("\r", "\n")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash in known_hashes:
            continue
        result.append(DiagnosticMethodDocument(
            id=f"LOCALDOC-{content_hash[:20]}",
            title=path.stem,
            source_type=source_type,
            version=1,
            device_type=None,
            module=None,
            content=content,
            content_sha256=content_hash,
            role=role,
        ))
        known_hashes.add(content_hash)
    case_scope = str(case.device_type or "").strip().upper()
    result.sort(key=lambda item: (
        0 if str(item.device_type or "").strip().upper() == case_scope else
        1 if str(item.device_type or "").strip().upper() in {"GENERAL", "OTHER", ""} else 2,
        item.source_type,
        item.title,
        item.id,
    ))
    return result


def _clean_candidate(value: str) -> str:
    candidate = value.strip()
    # Markdown is presentation, not part of the runtime log signature. Peel
    # nested emphasis/code wrappers so table cells such as **`message %u`**
    # compile to the message emitted by the device.
    for _ in range(4):
        previous = candidate
        candidate = re.sub(r"^(?:\*\*|__)(.*?)(?:\*\*|__)$", r"\1", candidate).strip()
        candidate = candidate.strip("`'\"“”‘’")
        if candidate == previous:
            break
    candidate = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if ":" in candidate and len(candidate.split(":", 1)[0]) <= 18:
        label, possible = candidate.split(":", 1)
        if any(hint in label.lower() for hint in _LOG_HINTS):
            candidate = possible.strip()
    return candidate


def _candidate_is_pattern(value: str) -> bool:
    if not (2 <= len(value) <= 500):
        return False
    if value.lower().startswith(("http://", "https://")):
        return False
    if value in {"是", "否", "正常", "异常", "无", "未知"}:
        return False
    # Huawei command output and runtime signatures commonly end in "!".
    # Only discard clear Chinese prose punctuation here; Markdown placement
    # and log-shape checks below still prevent arbitrary sentences becoming
    # scan patterns.
    if value.endswith(("。", "？")) and not _PLACEHOLDER.search(value):
        return False
    has_log_shape = bool(re.search(r"[A-Za-z_][A-Za-z0-9_.:/-]{2,}|%[a-zA-Z]|\[[Xx0-9/]+\]", value))
    has_chinese_keyword = len(value) <= 80 and any(
        token in value for token in ("失败", "超时", "离线", "上线", "下线", "异常", "心跳", "拓扑")
    )
    return has_log_shape or has_chinese_keyword


def _pattern_regex(value: str) -> tuple[str, str]:
    placeholders: list[tuple[str, str]] = []

    def remember(regex: str) -> str:
        token = f"\x00P{len(placeholders)}\x00"
        placeholders.append((token, regex))
        return token

    prepared = _PLACEHOLDER.sub(lambda match: remember(
        r"\S+" if match.group(0)[-1].lower() in {"s", "c", "p"} else r"[-+]?\d+(?:\.\d+)?"
    ), value)
    prepared = re.sub(
        r"\[(?:X{1,8}|Y{1,8}|Z{1,8}|x{1,8}|y{1,8}|z{1,8})\]",
        lambda _: remember(r"\[[^\]\r\n]+\]"),
        prepared,
    )
    prepared = re.sub(
        r"(?<![A-Za-z0-9_])(?:X{1,8}|Y{1,8}|Z{1,8}|x{2,8}|y{2,8}|z{2,8})(?![A-Za-z0-9_])",
        lambda _: remember(r"\S+"),
        prepared,
    )
    rendered = re.escape(prepared).replace(r"\ ", r"\s+")
    for token, regex in placeholders:
        rendered = rendered.replace(re.escape(token), regex)
    return rendered, "template" if placeholders else "literal"


def _clean_meaning(value: str) -> str:
    meaning = re.sub(r"<br\s*/?>", "；", value, flags=re.IGNORECASE)
    meaning = meaning.replace("`", "")
    meaning = re.sub(r"(?:\*\*|__)(.*?)(?:\*\*|__)", r"\1", meaning)
    meaning = meaning.strip("*_")
    meaning = re.sub(r"\s+", " ", meaning).strip(" ：:|-—")
    return meaning[:1000]


def _fallback_meaning(document_title: str, heading: str) -> str:
    section = heading or "日志分析方法"
    return f"用于《{document_title}》中“{section}”的筛查（Skill 未提供单独含义）"


def _line_meaning(
    raw_line: str,
    candidate: str,
    *,
    document_title: str,
    heading: str,
) -> str:
    prose = _INLINE_CODE.sub(" ", raw_line)
    prose = prose.replace(candidate, " ")
    prose = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", prose)
    prose = _clean_meaning(prose)
    return prose if len(prose) >= 2 else _fallback_meaning(document_title, heading)


def _table_candidates(lines: list[str]) -> list[tuple[str, int, str, str]]:
    result: list[tuple[str, int, str, str]] = []
    index = 0
    while index + 1 < len(lines):
        header = lines[index]
        separator = lines[index + 1]
        if "|" not in header or "|" not in separator:
            index += 1
            continue
        headers = [cell.strip().lower() for cell in header.strip().strip("|").split("|")]
        separators = [cell.strip() for cell in separator.strip().strip("|").split("|")]
        if len(headers) != len(separators) or not all(_TABLE_SEPARATOR.match(cell) for cell in separators):
            index += 1
            continue
        selected_columns = [
            position
            for position, cell in enumerate(headers)
            if any(hint in cell for hint in _LOG_HINTS)
        ]
        meaning_columns = [
            position
            for position, cell in enumerate(headers)
            if any(hint in cell for hint in _MEANING_HINTS)
        ]
        row_index = index + 2
        while row_index < len(lines) and "|" in lines[row_index]:
            cells = [cell.strip() for cell in lines[row_index].strip().strip("|").split("|")]
            meaning = "；".join(
                cleaned
                for position in meaning_columns
                if position < len(cells)
                and (cleaned := _clean_meaning(cells[position]))
            )
            for position in selected_columns:
                if position < len(cells):
                    result.append((
                        cells[position],
                        row_index + 1,
                        "Markdown table log column",
                        meaning,
                    ))
            row_index += 1
        index = row_index
    return result


def _heading_for_line(lines: list[str], line_number: int) -> str:
    heading = ""
    for raw_line in lines[:max(0, line_number - 1)]:
        match = _HEADING.match(raw_line)
        if match:
            heading = match.group(2).strip()
    return heading


def compile_diagnostic_patterns(
    documents: list[DiagnosticMethodDocument],
) -> list[DiagnosticPattern]:
    patterns: list[DiagnosticPattern] = []
    seen: set[tuple[str, str]] = set()
    for document in documents:
        if document.source_type not in LOG_METHOD_SOURCE_TYPES:
            continue
        lines = document.content.splitlines()
        candidates = _table_candidates(lines)
        heading = ""
        heading_is_log_section = False
        for line_number, raw_line in enumerate(lines, start=1):
            heading_match = _HEADING.match(raw_line)
            if heading_match:
                heading = heading_match.group(2).strip()
                heading_is_log_section = any(hint in heading.lower() for hint in _LOG_HINTS)
                continue
            inline_candidates = _INLINE_CODE.findall(raw_line)
            for inline in inline_candidates:
                candidates.append((
                    inline,
                    line_number,
                    "Inline code log pattern",
                    _line_meaning(
                        raw_line,
                        inline,
                        document_title=document.title,
                        heading=heading,
                    ),
                ))
            # Inline code and table-column extraction are more precise than a
            # complete Markdown row. Avoid compiling pipes, emphasis and prose
            # into a regex that can never occur in the raw device log.
            is_table_row = raw_line.lstrip().startswith("|")
            if _PLACEHOLDER.search(raw_line) and not inline_candidates and not is_table_row:
                candidates.append((
                    raw_line,
                    line_number,
                    "Printf-style log template",
                    _line_meaning(
                        raw_line,
                        raw_line,
                        document_title=document.title,
                        heading=heading,
                    ),
                ))
            if (
                heading_is_log_section
                and not inline_candidates
                and re.match(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", raw_line)
            ):
                candidates.append((
                    raw_line,
                    line_number,
                    f"Log method section: {heading}",
                    _line_meaning(
                        raw_line,
                        raw_line,
                        document_title=document.title,
                        heading=heading,
                    ),
                ))

        for raw_candidate, line_number, reason, meaning in candidates:
            candidate = _clean_candidate(raw_candidate)
            if not _candidate_is_pattern(candidate):
                continue
            normalized = candidate.casefold()
            key = (document.id, normalized)
            if key in seen:
                continue
            seen.add(key)
            regex, match_kind = _pattern_regex(candidate)
            pattern_id = "DPAT-" + hashlib.sha256(
                f"{document.id}\0{document.version}\0{normalized}".encode("utf-8")
            ).hexdigest()[:20]
            patterns.append(DiagnosticPattern(
                id=pattern_id,
                text=candidate,
                match_kind=match_kind,
                regex=regex,
                document_id=document.id,
                document_title=document.title,
                document_version=document.version,
                source_type=document.source_type,
                heading=_heading_for_line(lines, line_number),
                line_start=line_number,
                reason=reason,
                meaning=meaning or _fallback_meaning(
                    document.title,
                    _heading_for_line(lines, line_number),
                ),
            ))
    return patterns


def method_prompt_bundle(documents: list[DiagnosticMethodDocument]) -> list[dict[str, Any]]:
    return [
        {
            **document.public_snapshot(),
            "content": document.content,
            "evidence_id": document.id,
        }
        for document in documents
    ]
