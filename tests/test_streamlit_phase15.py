"""Phase 15 API boundaries and testable UI helper regressions."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.evaluation.framework import (
    CaseObservation,
    EvaluationReport,
    EvaluationTier,
    MetricSummary,
    RetrievalConfiguration,
)
from app.evaluation.reporting import write_report
from app.main import app
from app.models import (
    QueryRequest,
    RetrievalMode,
    Source,
    SourceConfig,
    SourceStatus,
    SourceType,
    WebsiteIngestionRequest,
)
from ui.api_client import ApiError, RagApiClient
from ui.pages import (
    can_inspect_documents,
    parse_table_specs,
    quality_chart_metrics,
    valid_sql_selection,
)


def _metrics() -> MetricSummary:
    return MetricSummary(
        case_count=1,
        recall_at_k=1,
        precision_at_k=1,
        hit_rate_at_k=1,
        mean_reciprocal_rank=1,
        ndcg_at_k=1,
        citation_accuracy=1,
        faithfulness=1,
        answer_relevance=1,
        route_accuracy=1,
        sql_safety_accuracy=1,
        median_latency_seconds=0.1,
        p95_latency_seconds=0.1,
    )


def _report(name: str, created_at: datetime) -> EvaluationReport:
    return EvaluationReport(
        benchmark_version="15",
        benchmark_sha256="a" * 64,
        configuration=RetrievalConfiguration(
            name=name,
            retrieval_mode=RetrievalMode.VECTOR,
            retrievers=[RetrievalMode.VECTOR],
        ),
        tier=EvaluationTier.DETERMINISTIC,
        created_at=created_at,
        observations=[CaseObservation(case_id="case", latency_seconds=0.1)],
        aggregate=_metrics(),
        passed=True,
    )


@pytest.mark.asyncio
async def test_evaluation_routes_list_newest_and_load_validated_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVALUATION_REPORTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        older = write_report(_report("older", datetime.now(UTC) - timedelta(days=1)), tmp_path)
        newer = write_report(_report("newer", datetime.now(UTC)), tmp_path)
        (tmp_path / "invalid.json").write_text("not-json")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listing = await client.get("/evaluations/reports")
            loaded = await client.get(f"/evaluations/reports/{newer.name}")
            traversal = await client.get("/evaluations/reports/%2E%2E%2Fsecret.json")
        assert listing.status_code == 200
        payload = listing.json()
        assert [item["configuration_name"] for item in payload["reports"]] == [
            "newer",
            "older",
        ]
        assert payload["invalid_report_ids"] == ["invalid.json"]
        assert loaded.status_code == 200
        assert loaded.json()["configuration"]["name"] == "newer"
        assert traversal.status_code == 404
        assert older.exists()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_effective_settings_never_expose_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-leak")
    monkeypatch.setenv("GRAPH_EXTRACTION_MODEL", "graph-model")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/settings")
        serialized = json.dumps(response.json())
        assert response.status_code == 200
        assert "do-not-leak" not in serialized
        assert "openai_api_key" not in serialized
        assert response.json()["graph_retrieval_configured"] is True
    finally:
        get_settings.cache_clear()


def test_query_request_accepts_bounded_experiment_similarity() -> None:
    request = QueryRequest(
        workspace_id="local",
        question="Question",
        retrieval_mode=RetrievalMode.VECTOR,
        min_similarity=0.25,
    )
    assert request.min_similarity == 0.25
    with pytest.raises(ValueError):
        QueryRequest(
            workspace_id="local",
            question="Question",
            retrieval_mode=RetrievalMode.VECTOR,
            min_similarity=1.1,
        )


def test_database_table_specs_enforce_shared_tenant_column() -> None:
    assert parse_table_specs("public.sales:workspace_id", shared=True) == [
        {"schema_name": "public", "table_name": "sales", "tenant_column": "workspace_id"}
    ]
    with pytest.raises(ValueError, match="tenant_column"):
        parse_table_specs("public.sales", shared=True)


def test_api_client_translates_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> httpx.Response:
        del args, kwargs
        request = httpx.Request("GET", "http://test/settings")
        return httpx.Response(422, json={"detail": "invalid setting"}, request=request)

    monkeypatch.setattr(httpx, "request", reject)
    with pytest.raises(ApiError, match="invalid setting"):
        RagApiClient("http://test").settings()


def test_api_client_translates_invalid_json_and_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "http://test/settings")
    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(200, content=b"not-json", request=request),
    )
    with pytest.raises(ApiError, match="invalid JSON"):
        RagApiClient("http://test").settings()

    monkeypatch.setattr(
        httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(200, json={}, request=request),
    )
    with pytest.raises(ApiError, match="does not match"):
        RagApiClient("http://test").settings()


def test_website_ingestion_uses_long_operation_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source(
        workspace_id="local",
        name="Docs",
        config=SourceConfig(source_type=SourceType.WEBSITE),
        status=SourceStatus.READY,
    )
    observed_timeout: list[float] = []

    def accept(*args: object, **kwargs: object) -> httpx.Response:
        del args
        timeout = kwargs["timeout"]
        assert isinstance(timeout, int | float)
        observed_timeout.append(float(timeout))
        request = httpx.Request("POST", "http://test/sources/website")
        return httpx.Response(
            201,
            json={
                "source": source.model_dump(mode="json"),
                "page_count": 1,
                "chunk_count": 1,
                "skipped_count": 0,
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "request", accept)
    result = RagApiClient("http://test").add_website(
        WebsiteIngestionRequest(workspace_id="local", url="https://example.test")
    )
    assert result.source == source
    assert observed_timeout == [120]


def test_source_actions_and_quality_metrics_are_semantically_constrained() -> None:
    database = Source(
        workspace_id="local",
        name="Database",
        config=SourceConfig(source_type=SourceType.DATABASE),
        status=SourceStatus.READY,
    )
    pdf = Source(
        workspace_id="local",
        name="PDF",
        config=SourceConfig(source_type=SourceType.PDF),
        status=SourceStatus.READY,
    )
    assert not can_inspect_documents(database)
    assert can_inspect_documents(pdf)
    assert valid_sql_selection([database])
    assert not valid_sql_selection([pdf])
    assert not valid_sql_selection([database, pdf])
    assert quality_chart_metrics(
        {"recall_at_k": 0.8, "p95_latency_seconds": 0.1, "mean_cost_usd": 0.01}
    ) == {"recall_at_k": 0.8}
