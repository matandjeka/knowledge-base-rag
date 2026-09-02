"""Process-local dependency construction for API routes."""

from functools import lru_cache

from app.core.config import get_settings
from app.ingestion.csv import CsvConnector
from app.ingestion.csv_service import CsvIngestionService
from app.ingestion.pdf import PdfConnector
from app.ingestion.service import PdfIngestionService
from app.ingestion.website import SafeHttpFetcher, WebsiteCrawler
from app.ingestion.website_service import WebsiteIngestionService
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


@lru_cache
def get_csv_ingestion_service() -> CsvIngestionService:
    """Return the configured CSV ingestion orchestrator."""
    settings = get_settings()
    return CsvIngestionService(
        repository=get_source_repository(),
        storage=get_source_storage(),
        connector=CsvConnector(
            max_size_bytes=settings.max_csv_size_bytes,
            max_rows=settings.csv_max_rows,
            max_columns=settings.csv_max_columns,
            max_field_characters=settings.csv_max_field_characters,
            preview_rows=settings.csv_preview_rows,
        ),
    )


@lru_cache
def get_website_ingestion_service() -> WebsiteIngestionService:
    """Return the configured website ingestion orchestrator."""
    settings = get_settings()
    fetcher = SafeHttpFetcher(
        user_agent=settings.website_user_agent,
        timeout_seconds=settings.website_request_timeout_seconds,
        max_response_bytes=settings.website_max_response_bytes,
        max_redirects=settings.website_max_redirects,
    )
    crawler = WebsiteCrawler(
        fetcher,
        user_agent=settings.website_user_agent,
        default_delay_seconds=settings.website_crawl_delay_seconds,
    )
    return WebsiteIngestionService(
        repository=get_source_repository(),
        storage=get_source_storage(),
        crawler=crawler,
        chunk_size=settings.website_chunk_size,
        chunk_overlap=settings.website_chunk_overlap,
        max_pages=settings.website_max_pages,
    )
