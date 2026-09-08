"""Organization and membership management endpoints.

Inputs: an authenticated session (``request.state.user``) plus JSON bodies.
Outputs: organization / member / invitation representations.
Side effects: rows in ``rag_organizations``, ``rag_memberships``, ``rag_invitations``;
outbound invitation email via the configured webhook.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_metadata_engine
from app.audit import record
from app.auth.organizations import Membership, OrganizationRepository, Role, role_allows
from app.auth.routes import check_origin, send_link
from app.models import Classification

router = APIRouter(prefix="/orgs", tags=["organizations"])


def _repository() -> OrganizationRepository:
    return OrganizationRepository(get_metadata_engine())


def _actor(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return dict(user)


def require_org_role(minimum: Role) -> Any:
    """Dependency: the caller must hold at least ``minimum`` in the path organization."""

    async def dependency(org_id: str, request: Request) -> Membership:
        actor = _actor(request)
        membership = await _repository().membership_by_org(user_id=actor["id"], org_id=org_id)
        if membership is None:
            raise HTTPException(404, "Organization not found.")
        if not role_allows(membership.role, minimum):
            raise HTTPException(403, "Your role does not permit this action.")
        return membership

    return Depends(dependency)


class CreateOrganization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class InviteMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    role: Role = Role.MEMBER


class UpdateMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role | None = None
    clearance: Classification | None = None


class AcceptInvitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=256)


@router.get("")
async def list_organizations(request: Request) -> list[dict[str, Any]]:
    actor = _actor(request)
    return [
        org.model_dump(mode="json")
        for org in await _repository().organizations_for_user(actor["id"])
    ]


@router.post("", status_code=201)
async def create_organization(body: CreateOrganization, request: Request) -> dict[str, Any]:
    check_origin(request)
    actor = _actor(request)
    org = await _repository().create_organization(name=body.name, owner_user_id=actor["id"])
    await record(
        request, "org.created", org_id=org.id, workspace_id=org.workspace_id, target_type="org"
    )
    return org.model_dump(mode="json")


@router.get("/{org_id}/members")
async def list_members(
    org_id: str, _: Annotated[Membership, require_org_role(Role.VIEWER)]
) -> list[dict[str, Any]]:
    return [member.model_dump(mode="json") for member in await _repository().members(org_id)]


@router.post("/{org_id}/invitations", status_code=202)
async def invite_member(
    org_id: str,
    body: InviteMember,
    request: Request,
    membership: Annotated[Membership, require_org_role(Role.ADMIN)],
) -> dict[str, str]:
    check_origin(request)
    if body.role in {Role.ADMIN, Role.OWNER} and not role_allows(membership.role, Role.OWNER):
        raise HTTPException(403, "Only an owner may grant admin or owner.")
    invitation, token = await _repository().create_invitation(
        org_id=org_id,
        email=body.email,
        role=body.role,
        invited_by=membership.user_id,
    )
    await send_link(invitation.email, token, "invite")
    await record(
        request,
        "member.invited",
        org_id=org_id,
        target_type="email",
        target_id=invitation.email,
        metadata={"role": body.role.value},
    )
    return {"message": "Invitation sent."}


@router.post("/invitations/accept")
async def accept_invitation(body: AcceptInvitation, request: Request) -> dict[str, Any]:
    check_origin(request)
    actor = _actor(request)
    org = await _repository().accept_invitation(
        token=body.token, user_id=actor["id"], email=actor["email"]
    )
    await record(
        request,
        "member.joined",
        org_id=org.id,
        workspace_id=org.workspace_id,
        target_type="user",
        target_id=actor["id"],
    )
    return org.model_dump(mode="json")


@router.patch("/{org_id}/members/{user_id}")
async def update_member(
    org_id: str,
    user_id: str,
    body: UpdateMember,
    request: Request,
    membership: Annotated[Membership, require_org_role(Role.ADMIN)],
) -> dict[str, bool]:
    if body.role is None and body.clearance is None:
        raise HTTPException(422, "Provide a role or a clearance.")
    target = await _repository().membership_by_org(user_id=user_id, org_id=org_id)
    if target is None:
        raise HTTPException(404, "That member was not found.")
    if body.role is not None:
        if (
            body.role in {Role.ADMIN, Role.OWNER} or target.role in {Role.ADMIN, Role.OWNER}
        ) and not role_allows(membership.role, Role.OWNER):
            raise HTTPException(403, "Only an owner may change admin or owner roles.")
        await _repository().set_role(org_id=org_id, user_id=user_id, role=body.role)
        await record(
            request,
            "member.role_changed",
            org_id=org_id,
            target_type="user",
            target_id=user_id,
            metadata={"role": body.role.value},
        )
    if body.clearance is not None:
        await _repository().set_clearance(org_id=org_id, user_id=user_id, clearance=body.clearance)
        await record(
            request,
            "member.clearance_changed",
            org_id=org_id,
            target_type="user",
            target_id=user_id,
            metadata={"clearance": body.clearance.value},
        )
    return {"ok": True}


@router.delete("/{org_id}/members/{user_id}")
async def remove_member(
    org_id: str,
    user_id: str,
    request: Request,
    membership: Annotated[Membership, require_org_role(Role.ADMIN)],
) -> dict[str, bool]:
    target = await _repository().membership_by_org(user_id=user_id, org_id=org_id)
    if target is None:
        raise HTTPException(404, "That member was not found.")
    if (
        target.role in {Role.ADMIN, Role.OWNER}
        and user_id != membership.user_id
        and not role_allows(membership.role, Role.OWNER)
    ):
        raise HTTPException(403, "Only an owner may remove an admin or owner.")
    await _repository().remove_member(org_id=org_id, user_id=user_id)
    await record(request, "member.removed", org_id=org_id, target_type="user", target_id=user_id)
    return {"ok": True}
