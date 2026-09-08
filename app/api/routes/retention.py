"""Per-workspace retention-policy read and update endpoints (admin and owner only)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_metadata_engine
from app.audit import record
from app.auth.organizations import Membership, Role, role_allows
from app.retention import RetentionPolicy, RetentionRepository

router = APIRouter(prefix="/retention", tags=["retention"])
crawl_router = APIRouter(prefix="/crawl-allowlist", tags=["governance"])

WorkspaceQuery = Annotated[str, Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class RetentionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_data_days: int | None = Field(default=None, ge=1, le=3650)
    audit_days: int | None = Field(default=None, ge=1, le=3650)
    query_log_days: int | None = Field(default=None, ge=1, le=3650)


def _admin_membership(request: Request) -> Membership:
    membership: Membership | None = getattr(request.state, "membership", None)
    if membership is None or not role_allows(membership.role, Role.ADMIN):
        raise HTTPException(403, "Retention policy changes require an admin role.")
    return membership


@router.get("")
async def get_policy(request: Request, workspace_id: WorkspaceQuery) -> dict[str, Any]:
    _admin_membership(request)
    policy = await RetentionRepository(get_metadata_engine()).get(workspace_id)
    return policy.model_dump(mode="json")


@router.put("")
async def set_policy(
    body: RetentionUpdate, request: Request, workspace_id: WorkspaceQuery
) -> dict[str, Any]:
    _admin_membership(request)
    policy = RetentionPolicy(workspace_id=workspace_id, **body.model_dump())
    saved = await RetentionRepository(get_metadata_engine()).set(policy)
    await record(
        request,
        "retention.policy_changed",
        target_type="workspace",
        target_id=workspace_id,
        metadata={
            "source_data_days": body.source_data_days or 0,
            "audit_days": body.audit_days or 0,
            "query_log_days": body.query_log_days or 0,
        },
    )
    return saved.model_dump(mode="json")


class CrawlAllowlistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: list[str] | None = Field(default=None, max_length=200)


@crawl_router.get("")
async def get_crawl_allowlist(request: Request, workspace_id: WorkspaceQuery) -> dict[str, Any]:
    _admin_membership(request)
    domains = await RetentionRepository(get_metadata_engine()).crawl_allowlist(workspace_id)
    return {"domains": domains}


@crawl_router.put("")
async def set_crawl_allowlist(
    body: CrawlAllowlistUpdate, request: Request, workspace_id: WorkspaceQuery
) -> dict[str, Any]:
    _admin_membership(request)
    cleaned = (
        None if not body.domains else sorted({d.strip().lower() for d in body.domains if d.strip()})
    )
    domains = await RetentionRepository(get_metadata_engine()).set_crawl_allowlist(
        workspace_id, cleaned
    )
    await record(
        request,
        "crawl_allowlist_changed",
        target_type="workspace",
        target_id=workspace_id,
        metadata={"count": len(domains or [])},
    )
    return {"domains": domains}
