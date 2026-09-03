"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.routes.graph import router as graph_router
from app.api.routes.health import router as health_router
from app.api.routes.query import router as query_router
from app.api.routes.sources import router as sources_router
from app.core.config import get_settings
from app.core.exceptions import GraphConfigurationError
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure application services for the process lifetime."""
    settings = get_settings()
    configure_logging(settings.log_level)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


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
