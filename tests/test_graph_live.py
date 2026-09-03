"""Opt-in live OpenAI graph extraction smoke test."""

import os
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.graph.openai_extractor import OpenAIGraphExtractor
from app.models import NormalizedDocument, SourceType


@pytest.mark.live_openai
@pytest.mark.asyncio
async def test_live_openai_graph_extraction() -> None:
    if os.getenv("RUN_LIVE_OPENAI_GRAPH_TEST") != "1":
        pytest.skip("Set RUN_LIVE_OPENAI_GRAPH_TEST=1 to call OpenAI")
    settings = Settings()
    if settings.openai_api_key is None or settings.graph_extraction_model is None:
        pytest.fail("Live graph extraction requires OpenAI graph settings")
    extractor = OpenAIGraphExtractor(
        api_key=settings.openai_api_key.get_secret_value(),
        model_name=settings.graph_extraction_model,
        timeout_seconds=settings.graph_extraction_timeout_seconds,
        max_retries=settings.graph_extraction_max_retries,
    )
    document = NormalizedDocument(
        workspace_id="live-smoke",
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="Project Atlas is part of the Operations department.",
        page_number=1,
    )

    extraction = await extractor.extract([document])

    assert len(extraction) == 1
    assert extraction[0].document_id == document.document_id
    assert extraction[0].entities
    assert extraction[0].relationships
