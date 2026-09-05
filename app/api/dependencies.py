"""Process-local dependency construction for API routes."""

from functools import lru_cache

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
from app.graph.openai_extractor import OpenAIGraphExtractor
from app.graph.retrieval import GraphRetriever
from app.graph.store import LocalGraphStore
from app.ingestion.csv import CsvConnector
from app.ingestion.csv_service import CsvIngestionService
from app.ingestion.pdf import PdfConnector
from app.ingestion.service import PdfIngestionService
from app.ingestion.website import SafeHttpFetcher, WebsiteCrawler
from app.ingestion.website_service import WebsiteIngestionService
from app.models import RetrievalMode
from app.repositories import InMemorySourceRepository
from app.reranking.service import HuggingFaceCrossEncoderReranker, RerankingService
from app.retrieval.base import Retriever
from app.retrieval.embedding import HuggingFaceEmbeddingService
from app.retrieval.fusion import FusionRetriever
from app.retrieval.indexing import VectorIndexingService
from app.retrieval.lexical import LexicalRetriever, LocalLexicalStore
from app.retrieval.pinecone_store import PineconeVectorStore
from app.retrieval.query_service import QueryService
from app.retrieval.sentence_window import SentenceWindowRetriever
from app.retrieval.vector import VectorRetriever
from app.retrieval.vector_store import FaissVectorStore
from app.routing import RuleBasedQueryRouter
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
def get_graph_store() -> LocalGraphStore:
    """Return the durable local graph store."""
    settings = get_settings()
    return LocalGraphStore(
        settings.data_dir, expected_extractor_model=settings.graph_extraction_model
    )


@lru_cache
def get_lexical_store() -> LocalLexicalStore:
    """Return the durable local BM25 store."""
    settings = get_settings()
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
        get_source_storage(),
        extractor,
        get_graph_store(),
        batch_size=settings.graph_extraction_batch_size,
        max_batch_characters=settings.graph_extraction_max_batch_characters,
    )


@lru_cache
def get_reranking_service() -> RerankingService:
    """Return the lazy local cross-encoder re-ranking service."""
    settings = get_settings()
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
