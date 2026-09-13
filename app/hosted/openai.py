"""OpenAI embeddings with bounded retries and strict vector validation."""

from collections.abc import Sequence
from typing import cast

import httpx
import numpy as np
from numpy.typing import NDArray
from openai import AsyncOpenAI, OpenAIError

from app.core.exceptions import IndexingError


class OpenAIEmbeddingService:
    """Embed queries and documents in the same explicitly dimensioned model space."""

    def __init__(
        self,
        api_key: str,
        *,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1024,
        batch_size: int = 32,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if batch_size < 1 or dimension < 1:
            raise ValueError("Embedding batch size and dimension must be positive")
        self._api_key = api_key
        self._transport = transport
        self.model_name = model_name
        self.dimension = dimension
        # Bound the aggregate request even when the local adapter uses larger batches.
        self.batch_size = min(batch_size, 32)

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        if any(not text.strip() for text in texts):
            raise IndexingError("OpenAI embedding inputs must not be empty")
        batches: list[NDArray[np.float32]] = []
        try:
            async with AsyncOpenAI(
                api_key=self._api_key,
                timeout=45,
                max_retries=2,
                http_client=httpx.AsyncClient(transport=self._transport, timeout=45),
            ) as client:
                for offset in range(0, len(texts), self.batch_size):
                    batch = list(texts[offset : offset + self.batch_size])
                    result = await client.embeddings.create(
                        model=self.model_name,
                        input=batch,
                        dimensions=self.dimension,
                        encoding_format="float",
                    )
                    if result.model != self.model_name:
                        raise ValueError("Unexpected embedding model")
                    data = sorted(result.data, key=lambda row: row.index)
                    if [row.index for row in data] != list(range(len(batch))):
                        raise ValueError("Missing or duplicate embedding indexes")
                    vectors = np.asarray([row.embedding for row in data], dtype=np.float32)
                    if vectors.shape != (len(batch), self.dimension):
                        raise ValueError("Incorrect embedding dimensions")
                    norms = np.linalg.norm(vectors.astype(np.float64), axis=1, keepdims=True)
                    if not np.isfinite(vectors).all() or (norms == 0).any():
                        raise ValueError("Invalid embedding values")
                    batches.append(cast(NDArray[np.float32], (vectors / norms).astype(np.float32)))
        except (OpenAIError, httpx.HTTPError, ValueError, TypeError, AttributeError):
            # Provider errors can contain request text; expose only a fixed application error.
            raise IndexingError("OpenAI embedding failed validation or execution") from None
        return cast(NDArray[np.float32], np.concatenate(batches))

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        return cast(NDArray[np.float32], (await self.embed_documents([text]))[0])
