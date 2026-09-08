"""Process-local dependency construction for API routes."""

from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.exceptions import GraphConfigurationError
from app.database import (
    DatabaseConnectionManager,
    DatabaseRegistrationService,
    DatabaseRetriever,
    EnvironmentSecretResolver,
    OpenAISqlGenerator,
    SqlValidator,
)
from app.generation.extractive import ExtractiveGenerator
from app.graph.indexing import GraphIndexingService
from app.graph.neo4j_store import Neo4jGraphStore
from app.graph.openai_extractor import OpenAIGraphExtractor
from app.graph.retrieval import GraphRetriever
from app.graph.store import GraphStore, LocalGraphStore
from app.hosted.voyage import VoyageClient, VoyageEmbeddingService, VoyageReranker
from app.ingestion.csv import CsvConnector
from app.ingestion.csv_service import CsvIngestionService
from app.ingestion.pdf import PdfConnector
from app.ingestion.service import PdfIngestionService
from app.ingestion.website import SafeHttpFetcher, WebsiteCrawler
from app.ingestion.website_service import WebsiteIngestionService
from app.models import RetrievalMode
from app.persistence import PersistenceRepository, PostgresPersistenceRepository
from app.repositories import InMemorySourceRepository, SourceRepository
from app.reranking.service import HuggingFaceCrossEncoderReranker, RerankingService
from app.retrieval.base import Retriever
from app.retrieval.blob_lexical import BlobLexicalStore
from app.retrieval.embedding import EmbeddingService, HuggingFaceEmbeddingService
from app.retrieval.fusion import FusionRetriever
from app.retrieval.indexing import VectorIndexingService
from app.retrieval.lexical import LexicalRetriever, LexicalStore, LocalLexicalStore
from app.retrieval.pinecone_store import PineconeVectorStore
from app.retrieval.query_service import QueryService
from app.retrieval.sentence_window import SentenceWindowRetriever
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import FaissVectorStore
from app.routing import RuleBasedQueryRouter
from app.storage import AzureBlobSourceStorage, LocalSourceStorage, SourceStorage
from app.storage.vercel_blob import VercelBlobLexicalStore, VercelBlobSourceStorage


async def close_application_dependencies() -> None:
    """Close process-wide production clients without constructing unused adapters."""
    settings = get_settings()
    await get_database_connection_manager().close()
    if settings.graph_store_backend == "neo4j":
        graph_store = get_graph_store()
        if isinstance(graph_store, Neo4jGraphStore):
            await graph_store.close()
    if settings.source_storage_backend == "vercel_blob":
        vercel_storage = get_source_storage()
        if isinstance(vercel_storage, VercelBlobSourceStorage):
            await vercel_storage.close()
            get_source_storage.cache_clear()
            get_lexical_store.cache_clear()
    if settings.source_storage_backend == "azure_blob":
        source_storage = get_source_storage()
        if isinstance(source_storage, AzureBlobSourceStorage):
            await source_storage.close()
    if settings.metadata_store_backend == "postgresql":
        await get_metadata_engine().dispose()


@lru_cache
def get_metadata_engine() -> AsyncEngine:
    """Return the process-wide async metadata database engine."""
    settings = get_settings()
    if settings.metadata_database_url is None:
        raise RuntimeError("METADATA_DATABASE_URL is required")
    if settings.serverless:
        return create_async_engine(
            settings.metadata_database_url.get_secret_value(),
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": settings.metadata_database_schema}},
        )
    return create_async_engine(
        settings.metadata_database_url.get_secret_value(),
        pool_size=settings.metadata_pool_size,
        pool_timeout=settings.metadata_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": settings.metadata_database_schema}},
    )


@lru_cache
def get_source_repository() -> SourceRepository:
    """Return the configured authoritative source registry."""
    if get_settings().metadata_store_backend == "postgresql":
        return PostgresPersistenceRepository(get_metadata_engine())
    return InMemorySourceRepository()


def get_persistence_repository() -> PersistenceRepository:
    """Return durable generation coordination when production metadata is enabled."""
    repository = get_source_repository()
    if not isinstance(repository, PostgresPersistenceRepository):
        raise RuntimeError("Durable generation coordination requires PostgreSQL metadata")
    return repository


