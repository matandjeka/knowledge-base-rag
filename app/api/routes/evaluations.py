"""Read-only access to validated immutable evaluation reports."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import require_blob_storage
from app.core.config import get_settings
from app.evaluation.framework import EvaluationReport
from app.evaluation.reporting import load_report
from app.models.system import EvaluationReportListing, EvaluationReportSummary

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
_REPORT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
# Bound to keep a single listing request from reading an unbounded number of blob objects.
_MAX_WORKSPACE_REPORTS = 200


def _report_path(report_id: str) -> Path:
    if _REPORT_ID.fullmatch(report_id) is None or not report_id.endswith(".json"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    directory = get_settings().evaluation_reports_dir.resolve()
    path = (directory / report_id).resolve()
    if path.parent != directory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return path


@router.get("/reports", response_model=EvaluationReportListing)
async def list_evaluation_reports(request: Request) -> EvaluationReportListing:
    """List valid reports newest-first without exposing answer observations."""
    if get_settings().auth_enabled:
        blob = require_blob_storage()
        prefix = f"workspaces/{request.state.user['workspace_id']}/evaluations/"
        reports = []
        invalid = []
        names = [
            name
            for name in await blob.names(prefix)
            if _REPORT_ID.fullmatch(name.removeprefix(prefix)) and name.endswith(".json")
        ]
        if len(names) > _MAX_WORKSPACE_REPORTS:
            names = sorted(names, reverse=True)[:_MAX_WORKSPACE_REPORTS]
        for name in names:
            report_id = name.removeprefix(prefix)
            try:
                report = EvaluationReport.model_validate_json(await blob.read(name))
                reports.append(
                    EvaluationReportSummary(
                        report_id=report_id,
                        configuration_name=report.configuration.name,
                        benchmark_version=report.benchmark_version,
                        benchmark_sha256=report.benchmark_sha256,
                        tier=report.tier,
                        created_at=report.created_at,
                        passed=report.passed,
                        baseline_configuration=report.baseline_configuration,
                    )
                )
            except ValueError:
                invalid.append(report_id)
        return EvaluationReportListing(
            reports=sorted(reports, key=lambda r: r.created_at, reverse=True),
            invalid_report_ids=sorted(invalid),
        )
    directory = get_settings().evaluation_reports_dir
    if not directory.exists():
        return EvaluationReportListing(reports=[], invalid_report_ids=[])
    summaries: list[EvaluationReportSummary] = []
    invalid_report_ids: list[str] = []
    for path in directory.glob("*.json"):
        if path.is_symlink():
            invalid_report_ids.append(path.name)
            continue
        try:
            report = load_report(path)
        except (OSError, ValueError):
            invalid_report_ids.append(path.name)
            continue
        summaries.append(
            EvaluationReportSummary(
                report_id=path.name,
                configuration_name=report.configuration.name,
                benchmark_version=report.benchmark_version,
                benchmark_sha256=report.benchmark_sha256,
                tier=report.tier,
                created_at=report.created_at,
                passed=report.passed,
                baseline_configuration=report.baseline_configuration,
            )
        )
    return EvaluationReportListing(
        reports=sorted(summaries, key=lambda item: item.created_at, reverse=True),
        invalid_report_ids=sorted(invalid_report_ids),
    )


@router.get("/reports/{report_id}", response_model=EvaluationReport)
async def get_evaluation_report(report_id: str, request: Request) -> EvaluationReport:
    """Load one strictly validated report by its safe filename identifier."""
    if get_settings().auth_enabled:
        if not _REPORT_ID.fullmatch(report_id) or not report_id.endswith(".json"):
            raise HTTPException(404, "Report not found")
        name = f"workspaces/{request.state.user['workspace_id']}/evaluations/{report_id}"
        try:
            payload = await require_blob_storage().read(name)
        except ValueError:
            raise HTTPException(404, "Report not found") from None
        try:
            return EvaluationReport.model_validate_json(payload)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Evaluation report is invalid",
            ) from error
    path = _report_path(report_id)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        return load_report(path)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Evaluation report is invalid",
        ) from error
