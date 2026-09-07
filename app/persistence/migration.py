"""Explicit, resumable migration from local persistence to production backends."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import faiss
import numpy as np
from pydantic import TypeAdapter

from app.api.dependencies import (
    close_application_dependencies,
    get_graph_store,
    get_lexical_store,
    get_persistence_repository,
    get_source_repository,
    get_source_storage,
    get_vector_store,
)
from app.core.config import get_settings
from app.core.exceptions import SourceNotFoundError
from app.graph.neo4j_store import Neo4jGraphStore
from app.graph.store import LocalGraphStore
from app.models import CrawlManifest, NormalizedDocument, Source, SourceStatus, SourceUpdate
from app.persistence import GenerationKind, PersistenceRepository
from app.repositories import SourceRepository
from app.retrieval.blob_lexical import BlobLexicalStore
from app.retrieval.pinecone_store import PineconeVectorStore
from app.retrieval.vector_store import (
    VectorGenerationMetadata,
    VectorIndexKind,
    VectorIndexPayload,
)
from app.storage import AzureBlobSourceStorage, LocalSourceStorage

_DOCUMENTS = TypeAdapter(list[NormalizedDocument])


class LocalPersistenceMigrator:
    """Copy verified local state without deleting or mutating its source files."""

    def __init__(
        self,
        local_root: Path,
        source_repository: SourceRepository,
        persistence_repository: PersistenceRepository,
        source_storage: AzureBlobSourceStorage,
        vector_store: PineconeVectorStore,
        lexical_store: BlobLexicalStore,
        graph_store: Neo4jGraphStore,
        migration_id: UUID,
    ) -> None:
        self._root = local_root.resolve()
        self._local_sources = LocalSourceStorage(self._root)
        self._local_graph = LocalGraphStore(self._root)
        self._sources = source_repository
        self._persistence = persistence_repository
        self._storage = source_storage
        self._vectors = vector_store
        self._lexical = lexical_store
        self._graphs = graph_store
        self._migration_id = migration_id

    def workspaces(self) -> list[str]:
        root = self._root / "workspaces"
        if not root.is_dir():
            return []
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    async def inventory(self, workspace_filter: str | None = None) -> dict[str, Any]:
        workspaces = [workspace_filter] if workspace_filter else self.workspaces()
        rows: list[dict[str, Any]] = []
        for workspace_id in workspaces:
            sources = await self._local_sources.list_sources(workspace_id)
            document_count = 0
            for source in sources:
                document_count += len(
                    await self._local_sources.load_documents(workspace_id, source.source_id)
                )
            rows.append(
                {
                    "workspace_id": workspace_id,
                    "source_count": len(sources),
                    "document_count": document_count,
                    "vector_generation": self._local_generation("vector-indexes", workspace_id),
                    "lexical_generation": self._local_generation("lexical-indexes", workspace_id),
                    "graph_generation": self._local_generation("graph-indexes", workspace_id),
                }
            )
        return {"migration_id": str(self._migration_id), "workspaces": rows}

    async def migrate(self, workspace_filter: str | None = None) -> dict[str, Any]:
        inventory = await self.inventory(workspace_filter)
        for row in inventory["workspaces"]:
            workspace_id = row["workspace_id"]
            await self._migrate_sources(workspace_id)
            if row["vector_generation"] is not None:
                await self._migrate_retrieval(workspace_id, UUID(row["vector_generation"]))
            if row["graph_generation"] is not None:
                await self._migrate_graph(workspace_id, UUID(row["graph_generation"]))
        return await self.verify(workspace_filter)

    async def verify(self, workspace_filter: str | None = None) -> dict[str, Any]:
        inventory = await self.inventory(workspace_filter)
        failures: list[str] = []
        for row in inventory["workspaces"]:
            workspace_id = row["workspace_id"]
            local_sources = await self._local_sources.list_sources(workspace_id)
            remote_sources = await self._sources.list(workspace_id)
            if {source.source_id for source in local_sources} != {
                source.source_id for source in remote_sources
            }:
                failures.append(f"{workspace_id}:source_ids")
            for source in local_sources:
                local_documents = await self._local_sources.load_documents(
                    workspace_id, source.source_id
                )
                remote_documents = await self._storage.load_documents(
                    workspace_id, source.source_id
                )
                if _documents_checksum(local_documents) != _documents_checksum(remote_documents):
                    failures.append(f"{workspace_id}:{source.source_id}:documents")
        return {**inventory, "verified": not failures, "failures": failures}

    async def cutover(self, workspace_filter: str | None = None) -> dict[str, Any]:
        verification = await self.verify(workspace_filter)
        if not verification["verified"]:
            raise ValueError("Migration verification failed; cutover refused")
        for row in verification["workspaces"]:
            workspace_id = row["workspace_id"]
            if row["vector_generation"] is not None:
                generation_id = UUID(row["vector_generation"])
                await self._vectors.activate(workspace_id, generation_id)
                await self._lexical.activate(workspace_id, generation_id)
                operation = await self._persistence.operation_for_generation(
                    workspace_id, generation_id, GenerationKind.RETRIEVAL
                )
                await self._persistence.publish(
                    operation.operation_id, frozenset({"vector", "lexical"})
                )
            if row["graph_generation"] is not None:
                generation_id = UUID(row["graph_generation"])
                await self._graphs.activate(workspace_id, generation_id)
                operation = await self._persistence.operation_for_generation(
                    workspace_id, generation_id, GenerationKind.GRAPH
                )
                await self._persistence.publish(operation.operation_id, frozenset({"graph"}))
        return {**verification, "cutover": True}

    async def _migrate_sources(self, workspace_id: str) -> None:
        for source in await self._local_sources.list_sources(workspace_id):
            checksum = hashlib.sha256(source.model_dump_json().encode()).hexdigest()
            key = f"source:{workspace_id}:{source.source_id}"
            if await self._persistence.migration_checkpoint(self._migration_id, key) == checksum:
                continue
            await self._copy_source_record(source)
            directory = self._root / "workspaces" / workspace_id / "sources" / str(source.source_id)
            for filename in ("original.pdf", "original.csv"):
                path = directory / filename
                if path.is_file():
                    locator = await self._storage.save_original(
                        workspace_id, source.source_id, path.read_bytes(), filename=filename
                    )
                    source = await self._sources.update(
                        workspace_id,
                        source.source_id,
                        SourceUpdate(
                            config=source.config.model_copy(update={"uri": locator}, deep=True)
                        ),
                    )
            documents = await self._local_sources.load_documents(workspace_id, source.source_id)
            await self._storage.save_documents(workspace_id, source.source_id, documents)
            manifest = directory / "crawl-manifest.json"
            if manifest.is_file():
                await self._storage.save_crawl_manifest(
                    workspace_id,
                    source.source_id,
                    CrawlManifest.model_validate_json(manifest.read_text()),
                )
            await self._storage.save_source(source)
            await self._persistence.checkpoint_migration(self._migration_id, key, checksum)

    async def _copy_source_record(self, source: Source) -> None:
        try:
            existing = await self._sources.get(source.workspace_id, source.source_id)
            if existing.status is not source.status:
                raise ValueError("Target source lifecycle differs from local source")
            return
        except SourceNotFoundError:
            pass
        created = await self._sources.create(
            source.model_copy(update={"status": SourceStatus.REGISTERED}, deep=True)
        )
        if source.status is SourceStatus.REGISTERED:
            return
        if source.status is SourceStatus.FAILED:
            await self._sources.transition(
                source.workspace_id, created.source_id, SourceStatus.FAILED
            )
            return
        await self._sources.transition(
            source.workspace_id, created.source_id, SourceStatus.INDEXING
        )
        if source.status is SourceStatus.READY:
            await self._sources.transition(
                source.workspace_id,
                created.source_id,
                SourceStatus.READY,
                transitioned_at=source.updated_at,
            )

    async def _migrate_retrieval(self, workspace_id: str, generation_id: UUID) -> None:
        operation = await self._operation(workspace_id, generation_id, GenerationKind.RETRIEVAL)
        root = self._root / "vector-indexes" / "workspaces" / workspace_id
        generation_root = root / "generations" / str(generation_id)
        metadata = VectorGenerationMetadata.model_validate_json(
            (generation_root / "metadata.json").read_text()
        )
        payloads: dict[VectorIndexKind, VectorIndexPayload] = {}
        for kind in metadata.indexes:
            directory = generation_root / kind.value
            documents = _DOCUMENTS.validate_json((directory / "documents.json").read_bytes())
            index = faiss.read_index(str(directory / "index.faiss"))
            vectors = np.asarray(index.reconstruct_n(0, index.ntotal), dtype=np.float32)
            payloads[kind] = VectorIndexPayload(list(documents), vectors)
        await self._vectors.prepare_bundle(
            workspace_id,
            payloads,
            model_name=metadata.indexes[VectorIndexKind.VECTOR].model_name,
            dimension=metadata.indexes[VectorIndexKind.VECTOR].dimension,
            generation_id=generation_id,
        )
        await self._persistence.mark_prepared(operation.operation_id, "vector")
        lexical_documents = payloads[VectorIndexKind.VECTOR].documents
        await self._lexical.prepare(workspace_id, lexical_documents, generation_id=generation_id)
        await self._persistence.mark_prepared(operation.operation_id, "lexical")

    async def _migrate_graph(self, workspace_id: str, generation_id: UUID) -> None:
        operation = await self._operation(workspace_id, generation_id, GenerationKind.GRAPH)
        snapshot = await self._local_graph.load(workspace_id)
        if snapshot.generation_id != generation_id:
            raise ValueError("Local graph pointer changed during migration")
        await self._graphs.prepare(snapshot)
        await self._persistence.mark_prepared(operation.operation_id, "graph")

    async def _operation(self, workspace_id: str, generation_id: UUID, kind: GenerationKind) -> Any:
        try:
            return await self._persistence.operation_for_generation(
                workspace_id, generation_id, kind
            )
        except ValueError:
            return await self._persistence.begin_operation(workspace_id, generation_id, kind)

    def _local_generation(self, family: str, workspace_id: str) -> str | None:
        pointer = self._root / family / "workspaces" / workspace_id / "CURRENT"
        if not pointer.is_file():
            return None
        return str(UUID(pointer.read_text().strip()))


def _documents_checksum(documents: Any) -> str:
    return hashlib.sha256(_DOCUMENTS.dump_json(list(documents))).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "migrate", "verify", "cutover"))
    parser.add_argument("--workspace")
    parser.add_argument("--migration-id", type=UUID)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    return asyncio.run(_run(arguments))


async def _run(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    migration_id = arguments.migration_id or uuid5(
        NAMESPACE_URL, f"rag-persistence-migration:{settings.data_dir.resolve()}"
    )
    if arguments.command == "inventory" or arguments.dry_run:
        storage = LocalSourceStorage(settings.data_dir)
        workspaces = sorted(
            path.name for path in (settings.data_dir / "workspaces").glob("*") if path.is_dir()
        )
        payload = {
            "migration_id": str(migration_id),
            "workspaces": [
                {
                    "workspace_id": workspace,
                    "source_count": len(await storage.list_sources(workspace)),
                }
                for workspace in workspaces
                if arguments.workspace is None or workspace == arguments.workspace
            ],
            "dry_run": arguments.dry_run,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    source_storage = get_source_storage()
    vector_store = get_vector_store()
    lexical_store = get_lexical_store()
    graph_store = get_graph_store()
    if not isinstance(source_storage, AzureBlobSourceStorage):
        raise ValueError("Migration requires SOURCE_STORAGE_BACKEND=azure_blob")
    if not isinstance(vector_store, PineconeVectorStore):
        raise ValueError("Migration requires VECTOR_STORE_BACKEND=pinecone")
    if not isinstance(lexical_store, BlobLexicalStore):
        raise ValueError("Migration requires LEXICAL_STORE_BACKEND=azure_blob")
    if not isinstance(graph_store, Neo4jGraphStore):
        raise ValueError("Migration requires GRAPH_STORE_BACKEND=neo4j")
    migrator = LocalPersistenceMigrator(
        settings.data_dir,
        get_source_repository(),
        get_persistence_repository(),
        source_storage,
        vector_store,
        lexical_store,
        graph_store,
        migration_id,
    )
    try:
        result = await getattr(migrator, arguments.command)(arguments.workspace)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("verified", True) else 1
    finally:
        await close_application_dependencies()


if __name__ == "__main__":
    raise SystemExit(main())
