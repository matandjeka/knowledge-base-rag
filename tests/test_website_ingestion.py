"""Website crawling, ingestion, network-safety, and API tests."""

from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_source_repository,
    get_source_storage,
    get_website_ingestion_service,
)
from app.core.exceptions import IngestionError, WebsiteValidationError
from app.ingestion.website import (
    FetchedResponse,
    SafeHttpFetcher,
    WebsiteCrawler,
    normalize_url,
    validate_public_url,
)
from app.ingestion.website_service import WebsiteIngestionService
from app.main import app
from app.models import Source, SourceStatus, SourceType
from app.repositories import InMemorySourceRepository
from app.storage import LocalSourceStorage
from tests.fakes import RecordingSourceIndexer


class _FakeFetcher:
    def __init__(self, responses: Mapping[str, tuple[str, str] | Exception]) -> None:
        self.responses = dict(responses)
        self.requested: list[str] = []

    async def fetch(self, url: str, accepted_content_types: frozenset[str]) -> FetchedResponse:
        del accepted_content_types
        self.requested.append(url)
        response = self.responses.get(url)
        if response is None:
            raise WebsiteValidationError(f"Missing fixture for {url}")
        if isinstance(response, Exception):
            raise response
        final_url, content = response
        return FetchedResponse(final_url, content.encode(), "text/html")


class _FailingWebsiteReadyStorage(LocalSourceStorage):
    async def save_source(self, source: Source) -> None:
        if source.status is SourceStatus.READY:
            raise OSError("ready metadata unavailable")
        await super().save_source(source)


def _html(title: str, body: str, links: tuple[str, ...] = ()) -> str:
    anchors = "".join(f'<a href="{link}">Next</a>' for link in links)
    return (
        f"<html><head><title>{title}</title></head><body><main>{body}</main>{anchors}</body></html>"
    )


def _crawler(
    responses: Mapping[str, tuple[str, str] | Exception],
) -> tuple[WebsiteCrawler, _FakeFetcher]:
    fetcher = _FakeFetcher(responses)
    crawler = WebsiteCrawler(fetcher, user_agent="TestCrawler", default_delay_seconds=0)
    return crawler, fetcher


def _service(
    tmp_path: Path, responses: Mapping[str, tuple[str, str] | Exception]
) -> tuple[WebsiteIngestionService, InMemorySourceRepository, LocalSourceStorage]:
    crawler, _ = _crawler(responses)
    repository = InMemorySourceRepository()
    storage = LocalSourceStorage(tmp_path / "data")
    service = WebsiteIngestionService(
        repository,
        storage,
        crawler,
        chunk_size=100,
        chunk_overlap=10,
        max_pages=20,
        indexer=RecordingSourceIndexer(),
    )
    return service, repository, storage


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "http://user:password@example.com/",
        "https://example.com:8443/",
        "https:///missing-host",
    ],
)
def test_url_normalization_rejects_unsafe_shapes(url: str) -> None:
    with pytest.raises(WebsiteValidationError):
        normalize_url(url)


def test_url_normalization_preserves_query_and_removes_fragment() -> None:
    assert (
        normalize_url("HTTPS://Example.COM:443/docs?q=policy#section")
        == "https://example.com/docs?q=policy"
    )


@pytest.mark.asyncio
async def test_network_validation_rejects_any_non_public_dns_result() -> None:
    async def resolver(_: str, __: int) -> tuple[str, ...]:
        return ("93.184.216.34", "127.0.0.1")

    with pytest.raises(WebsiteValidationError, match="non-public"):
        await validate_public_url("https://example.com/", resolver)