@lru_cache
def get_source_storage() -> SourceStorage:
    """Return configured source artifact storage."""
    settings = get_settings()
    if settings.source_storage_backend == "vercel_blob":
        assert settings.blob_read_write_token is not None
        return VercelBlobSourceStorage(settings.blob_read_write_token.get_secret_value())
    if settings.source_storage_backend == "local":
        return LocalSourceStorage(settings.data_dir)
    if settings.azure_blob_container is None:
        raise RuntimeError("Validated Azure Blob configuration is incomplete")
    return AzureBlobSourceStorage.from_connection(
        account_url=settings.azure_blob_account_url,
        container=settings.azure_blob_container,
        connection_string=(
            settings.azure_storage_connection_string.get_secret_value()
            if settings.azure_storage_connection_string is not None
            else None
        ),
    )


def require_blob_storage() -> VercelBlobSourceStorage:
    """Return the private Vercel Blob store, or fail closed when it is not configured."""
    value = get_source_storage()
    if not isinstance(value, VercelBlobSourceStorage):
        raise HTTPException(503, "This operation requires private Vercel Blob storage.")
    return value


@lru_cache
def get_embedding_service() -> EmbeddingService:
    """Return the cached local Hugging Face embedding adapter."""
    settings = get_settings()
    if settings.embedding_provider == "voyage":
        assert settings.voyage_api_key is not None
        return VoyageEmbeddingService(
            VoyageClient(settings.voyage_api_key.get_secret_value()),
            model_name=settings.voyage_embedding_model,
            dimension=settings.embedding_dimension,
            batch_size=settings.embedding_batch_size,
        )
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
        generation_repository=(
            get_persistence_repository()
            if settings.metadata_store_backend == "postgresql"
            else None
        ),
    )


@lru_cache
def get_graph_store() -> GraphStore:
    """Return the configured graph store."""
    settings = get_settings()
    if settings.graph_store_backend == "neo4j":
        if (
            settings.neo4j_uri is None
            or settings.neo4j_username is None
            or settings.neo4j_password is None
        ):
            raise RuntimeError("Validated Neo4j configuration is incomplete")
        return Neo4jGraphStore.from_connection(
            uri=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password.get_secret_value(),
            database=settings.neo4j_database,
            generations=get_persistence_repository(),
        )
    return LocalGraphStore(
        settings.data_dir, expected_extractor_model=settings.graph_extraction_model
    )


@lru_cache
def get_lexical_store() -> LexicalStore:
    """Return the configured immutable BM25 store."""
    settings = get_settings()
    if settings.lexical_store_backend == "vercel_blob":
        return VercelBlobLexicalStore(
            get_source_storage(),
            get_persistence_repository(),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
            max_cached_generations=settings.lexical_cache_max_generations,
        )
    if settings.lexical_store_backend == "azure_blob":
        storage = get_source_storage()
        if not isinstance(storage, AzureBlobSourceStorage):
            raise RuntimeError("Blob lexical storage requires Azure Blob source storage")
        return BlobLexicalStore(
            storage.container_client,
            get_persistence_repository(),
            k1=settings.bm25_k1,
            b=settings.bm25_b,
            max_cached_generations=settings.lexical_cache_max_generations,
        )
    return LocalLexicalStore(settings.data_dir, k1=settings.bm25_k1, b=settings.bm25_b)


@lru_cache
def get_graph_indexing_service() -> GraphIndexingService:
    """Return graph indexing only when its external extractor is configured."""
    settings = get_settings()
    if settings.openai_api_key is None or settings.graph_extraction_model is None:
        raise GraphConfigurationError(
            "OPENAI_API_KEY and GRAPH_EXTRACTION_MODEL are required for graph indexing"
        )
    extractor = OpenAIGraphExtractor(
        api_key=settings.openai_api_key.get_secret_value(),
        model_name=settings.graph_extraction_model,
        timeout_seconds=settings.graph_extraction_timeout_seconds,
        max_retries=settings.graph_extraction_max_retries,
    )
    return GraphIndexingService(
        get_source_repository(),
        get_source_storage(),
        extractor,
        get_graph_store(),
        batch_size=settings.graph_extraction_batch_size,
        max_batch_characters=settings.graph_extraction_max_batch_characters,
        persistence_repository=(
            get_persistence_repository()
            if settings.metadata_store_backend == "postgresql"
            else None
        ),
    )


