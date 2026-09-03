"""Common retrieval service contracts."""

from typing import Protocol
from uuid import UUID

from app.models import Evidence


class Retriever(Protocol):
    """Return canonical evidence for one workspace-scoped query."""

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int,
        source_ids: frozenset[UUID],
        min_similarity: float,
    ) -> list[Evidence]:
        """Retrieve ordered evidence using one retrieval strategy."""
        ...
