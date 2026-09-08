"""Configuration tests."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.bm25_k1 == 1.5
    assert settings.bm25_b == 0.75
    assert settings.lexical_title_boost == 0.5
    assert settings.lexical_min_score == 0
    assert settings.fusion_rrf_k == 60
    assert settings.fusion_candidate_multiplier == 3
    assert settings.fusion_vector_weight == 0.4
    assert settings.fusion_sentence_window_weight == 0.25
    assert settings.fusion_graph_weight == 0.25
    assert settings.fusion_lexical_weight == 0.1
    assert settings.reranker_model_name == "BAAI/bge-reranker-base"
    assert settings.reranker_batch_size == 16
    assert settings.reranker_max_length == 512
    assert settings.reranker_device == "auto"
    assert settings.reranking_candidate_pool_size == 30
    assert settings.reranking_max_per_source == 2


def test_settings_require_at_least_one_positive_fusion_weight() -> None:
    with pytest.raises(ValidationError, match="fusion retriever weight"):
        Settings(
            _env_file=None,
            fusion_vector_weight=0,
            fusion_sentence_window_weight=0,
            fusion_graph_weight=0,
            fusion_lexical_weight=0,
        )


def test_production_rejects_process_local_persistence_defaults() -> None:
    with pytest.raises(ValidationError, match="durable persistence backends"):
        Settings(_env_file=None, app_env="production")


def _vercel_blob_production_settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        app_env="production",
        metadata_store_backend="postgresql",
        metadata_database_url="postgresql+asyncpg://user:secret@db.example/rag",
        source_storage_backend="vercel_blob",
        lexical_store_backend="vercel_blob",
        blob_read_write_token="blob-token",
        vector_store_backend="pinecone",
        pinecone_api_key="secret",
        pinecone_index_name="rag",
        pinecone_index_host="rag.example.pinecone.io",
        graph_store_backend="neo4j",
        neo4j_uri="neo4j+s://graph.example.com",
        neo4j_username="neo4j",
        neo4j_password="secret",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_production_vercel_blob_requires_authentication() -> None:
    with pytest.raises(ValidationError, match="AUTH_ENABLED"):
        _vercel_blob_production_settings()


def test_production_vercel_blob_with_authentication_is_accepted() -> None:
    settings = _vercel_blob_production_settings(
        auth_enabled=True,
        jwt_secret="a-testing-secret-value-of-at-least-32-chars",
    )

    assert settings.source_storage_backend == "vercel_blob"


def test_production_accepts_complete_durable_persistence_configuration() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        metadata_store_backend="postgresql",
        metadata_database_url="postgresql+asyncpg://user:secret@db.example/rag",
        source_storage_backend="azure_blob",
        lexical_store_backend="azure_blob",
        azure_blob_account_url="https://storage.example.com",
        azure_blob_container="rag",
        vector_store_backend="pinecone",
        pinecone_api_key="secret",
        pinecone_index_name="rag",
        pinecone_index_host="rag.example.pinecone.io",
        graph_store_backend="neo4j",
        neo4j_uri="neo4j+s://graph.example.com",
        neo4j_username="neo4j",
        neo4j_password="secret",
    )

    assert settings.metadata_store_backend == "postgresql"
