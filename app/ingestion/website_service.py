"""Website ingestion orchestration across crawling, storage, and source metadata."""

import logging
from datetime import UTC, datetime

from app.core.exceptions import IngestionError, WebsiteValidationError
from app.ingestion.pdf import chunk_text
from app.ingestion.website import WebsiteCrawl, WebsiteCrawler, normalize_url
from app.models import (
    NormalizedDocument,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
    SourceUpdate,
    WebsiteIngestionResult,
)
from app.repositories import SourceRepository
from app.retrieval.indexing import SourceIndexer
from app.storage import SourceStorage

logger = logging.getLogger(__name__)


class WebsiteIngestionService:
    """Coordinate a safe bounded crawl and lifecycle state changes."""

    def __init__(
        self,
        repository: SourceRepository,
        storage: SourceStorage,
        crawler: WebsiteCrawler,
        *,
        chunk_size: int,
        chunk_overlap: int,
        max_pages: int,
        indexer: SourceIndexer,
    ) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self._repository = repository
        self._storage = storage
        self._crawler = crawler
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_pages = max_pages
        self._indexer = indexer

    async def ingest(
        self,
        workspace_id: str,
        url: str,
        *,
        crawl_same_domain: bool,
        page_limit: int,
    ) -> WebsiteIngestionResult:
        """Crawl, extract, chunk, and persist one website source."""
        seed_url = normalize_url(url)
        if page_limit > self._max_pages:
            raise WebsiteValidationError(f"Page limit cannot exceed {self._max_pages}")
        source = Source(
            workspace_id=workspace_id,
            name=seed_url,
            config=SourceConfig(
                source_type=SourceType.WEBSITE,
                uri=seed_url,
                options={
                    "crawl_same_domain": crawl_same_domain,
                    "page_limit": page_limit,
                },
            ),
        )
        await self._repository.create(source)

        try:
            source = await self._repository.transition(
                workspace_id, source.source_id, SourceStatus.INDEXING
            )
            await self._storage.save_source(source)
            crawl = await self._crawler.crawl(
                seed_url, crawl_same_domain=crawl_same_domain, page_limit=page_limit
            )
            source = await self._repository.update(
                workspace_id,
                source.source_id,
                SourceUpdate(name=crawl.pages[0].title or seed_url),
            )
            documents = self._build_documents(source, crawl)
            await self._storage.save_documents(workspace_id, source.source_id, documents)
            await self._storage.save_crawl_manifest(workspace_id, source.source_id, crawl.manifest)
            source = await self._repository.update(
                workspace_id,
                source.source_id,
                SourceUpdate(
                    config=source.config.model_copy(
                        update={
                            "options": {
                                **source.config.options,
                                "page_count": len(crawl.pages),
                                "chunk_count": len(documents),
                                "skipped_count": len(crawl.manifest.failures),
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
                    "Website ingestion failed and failure state could not be persisted",
                    extra={"workspace_id": source.workspace_id},
                )
                raise IngestionError(
                    f"Website ingestion and failure-state persistence failed for {seed_url}"
                ) from failure_error
            if isinstance(error, IngestionError):
                raise
            raise IngestionError(f"Website ingestion failed for {seed_url}") from error

        return WebsiteIngestionResult(
            source=source,
            page_count=len(crawl.pages),
            chunk_count=len(documents),
            skipped_count=len(crawl.manifest.failures),
        )

    def _build_documents(self, source: Source, crawl: WebsiteCrawl) -> list[NormalizedDocument]:
        documents: list[NormalizedDocument] = []
        chunk_index = 0
        for crawl_index, page in enumerate(crawl.pages):
            for page_chunk_index, content in enumerate(
                chunk_text(page.content, self._chunk_size, self._chunk_overlap)
            ):
                documents.append(
                    NormalizedDocument(
                        workspace_id=source.workspace_id,
                        source_id=source.source_id,
                        source_type=SourceType.WEBSITE,
                        title=page.title,
                        content=content,
                        source_uri=page.url,
                        metadata={
                            "chunk_index": chunk_index,
                            "page_chunk_index": page_chunk_index,
                            "crawl_index": crawl_index,
                        },
                    )
                )
                chunk_index += 1
        return documents
