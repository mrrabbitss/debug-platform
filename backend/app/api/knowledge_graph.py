from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Job
from app.schemas import DomainGraphSearchRequest, JobOut
from app.services.jobs import job_runner
from app.services.knowledge_graph import (
    domain_graph_status,
    rebuild_domain_graph_job,
    search_domain_graph,
)


router = APIRouter(prefix="/knowledge/graph", tags=["knowledge-graph"])
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    "rebuild_domain_graph",
    rebuild_domain_graph_job,
    (),
    cancellable=True,
)


@router.get("/status")
def get_domain_graph_status(db: Db) -> dict[str, Any]:
    return domain_graph_status(db)


@router.post("/rebuild", response_model=JobOut)
def rebuild_domain_graph(db: Db) -> Job:
    return job_runner.submit(
        db,
        "rebuild_domain_graph",
        rebuild_domain_graph_job,
        input_data={},
    )


@router.post("/search")
def run_domain_graph_search(
    payload: DomainGraphSearchRequest,
    db: Db,
) -> dict[str, Any]:
    return search_domain_graph(
        db,
        payload.query,
        top_k=payload.top_k,
        max_hops=payload.max_hops,
    )
