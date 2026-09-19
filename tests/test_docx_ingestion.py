"""DOCX extraction, validation, persistence, and citation coverage."""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_docx_ingestion_service
from app.citations.builder import build_citations
from app.core.exceptions import DocxValidationError
from app.ingestion.docx import DOCX_MIME, DocxConnector
from app.ingestion.docx_service import DocxIngestionService
from app.main import app
from app.models import Evidence, SourceStatus, SourceType
from app.repositories import InMemorySourceRepository
from app.storage import LocalSourceStorage
from tests.fakes import RecordingSourceIndexer


def docx_bytes(body: str, *, raw: bool = False) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            body
            if raw
            else (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}</w:body></w:document>"
            ),
        )
    return output.getvalue()


def test_docx_extracts_runs_tables_and_stable_paragraph_positions() -> None:
    data = docx_bytes(
        "<w:p><w:r><w:t>Hello</w:t><w:tab/><w:t>world</w:t></w:r></w:p>"
        "<w:p/><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table text</w:t>"
        "</w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    parsed = DocxConnector(10000).parse("REPORT.DOCX", DOCX_MIME, data)
    assert parsed.paragraph_count == 3
    assert [(p.paragraph_number, p.content) for p in parsed.paragraphs] == [
        (1, "Hello world"),
        (3, "Table text"),
    ]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a zip",
        docx_bytes("<w:p/>"),
        docx_bytes("<invalid", raw=True),
        docx_bytes('<!DOCTYPE x [<!ENTITY a "hello">]><x>&a;</x>', raw=True),
        docx_bytes("<w:p><w:r><w:t>" + "x" * (21 * 1024 * 1024) + "</w:t></w:r></w:p>"),
    ],
)
def test_docx_rejects_invalid_empty_and_oversized_xml(data: bytes) -> None:
    with pytest.raises(DocxValidationError):
        DocxConnector(1024 * 1024).parse("document.docx", DOCX_MIME, data)


def test_docx_validates_extension_mime_and_upload_limit() -> None:
    data = docx_bytes("<w:p><w:r><w:t>Text</w:t></w:r></w:p>")
    for filename, mime, maximum in [
        ("x.doc", DOCX_MIME, 10000),
        ("x.docx", "text/plain", 10000),
        ("x.docx", DOCX_MIME, 10),
    ]:
        with pytest.raises(DocxValidationError):
            DocxConnector(maximum).parse(filename, mime, data)


@pytest.mark.asyncio
async def test_docx_api_persists_original_and_builds_citations(tmp_path: Path) -> None:
    storage = LocalSourceStorage(tmp_path)
    service = DocxIngestionService(
        InMemorySourceRepository(),
        storage,
        DocxConnector(10000),
        1200,
        200,
        RecordingSourceIndexer(),
    )
    data = docx_bytes("<w:p><w:r><w:t>The policy requires annual review.</w:t></w:r></w:p>")
    app.dependency_overrides[get_docx_ingestion_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/sources/docx",
                data={"workspace_id": "local"},
                files={"file": ("policy.docx", data, DOCX_MIME)},
            )
            assert response.status_code == 201, response.text
            result = response.json()
            assert result["paragraph_count"] == 1
            assert result["source"]["status"] == SourceStatus.READY
            assert result["source"]["config"]["source_type"] == "docx"
            from uuid import UUID

            source_id = UUID(result["source"]["source_id"])
            documents = await storage.load_documents("local", source_id)
            assert next(tmp_path.rglob("original.docx")).read_bytes() == data
            document = documents[0]
            evidence = Evidence(
                source_id=source_id,
                source_type=SourceType.DOCX,
                content=document.content,
                row_id=document.row_id,
                retriever="vector",
                raw_score=0.9,
            )
            citation = build_citations([evidence])[0]
            assert citation.locator == "paragraph 1"
            assert citation.locator_details.source_type == SourceType.DOCX
            invalid = await client.post(
                "/sources/docx",
                data={"workspace_id": "local"},
                files={"file": ("bad.docx", b"bad", DOCX_MIME)},
            )
            assert invalid.status_code == 422
    finally:
        app.dependency_overrides.pop(get_docx_ingestion_service, None)
