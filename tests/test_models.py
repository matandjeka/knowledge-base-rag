"""Canonical model validation tests."""

from datetime import UTC, datetime
from uuid import uuid4

from app.models import Evidence, NormalizedDocument, SourceType


def test_evidence_uses_independent_mutable_defaults() -> None:
    source_id = uuid4()
    first = Evidence(
        retriever="vector",
        content="First passage",
        source_id=source_id,
        source_type=SourceType.PDF,
    )
    second = Evidence(
        retriever="vector",
        content="Second passage",
        source_id=source_id,
        source_type=SourceType.PDF,
    )

    first.metadata["page"] = 1

    assert second.metadata == {}


def test_normalized_document_preserves_tenant_and_creation_time() -> None:
    document = NormalizedDocument(
        tenant_id="tenant-123",
        source_id=uuid4(),
        source_type=SourceType.PDF,
        content="A normalized passage",
    )

    assert document.tenant_id == "tenant-123"
    assert document.created_at.tzinfo is UTC
    assert document.created_at <= datetime.now(UTC)
