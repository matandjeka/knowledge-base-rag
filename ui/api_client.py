"""Typed HTTP boundary used by every Streamlit page."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeVar, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.evaluation.framework import EvaluationReport
from app.models import (
    CsvIngestionResult,
    CsvPreviewResult,
    DatabaseSourceRequest,
    DatabaseSourceResult,
    NormalizedDocument,
    PdfIngestionResult,
    QueryRequest,
    QueryResponse,
    Source,
    SourceIndexResult,
    WebsiteIngestionRequest,
    WebsiteIngestionResult,
)
from app.models.system import EffectiveSettings, EvaluationReportListing

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class TimedQueryResult:
    """Validated query response paired with client-observed duration."""

    response: QueryResponse
    elapsed_seconds: float


class ApiError(RuntimeError):
    """A user-displayable API failure with transport details removed."""


class RagApiClient:
    """Small synchronous client for the local or remotely deployed FastAPI service."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 60) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    @property
    def base_url(self) -> str:
        return self._base_url

    def _request(
        self, method: str, path: str, *, timeout_seconds: float | None = None, **kwargs: Any
    ) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self._base_url}{path}",
                timeout=self._timeout if timeout_seconds is None else timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            try:
                detail = error.response.json().get("detail", "The API rejected the request")
            except ValueError:
                detail = "The API rejected the request"
            raise ApiError(str(detail)) from error
        except httpx.RequestError as error:
            raise ApiError(
                "Could not reach the API. Verify that the service is running."
            ) from error
        except ValueError as error:
            raise ApiError("The API returned an invalid JSON response.") from error

    @staticmethod
    def _model(model: type[ModelT], payload: Any) -> ModelT:
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            raise ApiError(
                "The API returned a response that does not match its contract."
            ) from error

    @staticmethod
    def _models(model: type[ModelT], payload: Any) -> list[ModelT]:
        try:
            return TypeAdapter(list[model]).validate_python(payload)  # type: ignore[valid-type]
        except ValidationError as error:
            raise ApiError(
                "The API returned a response that does not match its contract."
            ) from error

    def health(self) -> bool:
        payload = cast(dict[str, Any], self._request("GET", "/health"))
        return payload == {"status": "ok"}

    def settings(self) -> EffectiveSettings:
        return self._model(EffectiveSettings, self._request("GET", "/settings"))

    def list_sources(self, workspace_id: str) -> list[Source]:
        return self._models(
            Source, self._request("GET", "/sources", params={"workspace_id": workspace_id})
        )

    def list_documents(self, workspace_id: str, source_id: UUID) -> list[NormalizedDocument]:
        return self._models(
            NormalizedDocument,
            self._request(
                "GET", f"/sources/{source_id}/documents", params={"workspace_id": workspace_id}
            ),
        )

    def rebuild_index(self, workspace_id: str, source_id: UUID) -> SourceIndexResult:
        return self._model(
            SourceIndexResult,
            self._request(
                "POST", f"/sources/{source_id}/index", params={"workspace_id": workspace_id}
            ),
        )

    def query(self, request: QueryRequest) -> QueryResponse:
        return self._model(
            QueryResponse, self._request("POST", "/query", json=request.model_dump(mode="json"))
        )

    def query_timed(self, request: QueryRequest) -> TimedQueryResult:
        started_at = perf_counter()
        response = self.query(request)
        return TimedQueryResult(response=response, elapsed_seconds=perf_counter() - started_at)

    def preview_csv(self, filename: str, data: bytes) -> CsvPreviewResult:
        return self._model(
            CsvPreviewResult,
            self._request(
                "POST", "/sources/csv/preview", files={"file": (filename, data, "text/csv")}
            ),
        )

    def add_pdf(self, workspace_id: str, filename: str, data: bytes) -> PdfIngestionResult:
        return self._model(
            PdfIngestionResult,
            self._request(
                "POST",
                "/sources/pdf",
                data={"workspace_id": workspace_id},
                files={"file": (filename, data, "application/pdf")},
            ),
        )

    def add_csv(self, filename: str, data: bytes, form: dict[str, Any]) -> CsvIngestionResult:
        return self._model(
            CsvIngestionResult,
            self._request(
                "POST",
                "/sources/csv",
                timeout_seconds=120,
                data=form,
                files={"file": (filename, data, "text/csv")},
            ),
        )

    def add_website(self, request: WebsiteIngestionRequest) -> WebsiteIngestionResult:
        return self._model(
            WebsiteIngestionResult,
            self._request(
                "POST",
                "/sources/website",
                timeout_seconds=120,
                json=request.model_dump(mode="json"),
            ),
        )

    def add_database(self, request: DatabaseSourceRequest) -> DatabaseSourceResult:
        return self._model(
            DatabaseSourceResult,
            self._request("POST", "/sources/database", json=request.model_dump(mode="json")),
        )

    def list_reports(self) -> EvaluationReportListing:
        return self._model(EvaluationReportListing, self._request("GET", "/evaluations/reports"))

    def get_report(self, report_id: str) -> EvaluationReport:
        return self._model(
            EvaluationReport, self._request("GET", f"/evaluations/reports/{report_id}")
        )
