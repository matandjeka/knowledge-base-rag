"""Environment-driven application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Enterprise Knowledge Fusion RAG"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_base_url: str = "http://127.0.0.1:8000"
    data_dir: Path = Path("data")
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
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
