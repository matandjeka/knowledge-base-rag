"""Run the API locally with authentication enabled, backed by an embedded PostgreSQL.

    uv run python scripts/local_auth_stack.py

No Docker or system PostgreSQL required: this starts a private PostgreSQL 16 in
``.localdb/`` (via the ``pgserver`` wheel), runs the Alembic migrations, then launches
``uvicorn app.main:app`` on :8000 with ``AUTH_ENABLED=true``. Email verification is
disabled so accounts registered through the Next.js sign-in screen are usable
immediately. Press Ctrl-C to stop the API and the database.

The data directory persists between runs, so accounts survive a restart. Delete
``.localdb/`` to start from an empty database.

File uploads (Next.js "Add source" for PDF/CSV) additionally need Vercel Blob. Put a
store token in the repo-root ``.env`` as ``BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...``
(https://vercel.com/dashboard/stores -> create a Blob store -> ".env.local" tab). When
that variable is present this launcher enables ``vercel_blob`` source/lexical storage,
points the workflow dispatcher at the local Next.js server, and writes a matching
``WORKFLOW_SECRET`` into ``frontend/.env.local``.

Options:
    --port PORT   API port (default 8000).
    --once        Run migrations and exit without launching the API.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pgserver

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".localdb"
SECRET_FILE = DATA_DIR / "jwt_secret"
WORKFLOW_SECRET_FILE = DATA_DIR / "workflow_secret"
FRONTEND_ENV = ROOT / "frontend" / ".env.local"


def _database_url() -> str:
    server = pgserver.get_server(DATA_DIR)  # type: ignore[attr-defined]
    for statement in (
        "CREATE ROLE rag LOGIN PASSWORD 'rag' SUPERUSER",
        "CREATE DATABASE rag OWNER rag",
    ):
        try:
            server.psql(statement + ";")
        except Exception as error:
            # "already exists" is expected on every run after the first.
            if "already exists" not in str(error):
                raise
    return f"postgresql+asyncpg://rag:rag@/rag?host={DATA_DIR}"


def _persisted_secret(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


def _dotenv_value(name: str) -> str | None:
    """Read one key from the repo-root .env without importing the settings model."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{name}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip("\"'") or None
    return None


def _sync_frontend_env(values: dict[str, str]) -> None:
    """Mirror the given keys into frontend/.env.local so `next dev` and the API agree.

    The Blob token and workflow secret are single-sourced from the repo-root .env / the
    launcher; the Next.js server needs them too for the upload handshake and dispatch call.
    """
    if not FRONTEND_ENV.exists():
        return
    lines = FRONTEND_ENV.read_text().splitlines()
    for key, value in values.items():
        for index, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[index] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
    FRONTEND_ENV.write_text("\n".join(lines) + "\n")


def _environment(database_url: str) -> dict[str, str]:
    env = {
        "METADATA_STORE_BACKEND": "postgresql",
        "GRAPH_STORE_BACKEND": "postgresql",
        "EMBEDDING_PROVIDER": "openai",
        "OPENAI_EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_DIMENSION": "1024",
        "RERANKER_PROVIDER": "none",
        "METADATA_DATABASE_URL": database_url,
        "AUTH_ENABLED": "true",
        "AUTH_REQUIRE_VERIFICATION": "false",
        "JWT_SECRET": _persisted_secret(SECRET_FILE),
        "FRONTEND_URL": "http://localhost:3000",
        "OMP_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    blob_token = _dotenv_value("BLOB_READ_WRITE_TOKEN")
    if blob_token:
        workflow_secret = _persisted_secret(WORKFLOW_SECRET_FILE)
        _sync_frontend_env(
            {"BLOB_READ_WRITE_TOKEN": blob_token, "WORKFLOW_SECRET": workflow_secret}
        )
        env.update(
            {
                "SOURCE_STORAGE_BACKEND": "vercel_blob",
                "LEXICAL_STORE_BACKEND": "vercel_blob",
                "BLOB_READ_WRITE_TOKEN": blob_token,
                "WORKFLOW_SECRET": workflow_secret,
                "WORKFLOW_DISPATCH_URL": "http://localhost:3000/api/workflows/start",
            }
        )
        print("Vercel Blob token found: enabling file uploads and workflow dispatch.", flush=True)
    else:
        print(
            "No BLOB_READ_WRITE_TOKEN in .env: file uploads in the Next.js app stay disabled.",
            flush=True,
        )
    return env


def _run(command: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(command, cwd=str(ROOT), env={**os.environ, **env})
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(command)} exited {result.returncode}")


def _wait_for_health(base_url: str, *, timeout: float = 240.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"API at {base_url} did not become healthy within {timeout:.0f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    print("Starting embedded PostgreSQL in .localdb/ ...", flush=True)
    database_url = _database_url()
    env = _environment(database_url)

    print("Applying database migrations (alembic upgrade head) ...", flush=True)
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env)

    if args.once:
        print("Migrations applied (--once). Stopping.")
        return 0

    base_url = f"http://127.0.0.1:{args.port}"
    print(f"Launching API with authentication on {base_url} ...", flush=True)
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(args.port)],
        cwd=str(ROOT),
        env={**os.environ, **env},
    )
    try:
        _wait_for_health(base_url)
        print(
            "\n"
            "Auth stack is ready --------------------------------------------------\n"
            f"  API:          {base_url}  (AUTH_ENABLED=true, email verification off)\n"
            "  Frontend:     the Next.js proxy already points here via frontend/.env.local\n"
            "  Sign in:      open http://localhost:3000 and use 'Create account'\n"
            "\nLeave this terminal open. Press Ctrl-C to stop the API and the database."
        )
        while api.poll() is None:
            time.sleep(1)
        raise SystemExit("The API process exited unexpectedly.")
    except KeyboardInterrupt:
        print("\nStopping ...")
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
