"""Immutable graph snapshots sharing the PostgreSQL metadata database."""

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import GraphIndexNotFoundError, IndexingError
from app.graph.extraction import GRAPH_PROMPT_VERSION, GRAPH_SCHEMA_VERSION
from app.models import GraphGenerationMetadata, GraphSnapshot
from app.persistence import ActiveGenerationRepository, GenerationKind
from app.persistence.metadata import graph_snapshots


class PostgresGraphStore:
    """Prepare snapshots without exposing them until the coordinator publishes."""

    def __init__(self, engine: AsyncEngine, generations: ActiveGenerationRepository) -> None:
        self._engine = engine
        self._generations = generations

    async def prepare(self, snapshot: GraphSnapshot) -> GraphGenerationMetadata:
        payload = snapshot.model_dump_json()
        metadata = GraphGenerationMetadata(
            generation_id=snapshot.generation_id,
            schema_version=snapshot.schema_version,
            extractor_model=snapshot.extractor_model,
            prompt_version=snapshot.prompt_version,
            source_count=len(snapshot.source_ids),
            document_count=snapshot.document_count,
            entity_count=len(snapshot.entities),
            relationship_count=len(snapshot.relationships),
            graph_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        )
        insert = postgres_insert if self._engine.dialect.name == "postgresql" else sqlite_insert
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(graph_snapshots)
                .values(
                    workspace_id=snapshot.workspace_id,
                    generation_id=str(snapshot.generation_id),
                    snapshot_json=payload,
                    metadata_json=metadata.model_dump_json(),
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "generation_id"])
            )
            existing = (
                await connection.execute(
                    select(graph_snapshots.c.snapshot_json, graph_snapshots.c.metadata_json).where(
                        graph_snapshots.c.workspace_id == snapshot.workspace_id,
                        graph_snapshots.c.generation_id == str(snapshot.generation_id),
                    )
                )
            ).one()
            if (
                existing.snapshot_json != payload
                or existing.metadata_json != metadata.model_dump_json()
            ):
                raise IndexingError("Immutable PostgreSQL graph generation contains different data")
        return metadata

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        await self._read(workspace_id, generation_id)

    async def load(self, workspace_id: str) -> GraphSnapshot:
        from app.core.exceptions import IndexNotFoundError

        try:
            generation = await self._generations.active_generation(
                workspace_id, GenerationKind.GRAPH
            )
        except IndexNotFoundError as error:
            raise GraphIndexNotFoundError("No graph index exists for this workspace") from error
        return await self._read(workspace_id, generation)

    async def _read(self, workspace_id: str, generation_id: UUID) -> GraphSnapshot:
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(graph_snapshots).where(
                            graph_snapshots.c.workspace_id == workspace_id,
                            graph_snapshots.c.generation_id == str(generation_id),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise GraphIndexNotFoundError("Prepared PostgreSQL graph generation does not exist")
        try:
            snapshot = GraphSnapshot.model_validate_json(row["snapshot_json"])
            metadata = GraphGenerationMetadata.model_validate_json(row["metadata_json"])
        except ValueError as error:
            raise IndexingError("PostgreSQL graph generation is unreadable") from error
        if (
            snapshot.workspace_id != workspace_id
            or snapshot.generation_id != generation_id
            or metadata.generation_id != generation_id
            or snapshot.schema_version != metadata.schema_version
            or metadata.schema_version != GRAPH_SCHEMA_VERSION
            or snapshot.prompt_version != metadata.prompt_version
            or metadata.prompt_version != GRAPH_PROMPT_VERSION
            or snapshot.extractor_model != metadata.extractor_model
            or metadata.source_count != len(snapshot.source_ids)
            or metadata.document_count != snapshot.document_count
            or metadata.entity_count != len(snapshot.entities)
            or metadata.relationship_count != len(snapshot.relationships)
            or metadata.graph_sha256 != hashlib.sha256(row["snapshot_json"].encode()).hexdigest()
        ):
            raise IndexingError("PostgreSQL graph generation failed integrity validation")
        return snapshot
