"""Bounded DOCX paragraph extraction, including paragraphs inside tables."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app.core.exceptions import DocxValidationError
from app.ingestion.pdf import normalize_text

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_STRICT_WORD = "{http://purl.oclc.org/ooxml/wordprocessingml/main}"


@dataclass(frozen=True, slots=True)
class ParsedDocxParagraph:
    paragraph_number: int
    content: str


@dataclass(frozen=True, slots=True)
class ParsedDocx:
    paragraph_count: int
    title: str | None
    paragraphs: tuple[ParsedDocxParagraph, ...]


class DocxConnector:
    """Read document-body text without extracting files or following relationships."""

    def __init__(self, max_size_bytes: int) -> None:
        self._max_size_bytes = max_size_bytes

    def parse(self, filename: str, content_type: str | None, data: bytes) -> ParsedDocx:
        if Path(filename).suffix.lower() != ".docx":
            raise DocxValidationError("Only files with a .docx extension are accepted")
        if content_type not in {DOCX_MIME, "application/octet-stream", None}:
            raise DocxValidationError("The upload MIME type must identify a DOCX document")
        if not data or len(data) > self._max_size_bytes:
            raise DocxValidationError(
                f"The uploaded DOCX must contain 1 to {self._max_size_bytes} bytes"
            )
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or len({e.filename for e in entries}) != len(entries):
                    raise DocxValidationError(
                        "The DOCX archive contains too many or duplicate parts"
                    )
                if sum(e.file_size for e in entries) > 100 * 1024 * 1024:
                    raise DocxValidationError("The expanded DOCX exceeds the 100 MB limit")
                if "[Content_Types].xml" not in archive.namelist():
                    raise DocxValidationError("The file is not a DOCX package")
                root = self._read_xml(archive, "word/document.xml")
                namespace = _STRICT_WORD if root.tag == _STRICT_WORD + "document" else _WORD
                body = root.find(namespace + "body")
                if root.tag != namespace + "document" or body is None:
                    raise DocxValidationError("The DOCX document body is missing")
                paragraphs = []
                count = 0
                for count, paragraph in enumerate(body.iter(namespace + "p"), 1):
                    content = normalize_text(
                        "".join(
                            node.text or "" if node.tag == namespace + "t" else " "
                            for node in paragraph.iter()
                            if node.tag
                            in {
                                namespace + "t",
                                namespace + "tab",
                                namespace + "br",
                                namespace + "cr",
                            }
                        )
                    )
                    if content:
                        paragraphs.append(ParsedDocxParagraph(count, content))
                if not paragraphs:
                    raise DocxValidationError("The DOCX has no extractable text")
                title = None
                if "docProps/core.xml" in archive.namelist():
                    core = self._read_xml(archive, "docProps/core.xml")
                    title = (
                        normalize_text(core.findtext("{http://purl.org/dc/elements/1.1/}title", ""))
                        or None
                    )
                return ParsedDocx(count, title, tuple(paragraphs))
        except (
            BadZipFile,
            KeyError,
            RuntimeError,
            NotImplementedError,
            ElementTree.ParseError,
            OSError,
            ValueError,
        ) as error:
            raise DocxValidationError("The uploaded file is not a readable DOCX") from error

    @staticmethod
    def _read_xml(archive: ZipFile, name: str) -> ElementTree.Element:
        if archive.getinfo(name).file_size > 20 * 1024 * 1024:
            raise DocxValidationError("A DOCX XML part exceeds the 20 MB limit")
        payload = archive.read(name)
        # Removing NUL bytes also detects declarations in UTF-16/32 XML.
        declarations = payload.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
            raise DocxValidationError("DOCX XML entity declarations are not supported")
        return ElementTree.fromstring(payload)
