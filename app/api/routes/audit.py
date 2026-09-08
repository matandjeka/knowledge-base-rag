"""Audit-log read and chain-verification endpoints (admin and owner only)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.dependencies import get_metadata_engine
from app.audit import AuditRepository
from app.auth.organizations import Membership, Role, role_allows

router = APIRouter(prefix="/audit", tags=["audit"])

WorkspaceQuery = Annotated[str, Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


def _admin_membership(request: Request) -> Membership:
    membership: Membership | None = getattr(request.state, "membership", None)
    if membership is None or not role_allows(membership.role, Role.ADMIN):
        raise HTTPException(403, "Audit access requires an admin role.")
    return membership


@router.get("/events")
async def list_events(
    request: Request,
    workspace_id: WorkspaceQuery,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> list[dict[str, Any]]:
    membership = _admin_membership(request)
    events = await AuditRepository(get_metadata_engine()).read(
        org_id=membership.org_id, limit=limit, before=before
    )
    return [event.model_dump(mode="json") for event in events]


@router.get("/verify")
async def verify_chain(request: Request, workspace_id: WorkspaceQuery) -> dict[str, Any]:
    membership = _admin_membership(request)
    result = await AuditRepository(get_metadata_engine()).verify_chain(membership.org_id)
    return result.model_dump(mode="json")
