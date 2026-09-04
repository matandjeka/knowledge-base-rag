"""Baseline workspace-scoped retrieval and grounded-answer route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_query_service
from app.core.exceptions import (
    DatabaseConfigurationError,
    DatabaseExecutionError,
    IndexingError,
    IndexNotFoundError,
    RerankingError,
    RetrievalError,
    SourceNotFoundError,
    SqlValidationError,
)
from app.models import QueryRequest, QueryResponse
from app.retrieval.query_service import QueryService

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(
    request: QueryRequest,
    service: Annotated[QueryService, Depends(get_query_service)],
) -> QueryResponse:
    """Retrieve workspace evidence and return an extractive cited response."""
    try:
        return await service.query(request)
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
