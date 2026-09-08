"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.dependencies import close_application_dependencies
from app.api.routes.audit import router as audit_router
from app.api.routes.evaluations import router as evaluations_router
from app.api.routes.graph import router as graph_router
from app.api.routes.health import router as health_router
from app.api.routes.query import router as query_router
from app.api.routes.retention import crawl_router
from app.api.routes.retention import router as retention_router
from app.api.routes.settings import router as settings_router
from app.api.routes.sources import router as sources_router
from app.auth.authorization import authorize
from app.auth.org_routes import router as org_router
from app.auth.routes import router as auth_router
from app.core.config import get_settings
from app.core.exceptions import GraphConfigurationError
from app.core.logging import configure_logging
from app.jobs.routes import router as jobs_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure application services for the process lifetime."""
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        yield
    finally:
        await close_application_dependencies()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(authorize)],
    docs_url=None if settings.auth_enabled else "/docs",
    openapi_url=None if settings.auth_enabled else "/openapi.json",
    redoc_url=None if settings.auth_enabled else "/redoc",
)


@app.exception_handler(GraphConfigurationError)
async def graph_configuration_error(_: Request, error: GraphConfigurationError) -> JSONResponse:
    """Report optional graph extraction configuration failures clearly."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(error)},
    )


app.include_router(health_router)
app.include_router(graph_router)
app.include_router(sources_router)
app.include_router(query_router)
app.include_router(evaluations_router)
app.include_router(settings_router)

app.include_router(auth_router)
app.include_router(org_router)
app.include_router(jobs_router)
app.include_router(audit_router)
app.include_router(retention_router)
app.include_router(crawl_router)
