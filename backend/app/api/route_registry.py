"""Compose modular API routers without expanding the legacy case route module."""

from fastapi import APIRouter

from app.api.agent_runs import router as agent_runs_router
from app.api.client_discovery import router as client_discovery_router
from app.api.demo_cases import router as demo_cases_router
from app.api.diagnostics import router as diagnostics_router
from app.api.jobs import router as jobs_router
from app.api.knowledge import router as knowledge_router
from app.api.knowledge_intake import router as knowledge_intake_router
from app.api.knowledge_drafts import router as knowledge_drafts_router
from app.api.knowledge_curation import router as knowledge_curation_router
from app.api.knowledge_governance import router as knowledge_governance_router
from app.api.knowledge_graph import router as knowledge_graph_router
from app.api.knowledge_routing import router as knowledge_routing_router
from app.api.model_downloads import router as model_downloads_router
from app.api.memory_governance import router as memory_governance_router
from app.api.repositories import router as repositories_router
from app.api.retrieval_evaluation import router as retrieval_evaluation_router
from app.api.system import router as system_router
from app.api.workbench import router as workbench_router
from app.api.knowledge_assistant import router as knowledge_assistant_router


def include_modular_routers(router: APIRouter) -> None:
    for child in (
        workbench_router,
        knowledge_assistant_router,
        agent_runs_router,
        client_discovery_router,
        demo_cases_router,
        diagnostics_router,
        jobs_router,
        knowledge_router,
        knowledge_intake_router,
        knowledge_drafts_router,
        knowledge_governance_router,
        knowledge_graph_router,
        knowledge_routing_router,
        model_downloads_router,
        memory_governance_router,
        knowledge_curation_router,
        retrieval_evaluation_router,
        repositories_router,
        system_router,
    ):
        router.include_router(child)
