"""Phase 19a — organizations, roles, invitations, and role-gated endpoints over HTTP."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.service import AuthService
from app.core.config import Settings, get_settings
from app.main import app
from app.persistence.metadata import metadata


@pytest.fixture
async def env(tmp_path: Any, monkeypatch: Any) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/orgs.db")
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
    auth = AuthService(engine, settings)

    import app.api.dependencies as dependencies
    import app.api.routes.audit as audit_routes
    import app.api.routes.query as query_routes
    import app.api.routes.retention as retention_routes
    import app.auth.authorization as authorization
    import app.auth.org_routes as org_routes
    import app.auth.routes as auth_routes

    sent: list[tuple[str, str, str]] = []

    async def capture(email: str, token: str, purpose: str) -> None:
        sent.append((email, token, purpose))

    for module in (
        authorization,
        auth_routes,
        org_routes,
        dependencies,
        audit_routes,
        query_routes,
        retention_routes,
    ):
        monkeypatch.setattr(module, "get_metadata_engine", lambda: engine, raising=False)
    monkeypatch.setattr(authorization, "get_auth_service", lambda: auth)
    monkeypatch.setattr(authorization, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(org_routes, "send_link", capture)
    monkeypatch.setattr(auth_routes, "send_link", capture, raising=False)
    app.dependency_overrides[get_settings] = lambda: settings

    async def account(email: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        assert (
            await client.post(
                "/auth/register", json={"email": email, "password": "a-secure-password"}
            )
        ).status_code == 201
        login = await client.post(
            "/auth/login", json={"email": email, "password": "a-secure-password"}
        )
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        return client

    try:
        yield {"account": account, "sent": sent, "engine": engine}
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_registration_creates_owned_personal_organization(env: dict[str, Any]) -> None:
    client = await env["account"]("solo@example.com")
    me = (await client.get("/auth/me")).json()
    assert len(me["organizations"]) == 1
    org = me["organizations"][0]
    assert org["is_personal"] and org["workspace_id"] == me["workspace_id"]
    members = (await client.get(f"/orgs/{org['id']}/members")).json()
    assert [m["role"] for m in members] == ["owner"]
    await client.aclose()


@pytest.mark.asyncio
async def test_roles_gate_mutations_and_isolate_organizations(env: dict[str, Any]) -> None:
    owner = await env["account"]("owner@example.com")
    org = (await owner.post("/orgs", json={"name": "Acme"})).json()
    workspace = org["workspace_id"]

    viewer = await env["account"]("viewer@example.com")
    member = await env["account"]("member@example.com")
    for email, role in (("viewer@example.com", "viewer"), ("member@example.com", "member")):
        assert (
            await owner.post(f"/orgs/{org['id']}/invitations", json={"email": email, "role": role})
        ).status_code == 202
    tokens = {email: token for email, token, _ in env["sent"]}
    assert (
        await viewer.post("/orgs/invitations/accept", json={"token": tokens["viewer@example.com"]})
    ).status_code == 200
    assert (
        await member.post("/orgs/invitations/accept", json={"token": tokens["member@example.com"]})
    ).status_code == 200

    # Viewer may read the shared workspace but not mutate it.
    assert (await viewer.get(f"/sources?workspace_id={workspace}")).status_code == 200
    denied = await viewer.post(
        "/sources/database",
        json={
            "workspace_id": workspace,
            "name": "x",
            "secret_env_var": "IRRELEVANT",
            "isolation_mode": "dedicated",
            "tables": [{"table_name": "t"}],
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Your role does not permit this action."

    # Member clears the role gate (and is then stopped by the secret-name rule, not the role).
    passed = await member.post(
        "/sources/database",
        json={
            "workspace_id": workspace,
            "name": "x",
            "secret_env_var": "IRRELEVANT",
            "isolation_mode": "dedicated",
            "tables": [{"table_name": "t"}],
        },
    )
    assert passed.json()["detail"] != "Your role does not permit this action."

    # A member of Acme cannot touch the owner's personal workspace.
    owner_me = (await owner.get("/auth/me")).json()
    personal = next(o["workspace_id"] for o in owner_me["organizations"] if o["is_personal"])
    assert (await member.get(f"/sources?workspace_id={personal}")).status_code == 403

    for client in (owner, viewer, member):
        await client.aclose()


@pytest.mark.asyncio
async def test_last_owner_cannot_be_demoted_or_removed(env: dict[str, Any]) -> None:
    owner = await env["account"]("boss@example.com")
    org = (await owner.post("/orgs", json={"name": "Solo Corp"})).json()
    me = (await owner.get("/auth/me")).json()
    owner_id = me["id"]
    demote = await owner.patch(f"/orgs/{org['id']}/members/{owner_id}", json={"role": "member"})
    assert demote.status_code == 409
    remove = await owner.delete(f"/orgs/{org['id']}/members/{owner_id}")
    assert remove.status_code == 409
    await owner.aclose()


@pytest.mark.asyncio
async def test_admin_cannot_escalate_and_invitation_binds_to_email(env: dict[str, Any]) -> None:
    owner = await env["account"]("chief@example.com")
    org = (await owner.post("/orgs", json={"name": "Beta"})).json()
    admin = await env["account"]("deputy@example.com")
    assert (
        await owner.post(
            f"/orgs/{org['id']}/invitations", json={"email": "deputy@example.com", "role": "admin"}
        )
    ).status_code == 202
    token = env["sent"][-1][1]
    # The invitation is bound to deputy@example.com; a different account cannot consume it.
    intruder = await env["account"]("intruder@example.com")
    assert (
        await intruder.post("/orgs/invitations/accept", json={"token": token})
    ).status_code == 400
    assert (await admin.post("/orgs/invitations/accept", json={"token": token})).status_code == 200
    # An admin may not invite another admin.
    escalation = await admin.post(
        f"/orgs/{org['id']}/invitations", json={"email": "x@example.com", "role": "admin"}
    )
    assert escalation.status_code == 403
    for client in (owner, admin, intruder):
        await client.aclose()


@pytest.mark.asyncio
async def test_me_self_heals_account_without_organization(env: dict[str, Any]) -> None:
    from sqlalchemy import insert

    from app.auth.service import _hasher, users

    async with env["engine"].begin() as conn:
        await conn.execute(
            insert(users).values(
                id="legacy-user",
                email="legacy@example.com",
                password_hash=_hasher.hash("a-secure-password"),
                workspace_id="legacy-workspace",
                verified=True,
            )
        )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    login = await client.post(
        "/auth/login", json={"email": "legacy@example.com", "password": "a-secure-password"}
    )
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    me = (await client.get("/auth/me")).json()
    assert [o["workspace_id"] for o in me["organizations"]] == ["legacy-workspace"]
    await client.aclose()


@pytest.mark.asyncio
async def test_governance_endpoints_require_admin_and_reflect_activity(env: dict[str, Any]) -> None:
    owner = await env["account"]("gov-owner@example.com")
    org = (await owner.post("/orgs", json={"name": "GovCo"})).json()
    workspace = org["workspace_id"]

    # A viewer cannot read the audit log or change governance settings.
    viewer = await env["account"]("gov-viewer@example.com")
    invite = await owner.post(
        f"/orgs/{org['id']}/invitations", json={"email": "gov-viewer@example.com", "role": "viewer"}
    )
    assert invite.status_code == 202
    token = next(t for e, t, _ in env["sent"] if e == "gov-viewer@example.com")
    assert (await viewer.post("/orgs/invitations/accept", json={"token": token})).status_code == 200
    assert (await viewer.get(f"/audit/events?workspace_id={workspace}")).status_code == 403
    assert (
        await viewer.put(f"/retention?workspace_id={workspace}", json={"audit_days": 90})
    ).status_code == 403

    # The owner (implicitly admin) can, and the audit log already shows org + membership events.
    events = (await owner.get(f"/audit/events?workspace_id={workspace}")).json()
    actions = {event["action"] for event in events}
    assert {"org.created", "member.invited", "member.joined"} <= actions
    verify = (await owner.get(f"/audit/verify?workspace_id={workspace}")).json()
    assert verify["ok"] is True

    # Retention + crawl-allowlist round-trip, and each write is itself audited.
    assert (
        await owner.put(f"/retention?workspace_id={workspace}", json={"audit_days": 90})
    ).status_code == 200
    assert (await owner.get(f"/retention?workspace_id={workspace}")).json()["audit_days"] == 90
    assert (
        await owner.put(
            f"/crawl-allowlist?workspace_id={workspace}", json={"domains": ["Example.com", " "]}
        )
    ).json()["domains"] == ["example.com"]
    assert (await owner.get(f"/crawl-allowlist?workspace_id={workspace}")).json()["domains"] == [
        "example.com"
    ]
    after = (await owner.get(f"/audit/events?workspace_id={workspace}")).json()
    assert {"retention.policy_changed", "crawl_allowlist_changed"} <= {e["action"] for e in after}

    for client in (owner, viewer):
        await client.aclose()


@pytest.mark.asyncio
async def test_login_is_audited_against_the_personal_organization(env: dict[str, Any]) -> None:
    client = await env["account"]("audited-login@example.com")
    me = (await client.get("/auth/me")).json()
    workspace = me["workspace_id"]
    events = (await client.get(f"/audit/events?workspace_id={workspace}")).json()
    assert any(e["action"] == "auth.login" for e in events)
    await client.aclose()
