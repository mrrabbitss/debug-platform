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
