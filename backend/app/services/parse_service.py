from pathlib import Path

from sqlalchemy import delete, insert, or_, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import Artifact, Case, LogEvent
from app.services.archive import extract_archive
from app.services.jobs import JobCancelledError, JobContext
# Loading this module registers all built-in parsers on the shared registry.
from app.services import log_parsers as _builtin_parsers  # noqa: F401
from app.services.parser_registry import registry
from app.services.storage import storage
from app.services.text_files import looks_like_text_file, open_text_lines, read_text_file, read_text_sample


TEXT_SUFFIXES = {
    ".log", ".txt", ".out", ".err", ".trace", ".conf", ".cfg", ".json", ".jsonl", ".xml",
    ".ini", ".status", ".info", ".dump", "",
}

# Keep transactions large enough to avoid excessive SQLite fsync overhead on
# Windows, while still publishing progress and cancellation checkpoints often.
EVENT_INSERT_BATCH_SIZE = 5000


def _parse_artifact_impl(ctx: JobContext, case_id: str, artifact_id: str, parse_run_id: str) -> dict:
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        artifact = db.get(Artifact, artifact_id)
        if not case or not artifact:
            raise ValueError("Case or artifact not found")
        metadata = json_loads(artifact.metadata_json, {})
        metadata["pending_parse_run_id"] = parse_run_id
        artifact.metadata_json = json_dumps(metadata)
        if artifact.active_parse_run_id:
            artifact.status = "PARSED"
            case.status = "PARSED"
        else:
            artifact.status = "PARSING"
            case.status = "PARSING"
        db.commit()
        source = storage.resolve_path(artifact.stored_path)
        extract_dir = source.parent / "extracted"

    ctx.update(5, "Validating and extracting archive")
    manifest = extract_archive(source, extract_dir)

    text_files: list[tuple[Path, dict]] = []
    for item in manifest.files:
        candidate = extract_dir / item["path"]
        if candidate.suffix.lower() in TEXT_SUFFIXES or looks_like_text_file(candidate):
            text_files.append((candidate, item))
    parsed_files = 0
    event_count = 0
    device_info: dict[str, str] = {}
    parser_counts: dict[str, int] = {}
    level_counts: dict[str, int] = {}

    for index, (path, manifest_item) in enumerate(text_files):
        if not path.is_file():
            continue
        relative = str(path.relative_to(extract_dir)).replace("\\", "/")
        sample_result = read_text_sample(path)
        line_index: list[list[int]] = []
        opened = open_text_lines(
            path,
            index_stride=get_settings().text_line_index_stride,
            line_index=line_index,
        )
        if sample_result is None or opened is None:
            continue
        sample, sample_encoding = sample_result
        encoding, lines = opened
        parser = registry.select(path, sample)
        parser_counts[parser.parser_id] = parser_counts.get(parser.parser_id, 0) + 1
        line_count = 0

        def counted_lines():
            nonlocal line_count
            for line in lines:
                line_count += 1
                if line_count % 5000 == 0:
                    ctx.raise_if_cancelled()
                yield line

        if hasattr(parser, "parse_lines"):
            events = parser.parse_lines(path, relative, counted_lines(), sample)
        else:
            text = read_text_file(path)
            if text is None:
                continue
            line_count = len(text.splitlines())
            events = iter(parser.parse(path, relative, text))

        batch: list[dict] = []
        with SessionLocal() as db:
            for event in events:
                level_counts[event.level] = level_counts.get(event.level, 0) + 1
                batch.append({
                    "id": new_id("EVT"), "case_id": case_id, "artifact_id": artifact_id,
                    "parse_run_id": parse_run_id,
                    "source_file": event.source_file, "line_start": event.line_start, "line_end": event.line_end,
                    "timestamp_raw": event.timestamp_raw, "timestamp_normalized": event.timestamp_normalized,
                    "level": event.level, "module": event.module, "component": event.component,
                    "event_code": event.event_code, "message": event.message, "raw_text": event.raw_text,
                    "entities_json": json_dumps(event.entities), "parser_id": event.parser_id,
                    "parser_version": event.parser_version, "confidence": event.confidence,
                })
                if len(batch) >= EVENT_INSERT_BATCH_SIZE:
                    db.execute(insert(LogEvent), batch)
                    db.commit()
                    event_count += len(batch)
                    batch.clear()
                    ctx.update(
                        10 + int(80 * index / max(len(text_files), 1)),
                        f"Parsing {relative}: {line_count} lines, {event_count} events",
                    )
            if batch:
                db.execute(insert(LogEvent), batch)
                db.commit()
                event_count += len(batch)
        manifest_item["line_count"] = line_count
        manifest_item["encoding"] = encoding or sample_encoding
        manifest_item["line_index"] = line_index
        manifest_item["line_index_stride"] = get_settings().text_line_index_stride
        parsed_files += 1
        lower_text = sample.lower()
        for key, patterns in {
            "firmware": ["firmware version", "software version", "image version"],
            "model": ["device model", "product model", "board model"],
            "serial": ["serial number", "device sn"],
        }.items():
            if key in device_info:
                continue
            for pattern in patterns:
                pos = lower_text.find(pattern)
                if pos >= 0:
                    fragment = sample[pos:pos + 200].splitlines()[0]
                    if ":" in fragment:
                        device_info[key] = fragment.split(":", 1)[1].strip()[:128]
                    break
        ctx.update(10 + int(80 * (index + 1) / max(len(text_files), 1)), f"Parsed {relative}")

    parse_error = None
    if parsed_files == 0:
        max_mib = get_settings().parser_max_text_bytes // (1024 * 1024)
        parse_error = (
            "No readable text log files were parsed. The file may use an unsupported encoding, contain "
            f"binary/control bytes, or exceed {max_mib} MiB. Run scripts\\inspect_log_file.bat."
        )

    job_result = {
        "artifact_id": artifact_id,
        "files": len(manifest.files),
        "parsed_files": parsed_files,
        "events": event_count,
        "device_info": device_info,
    }

    with SessionLocal() as db:
        artifact = db.get(Artifact, artifact_id)
        case = db.get(Case, case_id)
        if not artifact or not case:
            raise ValueError("Case or artifact was removed while parsing")
        meta = json_loads(artifact.metadata_json, {})
        meta.update({
                "manifest": manifest.files[:5000],
                "manifest_file_count": len(manifest.files),
                "extracted_bytes": manifest.total_bytes,
                "parsed_files": parsed_files,
                "event_count": event_count,
                "parser_counts": parser_counts,
                "level_counts": level_counts,
                "device_info": device_info,
                "extract_root": storage.storage_key(extract_dir),
        })
        meta.pop("pending_parse_run_id", None)
        if parse_error:
            meta["parse_error"] = parse_error
        else:
            meta.pop("parse_error", None)
        artifact.metadata_json = json_dumps(meta)
        artifact.status = "PARSE_FAILED" if parse_error else "PARSED"
        case.status = "UPLOADED" if parse_error else "PARSED"
        if not parse_error:
            ctx.complete_in_transaction(db, job_result)
            artifact.active_parse_run_id = parse_run_id
            db.flush()
            db.execute(delete(LogEvent).where(
                LogEvent.artifact_id == artifact_id,
                or_(LogEvent.parse_run_id != parse_run_id, LogEvent.parse_run_id.is_(None)),
            ))
        if not case.device_model and device_info.get("model"):
            case.device_model = device_info["model"]
        if not case.firmware_version and device_info.get("firmware"):
            case.firmware_version = device_info["firmware"]
        db.commit()

    if parse_error:
        ctx.update(95, parse_error)
        raise ValueError(parse_error)
    return job_result


