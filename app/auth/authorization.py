"""Authenticate every request, resolve the caller's organization role, and gate writes.

Inputs: the incoming ``Request`` (bearer token, ``workspace_id`` in query/body/form).
Outputs: ``request.state.user``, ``request.state.workspace_id``, ``request.state.membership``.
Side effects: none beyond attaching request state; raises ``HTTPException`` on denial.
"""

import secrets
from collections.abc import Iterator
from typing import Any

from fastapi import HTTPException, Request

from app.api.dependencies import get_metadata_engine
from app.auth.organizations import OrganizationRepository, Role, role_allows
from app.auth.routes import get_auth_service
from app.core.config import get_settings

PUBLIC = {
    "/health",
    "/auth/register",
    "/auth/login",
    "/auth/refresh",
    "/auth/logout",
    "/auth/request-link",
    "/auth/verify",
    "/auth/reset",
}

# Endpoints whose own router resolves organization RBAC from a path parameter, and read-style
# POSTs that any member may call. Everything else that mutates requires at least ``member``.
_SELF_SERVICE_PREFIXES = ("/orgs", "/auth")
_ROLE_EXEMPT_PATHS = {"/query"}


def _has_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _workspace_references(value: Any) -> Iterator[str]:
    """Yield every ``workspace_id`` value anywhere in a decoded JSON body."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "workspace_id":
                yield nested
            else:
                yield from _workspace_references(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _workspace_references(item)


def _required_role(method: str, path: str) -> Role | None:
    """Minimum organization role for a request, or ``None`` when any member may call it."""
    if method == "GET" or path in _ROLE_EXEMPT_PATHS:
        return None
    if _has_prefix(path, _SELF_SERVICE_PREFIXES):
        return None
    return Role.MEMBER


async def authorize(request: Request) -> None:
    """Derive ownership from a verified session and organization membership, never from a field."""
    settings = get_settings()
    # Workflow-only endpoints are never reachable without the shared secret, regardless of
    # whether interactive authentication is enabled for this deployment.
    if request.url.path.startswith("/internal/"):
        secret = settings.workflow_secret
        provided = request.headers.get("authorization", "")
        if secret is None or not secrets.compare_digest(
            provided, "Bearer " + secret.get_secret_value()
        ):
            raise HTTPException(401, "Invalid workflow credentials.")
        return
    if not settings.auth_enabled or request.url.path.rstrip("/") in PUBLIC:
        return
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue.")
    user = await get_auth_service().authenticate(authorization[7:])
    request.state.user = user
    if (
        settings.serverless
        and request.method == "POST"
        and (
            request.url.path in {"/sources/pdf", "/sources/csv", "/sources/website", "/graph/index"}
            or (request.url.path.startswith("/sources/") and request.url.path.endswith("/index"))
        )
    ):
        raise HTTPException(409, "Use a durable ingestion job for this operation.")

    body: Any = None
    candidates: list[Any] = request.query_params.getlist("workspace_id")
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "Invalid JSON.") from None
        # Scan the whole decoded body so a nested workspace_id cannot bypass the check.
        candidates.extend(_workspace_references(body))
    elif "multipart/form-data" in content_type:
        candidates.extend((await request.form()).getlist("workspace_id"))

    if any(not isinstance(value, str) for value in candidates):
        raise HTTPException(422, "workspace_id must be a string.")
    distinct = set(candidates)
    if len(distinct) > 1:
        raise HTTPException(403, "A request may target only one workspace.")

    # Account and organization self-service endpoints do not act on workspace data; the
    # /orgs router resolves its own membership from the path organization id.
    path = request.url.path
    self_service = _has_prefix(path, _SELF_SERVICE_PREFIXES)
    target = next(iter(distinct), None)
    if target is None and not self_service:
        target = user["workspace_id"]

    if target is not None:
        membership = await OrganizationRepository(get_metadata_engine()).membership(
            user_id=user["id"], workspace_id=target
        )
        if membership is None:
            raise HTTPException(403, "Workspace access denied.")
        request.state.membership = membership
        request.state.workspace_id = target
        required = _required_role(request.method, path)
        if required is not None and not role_allows(membership.role, required):
            raise HTTPException(403, "Your role does not permit this action.")

    if path == "/sources/database":
        if not isinstance(body, dict) or target is None:
            raise HTTPException(422, "Invalid JSON.")
        # A client must never name another client's or the platform's environment secret.
        reference = body.get("secret_env_var", "")
        if not reference.startswith("WORKSPACE_" + target.replace("-", "_").upper() + "_"):
            raise HTTPException(403, "Database connection must be provisioned for this workspace.")
