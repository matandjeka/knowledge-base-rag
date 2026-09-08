"""Apply authentication and workspace checks to every application endpoint."""

import secrets
from collections.abc import Iterator
from typing import Any

from fastapi import HTTPException, Request

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


async def authorize(request: Request) -> None:
    """Derive ownership from a verified session, never from a caller's workspace field."""
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
    request.state.workspace_id = user["workspace_id"]
    if (
        settings.serverless
        and request.method == "POST"
        and (
            request.url.path in {"/sources/pdf", "/sources/csv", "/sources/website", "/graph/index"}
            or (request.url.path.startswith("/sources/") and request.url.path.endswith("/index"))
        )
    ):
        raise HTTPException(409, "Use a durable ingestion job for this operation.")
    expected = user["workspace_id"]
    candidates: list[Any] = request.query_params.getlist("workspace_id")
    content_type = request.headers.get("content-type", "")
    body: Any = None
    if "application/json" in content_type:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "Invalid JSON.") from None
        # Scan the whole decoded body so a nested workspace_id cannot bypass the check.
        candidates.extend(_workspace_references(body))
    elif "multipart/form-data" in content_type:
        candidates.extend((await request.form()).getlist("workspace_id"))
    if any(value != expected for value in candidates):
        raise HTTPException(403, "Workspace access denied.")
    if request.url.path == "/sources/database":
        if not isinstance(body, dict):
            raise HTTPException(422, "Invalid JSON.")
        # A client must never name another client's or the platform's environment secret.
        reference = body.get("secret_env_var", "")
        if not reference.startswith("WORKSPACE_" + expected.replace("-", "_").upper() + "_"):
            raise HTTPException(403, "Database connection must be provisioned for this workspace.")
