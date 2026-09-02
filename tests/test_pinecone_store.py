"""Pinecone vector-store contract tests without external network access."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pytest
from numpy.typing import NDArray

from app.core.config import Settings
from app.core.exceptions import IndexingError, IndexNotFoundError
from app.models import NormalizedDocument, SourceType
from app.retrieval.pinecone_store import PineconeIndex, PineconeVectorStore


class MemoryPineconeIndex:
    """Minimal async data-plane emulator for adapter behavior tests."""

    def __init__(self) -> None:
        self.namespaces: dict[str, dict[str, dict[str, Any]]] = {}
        self.upsert_sizes: list[int] = []
        self.delete_filters: list[Mapping[str, Any]] = []
        self.fail_cleanup = False
        self.fail_control_upsert = False

    async def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str) -> None:
        self.upsert_sizes.append(len(vectors))
        records = self.namespaces.setdefault(namespace, {})
        for vector in vectors:
            identifier = str(vector["id"])
            if identifier == "__control__" and self.fail_control_upsert:
                raise RuntimeError("control unavailable")
            records[identifier] = dict(vector)

    async def fetch(self, *, ids: Sequence[str], namespace: str) -> dict[str, Any]:
        records = self.namespaces.get(namespace, {})
        return {
            "vectors": {
                identifier: records[identifier] for identifier in ids if identifier in records
            }
        }

    async def query(
        self,
        *,
        vector: Sequence[float],
        top_k: int,
        namespace: str,
        filter: Mapping[str, Any],
        include_metadata: bool,
        include_values: bool,
    ) -> dict[str, Any]:
        del include_metadata, include_values
        matches: list[dict[str, Any]] = []
        for identifier, record in self.namespaces.get(namespace, {}).items():
            metadata = record["metadata"]
            if not _matches_filter(metadata, filter):
                continue
            score = float(np.dot(np.asarray(record["values"]), np.asarray(vector)))
            matches.append({"id": identifier, "score": score, "metadata": metadata})
        matches.sort(key=lambda match: match["score"], reverse=True)
        return {"matches": matches[:top_k]}

    async def delete(self, *, filter: Mapping[str, Any], namespace: str) -> None:
        self.delete_filters.append(filter)
        if self.fail_cleanup:
            raise RuntimeError("cleanup unavailable")
        records = self.namespaces.get(namespace, {})
        for identifier in list(records):
            if _matches_filter(records[identifier]["metadata"], filter):
                del records[identifier]


class IndexContext(AbstractAsyncContextManager[PineconeIndex]):
    def __init__(self, index: PineconeIndex) -> None:
        self._index = index

    async def __aenter__(self) -> PineconeIndex:
        return self._index

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _matches_filter(metadata: Mapping[str, Any], filter: Mapping[str, Any]) -> bool:
    for field, condition in filter.items():
        value = metadata.get(field)
        if "$eq" in condition and value != condition["$eq"]:
            return False
        if "$in" in condition and value not in condition["$in"]:
            return False
    return True


def _document(source_id: UUID, content: str) -> NormalizedDocument:
    return NormalizedDocument(
        workspace_id="workspace",
        source_id=source_id,
        source_type=SourceType.PDF,
        content=content,
        page_number=1,
    )


def _store(index: MemoryPineconeIndex, *, batch_size: int = 2) -> PineconeVectorStore:
    async def describe() -> dict[str, object]:
        return {
            "dimension": 3,
            "metric": "cosine",
            "host": "https://index.example.test",
        }

    return PineconeVectorStore(
        api_key="secret",
        index_name="knowledge",
        index_host="index.example.test",
        dimension=3,
        timeout_seconds=5,
        batch_size=batch_size,
        consistency_retries=1,
        consistency_delay_seconds=0,
        index_factory=lambda: IndexContext(index),
        index_descriptor=describe,
    )


@pytest.mark.asyncio
async def test_prepare_batches_records_and_requires_activation() -> None:
    index = MemoryPineconeIndex()
    store = _store(index, batch_size=2)
    source_id = uuid4()
    documents = [_document(source_id, f"passage {number}") for number in range(3)]
    vectors = np.asarray([[1.0, 0.0, 0.0]] * 3, dtype=np.float32)

    metadata = await store.prepare("workspace", documents, vectors, model_name="fixed", dimension=3)

    assert index.upsert_sizes == [2, 2]
    with pytest.raises(IndexNotFoundError):
        await store.search(
            "workspace",
            vectors[0],
            top_k=3,
            source_ids=frozenset(),
            model_name="fixed",
            dimension=3,
        )

    await store.activate("workspace", metadata.generation_id)
    matches = await store.search(
        "workspace",
        vectors[0],
        top_k=3,
        source_ids=frozenset(),
        model_name="fixed",
        dimension=3,
    )
    assert {match.document.document_id for match in matches} == {
        document.document_id for document in documents
    }


@pytest.mark.asyncio
async def test_search_filters_sources_server_side() -> None:
    index = MemoryPineconeIndex()
    store = _store(index)
    included = uuid4()
    excluded = uuid4()
    documents = [_document(included, "included"), _document(excluded, "excluded")]
    vectors = np.asarray([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    await store.rebuild("workspace", documents, vectors, model_name="fixed", dimension=3)

    matches = await store.search(
        "workspace",
        vectors[0],
        top_k=2,
        source_ids=frozenset({included}),
        model_name="fixed",
        dimension=3,
    )

    assert [match.document.source_id for match in matches] == [included]


@pytest.mark.asyncio
async def test_activation_failure_keeps_previous_generation_active() -> None:
    index = MemoryPineconeIndex()
    store = _store(index)
    source_id = uuid4()
    first = _document(source_id, "first")
    query = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    await store.rebuild("workspace", [first], query.reshape(1, -1), model_name="fixed", dimension=3)
    second = _document(source_id, "second")
    prepared = await store.prepare(
        "workspace", [second], query.reshape(1, -1), model_name="fixed", dimension=3
    )
    index.fail_control_upsert = True

    with pytest.raises(IndexingError, match="activation"):
        await store.activate("workspace", prepared.generation_id)

    index.fail_control_upsert = False
    matches = await store.search(
        "workspace",
        query,
        top_k=1,
        source_ids=frozenset(),
        model_name="fixed",
        dimension=3,
    )
    assert matches[0].document.content == "first"


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_rollback_activation() -> None:
    index = MemoryPineconeIndex()
    store = _store(index)
    source_id = uuid4()
    vector: NDArray[np.float32] = np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)
    await store.rebuild(
        "workspace", [_document(source_id, "first")], vector, model_name="fixed", dimension=3
    )
    prepared = await store.prepare(
        "workspace", [_document(source_id, "second")], vector, model_name="fixed", dimension=3
    )
    index.fail_cleanup = True

    await store.activate("workspace", prepared.generation_id)

    matches = await store.search(
        "workspace",
        vector[0],
        top_k=1,
        source_ids=frozenset(),
        model_name="fixed",
        dimension=3,
    )
    assert matches[0].document.content == "second"
    assert index.delete_filters


@pytest.mark.asyncio
async def test_incompatible_index_is_rejected() -> None:
    index = MemoryPineconeIndex()

    async def describe() -> dict[str, object]:
        return {"dimension": 3, "metric": "dotproduct", "host": "index.example.test"}

    store = _store(index)
    store._index_descriptor = describe

    with pytest.raises(IndexingError, match="cosine"):
        await store.prepare(
            "workspace",
            [_document(uuid4(), "content")],
            np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32),
            model_name="fixed",
            dimension=3,
        )


def test_pinecone_settings_are_conditional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None).vector_store_backend == "faiss"

    with pytest.raises(ValueError, match="PINECONE_API_KEY"):
        Settings(vector_store_backend="pinecone", _env_file=None)

    settings = Settings(
        vector_store_backend="pinecone",
        pinecone_api_key="secret",
        pinecone_index_name="knowledge",
        pinecone_index_host="index.example.test",
        _env_file=None,
    )
    assert settings.pinecone_api_key is not None
    assert settings.pinecone_api_key.get_secret_value() == "secret"
