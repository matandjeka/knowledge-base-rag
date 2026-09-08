"""Authorized job creation, direct-upload ownership, and workflow callbacks."""

from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update

from app.api.dependencies import get_metadata_engine
from app.core.config import get_settings
from app.core.rate_limit import rate_limit
from app.jobs.processing import public_job, step_job, storage
from app.jobs.repository import JobRepository, jobs

router = APIRouter(tags=["jobs"])


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["pdf", "csv", "website", "reindex", "graph"]
    filename: str | None = Field(default=None, max_length=200)
    pathname: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    source_id: UUID | None = None
    text_columns: list[str] = Field(default_factory=list, max_length=200)
    metadata_columns: list[str] = Field(default_factory=list, max_length=200)
    row_id_column: str | None = None
    crawl_same_domain: bool = False
    page_limit: int = Field(default=1, ge=1, le=20)


class StepRequest(BaseModel):
    step: int = Field(ge=0)


def repository() -> JobRepository:
    return JobRepository(get_metadata_engine())


def workspace(request: Request) -> str:
    if not get_settings().auth_enabled:
        raise HTTPException(503, "Jobs require authentication.")
    return str(request.state.workspace_id)


async def dispatch(job_id: str) -> bool:
    settings = get_settings()
    if not settings.workflow_dispatch_url or not settings.workflow_secret:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                settings.workflow_dispatch_url,
                headers={
                    "Authorization": "Bearer " + settings.workflow_secret.get_secret_value(),
                    **(
                        {
                            "x-vercel-protection-bypass": (
                                settings.workflow_bypass_secret.get_secret_value()
                            )
                        }
                        if settings.workflow_bypass_secret
                        else {}
                    ),
                },
                json={"job_id": job_id},
            )
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


@router.post(
    "/jobs",
    status_code=202,
    dependencies=[rate_limit("job", "rate_limit_job_per_minute")],
)
async def create_job(body: JobRequest, request: Request) -> dict[str, Any]:
    owner = workspace(request)
    spec = body.model_dump(mode="json", exclude_none=True, exclude={"id"})
    if body.kind in {"pdf", "csv"}:
        prefix = f"workspaces/{owner}/uploads/{body.id}/"
        if not body.pathname or not body.pathname.startswith(prefix) or ".." in body.pathname:
            raise HTTPException(403, "Upload does not belong to this job and workspace.")
        if not body.filename or not body.filename.lower().endswith("." + body.kind):
            raise HTTPException(422, "File extension must match the source type.")
        if body.kind == "csv" and not body.text_columns:
            raise HTTPException(422, "Select at least one text column.")
        head = await storage().client.head(body.pathname)
        maximum = (
            get_settings().max_pdf_size_bytes
            if body.kind == "pdf"
            else get_settings().max_csv_size_bytes
        )
        if head.size > maximum:
            raise HTTPException(413, "Upload exceeds the source size limit.")
    if body.kind == "website":
        from app.ingestion.website import normalize_url

        if not body.url:
            raise HTTPException(422, "A website URL is required.")
        spec["url"] = normalize_url(body.url)
    if body.kind == "reindex" and body.source_id is None:
        raise HTTPException(422, "Source ID is required.")
    job = await repository().create(owner, spec, str(body.id))
    dispatched = await dispatch(job["id"])
    return {**public_job(job), "dispatched": dispatched}


@router.get("/jobs")
async def list_jobs(request: Request) -> list[dict[str, Any]]:
    owner = workspace(request)
    return [public_job(job) for job in await repository().list(owner)]


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, request: Request) -> dict[str, Any]:
    owner = workspace(request)
    return public_job(await repository().get(owner, str(job_id)))


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: UUID, request: Request) -> dict[str, bool]:
    owner = workspace(request)
    job = await repository().get(owner, str(job_id))
    if job["status"] in {"complete", "cancelled"}:
        raise HTTPException(409, "This job has ended.")
    return {"dispatched": await dispatch(str(job_id))}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID, request: Request) -> dict[str, bool]:
    owner = workspace(request)
    await repository().cancel(owner, str(job_id))
    return {"ok": True}


@router.post("/internal/jobs/{job_id}/step")
async def step(job_id: UUID, body: StepRequest) -> dict[str, Any]:
    try:
        return await step_job(str(job_id), body.step)
    except HTTPException:
        raise
    except Exception:
        async with get_metadata_engine().begin() as conn:
            await conn.execute(
                update(jobs)
                .where(jobs.c.id == str(job_id))
                .values(
                    status="retrying",
                    error="Processing failed. The job can be retried from its last checkpoint.",
                )
            )
        raise HTTPException(503, "Processing step failed; retry from its checkpoint.") from None


@router.post("/internal/jobs/{job_id}/failed")
async def failed(job_id: UUID) -> dict[str, bool]:
    async with get_metadata_engine().begin() as conn:
        await conn.execute(
            update(jobs)
            .where(jobs.c.id == str(job_id), jobs.c.status.not_in(["complete", "cancelled"]))
            .values(
                status="failed", error="Automatic retries exhausted. Resume or cancel this job."
            )
        )
    return {"ok": True}


@router.get("/uploads/preview")
async def preview_upload(pathname: str, request: Request) -> Any:
    owner = workspace(request)
    if not pathname.startswith(f"workspaces/{owner}/uploads/") or ".." in pathname:
        raise HTTPException(403, "Upload access denied.")
    from app.api.dependencies import get_csv_ingestion_service

    head = await storage().client.head(pathname)
    if head.size > get_settings().max_csv_size_bytes:
        raise HTTPException(413, "Upload exceeds the CSV size limit.")
    data = await storage().read(pathname)
    return await get_csv_ingestion_service().preview("upload.csv", "text/csv", data)
