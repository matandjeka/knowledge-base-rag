"""Explicit workspace knowledge-graph indexing route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_graph_indexing_service
from app.core.exceptions import IndexingError
from app.graph.indexing import GraphIndexingService
from app.models import GraphIndexRequest, GraphIndexResult

router = APIRouter(prefix="/graph", tags=["graph"])


@router.post("/index", response_model=GraphIndexResult)
async def index_graph(
    request: GraphIndexRequest,
    service: Annotated[GraphIndexingService, Depends(get_graph_indexing_service)],
) -> GraphIndexResult:
    """Rebuild and atomically activate the complete graph for one workspace."""
    try:
        return await service.rebuild(request.workspace_id)
    except IndexingError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error