def _rollback_parse_run(
    case_id: str,
    artifact_id: str,
    parse_run_id: str,
    error_message: str | None,
) -> None:
    with SessionLocal() as db:
        artifact = db.get(Artifact, artifact_id)
        case = db.get(Case, case_id)
        if artifact:
            db.execute(delete(LogEvent).where(
                LogEvent.artifact_id == artifact_id,
                LogEvent.parse_run_id == parse_run_id,
            ))
            metadata = json_loads(artifact.metadata_json, {})
            metadata.pop("pending_parse_run_id", None)
            if error_message:
                metadata["parse_error"] = error_message[:2000]
            else:
                metadata["last_parse_cancelled_at"] = utcnow().isoformat()
            artifact.metadata_json = json_dumps(metadata)
            artifact.status = "PARSED" if artifact.active_parse_run_id else (
                "PARSE_FAILED" if error_message else "UPLOADED"
            )
        if case:
            has_parsed_artifact = db.scalar(
                select(Artifact.id).where(
                    Artifact.case_id == case_id,
                    Artifact.active_parse_run_id.is_not(None),
                ).limit(1)
            )
            case.status = "PARSED" if has_parsed_artifact else "UPLOADED"
        db.commit()


def parse_artifact_job(ctx: JobContext, case_id: str, artifact_id: str) -> dict:
    parse_run_id = new_id("PRUN")
    try:
        return _parse_artifact_impl(ctx, case_id, artifact_id, parse_run_id)
    except JobCancelledError:
        _rollback_parse_run(case_id, artifact_id, parse_run_id, None)
        raise
    except Exception as exc:
        _rollback_parse_run(
            case_id,
            artifact_id,
            parse_run_id,
            str(exc) or type(exc).__name__,
        )
        raise
