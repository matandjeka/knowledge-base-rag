"""Original-source and normalized-document storage adapters."""

from app.storage.azure_blob import AzureBlobSourceStorage
from app.storage.local import LocalSourceStorage, SourceStorage

__all__ = ["AzureBlobSourceStorage", "LocalSourceStorage", "SourceStorage"]
