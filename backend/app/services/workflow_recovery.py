"""Recover persisted domain state in the dispatcher's existing transaction."""


def recover_persisted_workflows(db):
    from app.services.knowledge_publication import recover_abandoned_publications
    from app.services.assistant_state import recover_abandoned_assistant_sessions
    from app.services.knowledge_reset import recover_abandoned_resets
    from app.services.knowledge_contribution_publication import recover_abandoned_contributions
    recover_abandoned_publications(db)
    recover_abandoned_assistant_sessions(db)
    recover_abandoned_resets(db)
    recover_abandoned_contributions(db)
