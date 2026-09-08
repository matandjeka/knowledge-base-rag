"""PostgreSQL jobs, idempotency, and workspace serialization."""

import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import JSON, Column, Integer, String, Table, delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.persistence.metadata import metadata

jobs = Table(
    "rag_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("workspace_id", String(64), nullable=False, index=True),
    Column("spec", JSON, nullable=False),
    Column("checkpoint", JSON, nullable=False),
    Column("step", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False),
    Column("created", Integer, nullable=False),
    Column("error", String(256)),
)
workspace_jobs = Table(
    "rag_workspace_jobs",
    metadata,
    Column("workspace_id", String(64), primary_key=True),
    Column("job_id", String(36), nullable=False, unique=True),
)


class JobRepository:
    """Persist jobs before dispatch; a lost dispatch can be explicitly resumed."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(self, workspace_id: str, spec: dict[str, Any], job_id: str) -> dict[str, Any]:
        async with self.engine.begin() as conn:
            existing = (
                (await conn.execute(select(jobs).where(jobs.c.id == job_id))).mappings().first()
            )
            if existing:
                if existing["workspace_id"] != workspace_id or existing["spec"] != spec:
                    raise HTTPException(409, "Idempotency key already used.")
                return dict(existing)
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        insert(workspace_jobs).values(workspace_id=workspace_id, job_id=job_id)
                    )
            except IntegrityError:
                raise HTTPException(
                    409, "Another indexing job is active in this workspace."
                ) from None
            checkpoint = {
                "stage": "parse",
                "source_id": spec.get("source_id", str(uuid4())),
                "generation_id": str(uuid4()),
            }
            value = {
                "id": job_id,
                "workspace_id": workspace_id,
                "spec": spec,
                "checkpoint": checkpoint,
                "step": 0,
                "status": "queued",
                "created": int(time.time()),
                "error": None,
            }
            await conn.execute(insert(jobs).values(**value))
            return value

    async def get(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(jobs).where(jobs.c.id == job_id, jobs.c.workspace_id == workspace_id)
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise HTTPException(404, "Job not found.")
        return dict(row)

    async def list(self, workspace_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(jobs)
                        .where(jobs.c.workspace_id == workspace_id)
                        .order_by(jobs.c.created.desc())
                        .limit(50)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def cancel(self, workspace_id: str, job_id: str) -> None:
        from sqlalchemy import update

        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(jobs)
                        .where(jobs.c.id == job_id, jobs.c.workspace_id == workspace_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise HTTPException(404, "Job not found.")
            if (
                row["checkpoint"]["stage"] in {"publish", "graph_publish"}
                or row["status"] == "complete"
            ):
                raise HTTPException(409, "Publication has begun; allow this job to finish.")
            await conn.execute(update(jobs).where(jobs.c.id == job_id).values(status="cancelled"))
            await conn.execute(delete(workspace_jobs).where(workspace_jobs.c.job_id == job_id))
