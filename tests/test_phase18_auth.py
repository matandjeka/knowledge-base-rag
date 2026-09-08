"""Verify JWT session security and client isolation using real database transactions."""

import time
from typing import Any

import httpx
import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.service import AuthService
from app.core.config import Settings, get_settings
from app.main import app
from app.persistence.metadata import metadata


@pytest.fixture
async def auth(tmp_path: Any) -> Any:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/auth.db")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        auth_require_verification=False,
        metadata_store_backend="postgresql",
        metadata_database_url="postgresql+asyncpg://unused",
        jwt_secret="test-only-secret-that-has-at-least-32-characters",
    )
    service = AuthService(engine, settings)
    yield service
    await engine.dispose()


@pytest.mark.asyncio
async def test_accounts_have_distinct_workspaces_and_passwords_are_hashed(
    auth: AuthService,
) -> None:
    first = await auth.register("first@example.com", "a-secure-password")
    second = await auth.register("second@example.com", "a-secure-password")
    assert first["workspace_id"] != second["workspace_id"]
    assert first["password_hash"].startswith("$argon2id$")
    with pytest.raises(HTTPException) as duplicate:
        await auth.register("FIRST@example.com", "a-secure-password")
    assert duplicate.value.status_code == 409
    with pytest.raises(HTTPException):
        await auth.login("first@example.com", "wrong-password")


@pytest.mark.asyncio
async def test_refresh_rotation_replay_revokes_session(auth: AuthService) -> None:
    await auth.register("client@example.com", "a-secure-password")
    original, refresh = await auth.login("client@example.com", "a-secure-password")
    renewed, replacement = await auth.refresh(refresh)
    assert refresh != replacement
    await auth.authenticate(renewed["access_token"])
    with pytest.raises(HTTPException):
        await auth.refresh(refresh)
    with pytest.raises(HTTPException):
        await auth.authenticate(original["access_token"])
    with pytest.raises(HTTPException):
        await auth.refresh(replacement)


@pytest.mark.asyncio
async def test_expired_wrong_audience_and_tampered_tokens_rejected(auth: AuthService) -> None:
    await auth.register("client@example.com", "a-secure-password")
    session, _ = await auth.login("client@example.com", "a-secure-password")
    token = session["access_token"]
    assert auth.settings.jwt_secret
    key = auth.settings.jwt_secret.get_secret_value()
    claims = jwt.decode(token, key, algorithms=["HS256"], audience="rag-api")
    changes_list: list[dict[str, Any]] = [
        {"exp": int(time.time()) - 1},
        {"aud": "different"},
        {"workspace_id": "other"},
        {"type": "refresh"},
    ]
    for changes in changes_list:
        bad = jwt.encode({**claims, **changes}, key, algorithm="HS256")
        with pytest.raises(HTTPException):
            await auth.authenticate(bad)
    with pytest.raises(HTTPException):
        await auth.authenticate(token + "tampered")


@pytest.mark.asyncio
async def test_verification_reset_and_logout(auth: AuthService) -> None:
    auth.settings.auth_require_verification = True
    user = await auth.register("client@example.com", "a-secure-password")
    with pytest.raises(HTTPException):
        await auth.login("client@example.com", "a-secure-password")
    verification = await auth.issue_email_token(user["id"], "verify")
    await auth.consume_email_token(verification, "verify")
    session, refresh = await auth.login("client@example.com", "a-secure-password")
    reset = await auth.issue_email_token(user["id"], "reset")
    await auth.consume_email_token(reset, "reset", "a-new-secure-password")
    with pytest.raises(HTTPException):
        await auth.authenticate(session["access_token"])
    with pytest.raises(HTTPException):
        await auth.consume_email_token(reset, "reset", "another-password")
    session, refresh = await auth.login("client@example.com", "a-new-secure-password")
    await auth.logout(refresh)
    with pytest.raises(HTTPException):
        await auth.authenticate(session["access_token"])


@pytest.mark.asyncio
async def test_api_rejects_foreign_workspaces_and_credentials(
    auth: AuthService, monkeypatch: Any
) -> None:
    import app.auth.authorization as authorization
    import app.auth.org_routes as org_routes
    import app.auth.routes as auth_routes
    from app.auth.organizations import OrganizationRepository

    user = await auth.register("client@example.com", "a-secure-password")
    await OrganizationRepository(auth.engine).create_personal_organization(
        user_id=user["id"], email=user["email"], workspace_id=user["workspace_id"]
    )
    session, _ = await auth.login("client@example.com", "a-secure-password")
    for module in (authorization, auth_routes, org_routes):
        monkeypatch.setattr(module, "get_metadata_engine", lambda: auth.engine)
    monkeypatch.setattr(authorization, "get_auth_service", lambda: auth)
    monkeypatch.setattr(authorization, "get_settings", lambda: auth.settings)
    monkeypatch.setattr(auth_routes, "get_settings", lambda: auth.settings)
    app.dependency_overrides[get_settings] = lambda: auth.settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/sources?workspace_id=other")).status_code == 401
            client.headers["Authorization"] = "Bearer " + session["access_token"]
            assert (await client.get("/sources?workspace_id=other")).status_code == 403
            assert (
                await client.post("/query", json={"question": "private", "workspace_id": "other"})
            ).status_code == 403
            owner = session["user"]["workspace_id"]
            # A foreign workspace_id nested anywhere in the body is rejected too.
            assert (
                await client.post(
                    "/query",
                    json={
                        "question": "private",
                        "workspace_id": owner,
                        "nested": [{"workspace_id": "other"}],
                    },
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/sources/database",
                    json={
                        "workspace_id": owner,
                        "name": "secret",
                        "secret_env_var": "METADATA_DATABASE_URL",
                        "isolation_mode": "dedicated",
                        "tables": [{"table_name": "rag_users"}],
                    },
                )
            ).status_code == 403
            assert (await client.get("/auth/me")).json()["workspace_id"] == owner
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_rate_limit_survives_service_recreation(auth: AuthService) -> None:
    await auth.throttle("test", maximum=1)
    restarted = AuthService(auth.engine, auth.settings)
    with pytest.raises(HTTPException) as error:
        await restarted.throttle("test", maximum=1)
    assert error.value.status_code == 429
