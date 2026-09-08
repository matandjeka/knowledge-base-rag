"""Pinecone implementation of the workspace vector-store contract."""

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray

from app.core.exceptions import IndexingError, IndexNotFoundError
from app.models import NormalizedDocument
from app.persistence import ActiveGenerationRepository, GenerationKind
from app.retrieval.vector_store import (
    VectorGenerationMetadata,
    VectorIndexKind,
    VectorIndexMetadata,
    VectorIndexPayload,
    VectorSearchMatch,
)

logger = logging.getLogger(__name__)

_CONTROL_ID = "__control__"
_DOCUMENT_KIND = "document"
_GENERATION_KIND = "generation"
_CONTROL_KIND = "control"


class PineconeIndex(Protocol):
    """Subset of the async Pinecone data-plane client used by the adapter."""

    async def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str) -> Any: ...

    async def fetch(self, *, ids: Sequence[str], namespace: str) -> Any: ...

    async def query(
        self,
        *,
        vector: Sequence[float],
        top_k: int,
        namespace: str,
        filter: Mapping[str, Any],
        include_metadata: bool,
        include_values: bool,
    ) -> Any: ...

    async def delete(self, *, filter: Mapping[str, Any], namespace: str) -> Any: ...


IndexFactory = Callable[[], AbstractAsyncContextManager[PineconeIndex]]
IndexDescriptor = Callable[[], Any]


