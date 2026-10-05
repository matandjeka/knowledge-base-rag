"""Knowledge-source registration and inspection routes."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    get_csv_ingestion_service,
    get_database_registration_service,
    get_docx_ingestion_service,
    get_pdf_ingestion_service,
    get_source_repository,
    get_source_storage,
    get_vector_indexing_service,
    get_website_ingestion_service,
)
from app.core.config import get_settings
from app.core.exceptions import (
    CsvValidationError,
    DatabaseConfigurationError,
    DatabaseExecutionError,
    DocxValidationError,
    IndexingError,
    IngestionError,
    PdfValidationError,
    SourceNotFoundError,
    WebsiteValidationError,
)
from app.core.rate_limit import rate_limit
from app.database import DatabaseRegistrationService
from app.ingestion.csv_service import CsvIngestionService
from app.ingestion.docx_service import DocxIngestionService
from app.ingestion.service import PdfIngestionService
from app.ingestion.website_service import WebsiteIngestionService
from app.models import (
    Classification,
    CsvIngestionResult,
    CsvPreviewResult,
    DatabaseSourceRequest,
    DatabaseSourceResult,
    DocxIngestionResult,
    NormalizedDocument,
    PdfIngestionResult,
    Source,
    SourceIndexResult,
    SourceUpdate,
    WebsiteIngestionRequest,
    WebsiteIngestionResult,
    classification_visible,
)
from app.repositories import SourceRepository
from app.retrieval.indexing import VectorIndexingService
from app.storage import SourceStorage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])
_ingest_limit = rate_limit("ingest", "rate_limit_ingest_per_minute")

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


@router.post(
    "/database",
    response_model=DatabaseSourceResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_ingest_limit],
)
async def add_database(
    request: DatabaseSourceRequest,
    service: Annotated[DatabaseRegistrationService, Depends(get_database_registration_service)],
) -> DatabaseSourceResult:
    """Verify and register one credential-safe relational database source."""
    try:
        return await service.register(request)
    except (DatabaseConfigurationError, DatabaseExecutionError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.post(
    "/pdf",
    response_model=PdfIngestionResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_ingest_limit],
)
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


@router.post(
    "/docx",
    response_model=DocxIngestionResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_ingest_limit],
)
async def upload_docx(
    workspace_id: WorkspaceForm,
    file: Annotated[UploadFile, File(description="DOCX knowledge source")],
    service: Annotated[DocxIngestionService, Depends(get_docx_ingestion_service)],
) -> DocxIngestionResult:
    """Validate, store, parse, chunk, and register one DOCX."""
    settings = get_settings()
    data = await file.read(settings.max_docx_size_bytes + 1)
    await file.close()
    try:
        return await service.ingest(
            workspace_id=workspace_id,
            filename=file.filename or "",
            content_type=file.content_type,
            data=data,
        )
    except DocxValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except IngestionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error


@router.post(
    "/website",
    response_model=WebsiteIngestionResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_ingest_limit],
)
async def add_website(
    request: WebsiteIngestionRequest,
    service: Annotated[WebsiteIngestionService, Depends(get_website_ingestion_service)],
) -> WebsiteIngestionResult:
    """Validate, crawl, extract, chunk, and register one website."""
    allowed_domains = None
    if get_settings().metadata_store_backend == "postgresql":
        from app.api.dependencies import get_metadata_engine
        from app.retention import RetentionRepository

        allowed_domains = await RetentionRepository(get_metadata_engine()).crawl_allowlist(
            request.workspace_id
        )
    try:
        return await service.ingest(
            request.workspace_id,
            request.url,
            crawl_same_domain=request.crawl_same_domain,
            page_limit=request.page_limit,
            allowed_domains=allowed_domains,
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


@router.post(
    "/csv",
    response_model=CsvIngestionResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_ingest_limit],
)
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


def _clearance(request: Request) -> Classification:
    membership = getattr(request.state, "membership", None)
    return membership.effective_clearance if membership is not None else Classification.RESTRICTED


@router.get("", response_model=list[Source])
async def list_sources(
    workspace_id: WorkspaceQuery,
    request: Request,
    repository: Annotated[SourceRepository, Depends(get_source_repository)],
) -> list[Source]:
    """List source metadata the caller's clearance permits within one workspace."""
    clearance = _clearance(request)
    return [
        source
        for source in await repository.list(workspace_id)
        if classification_visible(source.classification, clearance)
    ]


class ClassificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Classification


@router.patch("/{source_id}", response_model=Source)
async def set_source_classification(
    source_id: UUID,
    body: ClassificationUpdate,
    workspace_id: WorkspaceQuery,
    request: Request,
    repository: Annotated[SourceRepository, Depends(get_source_repository)],
) -> Source:
    """Change a source's confidentiality label (admin and owner only)."""
    from app.auth.organizations import Role, role_allows

    membership = getattr(request.state, "membership", None)
    settings = get_settings()
    if settings.auth_enabled and (
        membership is None or not role_allows(membership.role, Role.ADMIN)
    ):
        raise HTTPException(403, "Changing a security label requires an admin role.")
    try:
        current = await repository.get(workspace_id, source_id)
        if not classification_visible(current.classification, _clearance(request)):
            raise SourceNotFoundError(str(source_id))
        updated = await repository.update(
            workspace_id, source_id, SourceUpdate(classification=body.classification)
        )
        from app.audit import record

        await record(
            request,
            "source.classification_changed",
            target_type="source",
            target_id=str(source_id),
            metadata={
                "from": current.classification.value,
                "to": body.classification.value,
            },
        )
        return updated
    except SourceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        ) from error


_FINISHED_JOB_STATUSES = frozenset({"complete", "cancelled", "failed"})


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: UUID,
    workspace_id: WorkspaceQuery,
    request: Request,
    repository: Annotated[SourceRepository, Depends(get_source_repository)],
    storage: Annotated[SourceStorage, Depends(get_source_storage)],
) -> None:
    """Remove a source and its stored artifacts (admin and owner only).

    Retrieval is restricted to registered sources, so the source stops appearing in answers
    immediately; its index entries are dropped at the workspace's next index build.
    """
    from app.auth.organizations import Role, role_allows

    membership = getattr(request.state, "membership", None)
    settings = get_settings()
    if settings.auth_enabled and (
        membership is None or not role_allows(membership.role, Role.ADMIN)
    ):
        raise HTTPException(403, "Removing a source requires an admin role.")
    try:
        source = await repository.get(workspace_id, source_id)
        if not classification_visible(source.classification, _clearance(request)):
            raise SourceNotFoundError(str(source_id))
        if settings.metadata_store_backend == "postgresql":
            from app.api.dependencies import get_metadata_engine
            from app.jobs.repository import JobRepository

            jobs = await JobRepository(get_metadata_engine()).list(workspace_id)
            if any(job["status"] not in _FINISHED_JOB_STATUSES for job in jobs):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Wait for ingestion activity to finish, or cancel it, "
                    "before removing a source.",
                )
        await repository.delete(workspace_id, source_id)
    except SourceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        ) from error
    try:
        await storage.delete_source(workspace_id, source_id)
    except Exception:
        # The registry is authoritative and retrieval already excludes the source.
        logger.exception("Stored artifacts of a removed source could not be deleted")
    from app.audit import record

    await record(
        request,
        "source.deleted",
        target_type="source",
        target_id=str(source_id),
        metadata={"name": source.name, "type": source.config.source_type.value},
    )


@router.post("/{source_id}/index", response_model=SourceIndexResult)
async def index_source(
    source_id: UUID,
    workspace_id: WorkspaceQuery,
    repository: Annotated[SourceRepository, Depends(get_source_repository)],
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
    request: Request,
    repository: Annotated[SourceRepository, Depends(get_source_repository)],
    storage: Annotated[SourceStorage, Depends(get_source_storage)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> list[NormalizedDocument]:
    """Return persisted chunks and page locators for an accessible source.

    ``limit`` is optional: when omitted the full document set is returned, preserving the
    original contract for callers such as the Streamlit source inspector. A source above the
    caller's clearance is reported as not found.
    """
    try:
        source = await repository.get(workspace_id, source_id)
        if not classification_visible(source.classification, _clearance(request)):
            raise SourceNotFoundError(str(source_id))
        documents = list(await storage.load_documents(workspace_id, source_id))
        end = None if limit is None else offset + limit
        return documents[offset:end]
    except (SourceNotFoundError, FileNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        ) from error
