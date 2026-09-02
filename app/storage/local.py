"""Workspace-scoped local filesystem storage for ingested sources."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import TypeAdapter

from app.models import CrawlManifest, NormalizedDocument, Source

_DOCUMENTS_ADAPTER = TypeAdapter(list[NormalizedDocument])


class SourceStorage(Protocol):
    """Persistence boundary for original files and normalized documents."""

    async def save_original(
        self,
        workspace_id: str,
        source_id: UUID,
        data: bytes,
        *,
        filename: str = "original.pdf",
    ) -> str:
        """Persist an original source file and return its backend-neutral locator."""
        ...

    async def save_source(self, source: Source) -> None:
        """Persist source metadata."""
        ...

    async def save_documents(
        self, workspace_id: str, source_id: UUID, documents: Sequence[NormalizedDocument]
    ) -> None:
        """Persist normalized documents as JSON Lines."""
        ...

    async def load_documents(
        self, workspace_id: str, source_id: UUID
    ) -> tuple[NormalizedDocument, ...]:
        """Load normalized documents for inspection."""
        ...

    async def save_crawl_manifest(
        self, workspace_id: str, source_id: UUID, manifest: CrawlManifest
    ) -> None:
        """Persist website crawl diagnostics."""
        ...


class LocalSourceStorage:
    """Store ingestion artifacts beneath a controlled local data root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def save_original(
        self,
        workspace_id: str,
        source_id: UUID,
        data: bytes,
        *,
        filename: str = "original.pdf",
    ) -> str:
        """Atomically persist an original source file."""
        if Path(filename).name != filename or filename not in {"original.pdf", "original.csv"}:
            raise ValueError("Original artifact filename is not allowed")
        directory = self._source_directory(workspace_id, source_id)
        path = directory / filename
        await asyncio.to_thread(self._write_bytes, path, data)
        working_directory = Path.cwd().resolve()
        return (
            path.relative_to(working_directory).as_posix()
            if path.is_relative_to(working_directory)
            else path.as_uri()
        )

    async def save_source(self, source: Source) -> None:
        """Atomically persist source metadata as JSON."""
        path = self._source_directory(source.workspace_id, source.source_id) / "source.json"
        payload = source.model_dump_json(indent=2).encode()
        await asyncio.to_thread(self._write_bytes, path, payload)

    async def save_documents(
        self, workspace_id: str, source_id: UUID, documents: Sequence[NormalizedDocument]
    ) -> None:
        """Atomically persist normalized documents as JSON Lines."""
        path = self._source_directory(workspace_id, source_id) / "documents.jsonl"
        payload = b"\n".join(document.model_dump_json().encode() for document in documents) + b"\n"
        await asyncio.to_thread(self._write_bytes, path, payload)

    async def load_documents(
        self, workspace_id: str, source_id: UUID
    ) -> tuple[NormalizedDocument, ...]:
        """Load and validate persisted normalized documents."""
        path = self._source_directory(workspace_id, source_id) / "documents.jsonl"
        text = await asyncio.to_thread(path.read_text)
        payloads = [line for line in text.splitlines() if line]
        return tuple(_DOCUMENTS_ADAPTER.validate_json(f"[{','.join(payloads)}]"))

    async def save_crawl_manifest(
        self, workspace_id: str, source_id: UUID, manifest: CrawlManifest
    ) -> None:
        """Atomically persist website crawl diagnostics as JSON."""
        path = self._source_directory(workspace_id, source_id) / "crawl-manifest.json"
        payload = manifest.model_dump_json(indent=2).encode()
        await asyncio.to_thread(self._write_bytes, path, payload)

    def _source_directory(self, workspace_id: str, source_id: UUID) -> Path:
        if not workspace_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in workspace_id
        ):
            raise ValueError(
                "workspace_id may contain only letters, numbers, hyphens, and underscores"
            )
        directory = (
            self._root / "workspaces" / workspace_id / "sources" / str(source_id)
        ).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("Source path escapes the configured data directory")
        return directory

    @staticmethod
    def _write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
