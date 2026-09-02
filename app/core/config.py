"""Environment-driven application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
