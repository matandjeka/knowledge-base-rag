"""Process-local dependency construction for API routes."""

from functools import lru_cache

from app.core.config import get_settings
from app.ingestion.pdf import PdfConnector
from app.ingestion.service import PdfIngestionService
from app.repositories import InMemorySourceRepository
from app.storage import LocalSourceStorage


@lru_cache
def get_source_repository() -> InMemorySourceRepository:
    """Return the process-local source registry."""
    return InMemorySourceRepository()


@lru_cache
def get_source_storage() -> LocalSourceStorage:
    """Return local source artifact storage."""
    return LocalSourceStorage(get_settings().data_dir)


@lru_cache
def get_pdf_ingestion_service() -> PdfIngestionService:
    """Return the configured PDF ingestion orchestrator."""
    settings = get_settings()
    return PdfIngestionService(
        repository=get_source_repository(),
        storage=get_source_storage(),
        connector=PdfConnector(settings.max_pdf_size_bytes),
        chunk_size=settings.pdf_chunk_size,
        chunk_overlap=settings.pdf_chunk_overlap,
    )
