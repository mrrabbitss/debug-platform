from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.migrations import run_database_migrations
from app.core.security import verify_api_key
from app.services.knowledge import seed_builtin_knowledge
from app.services.knowledge_taxonomy import assign_uncategorized_documents, seed_knowledge_categories
from app.services.jobs import job_runner
from app.services.model_profiles import seed_model_profiles
from app.services.retrieval_models import ensure_builtin_embedding_index


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_database_migrations()
    seed_dir = Path(__file__).resolve().parent / "seed_knowledge"
    with SessionLocal() as db:
        seed_knowledge_categories(db)
        seed_model_profiles(db)
        seed_builtin_knowledge(db, seed_dir)
        assign_uncategorized_documents(db)
        ensure_builtin_embedding_index(db)
    job_runner.resume_incomplete()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Evidence-driven GW/AP collectDebuginfo analysis, RAG and code correlation platform.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Start-Line", "X-Returned-Lines", "X-Total-Lines", "X-Has-More", "X-Text-Encoding",
    ],
)
app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(verify_api_key)])


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "docs": "/docs", "api": settings.api_prefix}
