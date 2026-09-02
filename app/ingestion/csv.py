"""Strict CSV validation, preview inference, and row normalization."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.core.exceptions import CsvValidationError
from app.models import CsvColumnPreview, CsvColumnType, CsvPreviewResult

_INTEGER = re.compile(r"[+-]?\d+")
_BOOLEAN_VALUES = frozenset({"true", "false"})


@dataclass(frozen=True, slots=True)
class ParsedCsv:
    """Validated CSV header and original cell text."""

    filename: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]


class CsvConnector:
    """Validate and parse comma-delimited UTF-8 CSV uploads."""

    def __init__(
        self,
        *,
        max_size_bytes: int,
        max_rows: int,
        max_columns: int,
        max_field_characters: int,
        preview_rows: int,
    ) -> None:
        self._max_size_bytes = max_size_bytes
        self._max_rows = max_rows
        self._max_columns = max_columns
        self._max_field_characters = max_field_characters
        self._preview_rows = preview_rows

    def parse(self, filename: str, content_type: str | None, data: bytes) -> ParsedCsv:
        """Validate an upload and return its exact textual cell values."""
        self._validate_upload(filename, content_type, data)
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise CsvValidationError("The CSV must use UTF-8 encoding") from error
        if "\x00" in text:
            raise CsvValidationError("The CSV contains null bytes")
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", strict=True)
            raw_header = next(reader, None)
            if raw_header is None:
                raise CsvValidationError("The CSV is empty")
            columns = tuple(value.strip() for value in raw_header)
            self._validate_header(columns)
            rows: list[tuple[str | None, ...]] = []
            for row_number, raw_row in enumerate(reader, start=1):
                if row_number > self._max_rows:
                    raise CsvValidationError(f"The CSV exceeds the {self._max_rows}-row limit")
                if len(raw_row) != len(columns):
                    raise CsvValidationError(
                        f"Data row {row_number} has {len(raw_row)} fields; expected {len(columns)}"
                    )
                normalized_row: list[str | None] = []
                for value in raw_row:
                    if len(value) > self._max_field_characters:
                        raise CsvValidationError(
                            f"Data row {row_number} contains a field exceeding the "
                            f"{self._max_field_characters}-character limit"
                        )
                    normalized_row.append(value if value != "" else None)
                rows.append(tuple(normalized_row))
        except csv.Error as error:
            raise CsvValidationError("The CSV is malformed") from error
        if not rows:
            raise CsvValidationError("The CSV must contain at least one data row")
        if not any(value is not None for row in rows for value in row):
            raise CsvValidationError("The CSV has no usable values")
        return ParsedCsv(Path(filename).name, columns, tuple(rows))

    def preview(self, filename: str, content_type: str | None, data: bytes) -> CsvPreviewResult:
        """Return a validated schema, row count, and bounded sample."""
        parsed = self.parse(filename, content_type, data)
        column_previews = [
            CsvColumnPreview(
                name=column,
                inferred_type=infer_column_type(tuple(row[index] for row in parsed.rows)),
            )
            for index, column in enumerate(parsed.columns)
        ]
        sample_rows = [
            dict(zip(parsed.columns, row, strict=True)) for row in parsed.rows[: self._preview_rows]
        ]
        return CsvPreviewResult(
            filename=parsed.filename,
            row_count=len(parsed.rows),
            columns=column_previews,
            sample_rows=sample_rows,
        )

    def _validate_upload(self, filename: str, content_type: str | None, data: bytes) -> None:
        if Path(filename).suffix.lower() != ".csv":
            raise CsvValidationError("Only files with a .csv extension are accepted")
        if content_type not in {"text/csv", "application/csv", "application/vnd.ms-excel"}:
            raise CsvValidationError("The upload MIME type must identify CSV content")
        if not data:
            raise CsvValidationError("The uploaded CSV is empty")
        if len(data) > self._max_size_bytes:
            raise CsvValidationError(
                f"The uploaded CSV exceeds the {self._max_size_bytes}-byte limit"
            )

    def _validate_header(self, columns: tuple[str, ...]) -> None:
        if len(columns) > self._max_columns:
            raise CsvValidationError(f"The CSV exceeds the {self._max_columns}-column limit")
        if any(not column for column in columns):
            raise CsvValidationError("CSV headers cannot be blank")
        duplicates = sorted({column for column in columns if columns.count(column) > 1})
        if duplicates:
            raise CsvValidationError(f"CSV headers must be unique: {', '.join(duplicates)}")
        for column in columns:
            if len(column) > self._max_field_characters:
                raise CsvValidationError("A CSV header exceeds the field-length limit")


def infer_column_type(values: tuple[str | None, ...]) -> CsvColumnType:
    """Infer a conservative display type without mutating source values."""
    present_values = tuple(value for value in values if value is not None)
    if not present_values:
        return CsvColumnType.TEXT
    lowered = tuple(value.lower() for value in present_values)
    if all(value in _BOOLEAN_VALUES for value in lowered):
        return CsvColumnType.BOOLEAN
    if all(_INTEGER.fullmatch(value) for value in present_values):
        return CsvColumnType.INTEGER
    try:
        for value in present_values:
            Decimal(value)
    except InvalidOperation:
        pass
    else:
        return CsvColumnType.DECIMAL
    if all(_is_iso_datetime(value) for value in present_values):
        return CsvColumnType.DATETIME
    if all(_is_iso_date(value) for value in present_values):
        return CsvColumnType.DATE
    return CsvColumnType.TEXT


def _is_iso_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value or (" " in value and parsed.time() != datetime.min.time())


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
