"""Resumable parse, embed, verify, and publication steps; no browser lifetime dependency."""

import asyncio
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import numpy as np
from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import delete, select, update

from app.api.dependencies import (
    get_csv_ingestion_service,
    get_embedding_service,
    get_lexical_store,
    get_metadata_engine,
    get_pdf_ingestion_service,
    get_persistence_repository,
    get_source_repository,
    get_source_storage,
    get_vector_store,
    get_website_ingestion_service,
)
from app.core.config import get_settings
from app.core.exceptions import SourceNotFoundError
from app.jobs.repository import jobs, workspace_jobs
from app.models import NormalizedDocument, Source, SourceConfig, SourceStatus, SourceType
from app.persistence import GenerationKind
from app.retrieval.pinecone_store import PineconeVectorStore
from app.retrieval.sentence_window import build_sentence_window_documents
from app.retrieval.vector_store import (
    VectorGenerationMetadata,
    VectorIndexKind,
    VectorIndexMetadata,
)
from app.storage.vercel_blob import VercelBlobSourceStorage

_DOCS = TypeAdapter(list[NormalizedDocument])


def storage() -> VercelBlobSourceStorage:
    value = get_source_storage()
    if not isinstance(value, VercelBlobSourceStorage):
        raise HTTPException(503, "Durable jobs require private Vercel Blob storage.")
    return value


def artifact(job: dict[str, Any], name: str) -> str:
    return f"workspaces/{job['workspace_id']}/jobs/{job['id']}/{name}"


async def step_job(job_id: str, expected_step: int) -> dict[str, Any]:
    """A monotonic step-number fence makes duplicate deliveries harmless.

    The provider work runs outside any database transaction so a row lock is never held
    across embedding or vector-store calls. Every stage in :func:`advance` writes immutable,
    overwrite-safe artifacts, so a step that is retried (or delivered twice concurrently)
    re-produces the same checkpoint; only the first writer advances the step counter.
    """
    engine = get_metadata_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(select(jobs).where(jobs.c.id == job_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "Job not found.")
    job = dict(row)
    if job["step"] != expected_step or job["status"] in {"complete", "cancelled"}:
        return public_job(job)

    # Each invocation stays below the configured 300-second function budget.
    async with asyncio.timeout(220):
        checkpoint = await advance(job)

    complete = checkpoint["stage"] == "complete"
    next_step = job["step"] + 1
    status = "complete" if complete else "running"
    async with engine.begin() as conn:
        current = (
            (await conn.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()))
            .mappings()
            .first()
        )
        if current is None:
            raise HTTPException(404, "Job not found.")
        if current["step"] != job["step"] or current["status"] in {"complete", "cancelled"}:
            # A concurrent delivery already recorded this step's result.
            return public_job(dict(current))
        await conn.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(checkpoint=checkpoint, step=next_step, status=status, error=None)
        )
        if complete:
            await conn.execute(delete(workspace_jobs).where(workspace_jobs.c.job_id == job_id))
    job.update(checkpoint=checkpoint, step=next_step, status=status, error=None)
    return public_job(job)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    cp = job["checkpoint"]
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job["step"],
        "stage": cp["stage"],
        "source_id": cp["source_id"],
        "error": job["error"],
        "processed": cp.get("offset", 0),
        "total": cp.get("total", 0),
    }


async def advance(job: dict[str, Any]) -> dict[str, Any]:
    cp = dict(job["checkpoint"])
    if job["spec"]["kind"] == "graph":
        from app.jobs.graph import advance_graph

        return await advance_graph(job, cp)
    if "model_name" in cp and (
        cp["model_name"] != get_embedding_service().model_name
        or cp["dimension"] != get_embedding_service().dimension
    ):
        raise ValueError("Embedding configuration changed; cancel and create a new indexing job")
    stage = cp["stage"]
    if stage == "parse":
        return await parse(job, cp)
    if stage == "snapshot":
        return await snapshot(job, cp)
    if stage == "embed":
        return await embed(job, cp)
    if stage == "lexical":
        documents = _DOCS.validate_json(await storage().read(artifact(job, "vector.json")))
        await get_lexical_store().prepare(
            job["workspace_id"], documents, generation_id=UUID(cp["generation_id"])
        )
        cp["stage"] = "publish"
        return cp
    if stage == "publish":
        return await publish(job, cp)
    raise ValueError("Unknown job stage")


