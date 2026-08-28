from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_dumps, new_id
from app.models import (
    Case,
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
    RetrievalEvaluationRun,
)
from app.schemas import (
    EvaluationCaseCreate,
    EvaluationCaseUpdate,
    EvaluationDatasetCreate,
    EvaluationDatasetUpdate,
    JobOut,
)
from app.services.jobs import job_runner
from app.services.knowledge_governance import actor_id
from app.services.retrieval_evaluation import (
    dataset_to_dict,
    evaluation_case_to_dict,
    evaluation_run_to_dict,
    run_retrieval_evaluation_job,
)


router = APIRouter(prefix="/evaluation", tags=["retrieval-evaluation"])
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    "run_retrieval_evaluation",
    run_retrieval_evaluation_job,
    ("evaluation_run_id",),
    cancellable=True,
)


def _principal(request: Request) -> dict[str, Any]:
    return getattr(request.state, "principal", {}) or {}


def _dataset_or_404(
    db: Session,
    dataset_id: str,
) -> RetrievalEvaluationDataset:
    dataset = db.get(RetrievalEvaluationDataset, dataset_id)
    if not dataset:
        raise HTTPException(404, "Evaluation dataset not found")
    return dataset


def _case_or_404(
    db: Session,
    dataset_id: str,
    evaluation_case_id: str,
) -> RetrievalEvaluationCase:
    item = db.get(RetrievalEvaluationCase, evaluation_case_id)
    if not item or item.dataset_id != dataset_id:
        raise HTTPException(404, "Evaluation case not found")
    return item


def _validate_case(db: Session, case_id: str) -> None:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")


@router.get("/datasets")
def list_evaluation_datasets(db: Db) -> list[dict[str, Any]]:
    counts = dict(db.execute(
        select(
            RetrievalEvaluationCase.dataset_id,
            func.count(RetrievalEvaluationCase.id),
        ).group_by(RetrievalEvaluationCase.dataset_id)
    ).all())
    datasets = list(db.scalars(
        select(RetrievalEvaluationDataset)
        .order_by(RetrievalEvaluationDataset.updated_at.desc())
    ).all())
    return [
        dataset_to_dict(
            dataset,
            case_count=int(counts.get(dataset.id, 0)),
        )
        for dataset in datasets
    ]


