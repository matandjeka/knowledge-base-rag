"""DOCX ingestion orchestration across parsing, storage, and source metadata."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.core.exceptions import IngestionError
from app.ingestion.docx import DocxConnector, ParsedDocxParagraph
from app.ingestion.injection_scan import scan_documents
from app.ingestion.pdf import chunk_text
from app.models import (
    DocxIngestionResult,
    NormalizedDocument,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
    SourceUpdate,
)
from app.repositories import SourceRepository
from app.retrieval.indexing import SourceIndexer
from app.storage import SourceStorage

logger = logging.getLogger(__name__)


class DocxIngestionService:
    """Coordinate validated DOCX ingestion and lifecycle state changes."""

    def __init__(
        self,
        repository: SourceRepository,
        storage: SourceStorage,
        connector: DocxConnector,
        chunk_size: int,
        chunk_overlap: int,
        indexer: SourceIndexer,
    ) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self._repository = repository
        self._storage = storage
        self._connector = connector
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._indexer = indexer

    async def ingest(
        self,
        workspace_id: str,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> DocxIngestionResult:
        """Validate, parse, chunk, and persist one DOCX upload."""
        parsed = await asyncio.to_thread(self._connector.parse, filename, content_type, data)
        safe_name = Path(filename).name
        source = Source(
            workspace_id=workspace_id,
            name=parsed.title or safe_name,
            config=SourceConfig(
                source_type=SourceType.DOCX,
                options={"original_filename": safe_name},
            ),
        )
        await self._repository.create(source)

        try:
            locator = await self._storage.save_original(
                workspace_id, source.source_id, data, filename="original.docx"
            )
            source = await self._repository.update(
                workspace_id,
                source.source_id,
                SourceUpdate(
                    config=SourceConfig(
                        source_type=SourceType.DOCX,
                        uri=locator,
                        options={
                            "original_filename": safe_name,
                            "paragraph_count": parsed.paragraph_count,
                        },
                    )
                ),
            )
            source = await self._repository.transition(
                workspace_id, source.source_id, SourceStatus.INDEXING
            )
            await self._storage.save_source(source)

            documents = self.build_documents(source, parsed.paragraphs, locator)
            await self._storage.save_documents(workspace_id, source.source_id, documents)
            flags = scan_documents(documents)
            if flags:
                logger.warning(
                    "Ingested content flagged for possible prompt injection",
                    extra={"workspace_id": workspace_id, "action": "ingestion.flagged"},
                )
            source = await self._repository.update(
                workspace_id,
                source.source_id,
                SourceUpdate(
                    config=source.config.model_copy(
                        update={
                            "options": {
                                **source.config.options,
                                "chunk_count": len(documents),
                                **({"injection_flags": flags} if flags else {}),
                            }
                        },
                        deep=True,
                    )
                ),
            )
            generation = await self._indexer.prepare(workspace_id, source.source_id)
            ready_at = datetime.now(UTC)
            ready_source = source.model_copy(
                update={"status": SourceStatus.READY, "updated_at": ready_at}, deep=True
            )
            await self._storage.save_source(ready_source)
            source = await self._repository.transition(
                workspace_id,
                source.source_id,
                SourceStatus.READY,
                transitioned_at=ready_at,
            )
            await self._indexer.activate(workspace_id, generation.generation_id)
        except Exception as error:
            try:
                await self._record_failure(source)
            except Exception as failure_error:
                logger.exception(
                    "DOCX ingestion failed and failure state could not be persisted",
                    extra={"workspace_id": source.workspace_id},
                )
                raise IngestionError(
                    f"DOCX ingestion and failure-state persistence failed for {safe_name}"
                ) from failure_error
            if isinstance(error, IngestionError):
                raise
            raise IngestionError(f"DOCX ingestion failed for {safe_name}") from error

        return DocxIngestionResult(
            source=source,
            paragraph_count=parsed.paragraph_count,
            chunk_count=len(documents),
        )

    def build_documents(
        self, source: Source, paragraphs: tuple[ParsedDocxParagraph, ...], locator: str
    ) -> list[NormalizedDocument]:
        documents: list[NormalizedDocument] = []
        chunk_index = 0
        for paragraph in paragraphs:
            for paragraph_chunk_index, content in enumerate(
                chunk_text(paragraph.content, self._chunk_size, self._chunk_overlap)
            ):
                documents.append(
                    NormalizedDocument(
                        workspace_id=source.workspace_id,
                        source_id=source.source_id,
                        source_type=SourceType.DOCX,
                        title=source.name,
                        content=content,
                        source_uri=locator,
                        row_id=str(paragraph.paragraph_number),
                        metadata={
                            "chunk_index": chunk_index,
                            "paragraph_chunk_index": paragraph_chunk_index,
                        },
                    )
                )
                chunk_index += 1
        return documents

    async def _record_failure(self, source: Source) -> None:
        failed = await self._repository.transition(
            source.workspace_id, source.source_id, SourceStatus.FAILED
        )
        await self._storage.save_source(failed)
