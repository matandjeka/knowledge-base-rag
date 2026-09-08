"""Registration and secure session endpoints; email delivery uses a configured webhook."""

from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.dependencies import get_metadata_engine
from app.auth.service import AuthService, users
from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["authentication"])


def get_auth_service() -> AuthService:
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(503, "Authentication is not configured.")
    return AuthService(get_metadata_engine(), settings)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        value = value.strip().lower()
        if (
            value.count("@") != 1
            or "." not in value.split("@")[1]
            or any(c.isspace() for c in value)
        ):
            raise ValueError("Enter a valid email address")
        return value


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class LinkRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str | None = Field(default=None, min_length=12, max_length=128)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().auth_allowed_origins:
        raise HTTPException(403, "Origin is not allowed.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site session request rejected.")


def cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "rag_refresh",
        token,
        max_age=30 * 86400,
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="strict",
        path="/api/auth",
    )
    response.headers["Cache-Control"] = "no-store"


async def send_link(email: str, token: str, purpose: str) -> None:
    settings = get_settings()
    if not settings.auth_email_webhook_url or not settings.auth_email_webhook_secret:
        raise HTTPException(503, "Email delivery is not configured.")
    url = settings.frontend_url.rstrip("/") + "/?" + urlencode({"action": purpose, "token": token})
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            result = await client.post(
                settings.auth_email_webhook_url,
                headers={
                    "Authorization": "Bearer "
                    + settings.auth_email_webhook_secret.get_secret_value()
                },
                json={"to": email, "template": purpose, "url": url},
            )
            result.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(503, "Email delivery failed. Request a new link shortly.") from None


@router.post("/register", status_code=201)
async def register(
    body: Credentials, request: Request, auth: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    check_origin(request)
    await auth.throttle("register:" + (request.client.host if request.client else "unknown"))
    user = await auth.register(body.email, body.password)
    if get_settings().auth_require_verification:
        token = await auth.issue_email_token(user["id"], "verify")
        await send_link(body.email, token, "verify")
    return {
        "message": "Account created. Verify your email, then sign in."
        if get_settings().auth_require_verification
        else "Account created. Sign in to continue."
    }


@router.post("/login")
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, Any]:
    check_origin(request)
    await auth.throttle("login:" + body.email)
    await auth.throttle(
        "login-ip:" + (request.client.host if request.client else "unknown"), maximum=50
    )
    result, refresh = await auth.login(body.email, body.password)
    cookie(response, refresh)
    return result


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    rag_refresh: str = Cookie(default=""),
) -> dict[str, Any]:
    check_origin(request)
    result, token = await auth.refresh(rag_refresh)
    cookie(response, token)
    return result


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    rag_refresh: str = Cookie(default=""),
) -> dict[str, bool]:
    check_origin(request)
    await auth.logout(rag_refresh)
    response.delete_cookie(
        "rag_refresh",
        path="/api/auth",
        secure=get_settings().app_env == "production",
        httponly=True,
        samesite="strict",
    )
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return dict(user)


@router.post("/request-link")
async def request_link(
    body: EmailRequest, request: Request, auth: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    check_origin(request)
    await auth.throttle("email:" + body.email.lower(), maximum=3)
    await auth.throttle("email-ip:" + (request.client.host if request.client else "unknown"))
    async with auth.engine.connect() as conn:
        row = (
            (await conn.execute(select(users).where(users.c.email == body.email.strip().lower())))
            .mappings()
            .first()
        )
    if row:
        purpose = "reset" if row["verified"] else "verify"
        token = await auth.issue_email_token(row["id"], purpose)
        await send_link(row["email"], token, purpose)
    return {"message": "If the account exists, an email will arrive shortly."}


@router.post("/verify")
async def verify(
    body: LinkRequest, auth: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, bool]:
    await auth.consume_email_token(body.token, "verify")
    return {"ok": True}


@router.post("/reset")
async def reset(
    body: LinkRequest, auth: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, bool]:
    if body.password is None:
        raise HTTPException(422, "A new password is required.")
    await auth.consume_email_token(body.token, "reset", body.password)
    return {"ok": True}
