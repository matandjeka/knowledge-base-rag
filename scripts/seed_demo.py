"""One-command local demo: launch the API, serve the demo site, ingest all five source types.

    uv run python scripts/seed_demo.py

Leaves the API (`:8000`) and the static site (`:8900`) running. In another terminal run
`uv run streamlit run ui/streamlit_app.py` and open the Chat tab. Sample questions and the
walkthrough are in ``demo/GUIDE.md``. Press Ctrl-C here to stop everything.

Options:
    --api-url URL   Use an already-running API instead of launching one.
    --skip-graph    Skip knowledge-graph extraction (which needs OPENAI_API_KEY).
    --ask           After ingesting, run a few sample questions and print the cited answers.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
SITE_PORT = 8900
API_PORT = 8000
WORKSPACE = "local"

sys.path.insert(0, str(ROOT))
from scripts.build_demo_assets import build  # noqa: E402


def _serve_site() -> http.server.ThreadingHTTPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DEMO / "site"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", SITE_PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _launch_api(database: Path) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "WEBSITE_ALLOWED_PRIVATE_HOSTS": '["127.0.0.1"]',
        "SQL_ALLOW_SQLITE": "true",
        "MERIDIAN_DEMO_DB": f"sqlite:///{database}",
        "OMP_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(API_PORT)],
        env=env,
        cwd=str(ROOT),
    )


def _wait_for_health(base_url: str, *, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"API at {base_url} did not become healthy within {timeout:.0f}s")


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _ingest(client: httpx.Client, assets: dict[str, Path], *, skip_graph: bool) -> None:
    def step(label: str) -> None:
        print(f"  → {label}", flush=True)

    step("PDF: HR & finance policy handbook")
    _post_file(client, "/sources/pdf", assets["hr-and-finance-policies"], "application/pdf")
    step("PDF: information security program")
    _post_file(client, "/sources/pdf", assets["security-program"], "application/pdf")

    step(f"Website: crawling http://127.0.0.1:{SITE_PORT}/ (service catalog)")
    _check(
        client.post(
            "/sources/website",
            json={
                "workspace_id": WORKSPACE,
                "url": f"http://127.0.0.1:{SITE_PORT}/",
                "crawl_same_domain": True,
                "page_limit": 6,
            },
        )
    )

    step("CSV: parts inventory")
    _post_csv(
        client,
        DEMO / "sources" / "inventory.csv",
        text_columns=["sku", "item", "warehouse"],
        metadata_columns=["quantity", "unit_cost_usd"],
        row_id_column="sku",
    )
    step("CSV: vendor contracts")
    _post_csv(
        client,
        DEMO / "sources" / "contracts.csv",
        text_columns=["contract_id", "vendor", "category", "renews_on"],
        metadata_columns=["annual_value_usd"],
        row_id_column="contract_id",
    )

    step("Database: Meridian analytics (read-only SQLite)")
    database = _check(
        client.post(
            "/sources/database",
            json={
                "workspace_id": WORKSPACE,
                "name": "Meridian analytics",
                "secret_env_var": "MERIDIAN_DEMO_DB",
                "dialect": "sqlite",
                "isolation_mode": "dedicated",
                "tables": [
                    {"table_name": t}
                    for t in (
                        "regions",
                        "departments",
                        "products",
                        "customers",
                        "tickets",
                        "orders",
                        "quarterly_sales",
                    )
                ],
            },
        ),
        allow_status={201, 422},
    )
    if database is None:
        print("     (skipped — SQL retrieval needs OPENAI_API_KEY + SQL_GENERATION_MODEL)")

    if skip_graph:
        return
    step("Knowledge graph: extracting entities and relationships")
    response = client.post("/graph/index", json={"workspace_id": WORKSPACE})
    if response.status_code == 200:
        result = response.json()
        print(
            f"     {result.get('entity_count', '?')} entities, "
            f"{result.get('relationship_count', '?')} relationships"
        )
    else:
        print("     (skipped — graph extraction needs OPENAI_API_KEY + GRAPH_EXTRACTION_MODEL)")


def _post_file(client: httpx.Client, path: str, file: Path, content_type: str) -> None:
    _check(
        client.post(
            path,
            data={"workspace_id": WORKSPACE},
            files={"file": (file.name, file.read_bytes(), content_type)},
            timeout=180,
        )
    )


def _post_csv(
    client: httpx.Client,
    file: Path,
    *,
    text_columns: list[str],
    metadata_columns: list[str],
    row_id_column: str,
) -> None:
    _check(
        client.post(
            "/sources/csv",
            data={
                "workspace_id": WORKSPACE,
                "row_id_column": row_id_column,
                "text_columns": text_columns,
                "metadata_columns": metadata_columns,
            },
            files={"file": (file.name, file.read_bytes(), "text/csv")},
            timeout=180,
        )
    )


def _check(response: httpx.Response, *, allow_status: set[int] | None = None) -> dict | None:
    allowed = allow_status or {200, 201}
    if response.status_code not in allowed:
        raise SystemExit(
            f"{response.request.method} {response.request.url} -> "
            f"{response.status_code}: {response.text[:300]}"
        )
    if response.status_code >= 300 or (allow_status and response.status_code == 422):
        return None
    return response.json()


# (question, retrieval_mode) — auto routing handles most; a couple are pinned for the tour.
_SAMPLE_QUESTIONS = [
    ("What does HR-402 require?", "lexical"),
    ("What does API-GW-17 own?", "auto"),
    ("Where is SKU_88Z stored?", "lexical"),
    ("How long are records retained?", "sentence_window"),
    ("Who owns the gateway service?", "auto"),
]


def _ask(client: httpx.Client) -> None:
    print("\nSample answers ------------------------------------------------------------")
    for question, mode in _SAMPLE_QUESTIONS:
        try:
            response = client.post(
                "/query",
                json={"question": question, "workspace_id": WORKSPACE, "retrieval_mode": mode},
                timeout=120,
            )
            body = response.json()
        except httpx.HTTPError as error:
            print(f"\nQ: {question}\n   (error: {error})")
            continue
        answer = body.get("answer", "").replace("\n", " ")
        citations = ", ".join(
            f"{c['citation_id']}={c.get('source_title', '?')}:{c.get('locator', '?')}"
            for c in body.get("citations", [])
        )
        print(f"\nQ: {question}\nA: {answer}\n   [{citations or 'no citations'}]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--skip-graph", action="store_true")
    parser.add_argument("--ask", action="store_true")
    parser.add_argument(
        "--once", action="store_true", help="exit after ingesting instead of staying resident"
    )
    args = parser.parse_args()

    print("Building demo assets (PDFs + SQLite) ...", flush=True)
    assets = build()

    site = _serve_site()
    print(f"Serving demo site on http://127.0.0.1:{SITE_PORT}/", flush=True)

    api_process: subprocess.Popen[bytes] | None = None
    base_url = args.api_url or f"http://127.0.0.1:{API_PORT}"
    if args.api_url is None:
        if _port_open(API_PORT):
            raise SystemExit(
                f"Port {API_PORT} is already in use. Pass --api-url http://127.0.0.1:{API_PORT} "
                "to reuse it (it must run with SQL_ALLOW_SQLITE, WEBSITE_ALLOWED_PRIVATE_HOSTS, "
                "and MERIDIAN_DEMO_DB set), or free the port."
            )
        print("Launching API (uvicorn app.main:app) ...", flush=True)
        api_process = _launch_api(assets["database"])

    try:
        _wait_for_health(base_url)
        print("API is healthy. Ingesting sources (CPU embedding is slow — allow a few minutes):")
        with httpx.Client(base_url=base_url, timeout=600) as client:
            _ingest(client, assets, skip_graph=args.skip_graph)
            sources = client.get(f"/sources?workspace_id={WORKSPACE}").json()
            print(f"\n{len(sources)} source(s) registered:")
            for source in sources:
                kind = source["config"]["source_type"]
                print(f"  - {source['name']}  ({kind}, {source['status']})")
            if args.ask:
                _ask(client)

        if args.once:
            print("\nIngest complete (--once). Stopping the API and demo site.")
            return 0
        print(
            "\n"
            "Demo is ready ----------------------------------------------------------\n"
            f"  API:         {base_url}\n"
            f"  Demo site:   http://127.0.0.1:{SITE_PORT}/\n"
            "  Next:        in another terminal, run\n"
            "                 uv run streamlit run ui/streamlit_app.py\n"
            "               then open http://localhost:8501 and use the Chat tab.\n"
            "  Questions:   demo/GUIDE.md\n"
            "\nLeave this terminal open. Press Ctrl-C to stop the API and the demo site."
        )
        while True:
            if api_process is not None and api_process.poll() is not None:
                raise SystemExit("The API process exited unexpectedly.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping ...")
    finally:
        site.shutdown()
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api_process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
