"""PDF ingestion unit and API integration tests."""

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_pdf_ingestion_service,
    get_source_repository,
    get_source_storage,
)
from app.core.exceptions import IngestionError, PdfValidationError
from app.ingestion.pdf import PdfConnector, chunk_text
from app.ingestion.service import PdfIngestionService
from app.main import app
from app.models import Source, SourceStatus
from app.repositories import InMemorySourceRepository
from app.storage import LocalSourceStorage
from tests.fakes import RecordingSourceIndexer


class _FailingReadyStorage(LocalSourceStorage):
    async def save_source(self, source: Source) -> None:
        if source.status is SourceStatus.READY:
            raise OSError("ready metadata unavailable")
        await super().save_source(source)


class _UnavailableMetadataStorage(LocalSourceStorage):
    async def save_source(self, source: Source) -> None:
        raise OSError("metadata unavailable")


class _FailingActivationIndexer(RecordingSourceIndexer):
    async def activate(self, workspace_id: str, generation_id: UUID) -> None:
        del workspace_id, generation_id
        raise OSError("index activation unavailable")


def _pdf_bytes(*pages: str, title: str = "Employee Handbook") -> bytes:
    document: Any = pymupdf.open()  # type: ignore[no-untyped-call]
    document.set_metadata({"title": title})
    for content in pages:
        page = document.new_page()
        page.insert_textbox(
            page.rect + (36, 36, -36, -36),  # noqa: RUF005
            content,
            fontsize=11,
        )
    payload = cast(bytes, document.tobytes())
    document.close()
    return payload


def _service(
    tmp_path: Path,
) -> tuple[PdfIngestionService, InMemorySourceRepository, LocalSourceStorage]:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path / "data")
    service = PdfIngestionService(
        repository=repository,
        storage=storage,
        connector=PdfConnector(max_size_bytes=1024 * 1024),
        chunk_size=80,
        chunk_overlap=10,
        indexer=RecordingSourceIndexer(),
    )
    return service, repository, storage


def test_chunk_text_is_bounded_and_overlapping() -> None:
    content = " ".join(f"word-{index}" for index in range(60))

    chunks = chunk_text(content, chunk_size=80, overlap=10)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert all(chunk.strip() == chunk for chunk in chunks)


@pytest.mark.parametrize(
    ("filename", "content_type", "data"),
    [
        ("notes.txt", "application/pdf", b"%PDF-invalid"),
        ("notes.pdf", "text/plain", b"%PDF-invalid"),
        ("notes.pdf", "application/pdf", b"not-a-pdf"),
        ("notes.pdf", "application/pdf", b""),
    ],
)
def test_pdf_connector_rejects_invalid_uploads(
    filename: str, content_type: str, data: bytes
) -> None:
    connector = PdfConnector(max_size_bytes=1024)

    with pytest.raises(PdfValidationError):
        connector.parse(filename, content_type, data)


def test_pdf_connector_rejects_pdf_without_extractable_text() -> None:
    connector = PdfConnector(max_size_bytes=1024 * 1024)

    with pytest.raises(PdfValidationError, match="no extractable text"):
        connector.parse("scan.pdf", "application/pdf", _pdf_bytes(""))


def test_pdf_connector_enforces_size_limit() -> None:
    connector = PdfConnector(max_size_bytes=5)

    with pytest.raises(PdfValidationError, match="exceeds"):
        connector.parse("large.pdf", "application/pdf", b"%PDF-too-large")


