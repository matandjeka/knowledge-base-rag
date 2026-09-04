"""Knowledge-source registration and inspection routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.dependencies import (
    get_csv_ingestion_service,
    get_pdf_ingestion_service,
    get_source_repository,
    get_source_storage,
    get_vector_indexing_service,
    get_website_ingestion_service,
)
from app.core.config import get_settings
from app.core.exceptions import (
    CsvValidationError,
    IndexingError,
    IngestionError,
    PdfValidationError,
    SourceNotFoundError,
    WebsiteValidationError,
)
from app.ingestion.csv_service import CsvIngestionService
from app.ingestion.service import PdfIngestionService
from app.ingestion.website_service import WebsiteIngestionService
from app.models import (
    CsvIngestionResult,
    CsvPreviewResult,
    NormalizedDocument,
    PdfIngestionResult,
    Source,
    SourceIndexResult,
    WebsiteIngestionRequest,
    WebsiteIngestionResult,
)
from app.repositories import InMemorySourceRepository
from app.retrieval.indexing import VectorIndexingService
from app.storage import LocalSourceStorage

router = APIRouter(prefix="/sources", tags=["sources"])

WorkspaceForm = Annotated[
    str,
    Form(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Workspace isolation identifier",
    ),
]
WorkspaceQuery = Annotated[
    str,
    Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
]


@router.post("/pdf", response_model=PdfIngestionResult, status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    workspace_id: WorkspaceForm,
    file: Annotated[UploadFile, File(description="PDF knowledge source")],
    service: Annotated[PdfIngestionService, Depends(get_pdf_ingestion_service)],
) -> PdfIngestionResult:
    """Validate, store, parse, chunk, and register one PDF."""
    settings = get_settings()
    data = await file.read(settings.max_pdf_size_bytes + 1)
    await file.close()
    try:
        return await service.ingest(
            workspace_id=workspace_id,
            filename=file.filename or "",
            content_type=file.content_type,
            data=data,
        )
    except PdfValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except IngestionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error


@router.post("/website", response_model=WebsiteIngestionResult, status_code=status.HTTP_201_CREATED)
async def add_website(
    request: WebsiteIngestionRequest,
    service: Annotated[WebsiteIngestionService, Depends(get_website_ingestion_service)],
) -> WebsiteIngestionResult:
    """Validate, crawl, extract, chunk, and register one website."""
    try:
        return await service.ingest(
            request.workspace_id,
            request.url,
            crawl_same_domain=request.crawl_same_domain,
            page_limit=request.page_limit,
        )
    except WebsiteValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except IngestionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error


@router.post("/csv/preview", response_model=CsvPreviewResult)
async def preview_csv(
    file: Annotated[UploadFile, File(description="CSV knowledge source")],
    service: Annotated[CsvIngestionService, Depends(get_csv_ingestion_service)],
) -> CsvPreviewResult:
    """Validate a CSV upload and return its schema and bounded sample."""
    settings = get_settings()
    data = await file.read(settings.max_csv_size_bytes + 1)
    await file.close()
    try:
        return await service.preview(file.filename or "", file.content_type, data)
    except CsvValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.post("/csv", response_model=CsvIngestionResult, status_code=status.HTTP_201_CREATED)
async def upload_csv(
    workspace_id: WorkspaceForm,
    file: Annotated[UploadFile, File(description="CSV knowledge source")],
    text_columns: Annotated[list[str], Form()],
    service: Annotated[CsvIngestionService, Depends(get_csv_ingestion_service)],
    metadata_columns: Annotated[list[str] | None, Form()] = None,
    row_id_column: Annotated[str | None, Form()] = None,
) -> CsvIngestionResult:
    """Validate, normalize, persist, and register one CSV."""
    settings = get_settings()
    data = await file.read(settings.max_csv_size_bytes + 1)
    await file.close()
    try:
        return await service.ingest(
            workspace_id,
            file.filename or "",
            file.content_type,
            data,
            text_columns=text_columns,
            metadata_columns=metadata_columns or [],
            row_id_column=row_id_column,
        )
    except CsvValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except IngestionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error


@router.get("", response_model=list[Source])
async def list_sources(
    workspace_id: WorkspaceQuery,
    repository: Annotated[InMemorySourceRepository, Depends(get_source_repository)],
) -> list[Source]:
    """List source metadata within one workspace."""
    return list(await repository.list(workspace_id))


@router.post("/{source_id}/index", response_model=SourceIndexResult)
async def index_source(
    source_id: UUID,
    workspace_id: WorkspaceQuery,
    repository: Annotated[InMemorySourceRepository, Depends(get_source_repository)],
    indexer: Annotated[VectorIndexingService, Depends(get_vector_indexing_service)],
) -> SourceIndexResult:
    """Explicitly rebuild retrieval indexes for a source's workspace."""
    try:
        await repository.get(workspace_id, source_id)
        metadata = await indexer.rebuild(workspace_id, source_id)
    except SourceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        ) from error
    except IndexingError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error
    return SourceIndexResult(
        workspace_id=workspace_id,
        source_id=source_id,
        document_count=metadata.document_count,
        generation_id=metadata.generation_id,
    )


@router.get("/{source_id}/documents", response_model=list[NormalizedDocument])
async def list_source_documents(
    source_id: UUID,
    workspace_id: WorkspaceQuery,
    repository: Annotated[InMemorySourceRepository, Depends(get_source_repository)],
    storage: Annotated[LocalSourceStorage, Depends(get_source_storage)],
) -> list[NormalizedDocument]:
    """Return persisted chunks and page locators for an accessible source."""
    try:
        await repository.get(workspace_id, source_id)
        return list(await storage.load_documents(workspace_id, source_id))
    except (SourceNotFoundError, FileNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        ) from error
