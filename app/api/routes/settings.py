"""Read-only, secret-safe application settings route."""

from fastapi import APIRouter

from app.core.config import get_settings
from app.models.system import EffectiveSettings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=EffectiveSettings)
async def effective_settings() -> EffectiveSettings:
    """Return the operational subset needed by the Streamlit interface."""
    settings = get_settings()
    return EffectiveSettings(
        app_name=settings.app_name,
        app_env=settings.app_env,
        vector_store_backend=settings.vector_store_backend,
        metadata_store_backend=settings.metadata_store_backend,
        source_storage_backend=settings.source_storage_backend,
        lexical_store_backend=settings.lexical_store_backend,
        graph_store_backend=settings.graph_store_backend,
        retrieval_top_k=settings.retrieval_top_k,
        retrieval_max_top_k=settings.retrieval_max_top_k,
        retrieval_min_similarity=settings.retrieval_min_similarity,
        graph_retrieval_configured=(
            settings.openai_api_key is not None and settings.graph_extraction_model is not None
        ),
        sql_retrieval_configured=(
            settings.openai_api_key is not None and settings.sql_generation_model is not None
        ),
        production_persistence_ready=(
            settings.metadata_store_backend == "postgresql"
            and settings.source_storage_backend in {"azure_blob", "vercel_blob"}
            and settings.lexical_store_backend in {"azure_blob", "vercel_blob"}
            and settings.vector_store_backend == "pinecone"
            and settings.graph_store_backend == "neo4j"
        ),
    )