@pytest.mark.asyncio
async def test_ingestion_persists_original_metadata_and_page_chunks(tmp_path: Path) -> None:
    service, repository, storage = _service(tmp_path)
    data = _pdf_bytes(
        "Vacation requests must be submitted two weeks in advance.",
        "Termination notices require written confirmation.",
    )

    result = await service.ingest("workspace-a", "handbook.pdf", "application/pdf", data)
    source = await repository.get("workspace-a", result.source.source_id)
    documents = await storage.load_documents("workspace-a", source.source_id)
    source_directory = (
        tmp_path / "data" / "workspaces" / "workspace-a" / "sources" / str(source.source_id)
    )

    assert result.page_count == 2
    assert result.chunk_count == len(documents) == 2
    assert source.status is SourceStatus.READY
    assert [document.page_number for document in documents] == [1, 2]
    assert all(document.source_id == source.source_id for document in documents)
    assert (source_directory / "original.pdf").read_bytes() == data
    assert (source_directory / "source.json").is_file()
    assert (source_directory / "documents.jsonl").is_file()


@pytest.mark.asyncio
async def test_invalid_upload_creates_no_source_or_artifacts(tmp_path: Path) -> None:
    service, repository, _ = _service(tmp_path)

    with pytest.raises(PdfValidationError):
        await service.ingest("workspace-a", "notes.txt", "text/plain", b"invalid")

    assert await repository.list("workspace-a") == ()
    assert not (tmp_path / "data").exists()


@pytest.mark.asyncio
async def test_ready_metadata_failure_leaves_source_failed(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = _FailingReadyStorage(tmp_path / "data")
    service = PdfIngestionService(
        repository=repository,
        storage=storage,
        connector=PdfConnector(max_size_bytes=1024 * 1024),
        chunk_size=80,
        chunk_overlap=10,
        indexer=RecordingSourceIndexer(),
    )

    with pytest.raises(IngestionError):
        await service.ingest(
            "workspace-a",
            "policy.pdf",
            "application/pdf",
            _pdf_bytes("Policy text"),
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED
    source_file = (
        tmp_path
        / "data"
        / "workspaces"
        / "workspace-a"
        / "sources"
        / str(sources[0].source_id)
        / "source.json"
    )
    assert '"status": "failed"' in source_file.read_text()


@pytest.mark.asyncio
async def test_index_activation_failure_rolls_ready_source_back_to_failed(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path / "data")
    service = PdfIngestionService(
        repository=repository,
        storage=storage,
        connector=PdfConnector(max_size_bytes=1024 * 1024),
        chunk_size=80,
        chunk_overlap=10,
        indexer=_FailingActivationIndexer(),
    )

    with pytest.raises(IngestionError):
        await service.ingest(
            "workspace-a",
            "policy.pdf",
            "application/pdf",
            _pdf_bytes("Policy text"),
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED


@pytest.mark.asyncio
async def test_failure_state_persistence_error_is_not_suppressed(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = _UnavailableMetadataStorage(tmp_path / "data")
    service = PdfIngestionService(
        repository=repository,
        storage=storage,
        connector=PdfConnector(max_size_bytes=1024 * 1024),
        chunk_size=80,
        chunk_overlap=10,
        indexer=RecordingSourceIndexer(),
    )

    with pytest.raises(IngestionError, match="failure-state persistence failed"):
        await service.ingest(
            "workspace-a",
            "policy.pdf",
            "application/pdf",
            _pdf_bytes("Policy text"),
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED


@pytest.mark.asyncio
async def test_pdf_upload_and_document_inspection_endpoints(tmp_path: Path) -> None:
    service, repository, storage = _service(tmp_path)
    app.dependency_overrides[get_pdf_ingestion_service] = lambda: service
    app.dependency_overrides[get_source_repository] = lambda: repository
    app.dependency_overrides[get_source_storage] = lambda: storage
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/sources/pdf",
                data={"workspace_id": "workspace-a"},
                files={
                    "file": (
                        "policy.pdf",
                        _pdf_bytes("Policy HR-402 applies to all employees."),
                        "application/pdf",
                    )
                },
            )
            source_id = response.json()["source"]["source_id"]
            documents_response = await client.get(
                f"/sources/{source_id}/documents",
                params={"workspace_id": "workspace-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["source"]["status"] == "ready"
    assert documents_response.status_code == 200
    assert documents_response.json()[0]["page_number"] == 1
