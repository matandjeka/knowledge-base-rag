"""Safe operational contracts exposed to the web interface."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EffectiveSettings(BaseModel):
    """Non-secret runtime configuration safe for authenticated or local operators."""

    model_config = ConfigDict(extra="forbid")

    app_name: str
    app_env: Literal["development", "test", "production"]
    vector_store_backend: Literal["faiss", "pinecone"]
    metadata_store_backend: Literal["memory", "postgresql"]
    source_storage_backend: Literal["local", "azure_blob", "vercel_blob"]
    lexical_store_backend: Literal["local", "azure_blob", "vercel_blob"]
    graph_store_backend: Literal["local", "neo4j"]
    retrieval_top_k: int = Field(ge=1, le=20)
    retrieval_max_top_k: int = Field(ge=1, le=20)
    retrieval_min_similarity: float = Field(ge=-1, le=1)
    graph_retrieval_configured: bool
    sql_retrieval_configured: bool
    production_persistence_ready: bool


class EvaluationReportSummary(BaseModel):
    """Compact report metadata used by evaluation report selectors."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    configuration_name: str
    benchmark_version: str
    benchmark_sha256: str
    tier: Literal["deterministic", "live"]
    created_at: datetime
    passed: bool | None
    baseline_configuration: str | None


class EvaluationReportListing(BaseModel):
    """Valid report summaries plus artifacts rejected during discovery."""

    model_config = ConfigDict(extra="forbid")

    reports: list[EvaluationReportSummary]
    invalid_report_ids: list[str]
