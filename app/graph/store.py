"""Graph-store contract and durable local immutable-generation adapter."""

import asyncio
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.core.exceptions import GraphIndexNotFoundError, IndexingError
from app.graph.extraction import GRAPH_PROMPT_VERSION, GRAPH_SCHEMA_VERSION
from app.models import GraphGenerationMetadata, GraphSnapshot


class GraphStore(Protocol):
    """Prepare, activate, and load workspace-scoped graph generations."""

    async def prepare(self, snapshot: GraphSnapshot) -> GraphGenerationMetadata: ...

    async def activate(self, workspace_id: str, generation_id: UUID) -> None: ...

    async def load(self, workspace_id: str) -> GraphSnapshot: ...


class LocalGraphStore:
    """Persist checksummed graph snapshots beneath a controlled local root."""

    def __init__(self, root: Path, *, expected_extractor_model: str | None = None) -> None:
        self._root = (root / "graph-indexes" / "workspaces").resolve()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._expected_extractor_model = expected_extractor_model

    async def prepare(self, snapshot: GraphSnapshot) -> GraphGenerationMetadata:
        async with self._locks[snapshot.workspace_id]:
            return await asyncio.to_thread(self._write_generation, snapshot)

    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        async with self._locks[workspace_id]:
            await asyncio.to_thread(self._activate_generation, workspace_id, generation_id)

    async def load(self, workspace_id: str) -> GraphSnapshot:
        async with self._locks[workspace_id]:
            return await asyncio.to_thread(self._load_generation, workspace_id)

    def _write_generation(self, snapshot: GraphSnapshot) -> GraphGenerationMetadata:
        directory = (
            self._workspace_directory(snapshot.workspace_id)
            / "generations"
            / str(snapshot.generation_id)
        )
        directory.mkdir(parents=True, exist_ok=False)
        graph_payload = snapshot.model_dump_json(indent=2).encode()
        graph_path = directory / "graph.json"
        graph_path.write_bytes(graph_payload)
        metadata = GraphGenerationMetadata(
            generation_id=snapshot.generation_id,
            schema_version=snapshot.schema_version,
            extractor_model=snapshot.extractor_model,
            prompt_version=snapshot.prompt_version,
            source_count=len(snapshot.source_ids),
            document_count=snapshot.document_count,
            entity_count=len(snapshot.entities),
            relationship_count=len(snapshot.relationships),
            graph_sha256=hashlib.sha256(graph_payload).hexdigest(),
        )
        (directory / "metadata.json").write_text(metadata.model_dump_json(indent=2))
        return metadata

    def _activate_generation(self, workspace_id: str, generation_id: UUID) -> None:
        workspace = self._workspace_directory(workspace_id)
        directory = workspace / "generations" / str(generation_id)
        if not directory.is_dir():
            raise IndexingError("Prepared graph generation does not exist")
        self._read_and_validate(directory, generation_id, workspace_id)
        current = workspace / "CURRENT"
        temporary = workspace / "CURRENT.tmp"
        temporary.write_text(str(generation_id))
        temporary.replace(current)

    def _load_generation(self, workspace_id: str) -> GraphSnapshot:
        workspace = self._workspace_directory(workspace_id)
        current = workspace / "CURRENT"
        if not current.is_file():
            raise GraphIndexNotFoundError("No graph index exists for this workspace")
        try:
            generation_id = UUID(current.read_text().strip())
        except (OSError, ValueError) as error:
            raise IndexingError("The workspace graph pointer is invalid") from error
        return self._read_and_validate(
            workspace / "generations" / str(generation_id), generation_id, workspace_id
        )

    def _read_and_validate(
        self, directory: Path, generation_id: UUID, workspace_id: str
    ) -> GraphSnapshot:
        try:
            graph_payload = (directory / "graph.json").read_bytes()
            metadata = GraphGenerationMetadata.model_validate_json(
                (directory / "metadata.json").read_text()
            )
            snapshot = GraphSnapshot.model_validate_json(graph_payload)
        except (OSError, ValueError) as error:
            raise IndexingError("The current graph generation is unreadable") from error
        if metadata.generation_id != generation_id or snapshot.generation_id != generation_id:
            raise IndexingError("Graph generation metadata does not match its pointer")
        if snapshot.workspace_id != workspace_id:
            raise IndexingError("Graph generation belongs to another workspace")
        if (
            metadata.schema_version != snapshot.schema_version
            or metadata.extractor_model != snapshot.extractor_model
            or metadata.prompt_version != snapshot.prompt_version
        ):
            raise IndexingError("Graph generation version metadata is inconsistent")
        if (
            snapshot.schema_version != GRAPH_SCHEMA_VERSION
            or snapshot.prompt_version != GRAPH_PROMPT_VERSION
        ):
            raise IndexingError("Graph generation is incompatible; rebuild the graph index")
        if (
            self._expected_extractor_model is not None
            and snapshot.extractor_model != self._expected_extractor_model
        ):
            raise IndexingError("Graph extractor model changed; rebuild the graph index")
        if hashlib.sha256(graph_payload).hexdigest() != metadata.graph_sha256:
            raise IndexingError("Graph generation checksum validation failed")
        if (
            metadata.source_count != len(snapshot.source_ids)
            or metadata.document_count != snapshot.document_count
            or metadata.entity_count != len(snapshot.entities)
            or metadata.relationship_count != len(snapshot.relationships)
        ):
            raise IndexingError("Graph generation counts are inconsistent")
        return snapshot

    def _workspace_directory(self, workspace_id: str) -> Path:
        if not workspace_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in workspace_id
        ):
            raise ValueError("Invalid workspace identifier")
        directory = (self._root / workspace_id).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("Graph index path escapes the configured data directory")
        return directory
