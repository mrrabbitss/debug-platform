from __future__ import annotations

import math
import re
from statistics import fmean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, utcnow
from app.models import (
    ModelProfile,
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
    RetrievalEvaluationRun,
)
from app.services.agentic_search import agentic_search
from app.services.jobs import JobCancelledError, JobContext


def dataset_to_dict(
    dataset: RetrievalEvaluationDataset,
    *,
    case_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "active": dataset.active,
        "created_by": dataset.created_by,
        "case_count": case_count,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def evaluation_case_to_dict(
    item: RetrievalEvaluationCase,
) -> dict[str, Any]:
    return {
        "id": item.id,
        "dataset_id": item.dataset_id,
        "case_id": item.case_id,
        "query": item.query,
        "expected_evidence_ids": json_loads(
            item.expected_evidence_json,
            [],
        ),
        "expected_root_causes": json_loads(
            item.expected_root_causes_json,
            [],
        ),
        "modules": json_loads(item.modules_json, []),
        "top_k": item.top_k,
        "max_hops": item.max_hops,
        "metadata": json_loads(item.metadata_json, {}),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def evaluation_run_to_dict(
    run: RetrievalEvaluationRun,
) -> dict[str, Any]:
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "job_id": run.job_id,
        "status": run.status,
        "config": json_loads(run.config_json, {}),
        "metrics": json_loads(run.metrics_json, {}),
        "results": json_loads(run.results_json, []),
        "error_message": run.error_message,
        "created_by": run.created_by,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def _round_metric(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def calculate_ranking_metrics(
    expected_ids: list[str],
    actual_ids: list[str],
    *,
    top_k: int,
) -> dict[str, float | int | None]:
    expected = {str(item) for item in expected_ids if str(item)}
    ranked = [str(item) for item in actual_ids[:top_k]]
    if not expected:
        return {
            "expected_count": 0,
            "matched_count": 0,
            "recall_at_k": None,
            "precision_at_k": None,
            "mrr": None,
            "ndcg_at_k": None,
        }
    relevant = [1 if item in expected else 0 for item in ranked]
    matched = len(expected.intersection(ranked))
    first_rank = next(
        (index for index, value in enumerate(relevant, start=1) if value),
        None,
    )
    dcg = sum(
        value / math.log2(index + 1)
        for index, value in enumerate(relevant, start=1)
    )
    ideal_relevant = min(len(expected), top_k)
    ideal_dcg = sum(
        1.0 / math.log2(index + 1)
        for index in range(1, ideal_relevant + 1)
    )
    return {
        "expected_count": len(expected),
        "matched_count": matched,
        "recall_at_k": _round_metric(matched / len(expected)),
        "precision_at_k": _round_metric(matched / max(top_k, 1)),
        "mrr": _round_metric(1.0 / first_rank if first_rank else 0.0),
        "ndcg_at_k": _round_metric(dcg / ideal_dcg if ideal_dcg else 0.0),
    }


def _normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def root_cause_top_k(
    expected_root_causes: list[str],
    ranked_results: list[dict[str, Any]],
    *,
    top_k: int,
) -> float | None:
    expected = [
        _normalize_phrase(value)
        for value in expected_root_causes
        if _normalize_phrase(value)
    ]
    if not expected:
        return None
    for result in ranked_results[:top_k]:
        haystack = _normalize_phrase(
            f"{result.get('title', '')}\n{result.get('content', '')}"
        )
        if any(phrase in haystack for phrase in expected):
            return 1.0
    return 0.0


def _mean_available(
    rows: list[dict[str, Any]],
    key: str,
) -> float | None:
    values = [
        float(row[key])
        for row in rows
        if row.get(key) is not None
    ]
    return _round_metric(fmean(values)) if values else None


def _active_model_snapshot(db: Session) -> dict[str, Any]:
    profiles = list(db.scalars(
        select(ModelProfile).where(ModelProfile.is_active.is_(True))
    ).all())
    return {
        profile.task_type: {
            "id": profile.id,
            "provider": profile.provider,
            "mode": profile.mode,
            "model_name": profile.model_name,
            "embedding_generation_id": (
                profile.active_embedding_generation_id
                if profile.task_type == "embedding"
                else None
            ),
        }
        for profile in profiles
    }


def run_retrieval_evaluation_job(
    ctx: JobContext,
    evaluation_run_id: str,
) -> dict[str, Any]:
    try:
        with SessionLocal() as db:
            run = db.get(RetrievalEvaluationRun, evaluation_run_id)
            if not run:
                raise ValueError("Evaluation run not found")
            dataset = db.get(RetrievalEvaluationDataset, run.dataset_id)
            if not dataset:
                raise ValueError("Evaluation dataset not found")
            cases = list(db.scalars(
                select(RetrievalEvaluationCase)
                .where(RetrievalEvaluationCase.dataset_id == dataset.id)
                .order_by(RetrievalEvaluationCase.created_at)
            ).all())
            if not cases:
                raise ValueError("Evaluation dataset has no cases")
            run.status = "RUNNING"
            run.started_at = utcnow()
            run.error_message = None
            run.config_json = json_dumps({
                "algorithm": "agentic_search_v2",
                "dataset_updated_at": dataset.updated_at,
                "model_profiles": _active_model_snapshot(db),
                "memory_writes": False,
                "case_count": len(cases),
            })
            db.commit()

        case_results: list[dict[str, Any]] = []
        for index, evaluation_case in enumerate(cases):
            ctx.raise_if_cancelled()
            with SessionLocal() as db:
                response = agentic_search(
                    db,
                    case_id=evaluation_case.case_id,
                    query=evaluation_case.query,
                    top_k=evaluation_case.top_k,
                    max_hops=evaluation_case.max_hops,
                    requested_modules=json_loads(
                        evaluation_case.modules_json,
                        [],
                    ),
                    record_memory=False,
                )
            ranked_results = response["results"]
            actual_ids = [
                str(item["evidence_id"])
                for item in ranked_results
            ]
            expected_ids = json_loads(
                evaluation_case.expected_evidence_json,
                [],
            )
            expected_root_causes = json_loads(
                evaluation_case.expected_root_causes_json,
                [],
            )
            ranking_metrics = calculate_ranking_metrics(
                expected_ids,
                actual_ids,
                top_k=evaluation_case.top_k,
            )
            root_cause_score = root_cause_top_k(
                expected_root_causes,
                ranked_results,
                top_k=evaluation_case.top_k,
            )
            expected_set = {str(item) for item in expected_ids}
            case_results.append({
                "evaluation_case_id": evaluation_case.id,
                "case_id": evaluation_case.case_id,
                "query": evaluation_case.query,
                "top_k": evaluation_case.top_k,
                "metrics": {
                    **ranking_metrics,
                    "root_cause_top_k": root_cause_score,
                },
                "retrieved": [
                    {
                        "rank": rank,
                        "evidence_id": str(item["evidence_id"]),
                        "source_type": item["source_type"],
                        "modules": item.get("modules", []),
                        "source_score": item.get("source_score"),
                        "fusion_score": item.get("fusion_score"),
                        "dense_score": item.get("dense_score"),
                        "reranker_score": item.get("reranker_score"),
                        "matched_expected": (
                            str(item["evidence_id"]) in expected_set
                        ),
                    }
                    for rank, item in enumerate(
                        ranked_results,
                        start=1,
                    )
                ],
                "trace": response["trace"],
            })
            ctx.update(
                5 + int(90 * (index + 1) / len(cases)),
                f"Evaluated retrieval case {index + 1}/{len(cases)}",
            )

        metric_rows = [item["metrics"] for item in case_results]
        metrics = {
            "case_count": len(case_results),
            "evidence_scored_cases": sum(
                1
                for row in metric_rows
                if row["recall_at_k"] is not None
            ),
            "root_cause_scored_cases": sum(
                1
                for row in metric_rows
                if row["root_cause_top_k"] is not None
            ),
            "recall_at_k": _mean_available(metric_rows, "recall_at_k"),
            "precision_at_k": _mean_available(
                metric_rows,
                "precision_at_k",
            ),
            "mrr": _mean_available(metric_rows, "mrr"),
            "ndcg_at_k": _mean_available(metric_rows, "ndcg_at_k"),
            "root_cause_top_k": _mean_available(
                metric_rows,
                "root_cause_top_k",
            ),
        }
        result = {
            "evaluation_run_id": evaluation_run_id,
            "metrics": metrics,
        }
        with SessionLocal() as db:
            run = db.get(RetrievalEvaluationRun, evaluation_run_id)
            if not run:
                raise ValueError("Evaluation run was deleted")
            run.status = "COMPLETED"
            run.metrics_json = json_dumps(metrics)
            run.results_json = json_dumps(case_results)
            run.completed_at = utcnow()
            ctx.complete_in_transaction(
                db,
                result,
                message="Retrieval evaluation completed",
            )
            db.commit()
        return result
    except Exception as exc:
        with SessionLocal() as db:
            run = db.get(RetrievalEvaluationRun, evaluation_run_id)
            if run and run.status not in {"COMPLETED", "CANCELLED"}:
                run.status = (
                    "CANCELLED"
                    if isinstance(exc, JobCancelledError)
                    else "FAILED"
                )
                run.error_message = str(exc)[:4000]
                run.completed_at = utcnow()
                db.commit()
        raise
