"""Checkpoint graph extraction batches before atomically publishing Neo4j generations."""

from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from app.api.dependencies import (
    get_graph_indexing_service,
    get_graph_store,
    get_persistence_repository,
    get_source_repository,
)
from app.core.config import get_settings
from app.graph.extraction import GRAPH_SCHEMA_VERSION, resolve_graph
from app.models import (
    DocumentGraphExtraction,
    GraphSnapshot,
    NormalizedDocument,
    SourceStatus,
    SourceType,
)
from app.persistence import GenerationKind

_DOCUMENTS = TypeAdapter(list[NormalizedDocument])
_EXTRACTIONS = TypeAdapter(list[DocumentGraphExtraction])


async def advance_graph(job: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any]:
    from app.jobs.processing import artifact, storage

    service = get_graph_indexing_service()
    if cp["stage"] == "parse":
        sources = [
            source
            for source in await get_source_repository().list(job["workspace_id"])
            if source.status == SourceStatus.READY
            and source.config.source_type != SourceType.DATABASE
        ]
        documents: list[NormalizedDocument] = []
        for source in sources:
            documents.extend(await storage().load_documents(job["workspace_id"], source.source_id))
        if not documents or len(documents) > get_settings().job_max_documents:
            raise ValueError("Graph requires a bounded set of ready source documents")
        batches = service.batches(documents)
        await storage().write(
            artifact(job, "graph-documents.json"), _DOCUMENTS.dump_json(documents), overwrite=True
        )
        for i, batch in enumerate(batches):
            await storage().write(
                artifact(job, f"graph-batch-{i}.json"), _DOCUMENTS.dump_json(batch), overwrite=True
            )
        cp.update(
            stage="graph_extract",
            offset=0,
            total=len(batches),
            source_ids=[str(s.source_id) for s in sources],
            extractor_model=service.extractor.model_name,
            prompt_version=service.extractor.prompt_version,
        )
        return cp
    if (
        service.extractor.model_name != cp["extractor_model"]
        or service.extractor.prompt_version != cp["prompt_version"]
    ):
        raise ValueError("Graph extraction configuration changed during the job")
    if cp["stage"] == "graph_extract":
        batch = _DOCUMENTS.validate_json(
            await storage().read(artifact(job, f"graph-batch-{cp['offset']}.json"))
        )
        key = artifact(job, f"graph-extraction-{cp['offset']}.json")
        try:
            _EXTRACTIONS.validate_json(await storage().read(key))
        except Exception:
            extraction = await service.extractor.extract(batch)
            # Resolve validates source spans before a checkpoint accepts model output.
            resolve_graph(job["workspace_id"], batch, extraction)
            await storage().write(key, _EXTRACTIONS.dump_json(extraction))
        cp["offset"] += 1
        if cp["offset"] == cp["total"]:
            cp["stage"] = "graph_publish"
        return cp
    if cp["stage"] != "graph_publish":
        raise ValueError("Unknown graph job stage")
    documents = _DOCUMENTS.validate_json(
        await storage().read(artifact(job, "graph-documents.json"))
    )
    extractions = []
    for i in range(cp["total"]):
        extractions.extend(
            _EXTRACTIONS.validate_json(
                await storage().read(artifact(job, f"graph-extraction-{i}.json"))
            )
        )
    entities, relationships = resolve_graph(job["workspace_id"], documents, extractions)
    snapshot = GraphSnapshot(
        generation_id=UUID(cp["generation_id"]),
        workspace_id=job["workspace_id"],
        schema_version=GRAPH_SCHEMA_VERSION,
        extractor_model=cp["extractor_model"],
        prompt_version=cp["prompt_version"],
        source_ids=cp["source_ids"],
        document_count=len(documents),
        entities=entities,
        relationships=relationships,
    )
    # Store the exact snapshot once so retries preserve immutable creation timestamps.
    key = artifact(job, "graph-snapshot.json")
    try:
        snapshot = GraphSnapshot.model_validate_json(await storage().read(key))
    except Exception:
        await storage().write(key, snapshot.model_dump_json().encode())
    await get_graph_store().prepare(snapshot)
    await get_graph_store().activate(job["workspace_id"], snapshot.generation_id)
    persistence = get_persistence_repository()
    try:
        operation = await persistence.operation_for_generation(
            job["workspace_id"], snapshot.generation_id, GenerationKind.GRAPH
        )
    except ValueError:
        operation = await persistence.begin_operation(
            job["workspace_id"], snapshot.generation_id, GenerationKind.GRAPH
        )
    await persistence.mark_prepared(operation.operation_id, "graph")
    await persistence.publish(operation.operation_id, frozenset({"graph"}))
    cp["stage"] = "complete"
    return cp
