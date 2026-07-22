from pathlib import Path

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id
from app.models import Artifact, Case, LogEvent
from app.services.archive import extract_archive
from app.services.jobs import JobContext
# Loading this module registers all built-in parsers on the shared registry.
from app.services import log_parsers as _builtin_parsers  # noqa: F401
from app.services.parser_registry import registry
from app.services.text_files import looks_like_text_file, read_text_file


TEXT_SUFFIXES = {
    ".log", ".txt", ".out", ".err", ".trace", ".conf", ".cfg", ".json", ".jsonl", ".xml",
    ".ini", ".status", ".info", ".dump", "",
}


def parse_artifact_job(ctx: JobContext, case_id: str, artifact_id: str) -> dict:
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        artifact = db.get(Artifact, artifact_id)
        if not case or not artifact:
            raise ValueError("Case or artifact not found")
        artifact.status = "PARSING"
        case.status = "PARSING"
        db.commit()
        source = Path(artifact.stored_path)
        extract_dir = source.parent / "extracted"

    ctx.update(5, "Validating and extracting archive")
    manifest = extract_archive(source, extract_dir)

    with SessionLocal() as db:
        db.execute(delete(LogEvent).where(LogEvent.artifact_id == artifact_id))
        db.commit()

    text_files = []
    for item in manifest.files:
        candidate = extract_dir / item["path"]
        if candidate.suffix.lower() in TEXT_SUFFIXES or looks_like_text_file(candidate):
            text_files.append(candidate)
    parsed_files = 0
    event_count = 0
    device_info: dict[str, str] = {}
    parser_counts: dict[str, int] = {}
    level_counts: dict[str, int] = {}

    for index, path in enumerate(text_files):
        if not path.is_file():
            continue
        relative = str(path.relative_to(extract_dir)).replace("\\", "/")
        text = read_text_file(path)
        if text is None:
            continue
        sample = text[:20000]
        parser = registry.select(path, sample)
        events = parser.parse(path, relative, text)
        parser_counts[parser.parser_id] = parser_counts.get(parser.parser_id, 0) + 1
        with SessionLocal() as db:
            for event in events:
                level_counts[event.level] = level_counts.get(event.level, 0) + 1
                db.add(LogEvent(
                    id=new_id("EVT"), case_id=case_id, artifact_id=artifact_id,
                    source_file=event.source_file, line_start=event.line_start, line_end=event.line_end,
                    timestamp_raw=event.timestamp_raw, timestamp_normalized=event.timestamp_normalized,
                    level=event.level, module=event.module, component=event.component,
                    event_code=event.event_code, message=event.message, raw_text=event.raw_text,
                    entities_json=json_dumps(event.entities), parser_id=event.parser_id,
                    parser_version=event.parser_version, confidence=event.confidence,
                ))
            db.commit()
        event_count += len(events)
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

    with SessionLocal() as db:
        artifact = db.get(Artifact, artifact_id)
        case = db.get(Case, case_id)
        if artifact and case:
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
                "extract_root": str(extract_dir),
            })
            if parse_error:
                meta["parse_error"] = parse_error
            else:
                meta.pop("parse_error", None)
            artifact.metadata_json = json_dumps(meta)
            artifact.status = "PARSE_FAILED" if parse_error else "PARSED"
            case.status = "UPLOADED" if parse_error else "PARSED"
            if not case.device_model and device_info.get("model"):
                case.device_model = device_info["model"]
            if not case.firmware_version and device_info.get("firmware"):
                case.firmware_version = device_info["firmware"]
            db.commit()

    if parse_error:
        ctx.update(95, parse_error)
        raise ValueError(parse_error)

    ctx.update(95, "Building timeline")
    return {
        "artifact_id": artifact_id,
        "files": len(manifest.files),
        "parsed_files": parsed_files,
        "events": event_count,
        "device_info": device_info,
    }
