"""Async Voyage embeddings and reranking with bounded retries and validated outputs."""

import asyncio
import logging
from collections.abc import Sequence
from time import perf_counter
from typing import Any, cast

import httpx
import numpy as np
from numpy.typing import NDArray

from app.core.exceptions import IndexingError, RerankingError
from app.models import Evidence
from app.reranking.service import RerankerScores

logger = logging.getLogger(__name__)


class VoyageClient:
    """Keep credentials server-side and omit documents from diagnostics."""

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.transport = transport

    async def post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url="https://api.voyageai.com/v1/",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=45,
            transport=self.transport,
        ) as client:
            for attempt in range(3):
                try:
                    response = await client.post(endpoint, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    if not isinstance(result, dict):
                        raise ValueError("Invalid provider response")
                    logger.info(
                        "Hosted inference complete",
                        extra={
                            "provider": "voyage",
                            "model_name": payload["model"],
                            "usage": result.get("usage", {}),
                        },
                    )
                    return cast(dict[str, Any], result)
                except (httpx.TransportError, httpx.HTTPStatusError) as error:
                    retryable = not isinstance(error, httpx.HTTPStatusError) or (
                        error.response.status_code == 429 or error.response.status_code >= 500
                    )
                    if not retryable or attempt == 2:
                        raise RuntimeError("Hosted inference request failed") from None
                    await asyncio.sleep(0.5 * 2**attempt)
        raise RuntimeError("Hosted inference exhausted retries")


class VoyageEmbeddingService:
    """Use explicit query/document modes without the local BGE query prefix."""

    def __init__(
        self,
        client: VoyageClient,
        *,
        model_name: str = "voyage-4",
        dimension: int = 1024,
        batch_size: int = 32,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.dimension = dimension
        self.batch_size = batch_size

    async def embed_documents(self, texts: Sequence[str]) -> NDArray[np.float32]:
        return await self._embed(texts, "document")

    async def embed_query(self, text: str) -> NDArray[np.float32]:
        return cast(NDArray[np.float32], (await self._embed([text], "query"))[0])

    async def _embed(self, texts: Sequence[str], input_type: str) -> NDArray[np.float32]:
        rows: list[list[float]] = []
        try:
            for offset in range(0, len(texts), self.batch_size):
                batch = list(texts[offset : offset + self.batch_size])
                result = await self.client.post(
                    "embeddings",
                    {
                        "input": batch,
                        "model": self.model_name,
                        "input_type": input_type,
                        "output_dimension": self.dimension,
                        "truncation": False,
                    },
                )
                data = sorted(result["data"], key=lambda row: row["index"])
                if [row["index"] for row in data] != list(range(len(batch))):
                    raise ValueError("Invalid embedding indexes")
                rows.extend(row["embedding"] for row in data)
            vectors = np.asarray(rows, dtype=np.float32).reshape(len(texts), self.dimension)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if not np.isfinite(vectors).all() or (norms == 0).any():
                raise ValueError("Invalid embedding values")
            return cast(NDArray[np.float32], vectors / norms)
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            raise IndexingError("Voyage embedding failed validation or execution") from error


class VoyageReranker:
    """Restore provider-ranked results to the input evidence order."""

    def __init__(self, client: VoyageClient, model_name: str = "rerank-2.5") -> None:
        self.client = client
        self.model_name = model_name

    async def score(self, question: str, evidence: Sequence[Evidence]) -> RerankerScores:
        if not evidence:
            return RerankerScores((), self.model_name, 0)
        started = perf_counter()
        try:
            result = await self.client.post(
                "rerank",
                {
                    "query": question,
                    "documents": [item.content for item in evidence],
                    "model": self.model_name,
                    "top_k": len(evidence),
                    "truncation": False,
                },
            )
            data = sorted(result["data"], key=lambda row: row["index"])
            if [row["index"] for row in data] != list(range(len(evidence))):
                raise ValueError("Invalid reranking indexes")
            scores = tuple(float(row["relevance_score"]) for row in data)
            if not np.isfinite(scores).all():
                raise ValueError("Invalid scores")
            return RerankerScores(scores, self.model_name, perf_counter() - started)
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            raise RerankingError("Voyage reranking failed validation or execution") from error
