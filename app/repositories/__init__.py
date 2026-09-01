"""Persistence contracts and adapters."""

from app.repositories.source_registry import InMemorySourceRepository, SourceRepository

__all__ = ["InMemorySourceRepository", "SourceRepository"]
