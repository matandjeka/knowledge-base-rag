"""OpenAI provider validation and explicit fusion-only configuration."""

import json

import httpx
import numpy as np
import pytest

from app.core.config import Settings
from app.core.exceptions import IndexingError
from app.hosted.openai import OpenAIEmbeddingService


def response(
    data: list[dict[str, object]], model: str = "text-embedding-3-small"
) -> dict[str, object]:
    return {
        "object": "list",
        "model": model,
        "data": data,
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


@pytest.mark.asyncio
async def test_openai_batches_and_query_share_model_space() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json=response(
                [
                    {"object": "embedding", "index": i, "embedding": [3, 4]}
                    for i in reversed(range(len(body["input"])))
                ]
            ),
        )

    service = OpenAIEmbeddingService(
        "fake", dimension=2, batch_size=2, transport=httpx.MockTransport(handler)
    )
    np.testing.assert_allclose(await service.embed_documents(["a", "b", "c"]), [[0.6, 0.8]] * 3)
    np.testing.assert_allclose(await service.embed_query("question"), [0.6, 0.8])
    assert [r["input"] for r in requests] == [["a", "b"], ["c"], ["question"]]
    assert all(r["dimensions"] == 2 and r["encoding_format"] == "float" for r in requests)
    assert all("input_type" not in r for r in requests)
    assert (await service.embed_documents([])).shape == (0, 2)
    with pytest.raises(IndexingError):
        await service.embed_query(" ")
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 1, "embedding": [3, 4]}],
        [{"index": 0, "embedding": [3]}],
        [{"index": 0, "embedding": [0, 0]}],
        [{"index": 0, "embedding": [3, 4]}, {"index": 0, "embedding": [3, 4]}],
    ],
)
async def test_openai_rejects_invalid_results(data: list[dict[str, object]]) -> None:
    service = OpenAIEmbeddingService(
        "fake",
        dimension=2,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(data))),
    )
    with pytest.raises(IndexingError):
        await service.embed_query("a")


@pytest.mark.asyncio
async def test_openai_retries_transient_errors_but_sanitizes_auth_errors() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429, headers={"retry-after-ms": "1"}, json={"error": {"message": "busy"}}
            )
        return httpx.Response(200, json=response([{"index": 0, "embedding": [3, 4]}]))

    service = OpenAIEmbeddingService("fake", dimension=2, transport=httpx.MockTransport(handler))
    await service.embed_query("a")
    assert calls == 2
    service = OpenAIEmbeddingService(
        "fake",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, json={"error": {"message": "sensitive-content"}})
        ),
    )
    with pytest.raises(IndexingError) as error:
        await service.embed_query("a")
    assert "sensitive-content" not in str(error.value)
    assert error.value.__suppress_context__


def test_openai_settings_require_key_and_valid_dimension() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, embedding_provider="openai", openai_api_key=None)
    with pytest.raises(ValueError, match="dimension"):
        Settings(
            _env_file=None,
            embedding_provider="openai",
            openai_api_key="fake",
            embedding_dimension=2048,
        )
    settings = Settings(
        _env_file=None,
        embedding_provider="openai",
        openai_api_key="fake",
        embedding_dimension=1024,
        reranker_provider="none",
    )
    assert settings.voyage_api_key is None


def test_serverless_openai_profile_needs_no_voyage_or_neo4j() -> None:
    from tests.test_config import _vercel_blob_production_settings

    settings = _vercel_blob_production_settings(
        serverless=True,
        auth_enabled=True,
        jwt_secret="j" * 32,
        workflow_secret="w" * 32,
        auth_email_webhook_url="https://mail.example/send",
        auth_email_webhook_secret="mail-secret",
        embedding_provider="openai",
        openai_api_key="fake",
        embedding_dimension=1024,
        reranker_provider="none",
        graph_store_backend="postgresql",
        neo4j_uri=None,
        neo4j_username=None,
        neo4j_password=None,
    )
    assert settings.voyage_api_key is None


@pytest.mark.asyncio
async def test_disabled_reranking_rejects_before_retrieval() -> None:
    from typing import Any, cast

    from app.core.exceptions import RetrievalError
    from app.generation.extractive import ExtractiveGenerator
    from app.models import QueryRequest
    from app.repositories import InMemorySourceRepository
    from app.retrieval.query_service import QueryService

    service = QueryService(
        InMemorySourceRepository(),
        cast(Any, object()),
        cast(Any, object()),
        ExtractiveGenerator(),
        default_top_k=5,
        max_top_k=20,
        min_similarity=0,
    )
    with pytest.raises(RetrievalError, match="disabled"):
        await service.query(
            QueryRequest(
                workspace_id="test", question="question", retrieval_mode="fusion", rerank=True
            )
        )


@pytest.mark.asyncio
async def test_openai_rejects_wrong_model_and_nonfinite_vectors() -> None:
    for body in [
        response([{"index": 0, "embedding": [3, 4]}], "different-model"),
        response([{"index": 0, "embedding": ["NaN", 4]}]),
    ]:
        service = OpenAIEmbeddingService(
            "fake",
            dimension=2,
            transport=httpx.MockTransport(lambda _, body=body: httpx.Response(200, json=body)),
        )
        with pytest.raises(IndexingError):
            await service.embed_query("a")


@pytest.mark.asyncio
async def test_old_job_cannot_resume_with_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobs import processing

    monkeypatch.setattr(processing, "get_embedding_service", lambda: OpenAIEmbeddingService("fake"))
    with pytest.raises(ValueError, match="configuration changed"):
        await processing.advance(
            {
                "spec": {"kind": "pdf"},
                "checkpoint": {"stage": "embed", "model_name": "voyage-4", "dimension": 1024},
            }
        )
