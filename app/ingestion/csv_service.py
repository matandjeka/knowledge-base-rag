"""CSV ingestion orchestration across parsing, storage, and source metadata."""

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime

from app.core.exceptions import CsvValidationError, IngestionError
from app.ingestion.csv import CsvConnector, ParsedCsv
from app.models import (
    CsvIngestionResult,
    CsvPreviewResult,
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


class CsvIngestionService:
    """Coordinate strict CSV validation and lifecycle-safe persistence."""

    def __init__(
        self,
        repository: SourceRepository,
        storage: SourceStorage,
        connector: CsvConnector,
        indexer: SourceIndexer,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._connector = connector
        self._indexer = indexer

    async def preview(
        self, filename: str, content_type: str | None, data: bytes
    ) -> CsvPreviewResult:
        """Validate a CSV and return its bounded schema preview without persistence."""
        return await asyncio.to_thread(self._connector.preview, filename, content_type, data)

    async def ingest(
        self,
        workspace_id: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        *,
        text_columns: list[str],
        metadata_columns: list[str],
        row_id_column: str | None,
    ) -> CsvIngestionResult:
        """Validate, normalize, and persist one CSV upload."""
        parsed = await asyncio.to_thread(self._connector.parse, filename, content_type, data)
        selection = self.validate_selection(parsed, text_columns, metadata_columns, row_id_column)
        source = Source(
            workspace_id=workspace_id,
            name=parsed.filename,
            config=SourceConfig(
                source_type=SourceType.CSV,
                options={
                    "original_filename": parsed.filename,
                    "text_columns": list(selection.text_columns),
                    "metadata_columns": list(selection.metadata_columns),
                    "row_id_column": selection.row_id_column,
                },
            ),
        )
        await self._repository.create(source)

        try:
            locator = await self._storage.save_original(
                workspace_id,
                source.source_id,
                data,
                filename="original.csv",
            )
            source = await self._repository.update(
                workspace_id,
                source.source_id,
                SourceUpdate(config=source.config.model_copy(update={"uri": locator}, deep=True)),
            )
            source = await self._repository.transition(
                workspace_id, source.source_id, SourceStatus.INDEXING
            )
            await self._storage.save_source(source)
            documents, skipped_count = self.build_documents(source, parsed, selection, locator)
            if not documents:
                raise CsvValidationError("No row contains a value in the selected text columns")
            await self._storage.save_documents(workspace_id, source.source_id, documents)
            source = await self._repository.update(
                workspace_id,
                source.source_id,
                SourceUpdate(
                    config=source.config.model_copy(
                        update={
                            "options": {
                                **source.config.options,
                                "row_count": len(parsed.rows),
                                "document_count": len(documents),
                                "skipped_count": skipped_count,
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
                failed = await self._repository.transition(
                    source.workspace_id, source.source_id, SourceStatus.FAILED
                )
                await self._storage.save_source(failed)
            except Exception as failure_error:
                logger.exception(
                    "CSV ingestion failed and failure state could not be persisted",
                    extra={"workspace_id": source.workspace_id},
                )
                raise IngestionError(
                    f"CSV ingestion and failure-state persistence failed for {parsed.filename}"
                ) from failure_error
            if isinstance(error, IngestionError):
                raise
            raise IngestionError(f"CSV ingestion failed for {parsed.filename}") from error

        return CsvIngestionResult(
            source=source,
            row_count=len(parsed.rows),
            document_count=len(documents),
            skipped_count=skipped_count,
        )

    def validate_selection(
        self,
        parsed: ParsedCsv,
        text_columns: list[str],
        metadata_columns: list[str],
        row_id_column: str | None,
    ) -> "_CsvSelection":
        if not text_columns:
            raise CsvValidationError("Select at least one text column")
        if len(set(text_columns)) != len(text_columns):
            raise CsvValidationError("Text columns cannot contain duplicates")
        if len(set(metadata_columns)) != len(metadata_columns):
            raise CsvValidationError("Metadata columns cannot contain duplicates")
        available = set(parsed.columns)
        unknown = (set(text_columns) | set(metadata_columns)) - available
        if row_id_column is not None and row_id_column not in available:
            unknown.add(row_id_column)
        if unknown:
            raise CsvValidationError(f"Unknown CSV columns: {', '.join(sorted(unknown))}")
        if row_id_column is not None:
            index = parsed.columns.index(row_id_column)
            identifiers = [row[index] for row in parsed.rows]
            if any(identifier is None for identifier in identifiers):
                raise CsvValidationError("The selected row-ID column contains empty values")
            duplicates = sorted(
                identifier
                for identifier, count in Counter(identifiers).items()
                if count > 1 and identifier is not None
            )
            if duplicates:
                preview = ", ".join(duplicates[:5])
                raise CsvValidationError(
                    f"The selected row-ID column contains duplicate values: {preview}"
                )
        return _CsvSelection(tuple(text_columns), tuple(metadata_columns), row_id_column)

    def build_documents(
        self,
        source: Source,
        parsed: ParsedCsv,
        selection: "_CsvSelection",
        locator: str,
    ) -> tuple[list[NormalizedDocument], int]:
        indexes = {column: index for index, column in enumerate(parsed.columns)}
        documents: list[NormalizedDocument] = []
        skipped_count = 0
        for physical_row_id, row in enumerate(parsed.rows, start=1):
            content_lines = [
                f"{column}: {value}"
                for column in selection.text_columns
                if (value := row[indexes[column]]) is not None
            ]
            if not content_lines:
                skipped_count += 1
                continue
            row_id_value = (
                row[indexes[selection.row_id_column]]
                if selection.row_id_column is not None
                else str(physical_row_id)
            )
            if row_id_value is None:
                raise AssertionError("Validated row IDs cannot be null")
            metadata = {column: row[indexes[column]] for column in selection.metadata_columns}
            if selection.row_id_column is not None:
                metadata[selection.row_id_column] = row_id_value
            documents.append(
                NormalizedDocument(
                    workspace_id=source.workspace_id,
                    source_id=source.source_id,
                    source_type=SourceType.CSV,
                    title=source.name,
                    content="\n".join(content_lines),
                    source_uri=locator,
                    row_id=row_id_value,
                    metadata=metadata,
                )
            )
        return documents, skipped_count


class _CsvSelection:
    """Validated column roles used while normalizing CSV rows."""

    def __init__(
        self,
        text_columns: tuple[str, ...],
        metadata_columns: tuple[str, ...],
        row_id_column: str | None,
    ) -> None:
        self.text_columns = text_columns
        self.metadata_columns = metadata_columns
        self.row_id_column = row_id_column