async def parse(job: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any]:
    spec, workspace = job["spec"], job["workspace_id"]
    source_id = UUID(cp["source_id"])
    repository = get_source_repository()
    if spec["kind"] == "reindex":
        await repository.get(workspace, source_id)
        cp["stage"] = "snapshot"
        return cp
    try:
        source = await repository.get(workspace, source_id)
    except SourceNotFoundError:
        source = Source(
            source_id=source_id,
            workspace_id=workspace,
            name=spec.get("filename") or spec.get("url", "Source"),
            config=SourceConfig(source_type=SourceType(spec["kind"]), options=spec),
        )
        await repository.create(source)
    source = await repository.get(workspace, source_id)
    if source.status != SourceStatus.INDEXING:
        await repository.transition(workspace, source_id, SourceStatus.INDEXING)
    documents: list[NormalizedDocument] = []
    if spec["kind"] == "website":
        # One page per durable step. Reuse SSRF, redirect, robots and extraction policies.
        service = get_website_ingestion_service()
        queue = cp.get("urls", [spec["url"]])
        visited = cp.get("visited", [])
        current = queue.pop(0)
        crawl = await service._crawler.crawl(current, crawl_same_domain=False, page_limit=1)
        page_docs = service.build_documents(source, crawl)
        visited.append(current)
        for page in crawl.pages:
            for link in page.links:
                if (
                    (
                        spec.get("crawl_same_domain")
                        and urlsplit(link).hostname == urlsplit(spec["url"]).hostname
                    )
                    and link not in visited
                    and link not in queue
                    and len(queue) < 200
                ):
                    queue.append(link)
        await storage().write(
            artifact(job, f"page-{len(visited)}.json"), _DOCS.dump_json(page_docs), overwrite=True
        )
        if queue and len(visited) < spec.get("page_limit", 1):
            cp.update(urls=queue, visited=visited)
            return cp
        for number in range(1, len(visited) + 1):
            documents.extend(
                _DOCS.validate_json(await storage().read(artifact(job, f"page-{number}.json")))
            )
    else:
        path = spec["pathname"]
        expected = f"workspaces/{workspace}/uploads/"
        if not path.startswith(expected) or ".." in path or ":" in path:
            raise ValueError("Upload is outside the workspace")
        data = await storage().read(path)
        filename = spec["filename"]
        if spec["kind"] == "pdf":
            service_pdf = get_pdf_ingestion_service()
            parsed = await asyncio.to_thread(
                service_pdf._connector.parse, filename, "application/pdf", data
            )
            locator = await storage().save_original(workspace, source_id, data)
            documents = service_pdf.build_documents(source, parsed.pages, locator)
        else:
            service_csv = get_csv_ingestion_service()
            parsed_csv = await asyncio.to_thread(
                service_csv._connector.parse, filename, "text/csv", data
            )
            selection = service_csv.validate_selection(
                parsed_csv,
                spec["text_columns"],
                spec.get("metadata_columns", []),
                spec.get("row_id_column"),
            )
            locator = await storage().save_original(
                workspace, source_id, data, filename="original.csv"
            )
            documents, _ = service_csv.build_documents(source, parsed_csv, selection, locator)
    if not documents or len(documents) > get_settings().job_max_documents:
        raise ValueError("Source exceeds bounded indexing limits or contains no usable text")
    # Stable IDs make retries refer to the same underlying evidence.
    documents = [
        d.model_copy(update={"document_id": uuid5(source_id, str(i))})
        for i, d in enumerate(documents)
    ]
    await storage().save_documents(workspace, source_id, documents)
    cp["stage"] = "snapshot"
    return cp