@pytest.mark.asyncio
async def test_safe_fetcher_rejects_private_redirect_before_following() -> None:
    requests: list[httpx.Request] = []

    async def resolver(host: str, _: int) -> tuple[str, ...]:
        return ("127.0.0.1",) if host == "localhost" else ("93.184.216.34",)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://localhost/internal"})

    fetcher = SafeHttpFetcher(
        user_agent="TestCrawler",
        timeout_seconds=1,
        max_response_bytes=1024,
        max_redirects=2,
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(WebsiteValidationError, match="non-public"):
        await fetcher.fetch("https://example.com/", frozenset({"text/html"}))

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_safe_fetcher_pins_each_connection_and_validates_redirects() -> None:
    resolutions: list[str] = []

    async def resolver(host: str, _: int) -> tuple[str, ...]:
        resolutions.append(host)
        return ("93.184.216.34",) if host == "example.com" else ("93.184.216.35",)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            assert request.headers["host"] == "example.com"
            assert request.extensions["sni_hostname"] == "example.com"
            return httpx.Response(302, headers={"location": "https://www.example.net/final"})
        assert request.url.host == "93.184.216.35"
        assert request.headers["host"] == "www.example.net"
        assert request.extensions["sni_hostname"] == "www.example.net"
        return httpx.Response(200, headers={"content-type": "text/html"}, text="safe")

    fetcher = SafeHttpFetcher(
        user_agent="TestCrawler",
        timeout_seconds=1,
        max_response_bytes=1024,
        max_redirects=2,
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )

    response = await fetcher.fetch("https://example.com/start", frozenset({"text/html"}))

    assert response.url == "https://www.example.net/final"
    assert resolutions == ["example.com", "www.example.net"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "content", "message"),
    [
        ({"content-type": "application/pdf"}, b"document", "content type"),
        (
            {"content-type": "text/html", "content-length": "invalid"},
            b"short",
            "invalid Content-Length",
        ),
        (
            {"content-type": "text/html", "content-length": "100"},
            b"short",
            "size limit",
        ),
        ({"content-type": "text/html"}, b"too-large", "size limit"),
    ],
)
async def test_safe_fetcher_rejects_invalid_or_oversized_responses(
    headers: dict[str, str], content: bytes, message: str
) -> None:
    async def resolver(_: str, __: int) -> tuple[str, ...]:
        return ("93.184.216.34",)

    fetcher = SafeHttpFetcher(
        user_agent="TestCrawler",
        timeout_seconds=1,
        max_response_bytes=5,
        max_redirects=0,
        resolver=resolver,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=headers, content=content)
        ),
    )

    with pytest.raises(WebsiteValidationError, match=message):
        await fetcher.fetch("https://example.com/", frozenset({"text/html"}))


@pytest.mark.asyncio
async def test_crawler_respects_robots_for_seed() -> None:
    crawler, _ = _crawler(
        {
            "https://example.com/robots.txt": (
                "https://example.com/robots.txt",
                "User-agent: *\nDisallow: /private",
            )
        }
    )

    with pytest.raises(WebsiteValidationError, match="disallowed"):
        await crawler.crawl("https://example.com/private", crawl_same_domain=False, page_limit=1)


