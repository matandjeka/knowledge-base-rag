"""CSV parsing, ingestion, persistence, and API integration tests."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_csv_ingestion_service,
    get_source_repository,
    get_source_storage,
)
from app.core.exceptions import CsvValidationError, IngestionError
from app.ingestion.csv import CsvConnector
from app.ingestion.csv_service import CsvIngestionService
from app.main import app
from app.models import Source, SourceStatus, SourceType
from app.repositories import InMemorySourceRepository
from app.storage import LocalSourceStorage
from tests.fakes import RecordingSourceIndexer


class _FailingCsvReadyStorage(LocalSourceStorage):
    async def save_source(self, source: Source) -> None:
        if source.status is SourceStatus.READY:
            raise OSError("ready metadata unavailable")
        await super().save_source(source)


def _connector(
    *,
    max_size_bytes: int = 4096,
    max_rows: int = 10,
    max_columns: int = 6,
    max_field_characters: int = 50,
    preview_rows: int = 2,
) -> CsvConnector:
    return CsvConnector(
        max_size_bytes=max_size_bytes,
        max_rows=max_rows,
        max_columns=max_columns,
        max_field_characters=max_field_characters,
        preview_rows=preview_rows,
    )


def _service(
    tmp_path: Path,
) -> tuple[CsvIngestionService, InMemorySourceRepository, LocalSourceStorage]:
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path / "data")
    return (
        CsvIngestionService(repository, storage, _connector(), RecordingSourceIndexer()),
        repository,
        storage,
    )


def test_preview_infers_types_but_preserves_original_text() -> None:
    data = (
        "\ufeffid,active,amount,date,created,name\r\n"
        "001,true,10.50,2026-09-01,2026-09-01T10:30:00Z,Atlas\r\n"
        "002,false,11.75,2026-09-02,2026-09-02T11:45:00Z,Beacon\r\n"
        "003,true,12.00,2026-09-03,2026-09-03T12:00:00Z,Cedar\r\n"
    ).encode()

    preview = _connector().preview("products.csv", "text/csv", data)

    assert preview.row_count == 3
    assert [column.inferred_type for column in preview.columns] == [
        "integer",
        "boolean",
        "decimal",
        "date",
        "datetime",
        "text",
    ]
    assert preview.sample_rows[0]["id"] == "001"
    assert len(preview.sample_rows) == 2


@pytest.mark.parametrize(
    ("filename", "content_type", "data", "message"),
    [
        ("data.txt", "text/csv", b"id\n1\n", "extension"),
        ("data.csv", "text/plain", b"id\n1\n", "MIME"),
        ("data.csv", "text/csv", b"", "empty"),
        ("data.csv", "text/csv", b"\xff", "UTF-8"),
        ("data.csv", "text/csv", b"id,id\n1,2\n", "unique"),
        ("data.csv", "text/csv", b"id, \n1,2\n", "blank"),
        ("data.csv", "text/csv", b"id,name\n1\n", "expected 2"),
        ("data.csv", "text/csv", b"id,name\n", "data row"),
        ("data.csv", "text/csv", b"id,name\n,\n", "usable"),
        ("data.csv", "text/csv", b'id,name\n1,"unterminated\n', "malformed"),
    ],
)
def test_connector_rejects_invalid_csv(
    filename: str, content_type: str, data: bytes, message: str
) -> None:
    with pytest.raises(CsvValidationError, match=message):
        _connector().parse(filename, content_type, data)


def test_connector_enforces_size_row_column_and_field_limits() -> None:
    with pytest.raises(CsvValidationError, match="byte limit"):
        _connector(max_size_bytes=4).parse("data.csv", "text/csv", b"id\n123\n")
    with pytest.raises(CsvValidationError, match="row limit"):
        _connector(max_rows=1).parse("data.csv", "text/csv", b"id\n1\n2\n")
    with pytest.raises(CsvValidationError, match="column limit"):
        _connector(max_columns=1).parse("data.csv", "text/csv", b"id,name\n1,A\n")
    with pytest.raises(CsvValidationError, match="character limit"):
        _connector(max_field_characters=3).parse("data.csv", "text/csv", b"id\nlong\n")


@pytest.mark.asyncio
async def test_ingestion_persists_original_rows_metadata_and_selected_ids(tmp_path: Path) -> None:
    service, repository, storage = _service(tmp_path)
    data = b"sku,name,owner,note\n001,Atlas,Finance,\n002,Beacon,Sales,Internal\n"

    result = await service.ingest(
        "workspace-a",
        "products.csv",
        "text/csv",
        data,
        text_columns=["name", "note", "sku"],
        metadata_columns=["owner", "sku"],
        row_id_column="sku",
    )
    source = await repository.get("workspace-a", result.source.source_id)
    documents = await storage.load_documents("workspace-a", source.source_id)
    source_directory = (
        tmp_path / "data" / "workspaces" / "workspace-a" / "sources" / str(source.source_id)
    )

    assert source.status is SourceStatus.READY
    assert source.config.source_type is SourceType.CSV
    assert [document.row_id for document in documents] == ["001", "002"]
    assert documents[0].content == "name: Atlas\nsku: 001"
    assert documents[0].metadata == {
        "owner": "Finance",
        "sku": "001",
    }
    assert documents[0].source_uri is not None
    assert (source_directory / "original.csv").read_bytes() == data


@pytest.mark.asyncio
async def test_fallback_row_numbers_and_empty_text_rows(tmp_path: Path) -> None:
    service, _, storage = _service(tmp_path)
    result = await service.ingest(
        "workspace-a",
        "products.csv",
        "text/csv",
        b"name,owner\n,Finance\nAtlas,\nBeacon,Sales\n",
        text_columns=["name"],
        metadata_columns=["owner"],
        row_id_column=None,
    )
    documents = await storage.load_documents("workspace-a", result.source.source_id)

    assert result.row_count == 3
    assert result.document_count == 2
    assert result.skipped_count == 1
    assert [document.row_id for document in documents] == ["2", "3"]
    assert documents[0].metadata["owner"] is None


@pytest.mark.asyncio
async def test_user_physical_row_number_metadata_is_preserved(tmp_path: Path) -> None:
    service, _, storage = _service(tmp_path)
    result = await service.ingest(
        "workspace-a",
        "rows.csv",
        "text/csv",
        b"id,name,physical_row_number\nrow-a,Atlas,external-99\n",
        text_columns=["name"],
        metadata_columns=["physical_row_number"],
        row_id_column="id",
    )
    documents = await storage.load_documents("workspace-a", result.source.source_id)

    assert documents[0].row_id == "row-a"
    assert documents[0].metadata == {
        "physical_row_number": "external-99",
        "id": "row-a",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text_columns", "metadata_columns", "row_id_column", "message"),
    [
        ([], [], None, "at least one"),
        (["missing"], [], None, "Unknown"),
        (["name", "name"], [], None, "duplicates"),
        (["name"], ["owner", "owner"], None, "duplicates"),
        (["name"], [], "missing", "Unknown"),
    ],
)
async def test_selection_validation_creates_no_source(
    tmp_path: Path,
    text_columns: list[str],
    metadata_columns: list[str],
    row_id_column: str | None,
    message: str,
) -> None:
    service, repository, _ = _service(tmp_path)

    with pytest.raises(CsvValidationError, match=message):
        await service.ingest(
            "workspace-a",
            "data.csv",
            "text/csv",
            b"id,name,owner\n1,Atlas,Finance\n",
            text_columns=text_columns,
            metadata_columns=metadata_columns,
            row_id_column=row_id_column,
        )

    assert await repository.list("workspace-a") == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"id,name\n,Atlas\n", "empty"),
        (b"id,name\n1,Atlas\n1,Beacon\n", "duplicate"),
    ],
)
async def test_selected_row_id_must_be_complete_and_unique(
    tmp_path: Path, data: bytes, message: str
) -> None:
    service, repository, _ = _service(tmp_path)

    with pytest.raises(CsvValidationError, match=message):
        await service.ingest(
            "workspace-a",
            "data.csv",
            "text/csv",
            data,
            text_columns=["name"],
            metadata_columns=[],
            row_id_column="id",
        )

    assert await repository.list("workspace-a") == ()


@pytest.mark.asyncio
async def test_all_empty_selected_rows_leave_source_failed(tmp_path: Path) -> None:
    service, repository, _ = _service(tmp_path)

    with pytest.raises(CsvValidationError, match="No row"):
        await service.ingest(
            "workspace-a",
            "data.csv",
            "text/csv",
            b"name,owner\n,Finance\n,Sales\n",
            text_columns=["name"],
            metadata_columns=["owner"],
            row_id_column=None,
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED


@pytest.mark.asyncio
async def test_ready_metadata_failure_leaves_csv_source_failed(tmp_path: Path) -> None:
    repository = InMemorySourceRepository()
    storage = _FailingCsvReadyStorage(tmp_path / "data")
    service = CsvIngestionService(repository, storage, _connector(), RecordingSourceIndexer())

    with pytest.raises(IngestionError, match="CSV ingestion failed"):
        await service.ingest(
            "workspace-a",
            "data.csv",
            "text/csv",
            b"id,name\n1,Atlas\n",
            text_columns=["name"],
            metadata_columns=[],
            row_id_column="id",
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED


@pytest.mark.asyncio
async def test_csv_preview_ingestion_and_document_inspection_endpoints(tmp_path: Path) -> None:
    service, repository, storage = _service(tmp_path)
    app.dependency_overrides[get_csv_ingestion_service] = lambda: service
    app.dependency_overrides[get_source_repository] = lambda: repository
    app.dependency_overrides[get_source_storage] = lambda: storage
    transport = ASGITransport(app=app)
    data = b"id,name,owner\n001,Atlas,Finance\n002,Beacon,Sales\n"

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            preview_response = await client.post(
                "/sources/csv/preview",
                files={"file": ("products.csv", data, "text/csv")},
            )
            response = await client.post(
                "/sources/csv",
                data={
                    "workspace_id": "workspace-a",
                    "text_columns": ["name", "id"],
                    "metadata_columns": ["owner"],
                    "row_id_column": "id",
                },
                files={"file": ("products.csv", data, "text/csv")},
            )
            source_id = response.json()["source"]["source_id"]
            documents_response = await client.get(
                f"/sources/{source_id}/documents",
                params={"workspace_id": "workspace-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert preview_response.status_code == 200
    assert preview_response.json()["row_count"] == 2
    assert response.status_code == 201
    assert response.json()["document_count"] == 2
    assert documents_response.status_code == 200
    assert documents_response.json()[0]["row_id"] == "001"
