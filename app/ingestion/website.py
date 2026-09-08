"""Safe, bounded website crawling and main-content extraction."""

import asyncio
import hashlib
import ipaddress
import re
import socket
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from app.core.exceptions import WebsiteValidationError
from app.models import CrawlFailure, CrawlManifest

_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_ROBOTS_CONTENT_TYPES = frozenset({"text/plain", "text/html", "application/octet-stream"})
_WHITESPACE = re.compile(r"\s+")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    """Bounded response returned by the outbound HTTP boundary."""

    url: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """Clean text and citation metadata extracted from one HTML page."""

    url: str
    title: str | None
    content: str
    links: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WebsiteCrawl:
    """Usable pages and diagnostics emitted by a bounded crawl."""

    seed_url: str
    pages: tuple[ExtractedPage, ...]
    manifest: CrawlManifest


class WebsiteFetcher(Protocol):
    """Outbound HTTP boundary used by the crawler and tests."""

    async def fetch(self, url: str, accepted_content_types: frozenset[str]) -> FetchedResponse:
        """Fetch a validated URL with bounded redirects and response bytes."""
        ...


class SafeHttpFetcher:
    """Fetch public web resources after validating every redirect destination."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float,
        max_response_bytes: int,
        max_redirects: int,
        resolver: Callable[[str, int], Awaitable[tuple[str, ...]]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver or resolve_host
        self._transport = transport

    async def fetch(self, url: str, accepted_content_types: frozenset[str]) -> FetchedResponse:
        """Fetch one public URL without inheriting proxies from the process environment."""
        current = normalize_url(url)
        for redirect_count in range(self._max_redirects + 1):
            addresses = await resolve_public_addresses(current, self._resolver)
            last_connection_error: httpx.HTTPError | None = None
            redirect_target: str | None = None
            for address in addresses:
                try:
                    split = urlsplit(current)
                    address_authority = f"[{address}]" if ":" in address else address
                    connection_url = urlunsplit(
                        (split.scheme, address_authority, split.path, split.query, "")
                    )
                    async with (
                        httpx.AsyncClient(
                            follow_redirects=False,
                            timeout=self._timeout,
                            trust_env=False,
                            transport=self._transport,
                            headers={
                                "User-Agent": self._user_agent,
                                "Accept": "text/html,text/plain",
                                "Host": split.netloc,
                            },
                        ) as client,
                        client.stream(
                            "GET",
                            connection_url,
                            extensions={"sni_hostname": split.hostname},
                        ) as response,
                    ):
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise WebsiteValidationError("Redirect response has no destination")
                            if redirect_count >= self._max_redirects:
                                raise WebsiteValidationError("Website exceeded the redirect limit")
                            redirect_target = normalize_url(urljoin(current, location))
                            break
                        response.raise_for_status()
                        content_type = (
                            response.headers.get("content-type", "").split(";", 1)[0].lower()
                        )
                        if content_type not in accepted_content_types:
                            raise WebsiteValidationError(
                                f"Unsupported response content type: {content_type or 'missing'}"
                            )
                        declared_length = response.headers.get("content-length")
                        if declared_length:
                            try:
                                declared_size = int(declared_length)
                            except ValueError as error:
                                raise WebsiteValidationError(
                                    "Website response has an invalid Content-Length header"
                                ) from error
                            if declared_size < 0:
                                raise WebsiteValidationError(
                                    "Website response has an invalid Content-Length header"
                                )
                            if declared_size > self._max_response_bytes:
                                raise WebsiteValidationError(
                                    "Website response exceeds the size limit"
                                )
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > self._max_response_bytes:
                                raise WebsiteValidationError(
                                    "Website response exceeds the size limit"
                                )
                        return FetchedResponse(current, bytes(payload), content_type)
                except httpx.HTTPError as error:
                    last_connection_error = error
            if redirect_target is not None:
                current = redirect_target
                continue
            raise WebsiteValidationError(f"Could not fetch {current}") from last_connection_error
        raise WebsiteValidationError("Website exceeded the redirect limit")


class _PageParser(HTMLParser):
    """Collect title, canonical URL, links, and conservative visible text."""

    _ignored_tags = frozenset({"script", "style", "nav", "form", "noscript", "svg", "canvas"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.canonical: str | None = None
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "link" and "canonical" in (values.get("rel") or "").lower().split():
            self.canonical = values.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._ignored_depth:
            self.text_parts.append(data)


class WebsiteCrawler:
    """Crawl a seed URL and optional same-host links within strict bounds."""

    def __init__(
        self,
        fetcher: WebsiteFetcher,
        *,
        user_agent: str,
        default_delay_seconds: float,
    ) -> None:
        self._fetcher = fetcher
        self._user_agent = user_agent
        self._default_delay = default_delay_seconds

    async def crawl(
        self,
        seed_url: str,
        *,
        crawl_same_domain: bool,
        page_limit: int,
        allowed_domains: list[str] | None = None,
    ) -> WebsiteCrawl:
        """Return extracted pages in deterministic breadth-first order.

        ``allowed_domains`` (when set) restricts the seed and every fetched host to the given
        domain patterns; ``example.com`` also matches its subdomains.
        """
        seed = normalize_url(seed_url)
        await validate_url_shape(seed)
        seed_host = urlsplit(seed).hostname
        if not domain_allowed(seed_host, allowed_domains):
            raise WebsiteValidationError("The seed domain is not in the workspace crawl allowlist")
        robots = await self._load_robots(seed)
        robots_delay = robots.crawl_delay(self._user_agent)
        delay = float(robots_delay) if robots_delay is not None else self._default_delay
        queue = deque([seed])
        queued = {seed}
        fetched: list[str] = []
        indexed: list[str] = []
        failures: list[CrawlFailure] = []
        pages: list[ExtractedPage] = []
        canonical_urls: set[str] = set()
        content_hashes: set[str] = set()

        while queue and len(fetched) < page_limit:
            requested_url = queue.popleft()
            if not robots.can_fetch(self._user_agent, requested_url):
                if requested_url == seed:
                    raise WebsiteValidationError("The seed URL is disallowed by robots.txt")
                failures.append(CrawlFailure(url=requested_url, reason="Disallowed by robots.txt"))
                continue
            if fetched and delay:
                await asyncio.sleep(delay)
            try:
                response = await self._fetcher.fetch(requested_url, _HTML_CONTENT_TYPES)
                fetched.append(response.url)
                if urlsplit(response.url).hostname != seed_host:
                    raise WebsiteValidationError("Redirect left the allowed domain")
                page = extract_page(response)
                if page.url in canonical_urls:
                    failures.append(
                        CrawlFailure(url=response.url, reason="Duplicate canonical URL")
                    )
                    continue
                content_hash = hashlib.sha256(page.content.encode()).hexdigest()
                if content_hash in content_hashes:
                    failures.append(CrawlFailure(url=page.url, reason="Duplicate page content"))
                    continue
                canonical_urls.add(page.url)
                content_hashes.add(content_hash)
                pages.append(page)
                indexed.append(page.url)
                if crawl_same_domain:
                    for link in page.links:
                        if len(queued) >= page_limit * 10:
                            break
                        try:
                            candidate = normalize_url(urljoin(response.url, link))
                        except WebsiteValidationError:
                            continue
                        if urlsplit(candidate).hostname == seed_host and candidate not in queued:
                            queued.add(candidate)
                            queue.append(candidate)
            except WebsiteValidationError as error:
                if requested_url == seed:
                    raise
                failures.append(CrawlFailure(url=requested_url, reason=str(error)))

        if not pages:
            raise WebsiteValidationError("The website has no extractable text")
        manifest = CrawlManifest(
            seed_url=seed,
            fetched_urls=fetched,
            indexed_urls=indexed,
            failures=failures,
        )
        return WebsiteCrawl(seed_url=seed, pages=tuple(pages), manifest=manifest)

    async def _load_robots(self, seed_url: str) -> RobotFileParser:
        split = urlsplit(seed_url)
        robots_url = urlunsplit((split.scheme, split.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser(robots_url)
        try:
            response = await self._fetcher.fetch(robots_url, _ROBOTS_CONTENT_TYPES)
        except WebsiteValidationError:
            parser.parse([])
            return parser
        parser.parse(response.content.decode(errors="replace").splitlines())
        return parser


def normalize_url(value: str) -> str:
    """Return a fragment-free, absolute HTTP(S) URL with normalized authority."""
    try:
        split = urlsplit(value.strip())
        port = split.port
    except ValueError as error:
        raise WebsiteValidationError("The URL has an invalid port") from error
    if split.scheme.lower() not in {"http", "https"}:
        raise WebsiteValidationError("Only http and https URLs are supported")
    if not split.hostname:
        raise WebsiteValidationError("The URL must include a hostname")
    if split.username or split.password:
        raise WebsiteValidationError("URLs containing credentials are not supported")
    expected_port = 80 if split.scheme.lower() == "http" else 443
    if port not in {None, expected_port}:
        raise WebsiteValidationError("Only standard HTTP and HTTPS ports are supported")
    host = split.hostname.lower().rstrip(".")
    netloc = f"[{host}]" if ":" in host else host
    path = split.path or "/"
    normalized = SplitResult(split.scheme.lower(), netloc, path, split.query, "")
    return urlunsplit(normalized)


async def validate_url_shape(url: str) -> None:
    """Validate URL syntax without making a network request."""
    normalize_url(url)


def domain_allowed(host: str | None, patterns: list[str] | None) -> bool:
    """Return whether ``host`` matches an allowlist pattern (or the allowlist is unset)."""
    if not patterns:
        return True
    if not host:
        return False
    host = host.lower().rstrip(".")
    for pattern in patterns:
        cleaned = pattern.lower().strip().removeprefix("*.").rstrip(".")
        if cleaned and (host == cleaned or host.endswith("." + cleaned)):
            return True
    return False


async def resolve_host(host: str, port: int) -> tuple[str, ...]:
    """Resolve all addresses for a host without blocking the event loop."""
    loop = asyncio.get_running_loop()
    try:
        results = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise WebsiteValidationError(f"Could not resolve host: {host}") from error
    return tuple(sorted({result[4][0] for result in results}))


async def validate_public_url(
    url: str, resolver: Callable[[str, int], Awaitable[tuple[str, ...]]]
) -> None:
    """Reject URLs resolving to any non-global address."""
    await resolve_public_addresses(url, resolver)


async def resolve_public_addresses(
    url: str, resolver: Callable[[str, int], Awaitable[tuple[str, ...]]]
) -> tuple[str, ...]:
    """Return public addresses approved for a URL's outbound connection."""
    normalized = normalize_url(url)
    split = urlsplit(normalized)
    host = split.hostname or ""
    port = split.port or (80 if split.scheme == "http" else 443)
    try:
        literal = ipaddress.ip_address(host)
        addresses: tuple[str, ...] = (str(literal),)
    except ValueError:
        addresses = await resolver(host, port)
    if not addresses:
        raise WebsiteValidationError(f"Could not resolve host: {host}")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise WebsiteValidationError("Website resolves to a non-public network address")
    return addresses


def extract_page(response: FetchedResponse) -> ExtractedPage:
    """Extract clean main text, title, canonical URL, and links from HTML."""
    html = response.content.decode(errors="replace")
    parser = _PageParser()
    parser.feed(html)
    extracted = trafilatura.extract(
        html,
        fast=True,
        include_comments=False,
        include_tables=True,
        url=response.url,
    )
    content = normalize_website_text(extracted or " ".join(parser.text_parts))
    if not content:
        raise WebsiteValidationError("Page has no extractable text")
    title = normalize_website_text(" ".join(parser.title_parts)) or None
    canonical_url = response.url
    if parser.canonical:
        candidate = normalize_url(urljoin(response.url, parser.canonical))
        if urlsplit(candidate).hostname == urlsplit(response.url).hostname:
            canonical_url = candidate
    links = tuple(link for link in parser.links if link.strip())
    return ExtractedPage(canonical_url, title, content, links)


def normalize_website_text(value: str) -> str:
    """Collapse extracted website whitespace into stable plain text."""
    return _WHITESPACE.sub(" ", value).strip()
