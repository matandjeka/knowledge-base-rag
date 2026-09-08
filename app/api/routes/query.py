"""Baseline workspace-scoped retrieval and grounded-answer route."""

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_metadata_engine, get_query_service
from app.core.exceptions import (
    DatabaseConfigurationError,
    DatabaseExecutionError,
    GenerationError,
    IndexingError,
    IndexNotFoundError,
    RerankingError,
    RetrievalError,
    SourceNotFoundError,
    SqlValidationError,
)
from app.core.rate_limit import rate_limit
from app.models import Classification, QueryRequest, QueryResponse
from app.retrieval.query_service import QueryService

router = APIRouter(tags=["query"])


@router.post(
    "/query",
    response_model=QueryResponse,
    response_model_exclude_none=True,
    dependencies=[rate_limit("query", "rate_limit_query_per_minute")],
)
async def query_knowledge_base(
    request: QueryRequest,
    http_request: Request,
    service: Annotated[QueryService, Depends(get_query_service)],
) -> QueryResponse:
    """Retrieve workspace evidence and return an extractive cited response."""
    membership = getattr(http_request.state, "membership", None)
    clearance = (
        membership.effective_clearance if membership is not None else Classification.RESTRICTED
    )
    try:
        response = await service.query(request, max_classification=clearance)
    except SourceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        ) from error
    except IndexNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except RetrievalError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except SqlValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except DatabaseExecutionError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    except GenerationError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    except RerankingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except DatabaseConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except IndexingError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error
    if membership is not None:
        from app.retention import RetentionRepository

        with contextlib.suppress(Exception):
            await RetentionRepository(get_metadata_engine()).record_query(request.workspace_id)
    return response
