"""Embedding service contract and local Hugging Face adapter."""

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from app.core.exceptions import IndexingError


class EmbeddingService(Protocol):
    """Convert documents and queries into compatible normalized vectors."""

    @property
    def model_name(self) -> str:
        """Return the immutable model identifier stored with each index."""
        ...

    @property
    def dimension(self) -> int:
        """Return the expected vector dimension."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        """Embed source passages without a query instruction."""
        ...

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        """Embed one retrieval query with its configured instruction."""
        ...


class HuggingFaceEmbeddingService:
    """Run a cached Sentence Transformers model outside the event loop."""

    def __init__(
        self,
        *,
        model_name: str,
        dimension: int,
        batch_size: int,
        query_prefix: str,
    ) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._batch_size = batch_size
        self._query_prefix = query_prefix
        self._model: Any = None
        self._lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            return np.empty((0, self._dimension), dtype=np.float32)
        return await self._encode(list(texts))

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        vectors = await self._encode([f"{self._query_prefix}{text}"])
        return cast(NDArray[np.float32], vectors[0])

    async def _encode(self, texts: list[str]) -> NDArray[np.float32]:
        async with self._lock:
            model = await self._get_model()
            raw = await asyncio.to_thread(
                model.encode,
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        vectors = np.asarray(raw, dtype=np.float32)
        expected = (len(texts), self._dimension)
        if vectors.shape != expected:
            raise IndexingError(
                f"Embedding model returned shape {vectors.shape}; expected {expected}"
            )
        if not np.isfinite(vectors).all():
            raise IndexingError("Embedding model returned non-finite values")
        return vectors

    async def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = await asyncio.to_thread(SentenceTransformer, self._model_name)
        return self._model
