"""Private Vercel Blob storage with deterministic workspace-scoped object names."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from vercel.blob import AsyncBlobClient

from app.models import CrawlManifest, NormalizedDocument, Source
from app.retrieval.blob_lexical import BlobLexicalStore
from app.storage.azure_blob import AzureBlobSourceStorage

_DOCUMENTS = TypeAdapter(list[NormalizedDocument])


class VercelBlobSourceStorage:
    """Use private objects; credentials and raw Blob URLs never authorize a client."""

    def __init__(self, token: str, *, client: Any = None) -> None:
        self.client = client or AsyncBlobClient(token=token)

    async def close(self) -> None:
        await self.client.aclose()

    async def write(self, name: str, payload: bytes, *, overwrite: bool = False) -> str:
        try:
            result = await self.client.put(
                name,
                payload,
                access="private",
                overwrite=overwrite,
                add_random_suffix=False,
                content_type="application/octet-stream",
            )
            return str(result.url)
        except Exception:
            # Idempotent retry: identical immutable objects are already successful.
            if not overwrite:
                try:
                    if await self.read(name) == payload:
                        return f"vercelblob://{name}"
                except Exception:
                    pass
            raise

    async def read(self, name: str) -> bytes:
        result = await self.client.get(name, access="private", use_cache=False, timeout=45)
        if result.status_code != 200:
            raise ValueError("Private object could not be read")
        return bytes(result.content)

    async def names(self, prefix: str) -> list[str]:
        names: list[str] = []
        cursor = None
        while True:
            result = await self.client.list_objects(prefix=prefix, cursor=cursor, limit=1000)
            names.extend(item.pathname for item in result.blobs)
            if not result.has_more:
                return names
            cursor = result.cursor

    @staticmethod
    def name(workspace_id: str, source_id: UUID, filename: str) -> str:
        return f"{AzureBlobSourceStorage._workspace_prefix(workspace_id)}{source_id}/{filename}"

    async def save_original(
        self, workspace_id: str, source_id: UUID, data: bytes, *, filename: str = "original.pdf"
    ) -> str:
        if filename not in {"original.pdf", "original.csv"}:
            raise ValueError("Original filename is not allowed")
        name = self.name(workspace_id, source_id, filename)
        await self.write(name, data)
        return f"vercelblob://{name}"

    async def save_documents(
        self, workspace_id: str, source_id: UUID, documents: Sequence[NormalizedDocument]
    ) -> None:
        await self.write(
            self.name(workspace_id, source_id, "documents.json"),
            _DOCUMENTS.dump_json(list(documents)),
            overwrite=True,
        )

    async def load_documents(
        self, workspace_id: str, source_id: UUID
    ) -> tuple[NormalizedDocument, ...]:
        return tuple(
            _DOCUMENTS.validate_json(
                await self.read(self.name(workspace_id, source_id, "documents.json"))
            )
        )

    async def save_source(self, source: Source) -> None:
        await self.write(
            self.name(source.workspace_id, source.source_id, "source.json"),
            source.model_dump_json().encode(),
            overwrite=True,
        )

    async def list_sources(self, workspace_id: str) -> tuple[Source, ...]:
        names = await self.names(AzureBlobSourceStorage._workspace_prefix(workspace_id))
        return tuple(
            [
                Source.model_validate_json(await self.read(name))
                for name in names
                if name.endswith("/source.json")
            ]
        )

    async def save_crawl_manifest(
        self, workspace_id: str, source_id: UUID, manifest: CrawlManifest
    ) -> None:
        await self.write(
            self.name(workspace_id, source_id, "crawl-manifest.json"),
            manifest.model_dump_json().encode(),
            overwrite=True,
        )


class VercelBlobLexicalStore(BlobLexicalStore):
    """Reuse BM25 contracts and checksums with a Vercel object transport."""

    async def _upload(self, name: str, payload: bytes) -> None:
        await self._container.write(name, payload)

    async def _download(self, name: str) -> bytes:
        payload: bytes = await self._container.read(name)
        return payload
