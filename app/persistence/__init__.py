"""Production persistence coordination and adapter contracts."""

from app.persistence.metadata import (
    ActiveGenerationRepository,
    GenerationKind,
    InMemoryPersistenceRepository,
    OperationStatus,
    PersistenceOperation,
    PersistenceRepository,
    PostgresPersistenceRepository,
    create_metadata_schema,
)

__all__ = [
    "ActiveGenerationRepository",
    "GenerationKind",
    "InMemoryPersistenceRepository",
    "OperationStatus",
    "PersistenceOperation",
    "PersistenceRepository",
    "PostgresPersistenceRepository",
    "create_metadata_schema",
]