@pytest.mark.asyncio
async def test_crawler_is_breadth_first_same_host_bounded_and_deduplicated() -> None:
    crawler, fetcher = _crawler(
        {
            "https://example.com/robots.txt": (
                "https://example.com/robots.txt",
                "User-agent: *\nAllow: /",
            ),
            "https://example.com/": (
                "https://example.com/",
                _html(
                    "Home",
                    "Welcome to the employee policy knowledge base.",
                    ("/a", "/b", "https://sub.example.com/out"),
                ),
            ),
            "https://example.com/a": (
                "https://example.com/a",
                _html("A", "Vacation requests require two weeks notice.", ("/deep",)),
            ),
            "https://example.com/b": (
                "https://example.com/b",
                _html("A", "Vacation requests require two weeks notice.", ("/deep",)),
            ),
        }
    )

    crawl = await crawler.crawl("https://example.com", crawl_same_domain=True, page_limit=3)

    assert [page.url for page in crawl.pages] == ["https://example.com/", "https://example.com/a"]
    assert crawl.manifest.fetched_urls == [
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert crawl.manifest.failures[0].reason == "Duplicate page content"
    assert "https://sub.example.com/out" not in fetcher.requested


@pytest.mark.asyncio
async def test_child_failure_is_nonfatal_and_recorded() -> None:
    crawler, _ = _crawler(
        {
            "https://example.com/robots.txt": (
                "https://example.com/robots.txt",
                "User-agent: *\nAllow: /",
            ),
            "https://example.com/": (
                "https://example.com/",
                _html("Home", "Useful public policy content.", ("/broken",)),
            ),
            "https://example.com/broken": WebsiteValidationError("HTTP request failed"),
        }
    )

    crawl = await crawler.crawl("https://example.com/", crawl_same_domain=True, page_limit=2)

    assert len(crawl.pages) == 1
    assert crawl.manifest.failures[0].url == "https://example.com/broken"


@pytest.mark.asyncio
async def test_same_domain_canonical_url_is_preserved() -> None:
    crawler, _ = _crawler(
        {
            "https://example.com/robots.txt": (
                "https://example.com/robots.txt",
                "User-agent: *\nAllow: /",
            ),
            "https://example.com/start": (
                "https://example.com/start",
                (
                    '<html><head><title>Policy</title><link rel="canonical" '
                    'href="/policy"></head><body>Canonical policy content.</body></html>'
                ),
            ),
        }
    )

    crawl = await crawler.crawl("https://example.com/start", crawl_same_domain=False, page_limit=1)

    assert crawl.pages[0].url == "https://example.com/policy"


@pytest.mark.asyncio
async def test_ingestion_persists_url_chunks_metadata_and_manifest(tmp_path: Path) -> None:
    responses: dict[str, tuple[str, str] | Exception] = {
        "https://example.com/robots.txt": (
            "https://example.com/robots.txt",
            "User-agent: *\nAllow: /",
        ),
        "https://example.com/": (
            "https://example.com/",
            _html("Policy Center", "Vacation policy details for all employees."),
        ),
    }
    service, repository, storage = _service(tmp_path, responses)

    result = await service.ingest(
        "workspace-a", "https://example.com", crawl_same_domain=False, page_limit=1
    )
    source = await repository.get("workspace-a", result.source.source_id)
    documents = await storage.load_documents("workspace-a", source.source_id)
    source_directory = (
        tmp_path / "data" / "workspaces" / "workspace-a" / "sources" / str(source.source_id)
    )

    assert source.status is SourceStatus.READY
    assert source.config.source_type is SourceType.WEBSITE
    assert result.page_count == 1
    assert documents[0].source_uri == "https://example.com/"
    assert documents[0].title == "Policy Center"
    assert documents[0].metadata["crawl_index"] == 0
    assert (source_directory / "crawl-manifest.json").is_file()


@pytest.mark.asyncio
async def test_page_limit_over_server_cap_creates_no_source(tmp_path: Path) -> None:
    service, repository, _ = _service(tmp_path, {})

    with pytest.raises(WebsiteValidationError, match="cannot exceed"):
        await service.ingest(
            "workspace-a", "https://example.com/", crawl_same_domain=True, page_limit=21
        )

    assert await repository.list("workspace-a") == ()


@pytest.mark.asyncio
async def test_seed_failure_persists_failed_source(tmp_path: Path) -> None:
    responses: dict[str, tuple[str, str] | Exception] = {
        "https://example.com/robots.txt": (
            "https://example.com/robots.txt",
            "User-agent: *\nAllow: /",
        ),
        "https://example.com/": WebsiteValidationError("Seed unavailable"),
    }
    service, repository, _ = _service(tmp_path, responses)

    with pytest.raises(WebsiteValidationError, match="Seed unavailable"):
        await service.ingest(
            "workspace-a", "https://example.com/", crawl_same_domain=False, page_limit=1
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED


@pytest.mark.asyncio
async def test_ready_metadata_failure_leaves_website_source_failed(tmp_path: Path) -> None:
    responses = {
        "https://example.com/robots.txt": (
            "https://example.com/robots.txt",
            "User-agent: *\nAllow: /",
        ),
        "https://example.com/": (
            "https://example.com/",
            _html("Policy", "Employee policy content."),
        ),
    }
    crawler, _ = _crawler(responses)
    repository = InMemorySourceRepository()
    storage = _FailingWebsiteReadyStorage(tmp_path / "data")
    service = WebsiteIngestionService(
        repository,
        storage,
        crawler,
        chunk_size=100,
        chunk_overlap=10,
        max_pages=20,
        indexer=RecordingSourceIndexer(),
    )

    with pytest.raises(IngestionError, match="Website ingestion failed"):
        await service.ingest(
            "workspace-a", "https://example.com/", crawl_same_domain=False, page_limit=1
        )

    sources = await repository.list("workspace-a")
    assert len(sources) == 1
    assert sources[0].status is SourceStatus.FAILED


@pytest.mark.asyncio
async def test_website_endpoint_and_document_inspection(tmp_path: Path) -> None:
    responses = {
        "https://example.com/robots.txt": (
            "https://example.com/robots.txt",
            "User-agent: *\nAllow: /",
        ),
        "https://example.com/": (
            "https://example.com/",
            _html("Handbook", "Policy HR-402 applies to all employees."),
        ),
    }
    service, repository, storage = _service(tmp_path, responses)
    app.dependency_overrides[get_website_ingestion_service] = lambda: service
    app.dependency_overrides[get_source_repository] = lambda: repository
    app.dependency_overrides[get_source_storage] = lambda: storage
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/sources/website",
                json={
                    "workspace_id": "workspace-a",
                    "url": "https://example.com",
                    "crawl_same_domain": False,
                    "page_limit": 1,
                },
            )
            source_id = response.json()["source"]["source_id"]
            documents_response = await client.get(
                f"/sources/{source_id}/documents",
                params={"workspace_id": "workspace-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["source"]["status"] == "ready"
    assert documents_response.status_code == 200
    assert documents_response.json()[0]["source_uri"] == "https://example.com/"
