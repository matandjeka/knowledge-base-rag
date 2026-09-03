"""Process-local dependency construction for API routes."""

from functools import lru_cache

from app.core.config import get_settings
from app.generation.extractive import ExtractiveGenerator
from app.ingestion.csv import CsvConnector
from app.ingestion.csv_service import CsvIngestionService
from app.ingestion.pdf import PdfConnector
from app.ingestion.service import PdfIngestionService
from app.ingestion.website import SafeHttpFetcher, WebsiteCrawler
from app.ingestion.website_service import WebsiteIngestionService
from app.repositories import InMemorySourceRepository
from app.retrieval.embedding import HuggingFaceEmbeddingService
from app.retrieval.indexing import VectorIndexingService
from app.retrieval.pinecone_store import PineconeVectorStore
from app.retrieval.query_service import QueryService
from app.retrieval.sentence_window import SentenceWindowRetriever
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import FaissVectorStore
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
def get_embedding_service() -> HuggingFaceEmbeddingService:
    """Return the cached local Hugging Face embedding adapter."""
    settings = get_settings()
    return HuggingFaceEmbeddingService(
        model_name=settings.embedding_model_name,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        query_prefix=settings.embedding_query_prefix,
    )


@lru_cache
def get_vector_store() -> FaissVectorStore | PineconeVectorStore:
    """Return the configured durable vector store."""
    settings = get_settings()
    if settings.vector_store_backend == "faiss":
        return FaissVectorStore(settings.data_dir)
    if (
        settings.pinecone_api_key is None
        or settings.pinecone_index_name is None
        or settings.pinecone_index_host is None
    ):
        raise RuntimeError("Validated Pinecone settings are unexpectedly incomplete")
    return PineconeVectorStore(
        api_key=settings.pinecone_api_key.get_secret_value(),
        index_name=settings.pinecone_index_name,
        index_host=settings.pinecone_index_host,
        dimension=settings.embedding_dimension,
        timeout_seconds=settings.pinecone_timeout_seconds,
        batch_size=settings.pinecone_upsert_batch_size,
        consistency_retries=settings.pinecone_consistency_retries,
        consistency_delay_seconds=settings.pinecone_consistency_delay_seconds,
    )


@lru_cache
def get_vector_indexing_service() -> VectorIndexingService:
    """Return the workspace vector-index orchestrator."""
    return VectorIndexingService(
        get_source_repository(),
        get_source_storage(),
        get_embedding_service(),
        get_vector_store(),
        sentence_window_radius=get_settings().sentence_window_radius,
    )


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
        indexer=get_vector_indexing_service(),
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
        indexer=get_vector_indexing_service(),
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
        indexer=get_vector_indexing_service(),
    )


@lru_cache
def get_query_service() -> QueryService:
    """Return the configured baseline query orchestrator."""
    settings = get_settings()
    return QueryService(
        get_source_repository(),
        VectorRetriever(get_embedding_service(), get_vector_store()),
        SentenceWindowRetriever(get_embedding_service(), get_vector_store()),
        ExtractiveGenerator(),
        default_top_k=settings.retrieval_top_k,
        max_top_k=settings.retrieval_max_top_k,
        min_similarity=settings.retrieval_min_similarity,
    )
