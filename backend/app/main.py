from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from app.api.routes import router
from app.api.simple_login import router as simple_login_router
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.migrations import run_database_migrations
from app.core.security import verify_api_key
from app.mcp.integration import configure_debugplatform_mcp
from app.services.knowledge_taxonomy import assign_uncategorized_documents, seed_knowledge_categories
from app.services.jobs import job_runner
from app.services.audit import AuditMiddleware
from app.services.model_profiles import seed_model_profiles
from app.services.retrieval_models import ensure_builtin_embedding_index
from app.services.static_frontend import mount_static_frontend
from app.services.storage_capacity import StorageCapacityError
from app.services.model_capacity import ModelCapacityError
from app.services.workbench import WorkbenchConfigurationError


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_runner.start()
    run_database_migrations()
    with SessionLocal() as db:
        seed_knowledge_categories(db)
        seed_model_profiles(db)
        assign_uncategorized_documents(db)
        ensure_builtin_embedding_index(db)
    job_runner.resume_incomplete()
    try:
        async with AsyncExitStack() as stack:
            mcp_transport = getattr(app.state, "mcp_transport", None)
            if mcp_transport is not None:
                await stack.enter_async_context(mcp_transport.lifespan())
            yield
    finally:
        job_runner.shutdown(wait=False)


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Evidence-driven GW/AP collectDebuginfo analysis, RAG and code correlation platform.",
    lifespan=lifespan,
)


@app.exception_handler(StorageCapacityError)
@app.exception_handler(ModelCapacityError)
async def capacity_error_handler(_request, error):
    return JSONResponse(status_code=503, content={"detail": str(error)}, headers={"Retry-After": "30"})


@app.exception_handler(WorkbenchConfigurationError)
async def workbench_configuration_error_handler(_request, error):
    return JSONResponse(status_code=409, content={"detail": str(error)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Start-Line", "X-Returned-Lines", "X-Total-Lines", "X-Has-More", "X-Text-Encoding",
        "Mcp-Session-Id",
    ],
)
app.add_middleware(AuditMiddleware)
if settings.trusted_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()],
        www_redirect=False,
    )
app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(verify_api_key)])
app.include_router(simple_login_router, prefix=settings.api_prefix)
configure_debugplatform_mcp(app, settings)

if settings.static_frontend_root is not None:
    mount_static_frontend(app, settings.static_frontend_root)
else:

    @app.get("/")
    def root() -> dict:
        return {
            "name": settings.app_name,
            "docs": "/docs",
            "api": settings.api_prefix,
        }
