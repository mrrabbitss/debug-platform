from sqlalchemy import and_, or_

from app.models import Artifact, LogEvent


def active_log_event_clause():
    """Select only the parse generation currently published by its artifact.

    The NULL/NULL branch keeps compatibility with databases or tests created
    before parse generations were introduced.
    """
    return or_(
        LogEvent.parse_run_id == Artifact.active_parse_run_id,
        and_(Artifact.active_parse_run_id.is_(None), LogEvent.parse_run_id.is_(None)),
    )


def event_to_dict(
    event: LogEvent,
    source_device_type: str,
    source_device_role: str,
    *,
    entities: dict,
) -> dict:
    return {
        "id": event.id, "artifact_id": event.artifact_id,
        "source_file": event.source_file, "line_start": event.line_start,
        "line_end": event.line_end, "timestamp_raw": event.timestamp_raw,
        "timestamp_normalized": event.timestamp_normalized, "level": event.level,
        "module": event.module, "component": event.component,
        "event_code": event.event_code, "message": event.message,
        "raw_text": event.raw_text, "entities": entities,
        "confidence": event.confidence, "source_device_type": source_device_type,
        "source_device_role": source_device_role,
    }


def timeline_event_to_dict(
    event: LogEvent,
    source_device_type: str,
    source_device_role: str,
) -> dict:
    return {
        "id": event.id, "artifact_id": event.artifact_id,
        "time": event.timestamp_normalized or event.timestamp_raw,
        "module": event.module, "component": event.component, "level": event.level,
        "event_code": event.event_code, "message": event.message,
        "source_file": event.source_file, "line_start": event.line_start,
        "source_device_type": source_device_type,
        "source_device_role": source_device_role,
    }
