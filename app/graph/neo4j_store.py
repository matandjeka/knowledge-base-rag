"""Neo4j graph-generation adapter with PostgreSQL-controlled visibility."""

import hashlib
import json
from typing import Any
from uuid import UUID

from app.core.exceptions import GraphIndexNotFoundError, IndexingError
from app.graph.extraction import GRAPH_PROMPT_VERSION, GRAPH_SCHEMA_VERSION
from app.models import GraphGenerationMetadata, GraphSnapshot
from app.persistence import ActiveGenerationRepository, GenerationKind


class Neo4jGraphStore:
    """Persist immutable native graph records and load the metadata-active snapshot."""

    def __init__(
        self, driver: Any, generations: ActiveGenerationRepository, *, database: str
    ) -> None:
        self._driver = driver
        self._generations = generations
        self._database = database

    @classmethod
    def from_connection(
        cls,
        *,
        uri: str,
        username: str,
        password: str,
        database: str,
        generations: ActiveGenerationRepository,
    ) -> "Neo4jGraphStore":
        from neo4j import AsyncGraphDatabase

        return cls(
            AsyncGraphDatabase.driver(uri, auth=(username, password)),
            generations,
            database=database,
        )

    async def close(self) -> None:
        await self._driver.close()

    async def prepare(self, snapshot: GraphSnapshot) -> GraphGenerationMetadata:
        payload = snapshot.model_dump_json().encode()
        metadata = GraphGenerationMetadata(
            generation_id=snapshot.generation_id,
            schema_version=snapshot.schema_version,
            extractor_model=snapshot.extractor_model,
            prompt_version=snapshot.prompt_version,
            source_count=len(snapshot.source_ids),
            document_count=snapshot.document_count,
            entity_count=len(snapshot.entities),
            relationship_count=len(snapshot.relationships),
            graph_sha256=hashlib.sha256(payload).hexdigest(),
        )
        entities = [
            {
                "entity_id": str(entity.entity_id),
                "entity_type": entity.entity_type.value,
                "canonical_name": entity.canonical_name,
                "normalized_name": entity.normalized_name,
                "aliases": entity.aliases,
                "mentions_json": json.dumps(
                    [item.model_dump(mode="json") for item in entity.mentions], sort_keys=True
                ),
            }
            for entity in snapshot.entities
        ]
        relationships = [
            {
                "relationship_id": str(item.relationship_id),
                "subject_id": str(item.subject_id),
                "object_id": str(item.object_id),
                "predicate": item.predicate.value,
                "confidence": item.confidence,
                "supports_json": json.dumps(
                    [support.model_dump(mode="json") for support in item.supports], sort_keys=True
                ),
            }
            for item in snapshot.relationships
        ]
        try:
            async with self._driver.session(database=self._database) as session:
                await session.execute_write(
                    _write_generation,
                    snapshot.model_dump_json(),
                    metadata.model_dump_json(),
                    snapshot.workspace_id,
                    str(snapshot.generation_id),
                    entities,
                    relationships,
                )
        except Exception as error:
            raise IndexingError("Neo4j generation preparation failed") from error
        return metadata

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        await self._read(workspace_id, generation_id)

    async def load(self, workspace_id: str) -> GraphSnapshot:
        try:
            generation_id = await self._generations.active_generation(
                workspace_id, GenerationKind.GRAPH
            )
        except Exception as error:
            raise GraphIndexNotFoundError("No graph index exists for this workspace") from error
        return await self._read(workspace_id, generation_id)

    async def _read(self, workspace_id: str, generation_id: UUID) -> GraphSnapshot:
        query = (
            "MATCH (g:RagGraphGeneration "
            "{workspace_id: $workspace_id, generation_id: $generation_id}) "
            "RETURN g.snapshot_json AS snapshot_json, g.metadata_json AS metadata_json"
        )
        try:
            records, _, _ = await self._driver.execute_query(
                query,
                workspace_id=workspace_id,
                generation_id=str(generation_id),
                database_=self._database,
                routing_="r",
            )
            if not records:
                raise GraphIndexNotFoundError("Prepared Neo4j graph generation does not exist")
            snapshot = GraphSnapshot.model_validate_json(records[0]["snapshot_json"])
            metadata = GraphGenerationMetadata.model_validate_json(records[0]["metadata_json"])
        except GraphIndexNotFoundError:
            raise
        except Exception as error:
            raise IndexingError("Neo4j graph generation is unreadable") from error
        payload = snapshot.model_dump_json().encode()
        if (
            snapshot.workspace_id != workspace_id
            or snapshot.generation_id != generation_id
            or metadata.generation_id != generation_id
            or metadata.schema_version != GRAPH_SCHEMA_VERSION
            or metadata.prompt_version != GRAPH_PROMPT_VERSION
            or metadata.graph_sha256 != hashlib.sha256(payload).hexdigest()
        ):
            raise IndexingError("Neo4j graph generation failed integrity validation")
        return snapshot


async def _write_generation(
    transaction: Any,
    snapshot_json: str,
    metadata_json: str,
    workspace_id: str,
    generation_id: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> None:
    existing = await transaction.run(
        "MATCH (g:RagGraphGeneration {workspace_id: $workspace_id, generation_id: $generation_id}) "
        "RETURN g.snapshot_json AS snapshot_json",
        workspace_id=workspace_id,
        generation_id=generation_id,
    )
    record = await existing.single()
    if record is not None:
        if record["snapshot_json"] != snapshot_json:
            raise ValueError("Immutable Neo4j generation already contains different data")
        return
    await transaction.run(
        "CREATE (g:RagGraphGeneration {workspace_id: $workspace_id, generation_id: $generation_id, "
        "snapshot_json: $snapshot_json, metadata_json: $metadata_json})",
        workspace_id=workspace_id,
        generation_id=generation_id,
        snapshot_json=snapshot_json,
        metadata_json=metadata_json,
    )
    await transaction.run(
        "UNWIND $entities AS entity "
        "MATCH (g:RagGraphGeneration {workspace_id: $workspace_id, generation_id: $generation_id}) "
        "CREATE (e:RagEntity {workspace_id: $workspace_id, generation_id: $generation_id, "
        "entity_id: entity.entity_id, entity_type: entity.entity_type, "
        "canonical_name: entity.canonical_name, normalized_name: entity.normalized_name, "
        "aliases: entity.aliases, mentions_json: entity.mentions_json}) "
        "CREATE (g)-[:CONTAINS]->(e)",
        workspace_id=workspace_id,
        generation_id=generation_id,
        entities=entities,
    )
    await transaction.run(
        "UNWIND $relationships AS rel "
        "MATCH (s:RagEntity {workspace_id: $workspace_id, generation_id: $generation_id, "
        "entity_id: rel.subject_id}) "
        "MATCH (o:RagEntity {workspace_id: $workspace_id, generation_id: $generation_id, "
        "entity_id: rel.object_id}) "
        "CREATE (s)-[:RAG_RELATIONSHIP {relationship_id: rel.relationship_id, "
        "predicate: rel.predicate, confidence: rel.confidence, "
        "supports_json: rel.supports_json}]->(o)",
        workspace_id=workspace_id,
        generation_id=generation_id,
        relationships=relationships,
    )
