"""Application-level exceptions that isolate callers from dependency errors."""


class ApplicationError(Exception):
    """Base exception for expected application failures."""


class IngestionError(ApplicationError):
    """Raised when loading source material fails."""


class PdfValidationError(IngestionError):
    """Raised when an uploaded file is not an acceptable, extractable PDF."""


class WebsiteValidationError(IngestionError):
    """Raised when a website cannot be crawled safely or yields no usable content."""


class CsvValidationError(IngestionError):
    """Raised when an uploaded CSV or its ingestion configuration is invalid."""


class DatabaseConfigurationError(ApplicationError):
    """Raised when a database source cannot be configured safely."""


class SqlValidationError(ApplicationError):
    """Raised when generated SQL violates structured retrieval policy."""


class DatabaseExecutionError(ApplicationError):
    """Raised when safe database query execution fails."""


class ParsingError(ApplicationError):
    """Raised when source parsing or normalization fails."""


class IndexingError(ApplicationError):
    """Raised when index creation or persistence fails."""


class IndexNotFoundError(IndexingError):
    """Raised when a workspace has no persisted vector index."""


class GraphIndexNotFoundError(IndexNotFoundError):
    """Raised when a workspace has no activated knowledge graph."""


class GraphConfigurationError(IndexingError):
    """Raised when graph extraction configuration is incomplete."""


class RetrievalError(ApplicationError):
    """Raised when evidence retrieval fails."""


class RerankingError(ApplicationError):
    """Raised when candidate re-ranking fails."""


class GenerationError(ApplicationError):
    """Raised when grounded answer generation fails."""


class SourceNotFoundError(ApplicationError):
    """Raised when a requested source does not exist."""


class SourceAlreadyExistsError(ApplicationError):
    """Raised when a source identifier is already registered."""


class InvalidSourceTransitionError(ApplicationError):
    """Raised when a source lifecycle transition is not permitted."""


class UnauthorizedSourceError(ApplicationError):
    """Raised when a caller cannot access a source."""
