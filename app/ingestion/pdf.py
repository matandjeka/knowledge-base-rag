"""PDF validation, page extraction, normalization, and chunking."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from app.core.exceptions import PdfValidationError

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ParsedPdfPage:
    """Normalized text extracted from one physical PDF page."""

    page_number: int
    content: str


@dataclass(frozen=True, slots=True)
class ParsedPdf:
    """Validated PDF content and metadata."""

    page_count: int
    title: str | None
    pages: tuple[ParsedPdfPage, ...]


class PdfConnector:
    """Validate and extract text from an uploaded PDF byte stream."""

    def __init__(self, max_size_bytes: int) -> None:
        self._max_size_bytes = max_size_bytes

    def parse(self, filename: str, content_type: str | None, data: bytes) -> ParsedPdf:
        """Validate an upload and extract normalized page-level text."""
        self._validate_upload(filename, content_type, data)
        try:
            document: Any = pymupdf.open(  # type: ignore[no-untyped-call]
                stream=data, filetype="pdf"
            )
        except (pymupdf.FileDataError, RuntimeError) as error:
            raise PdfValidationError("The uploaded file is not a readable PDF") from error

        try:
            if document.needs_pass:
                raise PdfValidationError("Password-protected PDFs are not supported")
            if document.page_count < 1:
                raise PdfValidationError("The PDF does not contain any pages")
            pages = tuple(
                ParsedPdfPage(page_number=index + 1, content=text)
                for index, page in enumerate(document)
                if (text := normalize_text(page.get_text()))
            )
            if not pages:
                raise PdfValidationError(
                    "The PDF has no extractable text; scanned PDFs require OCR"
                )
            metadata = document.metadata or {}
            title = normalize_text(metadata.get("title", "")) or None
            return ParsedPdf(page_count=document.page_count, title=title, pages=pages)
        finally:
            document.close()

    def _validate_upload(self, filename: str, content_type: str | None, data: bytes) -> None:
        if Path(filename).suffix.lower() != ".pdf":
            raise PdfValidationError("Only files with a .pdf extension are accepted")
        if content_type != "application/pdf":
            raise PdfValidationError("The upload MIME type must be application/pdf")
        if not data:
            raise PdfValidationError("The uploaded PDF is empty")
        if len(data) > self._max_size_bytes:
            raise PdfValidationError(
                f"The uploaded PDF exceeds the {self._max_size_bytes}-byte limit"
            )
        if not data.startswith(b"%PDF-"):
            raise PdfValidationError("The uploaded file does not have a valid PDF signature")


def normalize_text(value: str) -> str:
    """Collapse PDF extraction whitespace into stable plain text."""
    return _WHITESPACE.sub(" ", value).strip()


def chunk_text(content: str, chunk_size: int, overlap: int) -> tuple[str, ...]:
    """Split text at whitespace boundaries with bounded character overlap."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            boundary = content.rfind(" ", start + 1, end + 1)
            if boundary > start:
                end = boundary
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(content):
            break
        start = max(end - overlap, start + 1)
        while start < end and content[start].isspace():
            start += 1
    return tuple(chunks)