@lru_cache
def get_reranking_service() -> RerankingService:
    """Return the lazy local cross-encoder re-ranking service."""
    settings = get_settings()
    if settings.reranker_provider == "voyage":
        assert settings.voyage_api_key is not None
        return RerankingService(
            VoyageReranker(
                VoyageClient(settings.voyage_api_key.get_secret_value()),
                settings.voyage_reranker_model,
            ),
            max_per_source=settings.reranking_max_per_source,
        )
    return RerankingService(
        HuggingFaceCrossEncoderReranker(
            model_name=settings.reranker_model_name,
            batch_size=settings.reranker_batch_size,
            max_length=settings.reranker_max_length,
            device=settings.reranker_device,
        ),
        max_per_source=settings.reranking_max_per_source,
    )


@lru_cache
def get_database_connection_manager() -> DatabaseConnectionManager:
    """Return the environment-backed external database connection boundary."""
    return DatabaseConnectionManager(
        EnvironmentSecretResolver(),
        timeout_seconds=get_settings().sql_execution_timeout_seconds,
    )


@lru_cache
def get_database_registration_service() -> DatabaseRegistrationService:
    """Return database source registration orchestration."""
    return DatabaseRegistrationService(get_source_repository(), get_database_connection_manager())


def _database_retriever() -> DatabaseRetriever | None:
    settings = get_settings()
    if settings.openai_api_key is None or settings.sql_generation_model is None:
        return None
    return DatabaseRetriever(
        get_source_repository(),
        get_database_connection_manager(),
        OpenAISqlGenerator(
            api_key=settings.openai_api_key.get_secret_value(),
            model_name=settings.sql_generation_model,
            timeout_seconds=settings.sql_generation_timeout_seconds,
            max_retries=settings.sql_generation_max_retries,
        ),
        SqlValidator(max_rows=settings.sql_max_rows),
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
        lexical_store=get_lexical_store(),
        persistence_repository=(
            get_persistence_repository()
            if get_settings().metadata_store_backend == "postgresql"
            else None
        ),
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
    vector_retriever = VectorRetriever(get_embedding_service(), get_vector_store())
    sentence_window_retriever = SentenceWindowRetriever(get_embedding_service(), get_vector_store())
    graph_retriever = GraphRetriever(get_graph_store(), max_hops=settings.graph_retrieval_max_hops)
    lexical_retriever = LexicalRetriever(
        get_lexical_store(),
        title_boost=settings.lexical_title_boost,
        generation_provider=get_vector_store(),
    )
    retrievers: dict[RetrievalMode, Retriever] = {
        RetrievalMode.VECTOR: vector_retriever,
        RetrievalMode.SENTENCE_WINDOW: sentence_window_retriever,
        RetrievalMode.GRAPH: graph_retriever,
        RetrievalMode.LEXICAL: lexical_retriever,
    }
    fusion_retriever = FusionRetriever(
        retrievers,
        max_top_k=settings.retrieval_max_top_k,
        candidate_multiplier=settings.fusion_candidate_multiplier,
        rrf_k=settings.fusion_rrf_k,
        weights={
            RetrievalMode.VECTOR: settings.fusion_vector_weight,
            RetrievalMode.SENTENCE_WINDOW: settings.fusion_sentence_window_weight,
            RetrievalMode.GRAPH: settings.fusion_graph_weight,
            RetrievalMode.LEXICAL: settings.fusion_lexical_weight,
        },
        min_similarity=settings.retrieval_min_similarity,
        lexical_min_score=settings.lexical_min_score,
    )
    return QueryService(
        get_source_repository(),
        vector_retriever,
        sentence_window_retriever,
        ExtractiveGenerator(),
        default_top_k=settings.retrieval_top_k,
        max_top_k=settings.retrieval_max_top_k,
        min_similarity=settings.retrieval_min_similarity,
        graph_retriever=graph_retriever,
        lexical_retriever=lexical_retriever,
        lexical_min_score=settings.lexical_min_score,
        fusion_retriever=fusion_retriever,
        reranking_service=get_reranking_service(),
        reranking_candidate_pool_size=settings.reranking_candidate_pool_size,
        database_retriever=_database_retriever(),
        query_router=RuleBasedQueryRouter(
            confidence_threshold=settings.routing_confidence_threshold,
            winning_margin=settings.routing_winning_margin,
        ),
    )