class PineconeVectorStore:
    """Store staged vector generations in one namespace per workspace."""

    def __init__(
        self,
        *,
        api_key: str,
        index_name: str,
        index_host: str,
        dimension: int,
        timeout_seconds: float,
        batch_size: int,
        consistency_retries: int,
        consistency_delay_seconds: float,
        index_factory: IndexFactory | None = None,
        index_descriptor: IndexDescriptor | None = None,
        generation_repository: ActiveGenerationRepository | None = None,
    ) -> None:
        self._api_key = api_key
        self._index_name = index_name
        self._index_host = index_host
        self._dimension = dimension
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size
        self._consistency_retries = consistency_retries
        self._consistency_delay_seconds = consistency_delay_seconds
        self._index_factory = index_factory or self._default_index_factory
        self._index_descriptor = index_descriptor or self._default_index_descriptor
        self._generation_repository = generation_repository
        self._compatibility_checked = False
        self._compatibility_lock = asyncio.Lock()

    async def upsert_job_batch(
        self,
        workspace_id: str,
        generation_id: UUID,
        kind: VectorIndexKind,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
    ) -> None:
        """Prepare and verify a bounded, idempotent job batch without publishing it."""
        self._validate_workspace(workspace_id)
        await self._ensure_compatible(self._dimension)
        self._validate_payloads({kind: VectorIndexPayload(documents, vectors)}, self._dimension)
        records = [
            {
                "id": f"{generation_id}:{kind.value}:{doc.document_id}",
                "values": vector.tolist(),
                "metadata": {
                    "record_type": _DOCUMENT_KIND,
                    "generation_id": str(generation_id),
                    "index_kind": kind.value,
                    "source_id": str(doc.source_id),
                    "document_json": doc.model_dump_json(),
                },
            }
            for doc, vector in zip(documents, vectors, strict=True)
        ]
        async with self._index_factory() as index:
            await index.upsert(vectors=records, namespace=workspace_id)
            ids = [record["id"] for record in records]
            for attempt in range(self._consistency_retries):
                response = await index.fetch(ids=ids, namespace=workspace_id)
                found = self._field(response, "vectors", {})
                if all(identifier in found for identifier in ids):
                    return
                await asyncio.sleep(self._consistency_delay_seconds * (attempt + 1))
        raise IndexingError("Pinecone batch was not visible before the verification deadline")

    async def finish_job_generation(
        self, workspace_id: str, generation: VectorGenerationMetadata
    ) -> None:
        """Write generation metadata only after every batch has been acknowledged and verified."""
        async with self._index_factory() as index:
            await index.upsert(
                vectors=[self._generation_record(generation)], namespace=workspace_id
            )
            await self._wait_for_generation(index, workspace_id, generation.generation_id)

    async def rebuild(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
        *,
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        metadata = await self.prepare(
            workspace_id, documents, vectors, model_name=model_name, dimension=dimension
        )
        await self.activate(workspace_id, metadata.generation_id)
        return metadata

    async def prepare(
        self,
        workspace_id: str,
        documents: Sequence[NormalizedDocument],
        vectors: NDArray[np.float32],
        *,
        model_name: str,
        dimension: int,
    ) -> VectorIndexMetadata:
        generation = await self.prepare_bundle(
            workspace_id,
            {VectorIndexKind.VECTOR: VectorIndexPayload(documents, vectors)},
            model_name=model_name,
            dimension=dimension,
        )
        return generation.indexes[VectorIndexKind.VECTOR]

    async def prepare_bundle(
        self,
        workspace_id: str,
        payloads: Mapping[VectorIndexKind, VectorIndexPayload],
        *,
        model_name: str,
        dimension: int,
        generation_id: UUID | None = None,
    ) -> VectorGenerationMetadata:
        self._validate_workspace(workspace_id)
        await self._ensure_compatible(dimension)
        self._validate_payloads(payloads, dimension)
        generation_id = generation_id or uuid4()
        indexes: dict[VectorIndexKind, VectorIndexMetadata] = {}
        records: list[dict[str, Any]] = []
        for kind, payload in payloads.items():
            document_payload = (
                b"["
                + b",".join(document.model_dump_json().encode() for document in payload.documents)
                + b"]"
            )
            contiguous_vectors = np.ascontiguousarray(payload.vectors, dtype=np.float32)
            indexes[kind] = VectorIndexMetadata(
                generation_id=generation_id,
                model_name=model_name,
                dimension=dimension,
                normalized=True,
                document_count=len(payload.documents),
                index_sha256=hashlib.sha256(contiguous_vectors.tobytes()).hexdigest(),
                documents_sha256=hashlib.sha256(document_payload).hexdigest(),
            )
            records.extend(
                {
                    "id": f"{generation_id}:{kind.value}:{document.document_id}",
                    "values": vector.tolist(),
                    "metadata": {
                        "record_type": _DOCUMENT_KIND,
                        "generation_id": str(generation_id),
                        "index_kind": kind.value,
                        "source_id": str(document.source_id),
                        "document_json": document.model_dump_json(),
                    },
                }
                for document, vector in zip(payload.documents, contiguous_vectors, strict=True)
            )
        generation = VectorGenerationMetadata(generation_id=generation_id, indexes=indexes)
        records.append(self._generation_record(generation))
        try:
            async with self._index_factory() as index:
                for start in range(0, len(records), self._batch_size):
                    await index.upsert(
                        vectors=records[start : start + self._batch_size],
                        namespace=workspace_id,
                    )
        except Exception as error:
            raise IndexingError("Pinecone generation preparation failed") from error
        return generation

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        self._validate_workspace(workspace_id)
        await self._ensure_compatible(self._dimension)
        try:
            async with self._index_factory() as index:
                metadata = await self._wait_for_generation(index, workspace_id, generation_id)
                if self._generation_repository is not None:
                    return
                previous = await self._active_generation(index, workspace_id, required=False)
                await index.upsert(
                    vectors=[
                        {
                            "id": _CONTROL_ID,
                            "values": [0.0] * self._dimension,
                            "metadata": {
                                "record_type": _CONTROL_KIND,
                                "active_generation": str(metadata.generation_id),
                            },
                        }
                    ],
                    namespace=workspace_id,
                )
                if previous is not None and previous != generation_id:
                    try:
                        await index.delete(
                            filter={"generation_id": {"$eq": str(previous)}},
                            namespace=workspace_id,
                        )
                    except Exception:
                        logger.warning(
                            "Pinecone stale-generation cleanup failed",
                            extra={"workspace_id": workspace_id},
                            exc_info=True,
                        )
        except IndexingError:
            raise
        except Exception as error:
            raise IndexingError("Pinecone generation activation failed") from error

    async def active_generation(self, workspace_id: str) -> UUID:
        """Return the currently activated Pinecone generation."""
        self._validate_workspace(workspace_id)
        if self._generation_repository is not None:
            return await self._generation_repository.active_generation(
                workspace_id, GenerationKind.RETRIEVAL
            )
        await self._ensure_compatible(self._dimension)
        try:
            async with self._index_factory() as index:
                generation = (
                    await self._generation_repository.active_generation(
                        workspace_id, GenerationKind.RETRIEVAL
                    )
                    if self._generation_repository is not None
                    else await self._active_generation(index, workspace_id, required=True)
                )
        except IndexNotFoundError:
            raise
        except Exception as error:
            raise IndexingError("Pinecone active-generation lookup failed") from error
        if generation is None:
            raise IndexNotFoundError("No vector index exists for this workspace")
        return generation

    async def search(
        self,
        workspace_id: str,
        query: NDArray[np.float32],
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        model_name: str,
        dimension: int,
        index_kind: VectorIndexKind = VectorIndexKind.VECTOR,
    ) -> tuple[VectorSearchMatch, ...]:
        self._validate_workspace(workspace_id)
        await self._ensure_compatible(dimension)
        if query.shape != (dimension,) or not np.isfinite(query).all():
            raise IndexingError("Query embedding is invalid for the vector index")
        try:
            async with self._index_factory() as index:
                generation = await self._active_generation(index, workspace_id, required=True)
                metadata_filter: dict[str, Any] = {
                    "record_type": {"$eq": _DOCUMENT_KIND},
                    "generation_id": {"$eq": str(generation)},
                    "index_kind": {"$eq": index_kind.value},
                }
                if source_ids:
                    metadata_filter["source_id"] = {
                        "$in": [str(source_id) for source_id in sorted(source_ids, key=str)]
                    }
                response = await index.query(
                    vector=query.tolist(),
                    top_k=top_k,
                    namespace=workspace_id,
                    filter=metadata_filter,
                    include_metadata=True,
                    include_values=False,
                )
        except IndexNotFoundError:
            raise
        except Exception as error:
            raise IndexingError("Pinecone vector search failed") from error
        matches: list[VectorSearchMatch] = []
        for match in self._field(response, "matches", []):
            payload = self._field(match, "metadata", {})
            document_json = payload.get("document_json") if isinstance(payload, Mapping) else None
            if not isinstance(document_json, str):
                raise IndexingError("Pinecone match is missing canonical document metadata")
            try:
                document = NormalizedDocument.model_validate_json(document_json)
                score = float(self._field(match, "score", 0.0))
                matches.append(VectorSearchMatch(document=document, score=score))
            except (TypeError, ValueError) as error:
                raise IndexingError("Pinecone returned an invalid vector match") from error
        return tuple(matches)

    async def _ensure_compatible(self, dimension: int) -> None:
        if dimension != self._dimension:
            raise IndexingError("Pinecone index dimension does not match the embedding model")
        if self._compatibility_checked:
            return
        async with self._compatibility_lock:
            if self._compatibility_checked:
                return
            try:
                description = await self._index_descriptor()
                actual_dimension = int(self._field(description, "dimension"))
                metric = str(self._field(description, "metric"))
                host = str(self._field(description, "host")).removeprefix("https://")
            except Exception as error:
                raise IndexingError("Could not validate the configured Pinecone index") from error
            configured_host = self._index_host.removeprefix("https://")
            if actual_dimension != self._dimension or metric != "cosine":
                raise IndexingError("Pinecone index must use the configured dimension and cosine")
            if host.rstrip("/") != configured_host.rstrip("/"):
                raise IndexingError("Pinecone index host does not match the configured index name")
            self._compatibility_checked = True

    async def _wait_for_generation(
        self, index: PineconeIndex, workspace_id: str, generation_id: UUID
    ) -> VectorGenerationMetadata:
        for attempt in range(self._consistency_retries):
            response = await index.fetch(
                ids=[f"__generation__:{generation_id}"], namespace=workspace_id
            )
            vectors = self._field(response, "vectors", {})
            record = vectors.get(f"__generation__:{generation_id}")
            if record is not None:
                payload = self._field(record, "metadata", {})
                encoded = payload.get("index_metadata") if isinstance(payload, Mapping) else None
                if isinstance(encoded, str):
                    try:
                        return VectorGenerationMetadata.model_validate_json(encoded)
                    except ValueError as error:
                        raise IndexingError("Pinecone generation metadata is invalid") from error
            if attempt + 1 < self._consistency_retries:
                await asyncio.sleep(self._consistency_delay_seconds)
        raise IndexingError("Prepared Pinecone generation is not yet visible")

    async def _active_generation(
        self, index: PineconeIndex, workspace_id: str, *, required: bool
    ) -> UUID | None:
        response = await index.fetch(ids=[_CONTROL_ID], namespace=workspace_id)
        vectors = self._field(response, "vectors", {})
        control = vectors.get(_CONTROL_ID)
        if control is None:
            if required:
                raise IndexNotFoundError("No vector index exists for this workspace")
            return None
        payload = self._field(control, "metadata", {})
        value = payload.get("active_generation") if isinstance(payload, Mapping) else None
        try:
            return UUID(str(value))
        except ValueError as error:
            raise IndexingError("Pinecone workspace control record is invalid") from error

    def _generation_record(self, metadata: VectorGenerationMetadata) -> dict[str, Any]:
        return {
            "id": f"__generation__:{metadata.generation_id}",
            "values": [0.0] * self._dimension,
            "metadata": {
                "record_type": _GENERATION_KIND,
                "generation_id": str(metadata.generation_id),
                "index_metadata": metadata.model_dump_json(),
            },
        }

    def _default_index_factory(self) -> AbstractAsyncContextManager[PineconeIndex]:
        from pinecone import AsyncIndex

        return AsyncIndex(
            host=self._index_host,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )

    async def _default_index_descriptor(self) -> Any:
        from pinecone import AsyncPinecone

        async with AsyncPinecone(api_key=self._api_key, timeout=self._timeout_seconds) as client:
            return await client.indexes.describe(self._index_name)

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    @staticmethod
    def _validate_workspace(workspace_id: str) -> None:
        if not workspace_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in workspace_id
        ):
            raise ValueError("Invalid workspace identifier")

    @staticmethod
    def _validate_payloads(
        payloads: Mapping[VectorIndexKind, VectorIndexPayload], dimension: int
    ) -> None:
        if not payloads:
            raise IndexingError("Cannot build a vector generation without representations")
        for kind, payload in payloads.items():
            if not payload.documents:
                raise IndexingError(f"Cannot build the {kind.value} index without documents")
            if payload.vectors.shape != (len(payload.documents), dimension):
                raise IndexingError("Document and embedding dimensions do not match")
            if not np.isfinite(payload.vectors).all():
                raise IndexingError("Vector index contains non-finite values")
