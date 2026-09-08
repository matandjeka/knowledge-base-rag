"""Database-backed authentication with hashed passwords and one-use refresh tokens."""

import asyncio
import hashlib
import secrets
import time
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, String, Table, delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.persistence.metadata import metadata

users = Table(
    "rag_users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", String(512), nullable=False),
    Column("workspace_id", String(64), nullable=False, unique=True),
    Column("verified", Boolean, nullable=False, default=False),
)
sessions = Table(
    "rag_sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("refresh_hash", String(64), nullable=False, unique=True),
    Column("expires", Integer, nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
)
refresh_history = Table(
    "rag_refresh_history",
    metadata,
    Column("hash", String(64), primary_key=True),
    Column("session_id", String(36), nullable=False),
)
limits = Table(
    "rag_auth_limits",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("count", Integer, nullable=False),
    Column("expires", Integer, nullable=False),
)
email_tokens = Table(
    "rag_email_tokens",
    metadata,
    Column("hash", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("purpose", String(16), nullable=False),
    Column("expires", Integer, nullable=False),
)
_hasher = PasswordHasher()
_dummy = _hasher.hash("constant-timing-dummy-password")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthService:
    """JWT claims are cross-checked against active sessions and account ownership."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    async def throttle(self, key: str, *, maximum: int = 10) -> None:
        bucket = int(time.time()) // 900
        hashed = digest(f"{key}:{bucket}")
        async with self.engine.begin() as conn:
            # Savepoint handles concurrent creation without aborting the outer transaction.
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        insert(limits).values(key=hashed, count=0, expires=(bucket + 1) * 900)
                    )
            except IntegrityError:
                pass
            count = (
                await conn.execute(
                    update(limits)
                    .where(limits.c.key == hashed)
                    .values(count=limits.c.count + 1)
                    .returning(limits.c.count)
                )
            ).scalar_one()
            await conn.execute(delete(limits).where(limits.c.expires < int(time.time())))
        if count > maximum:
            raise HTTPException(429, "Too many attempts. Try again later.")

    async def register(self, email: str, password: str) -> dict[str, Any]:
        password_hash = await asyncio.to_thread(_hasher.hash, password)
        user = {
            "id": str(uuid4()),
            "email": email.lower(),
            "password_hash": password_hash,
            "workspace_id": str(uuid4()),
            "verified": not self.settings.auth_require_verification,
        }
        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(users).values(**user))
        except IntegrityError:
            raise HTTPException(409, "Unable to register this email address.") from None
        return user

    async def login(self, email: str, password: str) -> tuple[dict[str, Any], str]:
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(select(users).where(users.c.email == email.lower())))
                .mappings()
                .first()
            )
        try:
            await asyncio.to_thread(
                _hasher.verify, row["password_hash"] if row else _dummy, password
            )
        except VerificationError:
            raise HTTPException(401, "Invalid email or password.") from None
        if row is None:
            raise HTTPException(401, "Invalid email or password.")
        if not row["verified"]:
            raise HTTPException(403, "Verify your email before signing in.")
        user = dict(row)
        session_id, refresh = str(uuid4()), secrets.token_urlsafe(48)
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(sessions).values(
                    id=session_id,
                    user_id=user["id"],
                    refresh_hash=digest(refresh),
                    expires=int(time.time()) + 30 * 86400,
                    revoked=False,
                )
            )
        return self._tokens(user, session_id), refresh

    def _tokens(self, user: dict[str, Any], session_id: str) -> dict[str, Any]:
        assert self.settings.jwt_secret is not None
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": user["id"],
                "sid": session_id,
                "workspace_id": user["workspace_id"],
                "iat": now,
                "exp": now + 900,
                "iss": self.settings.jwt_issuer,
                "aud": "rag-api",
                "type": "access",
            },
            self.settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": 900,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "workspace_id": user["workspace_id"],
            },
        }

    async def authenticate(self, token: str) -> dict[str, Any]:
        try:
            assert self.settings.jwt_secret is not None
            claims = jwt.decode(
                token,
                self.settings.jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                issuer=self.settings.jwt_issuer,
                audience="rag-api",
                options={"require": ["exp", "iat", "sub", "sid", "workspace_id", "type"]},
            )
            if claims["type"] != "access":
                raise ValueError("Incorrect token type")
        except (jwt.InvalidTokenError, ValueError, AssertionError):
            raise HTTPException(401, "Session is invalid or expired.") from None
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(users)
                        .join(sessions, sessions.c.user_id == users.c.id)
                        .where(
                            sessions.c.id == claims["sid"],
                            users.c.id == claims["sub"],
                            users.c.workspace_id == claims["workspace_id"],
                            users.c.verified.is_(True),
                            sessions.c.revoked.is_(False),
                            sessions.c.expires > int(time.time()),
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(401, "Session is invalid or expired.")
        return {"id": row["id"], "email": row["email"], "workspace_id": row["workspace_id"]}

    async def refresh(self, token: str) -> tuple[dict[str, Any], str]:
        hashed, replacement = digest(token), secrets.token_urlsafe(48)
        reused = False
        result: dict[str, Any] | None = None
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(sessions).where(sessions.c.refresh_hash == hashed).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row and not row["revoked"] and row["expires"] > int(time.time()):
                await conn.execute(
                    insert(refresh_history).values(hash=hashed, session_id=row["id"])
                )
                await conn.execute(
                    update(sessions)
                    .where(sessions.c.id == row["id"])
                    .values(refresh_hash=digest(replacement))
                )
                user = (
                    (await conn.execute(select(users).where(users.c.id == row["user_id"])))
                    .mappings()
                    .one()
                )
                result = self._tokens(dict(user), row["id"])
            else:
                old = (
                    await conn.execute(
                        select(refresh_history.c.session_id).where(refresh_history.c.hash == hashed)
                    )
                ).scalar_one_or_none()
                if old:
                    await conn.execute(
                        update(sessions).where(sessions.c.id == old).values(revoked=True)
                    )
                    reused = True
        if result is None or reused:
            raise HTTPException(401, "Refresh token is invalid or expired.")
        return result, replacement

    async def logout(self, token: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(sessions)
                .where(sessions.c.refresh_hash == digest(token))
                .values(revoked=True)
            )

    async def delete_unprovisioned_user(self, user_id: str) -> None:
        """Remove a freshly created account when organization provisioning failed."""
        async with self.engine.begin() as conn:
            await conn.execute(delete(users).where(users.c.id == user_id))

    async def issue_email_token(self, user_id: str, purpose: str) -> str:
        token = secrets.token_urlsafe(48)
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(email_tokens).values(
                    hash=digest(token),
                    user_id=user_id,
                    purpose=purpose,
                    expires=int(time.time()) + 3600,
                )
            )
        return token

    async def consume_email_token(
        self, token: str, purpose: str, password: str | None = None
    ) -> None:
        password_hash = await asyncio.to_thread(_hasher.hash, password) if password else None
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    delete(email_tokens)
                    .where(
                        email_tokens.c.hash == digest(token),
                        email_tokens.c.purpose == purpose,
                        email_tokens.c.expires > int(time.time()),
                    )
                    .returning(email_tokens.c.user_id)
                )
            ).first()
            if row is None:
                raise HTTPException(400, "Link is invalid or expired.")
            values = {"password_hash": password_hash} if password_hash else {"verified": True}
            await conn.execute(update(users).where(users.c.id == row[0]).values(**values))
            if password_hash:
                await conn.execute(
                    update(sessions).where(sessions.c.user_id == row[0]).values(revoked=True)
                )