async def snapshot(job: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any]:
    documents: list[NormalizedDocument] = []
    for source in await get_source_repository().list(job["workspace_id"]):
        if source.status == SourceStatus.READY or str(source.source_id) == cp["source_id"]:
            documents.extend(await storage().load_documents(job["workspace_id"], source.source_id))
    if sum(len(d.content) for d in documents) > 16_000_000 or any(
        len(d.content) > 16000 for d in documents
    ):
        raise ValueError(
            "Source text exceeds the bounded indexing budget; split oversized CSV rows"
        )
    windows = build_sentence_window_documents(
        documents, radius=get_settings().sentence_window_radius
    )
    if not documents or len(documents) + len(windows) > get_settings().job_max_documents:
        raise ValueError("Workspace exceeds the configured bounded indexing limit")
    for kind, items in (("vector", documents), ("sentence_window", windows)):
        await storage().write(artifact(job, f"{kind}.json"), _DOCS.dump_json(items), overwrite=True)
    cp.update(
        stage="embed",
        kind="vector",
        offset=0,
        total=len(documents),
        hashes={},
        counts={},
        model_name=get_embedding_service().model_name,
        dimension=get_embedding_service().dimension,
    )
    return cp


async def embed(job: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any]:
    documents = _DOCS.validate_json(await storage().read(artifact(job, f"{cp['kind']}.json")))
    batch = documents[cp["offset"] : cp["offset"] + 32]
    key = artifact(job, f"embeddings-{cp['kind']}-{cp['offset']}.json")
    # Store provider output before upsert; retries avoid another billable embedding request.
    try:
        raw = json.loads(await storage().read(key))
        vectors = np.asarray(raw, dtype=np.float32)
    except Exception:
        vectors = await get_embedding_service().embed_documents([d.content for d in batch])
        await storage().write(key, json.dumps(vectors.tolist()).encode())
    store = get_vector_store()
    if not isinstance(store, PineconeVectorStore):
        raise ValueError("Durable jobs require Pinecone")
    await store.upsert_job_batch(
        job["workspace_id"], UUID(cp["generation_id"]), VectorIndexKind(cp["kind"]), batch, vectors
    )
    hashes = dict(cp["hashes"])
    hashes[cp["kind"]] = hashlib.sha256(
        (hashes.get(cp["kind"], "") + hashlib.sha256(vectors.tobytes()).hexdigest()).encode()
    ).hexdigest()
    cp.update(offset=cp["offset"] + len(batch), hashes=hashes)
    if cp["offset"] >= len(documents):
        counts = dict(cp["counts"])
        counts[cp["kind"]] = len(documents)
        cp["counts"] = counts
        if cp["kind"] == "vector":
            windows = _DOCS.validate_json(
                await storage().read(artifact(job, "sentence_window.json"))
            )
            cp.update(kind="sentence_window", offset=0, total=len(windows))
        else:
            cp["stage"] = "lexical"
    return cp


async def publish(job: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any]:
    generation_id, workspace = UUID(cp["generation_id"]), job["workspace_id"]
    indexes = {}
    for kind in VectorIndexKind:
        payload = await storage().read(artifact(job, f"{kind.value}.json"))
        indexes[kind] = VectorIndexMetadata(
            generation_id=generation_id,
            model_name=get_embedding_service().model_name,
            dimension=get_embedding_service().dimension,
            normalized=True,
            document_count=cp["counts"][kind.value],
            index_sha256=cp["hashes"][kind.value],
            documents_sha256=hashlib.sha256(payload).hexdigest(),
        )
    store = get_vector_store()
    assert isinstance(store, PineconeVectorStore)
    await store.finish_job_generation(
        workspace, VectorGenerationMetadata(generation_id=generation_id, indexes=indexes)
    )
    lexical = get_lexical_store()
    await lexical.activate(workspace, generation_id)
    persistence = get_persistence_repository()
    try:
        operation = await persistence.operation_for_generation(
            workspace, generation_id, GenerationKind.RETRIEVAL
        )
    except ValueError:
        operation = await persistence.begin_operation(
            workspace, generation_id, GenerationKind.RETRIEVAL
        )
    await persistence.mark_prepared(operation.operation_id, "vector")
    await persistence.mark_prepared(operation.operation_id, "lexical")
    await persistence.publish(operation.operation_id, frozenset({"vector", "lexical"}))
    source = await get_source_repository().get(workspace, UUID(cp["source_id"]))
    if source.status != SourceStatus.READY:
        source = await get_source_repository().transition(
            workspace, source.source_id, SourceStatus.READY
        )
    await storage().save_source(source)
    cp["stage"] = "complete"
    return cp
