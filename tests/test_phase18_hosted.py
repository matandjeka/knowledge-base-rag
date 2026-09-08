"""Hosted inference contracts and private Blob regression checks."""

from typing import Any
from uuid import uuid4

import httpx
import numpy as np
import pytest

from app.core.exceptions import IndexingError, RerankingError
from app.hosted.voyage import VoyageClient, VoyageEmbeddingService, VoyageReranker
from app.models import Evidence, NormalizedDocument, SourceType
from app.storage.vercel_blob import VercelBlobSourceStorage


@pytest.mark.asyncio
async def test_voyage_batches_preserves_indexes_and_normalizes() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [3, 4]}
                    for i in reversed(range(len(payload["input"])))
                ],
                "usage": {"total_tokens": 2},
            },
        )

    adapter = VoyageEmbeddingService(
        VoyageClient("fake", transport=httpx.MockTransport(handler)), dimension=2, batch_size=2
    )
    vectors = await adapter.embed_documents(["a", "b", "c"])
    assert vectors.shape == (3, 2)
    np.testing.assert_allclose(vectors, [[0.6, 0.8]] * 3)
    await adapter.embed_query("a")
    assert [r["input_type"] for r in requests] == ["document", "document", "query"]
    assert requests[-1]["input"] == ["a"]
    assert (await adapter.embed_documents([])).shape == (0, 2)


@pytest.mark.asyncio
async def test_voyage_rejects_invalid_dimensions_and_missing_results() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]})
    )
    adapter = VoyageEmbeddingService(VoyageClient("fake", transport=transport), dimension=2)
    with pytest.raises(IndexingError):
        await adapter.embed_documents(["a"])
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []}))
    with pytest.raises(RerankingError):
        await VoyageReranker(VoyageClient("fake", transport=transport)).score(
            "question", [_evidence()]
        )


def _evidence() -> Evidence:
    return Evidence(
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="Policy",
        retriever="vector",
        page_number=1,
        normalized_score=0.8,
        raw_score=0.8,
    )


@pytest.mark.asyncio
async def test_reranker_restores_input_order() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "data": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]
            },
        )
    )
    result = await VoyageReranker(VoyageClient("fake", transport=transport)).score(
        "question", [_evidence(), _evidence()]
    )
    assert result.scores == (0.1, 0.9)


class MemoryBlob:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.options: list[dict[str, Any]] = []

    async def put(self, name: str, body: bytes, **options: Any) -> Any:
        from types import SimpleNamespace

        self.options.append(options)
        if name in self.values and not options.get("overwrite"):
            raise ValueError("exists")
        self.values[name] = body
        return SimpleNamespace(url="https://private.example/" + name)

    async def get(self, name: str, **options: Any) -> Any:
        from types import SimpleNamespace

        assert options["access"] == "private"
        return SimpleNamespace(content=self.values[name], status_code=200)


@pytest.mark.asyncio
async def test_private_blob_restart_and_immutable_retry() -> None:
    client = MemoryBlob()
    storage = VercelBlobSourceStorage("fake", client=client)
    document = NormalizedDocument(
        workspace_id="client",
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="A policy",
        page_number=1,
    )
    await storage.save_documents("client", document.source_id, [document])
    restarted = VercelBlobSourceStorage("fake", client=client)
    assert await restarted.load_documents("client", document.source_id) == (document,)
    await storage.write("immutable", b"same")
    await storage.write("immutable", b"same")
    with pytest.raises(ValueError):
        await storage.write("immutable", b"different")
    assert all(o["access"] == "private" and o["add_random_suffix"] is False for o in client.options)
    with pytest.raises(ValueError):
        storage.name("../other", document.source_id, "documents.json")


def test_api_import_does_not_require_local_model_libraries() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys
class NoLocalML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'faiss', 'torch', 'sentence_transformers', 'streamlit'}:
            raise ImportError('Local ML dependency imported: ' + fullname)
sys.meta_path.insert(0, NoLocalML())
from app.main import app
assert app.title
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