@router.post("/datasets")
def create_evaluation_dataset(
    payload: EvaluationDatasetCreate,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    dataset = RetrievalEvaluationDataset(
        id=new_id("ESET"),
        name=payload.name,
        description=payload.description,
        created_by=actor_id(_principal(request)),
    )
    db.add(dataset)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Evaluation dataset name already exists") from exc
    db.refresh(dataset)
    return dataset_to_dict(dataset)


@router.patch("/datasets/{dataset_id}")
def update_evaluation_dataset(
    dataset_id: str,
    payload: EvaluationDatasetUpdate,
    db: Db,
) -> dict[str, Any]:
    dataset = _dataset_or_404(db, dataset_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(dataset, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Evaluation dataset name already exists") from exc
    db.refresh(dataset)
    case_count = int(db.scalar(
        select(func.count(RetrievalEvaluationCase.id)).where(
            RetrievalEvaluationCase.dataset_id == dataset.id
        )
    ) or 0)
    return dataset_to_dict(dataset, case_count=case_count)


@router.delete("/datasets/{dataset_id}")
def delete_evaluation_dataset(dataset_id: str, db: Db) -> dict[str, str]:
    dataset = _dataset_or_404(db, dataset_id)
    active_run = db.scalar(
        select(RetrievalEvaluationRun.id).where(
            RetrievalEvaluationRun.dataset_id == dataset_id,
            RetrievalEvaluationRun.status.in_(("QUEUED", "RUNNING")),
        ).limit(1)
    )
    if active_run:
        raise HTTPException(409, "Cancel the active evaluation run first")
    db.delete(dataset)
    db.commit()
    return {"deleted": dataset_id}


@router.get("/datasets/{dataset_id}/cases")
def list_evaluation_cases(
    dataset_id: str,
    db: Db,
) -> list[dict[str, Any]]:
    _dataset_or_404(db, dataset_id)
    items = list(db.scalars(
        select(RetrievalEvaluationCase)
        .where(RetrievalEvaluationCase.dataset_id == dataset_id)
        .order_by(RetrievalEvaluationCase.created_at)
    ).all())
    return [evaluation_case_to_dict(item) for item in items]


@router.post("/datasets/{dataset_id}/cases")
def create_evaluation_case(
    dataset_id: str,
    payload: EvaluationCaseCreate,
    db: Db,
) -> dict[str, Any]:
    _dataset_or_404(db, dataset_id)
    _validate_case(db, payload.case_id)
    item = RetrievalEvaluationCase(
        id=new_id("ECASE"),
        dataset_id=dataset_id,
        case_id=payload.case_id,
        query=payload.query,
        expected_evidence_json=json_dumps(payload.expected_evidence_ids),
        expected_root_causes_json=json_dumps(payload.expected_root_causes),
        modules_json=json_dumps(payload.modules),
        top_k=payload.top_k,
        max_hops=payload.max_hops,
        metadata_json=json_dumps(payload.metadata),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return evaluation_case_to_dict(item)


@router.patch("/datasets/{dataset_id}/cases/{evaluation_case_id}")
def update_evaluation_case(
    dataset_id: str,
    evaluation_case_id: str,
    payload: EvaluationCaseUpdate,
    db: Db,
) -> dict[str, Any]:
    item = _case_or_404(db, dataset_id, evaluation_case_id)
    values = payload.model_dump(exclude_unset=True)
    if "case_id" in values:
        _validate_case(db, values["case_id"])
    mappings = {
        "expected_evidence_ids": "expected_evidence_json",
        "expected_root_causes": "expected_root_causes_json",
        "modules": "modules_json",
        "metadata": "metadata_json",
    }
    for key, value in values.items():
        target = mappings.get(key, key)
        setattr(item, target, json_dumps(value) if key in mappings else value)
    db.commit()
    db.refresh(item)
    return evaluation_case_to_dict(item)


@router.delete("/datasets/{dataset_id}/cases/{evaluation_case_id}")
def delete_evaluation_case(
    dataset_id: str,
    evaluation_case_id: str,
    db: Db,
) -> dict[str, str]:
    item = _case_or_404(db, dataset_id, evaluation_case_id)
    db.delete(item)
    db.commit()
    return {"deleted": evaluation_case_id}


@router.get("/datasets/{dataset_id}/runs")
def list_evaluation_runs(
    dataset_id: str,
    db: Db,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[dict[str, Any]]:
    _dataset_or_404(db, dataset_id)
    runs = list(db.scalars(
        select(RetrievalEvaluationRun)
        .where(RetrievalEvaluationRun.dataset_id == dataset_id)
        .order_by(RetrievalEvaluationRun.created_at.desc())
        .limit(limit)
    ).all())
    return [evaluation_run_to_dict(run) for run in runs]


@router.get("/runs/{evaluation_run_id}")
def get_evaluation_run(
    evaluation_run_id: str,
    db: Db,
) -> dict[str, Any]:
    run = db.get(RetrievalEvaluationRun, evaluation_run_id)
    if not run:
        raise HTTPException(404, "Evaluation run not found")
    return evaluation_run_to_dict(run)


@router.post("/datasets/{dataset_id}/runs", status_code=202)
def start_evaluation_run(
    dataset_id: str,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    dataset = _dataset_or_404(db, dataset_id)
    case_count = int(db.scalar(
        select(func.count(RetrievalEvaluationCase.id)).where(
            RetrievalEvaluationCase.dataset_id == dataset_id
        )
    ) or 0)
    if not case_count:
        raise HTTPException(409, "Evaluation dataset has no cases")
    run = RetrievalEvaluationRun(
        id=new_id("ERUN"),
        dataset_id=dataset.id,
        status="QUEUED",
        created_by=actor_id(_principal(request)),
    )
    db.add(run)
    db.commit()
    job = job_runner.submit(
        db,
        "run_retrieval_evaluation",
        run_retrieval_evaluation_job,
        run.id,
        input_data={"evaluation_run_id": run.id},
        deduplicate=False,
    )
    run.job_id = job.id
    db.commit()
    db.refresh(run)
    return {
        "run": evaluation_run_to_dict(run),
        "job": JobOut.model_validate(job).model_dump(),
    }
