"""Environment-driven application configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_enabled: bool = False
    auth_require_verification: bool = True
    jwt_secret: SecretStr | None = None
    jwt_issuer: str = "knowledge-fusion"
    auth_allowed_origins: list[str] = ["http://localhost:3000"]
    frontend_url: str = "http://localhost:3000"
    auth_email_webhook_url: str | None = None
    auth_email_webhook_secret: SecretStr | None = None
    workflow_secret: SecretStr | None = None
    workflow_dispatch_url: str | None = None
    workflow_bypass_secret: SecretStr | None = None
    blob_read_write_token: SecretStr | None = None
    embedding_provider: Literal["huggingface", "voyage"] = "huggingface"
    reranker_provider: Literal["huggingface", "voyage"] = "huggingface"
    voyage_api_key: SecretStr | None = None
    voyage_embedding_model: str = "voyage-4"
    voyage_reranker_model: str = "rerank-2.5"
    serverless: bool = Field(default_factory=lambda: os.environ.get("VERCEL") == "1")
    job_max_documents: int = Field(default=10000, ge=1, le=100000)
    rate_limit_enabled: bool = True
    rate_limit_query_per_minute: int = Field(default=30, ge=1, le=10000)
    rate_limit_ingest_per_minute: int = Field(default=10, ge=1, le=10000)
    rate_limit_job_per_minute: int = Field(default=20, ge=1, le=10000)

    app_name: str = "Enterprise Knowledge Fusion RAG"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_base_url: str = "http://127.0.0.1:8000"
    data_dir: Path = Path("data")
    evaluation_reports_dir: Path = Path("data/evaluations")
    metadata_store_backend: Literal["memory", "postgresql"] = "memory"
    source_storage_backend: Literal["local", "azure_blob", "vercel_blob"] = "local"
    lexical_store_backend: Literal["local", "azure_blob", "vercel_blob"] = "local"
    graph_store_backend: Literal["local", "neo4j"] = "local"
    metadata_database_url: SecretStr | None = None
    metadata_database_schema: str = Field(
        default="rag", min_length=1, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    metadata_pool_size: int = Field(default=10, ge=1, le=100)
    metadata_pool_timeout_seconds: float = Field(default=30, gt=0, le=120)
    azure_blob_account_url: str | None = Field(default=None, min_length=1)
    azure_blob_container: str | None = Field(default=None, min_length=1, max_length=63)
    azure_storage_connection_string: SecretStr | None = None
    lexical_cache_max_generations: int = Field(default=8, ge=1, le=100)
    neo4j_uri: str | None = Field(default=None, min_length=1)
    neo4j_username: str | None = Field(default=None, min_length=1)
    neo4j_password: SecretStr | None = None
    neo4j_database: str = Field(default="neo4j", min_length=1, max_length=63)
    max_pdf_size_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    pdf_chunk_size: int = Field(default=1200, ge=100)
    pdf_chunk_overlap: int = Field(default=200, ge=0)
    website_max_pages: int = Field(default=20, ge=1, le=100)
    website_max_response_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    website_request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    website_max_redirects: int = Field(default=5, ge=0, le=10)
    website_crawl_delay_seconds: float = Field(default=0.25, ge=0, le=10)
    website_user_agent: str = Field(default="EnterpriseKnowledgeFusionRAG/0.1", min_length=1)
    website_chunk_size: int = Field(default=1200, ge=100)
    website_chunk_overlap: int = Field(default=200, ge=0)
    max_csv_size_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    csv_max_rows: int = Field(default=100_000, ge=1)
    csv_max_columns: int = Field(default=200, ge=1)
    csv_max_field_characters: int = Field(default=100_000, ge=1)
    csv_preview_rows: int = Field(default=10, ge=1, le=100)
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    embedding_dimension: int = Field(default=384, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1, le=512)
    embedding_query_prefix: str = "Represent this sentence for searching relevant passages: "
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_max_top_k: int = Field(default=20, ge=1, le=20)
    retrieval_min_similarity: float = Field(default=0.70, ge=-1, le=1)
    sentence_window_radius: int = Field(default=2, ge=0, le=10)
    bm25_k1: float = Field(default=1.5, gt=0)
    bm25_b: float = Field(default=0.75, ge=0, le=1)
    lexical_title_boost: float = Field(default=0.5, ge=0)
    lexical_min_score: float = Field(default=0.0, ge=0)
    fusion_rrf_k: int = Field(default=60, ge=1)
    fusion_candidate_multiplier: int = Field(default=3, ge=1, le=20)
    fusion_vector_weight: float = Field(default=0.40, ge=0)
    fusion_sentence_window_weight: float = Field(default=0.25, ge=0)
    fusion_graph_weight: float = Field(default=0.25, ge=0)
    fusion_lexical_weight: float = Field(default=0.10, ge=0)
    reranker_model_name: str = "BAAI/bge-reranker-base"
    reranker_batch_size: int = Field(default=16, ge=1, le=256)
    reranker_max_length: int = Field(default=512, ge=32, le=8192)
    reranker_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    reranking_candidate_pool_size: int = Field(default=30, ge=2, le=100)
    reranking_max_per_source: int = Field(default=2, ge=1, le=20)
    vector_store_backend: Literal["faiss", "pinecone"] = "faiss"
    pinecone_api_key: SecretStr | None = None
    pinecone_index_name: str | None = Field(default=None, min_length=1)
    pinecone_index_host: str | None = Field(default=None, min_length=1)
    pinecone_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    pinecone_upsert_batch_size: int = Field(default=100, ge=1, le=1000)
    pinecone_consistency_retries: int = Field(default=5, ge=1, le=20)
    pinecone_consistency_delay_seconds: float = Field(default=0.25, ge=0, le=5)
    openai_api_key: SecretStr | None = None
    graph_extraction_model: str | None = Field(default=None, min_length=1)
    graph_extraction_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    graph_extraction_max_retries: int = Field(default=2, ge=0, le=10)
    graph_extraction_batch_size: int = Field(default=8, ge=1, le=100)
    graph_extraction_max_batch_characters: int = Field(default=20_000, ge=100)
    graph_retrieval_max_hops: int = Field(default=2, ge=1, le=4)
    sql_generation_model: str | None = Field(default=None, min_length=1)
    sql_generation_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    sql_generation_max_retries: int = Field(default=2, ge=0, le=10)
    sql_execution_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    sql_max_rows: int = Field(default=100, ge=1, le=1000)
    routing_confidence_threshold: float = Field(default=0.70, ge=0, le=1)
    routing_winning_margin: float = Field(default=0.15, ge=0, le=1)

    @model_validator(mode="after")
    def validate_vector_store_configuration(self) -> "Settings":
        """Validate cross-field backend and fusion configuration."""
        if self.vector_store_backend == "pinecone":
            missing = [
                name
                for name, value in (
                    ("PINECONE_API_KEY", self.pinecone_api_key),
                    ("PINECONE_INDEX_NAME", self.pinecone_index_name),
                    ("PINECONE_INDEX_HOST", self.pinecone_index_host),
                )
                if value is None
            ]
            if missing:
                raise ValueError("Pinecone configuration is incomplete: " + ", ".join(missing))
        if (
            self.fusion_vector_weight
            + self.fusion_sentence_window_weight
            + self.fusion_graph_weight
            + self.fusion_lexical_weight
            <= 0
        ):
            raise ValueError("At least one fusion retriever weight must be positive")
        if self.auth_enabled:
            if self.jwt_secret is None or len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("JWT_SECRET must contain at least 32 characters")
            if self.metadata_store_backend != "postgresql":
                raise ValueError("Authentication requires PostgreSQL")
        if (
            self.embedding_provider == "voyage" or self.reranker_provider == "voyage"
        ) and self.voyage_api_key is None:
            raise ValueError("VOYAGE_API_KEY is required")
        if self.embedding_provider == "voyage" and self.embedding_dimension not in {
            256,
            512,
            1024,
            2048,
        }:
            raise ValueError("Voyage requires a supported embedding dimension and a new index")
        if "vercel_blob" in {self.source_storage_backend, self.lexical_store_backend}:
            if self.blob_read_write_token is None:
                raise ValueError("BLOB_READ_WRITE_TOKEN is required")
            if self.source_storage_backend != self.lexical_store_backend:
                raise ValueError("Vercel source and lexical storage must be enabled together")
            if self.app_env == "production" and not self.auth_enabled:
                raise ValueError(
                    "Private Vercel Blob storage in production requires AUTH_ENABLED so client "
                    "workspaces are isolated"
                )
        if self.serverless:
            if self.app_env != "production":
                raise ValueError("Vercel deployments require APP_ENV=production")
            if self.app_env == "production" and (
                not self.auth_require_verification
                or not self.auth_email_webhook_url
                or self.auth_email_webhook_secret is None
            ):
                raise ValueError(
                    "Public registration requires configured email verification and recovery"
                )
            if (
                not self.auth_enabled
                or self.embedding_provider != "voyage"
                or self.reranker_provider != "voyage"
            ):
                raise ValueError("Serverless requires authentication and hosted models")
            if self.workflow_secret is None or len(self.workflow_secret.get_secret_value()) < 32:
                raise ValueError("WORKFLOW_SECRET must contain at least 32 characters")
        required: list[str] = []
        if self.metadata_store_backend == "postgresql" and self.metadata_database_url is None:
            required.append("METADATA_DATABASE_URL")
        if (
            self.source_storage_backend == "azure_blob"
            or self.lexical_store_backend == "azure_blob"
        ):
            if self.azure_blob_account_url is None and self.azure_storage_connection_string is None:
                required.append("AZURE_BLOB_ACCOUNT_URL or AZURE_STORAGE_CONNECTION_STRING")
            if self.azure_blob_container is None:
                required.append("AZURE_BLOB_CONTAINER")
        if self.graph_store_backend == "neo4j":
            for name, value in (
                ("NEO4J_URI", self.neo4j_uri),
                ("NEO4J_USERNAME", self.neo4j_username),
                ("NEO4J_PASSWORD", self.neo4j_password),
            ):
                if value is None:
                    required.append(name)
        if required:
            raise ValueError(
                "Production persistence configuration is incomplete: " + ", ".join(required)
            )
        if self.app_env == "production":
            expected = {
                "METADATA_STORE_BACKEND": (self.metadata_store_backend, "postgresql"),
                "SOURCE_STORAGE_BACKEND": (self.source_storage_backend, "azure_blob"),
                "LEXICAL_STORE_BACKEND": (self.lexical_store_backend, "azure_blob"),
                "VECTOR_STORE_BACKEND": (self.vector_store_backend, "pinecone"),
                "GRAPH_STORE_BACKEND": (self.graph_store_backend, "neo4j"),
            }
            invalid = [
                name
                for name, (actual, wanted) in expected.items()
                if actual != wanted and not (wanted == "azure_blob" and actual == "vercel_blob")
            ]
            if invalid:
                raise ValueError(
                    "Production requires durable persistence backends: " + ", ".join(invalid)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
