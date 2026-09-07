"""Azure Blob implementation of immutable source-artifact storage."""

import hashlib
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from app.models import CrawlManifest, NormalizedDocument, Source

_DOCUMENTS = TypeAdapter(list[NormalizedDocument])


class AzureBlobSourceStorage:
    """Store workspace-scoped artifacts using an injected async Blob container client."""

    def __init__(self, container_client: Any) -> None:
        self._container = container_client

    @property
    def container_client(self) -> Any:
        """Expose the shared async container client to sibling Blob adapters."""
        return self._container

    @classmethod
    def from_connection(
        cls,
        *,
        account_url: str | None,
        container: str,
        connection_string: str | None = None,
    ) -> "AzureBlobSourceStorage":
        """Create a container client using a dev connection string or managed identity."""
        from azure.identity.aio import DefaultAzureCredential
        from azure.storage.blob.aio import BlobServiceClient

        if connection_string is not None:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            if account_url is None:
                raise ValueError("Azure Blob account URL is required")
            service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        return cls(service.get_container_client(container))

    async def close(self) -> None:
        await self._container.close()

    async def save_original(
        self,
        workspace_id: str,
        source_id: UUID,
        data: bytes,
        *,
        filename: str = "original.pdf",
    ) -> str:
        if filename not in {"original.pdf", "original.csv"}:
            raise ValueError("Original artifact filename is not allowed")
        name = self._name(workspace_id, source_id, filename)
        await self._upload_immutable(
            name,
            data,
            content_type={"original.pdf": "application/pdf", "original.csv": "text/csv"}[filename],
        )
        return f"azureblob://{self._container.container_name}/{name}"

    async def save_documents(
        self, workspace_id: str, source_id: UUID, documents: Sequence[NormalizedDocument]
    ) -> None:
        payload = b"\n".join(document.model_dump_json().encode() for document in documents) + b"\n"
        await self._upload_replaceable(
            self._name(workspace_id, source_id, "documents.jsonl"),
            payload,
            content_type="application/x-ndjson",
        )

    async def load_documents(
        self, workspace_id: str, source_id: UUID
    ) -> tuple[NormalizedDocument, ...]:
        payload = await self._download(self._name(workspace_id, source_id, "documents.jsonl"))
        lines = [line for line in payload.splitlines() if line]
        return tuple(_DOCUMENTS.validate_json(b"[" + b",".join(lines) + b"]"))

    async def save_crawl_manifest(
        self, workspace_id: str, source_id: UUID, manifest: CrawlManifest
    ) -> None:
        await self._upload_replaceable(
            self._name(workspace_id, source_id, "crawl-manifest.json"),
            manifest.model_dump_json(indent=2).encode(),
            content_type="application/json",
        )

    async def save_source(self, source: Source) -> None:
        """Compatibility mirror; PostgreSQL remains authoritative in production."""
        await self._upload_replaceable(
            self._name(source.workspace_id, source.source_id, "source.json"),
            source.model_dump_json(indent=2).encode(),
            content_type="application/json",
        )

    async def list_sources(self, workspace_id: str) -> tuple[Source, ...]:
        """Compatibility inventory for migration verification, not production authority."""
        prefix = self._workspace_prefix(workspace_id)
        sources: list[Source] = []
        async for item in self._container.list_blobs(name_starts_with=prefix):
            if str(item.name).endswith("/source.json"):
                sources.append(Source.model_validate_json(await self._download(str(item.name))))
        return tuple(sorted(sources, key=lambda source: (source.created_at, str(source.source_id))))

    async def _upload_immutable(self, name: str, data: bytes, *, content_type: str) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        try:
            await self._container.get_blob_client(name).upload_blob(
                data,
                overwrite=False,
                metadata={"sha256": hashlib.sha256(data).hexdigest()},
                content_settings=ContentSettings(content_type=content_type),
            )
        except ResourceExistsError:
            existing = await self._download(name)
            if existing != data:
                raise ValueError(
                    "Immutable source artifact already exists with different content"
                ) from None

    async def _upload_replaceable(self, name: str, data: bytes, *, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        await self._container.get_blob_client(name).upload_blob(
            data,
            overwrite=True,
            metadata={"sha256": hashlib.sha256(data).hexdigest()},
            content_settings=ContentSettings(content_type=content_type),
        )

    async def _download(self, name: str) -> bytes:
        stream = await self._container.get_blob_client(name).download_blob()
        payload: bytes = await stream.readall()
        return payload

    def _name(self, workspace_id: str, source_id: UUID, filename: str) -> str:
        return f"{self._workspace_prefix(workspace_id)}{source_id}/{filename}"

    @staticmethod
    def _workspace_prefix(workspace_id: str) -> str:
        if not workspace_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in workspace_id
        ):
            raise ValueError("Invalid workspace identifier")
        return f"workspaces/{workspace_id}/sources/"
