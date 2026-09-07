"""Publication manifests pin graph and vector generations for reproducibility."""
from sqlalchemy import select

from app.core.utils import json_loads
from app.models import KnowledgePublication


def pinned_generations(db, field: str, *, profile_id: str | None = None) -> set[str]:
    result = set()
    for raw in db.scalars(select(KnowledgePublication.manifest_json)):
        manifest = json_loads(raw, {})
        if profile_id and manifest.get("embedding_profile_id") != profile_id:
            continue
        value = manifest.get(field)
        if isinstance(value, str) and value:
            result.add(value)
    return result


def retained_graph_generations(db, active: str, previous: str | None) -> list[str]:
    return list({active, *([previous] if previous else []), *pinned_generations(db, "graph_generation_id")})
