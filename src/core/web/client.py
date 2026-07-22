from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from src.core.artifacts import ArtifactStore

Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class WebDocument:
    url: str
    final_url: str
    title: str
    text: str
    mime_type: str
    sha256: str
    artifact_id: str
    fetched_at: int
    untrusted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    source: str


class SafeWebClient:
    """HTTP client with redirect-by-redirect SSRF validation and Artifact capture."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        timeout: float = 20.0,
        max_response_bytes: int = 2_097_152,
        max_redirects: int = 3,
        search_url: str = "https://html.duckduckgo.com/html/?q={query}",
        user_agent: str = "qqbot-agent/1.0",
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self.search_url = search_url
        self.user_agent = user_agent
        self.resolver = resolver or _resolve
        self.transport = transport

    async def search(self, query: str, *, limit: int = 10) -> list[WebSearchResult]:
        url = self.search_url.format(query=quote_plus(query))
        response, final_url, content = await self._request(url, allowed_mime={"text/html"})
        del response
        parser = _SearchParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        return [
            WebSearchResult(title=item[0], url=_unwrap_result_url(item[1]), snippet=item[2], source=final_url)
            for item in parser.results[:limit]
        ]

    async def fetch(self, url: str) -> WebDocument:
        response, final_url, content = await self._request(
            url,
            allowed_mime={"text/html", "text/plain", "application/json", "application/xml", "text/xml"},
        )
        mime_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
        text = content.decode(response.encoding or "utf-8", errors="replace")
        title = final_url
        if mime_type == "text/html":
            parser = _TextParser()
            parser.feed(text)
            text = parser.text()
            title = parser.title or title
        digest = hashlib.sha256(content).hexdigest()
        file_name = Path(urlparse(final_url).path).name or "index.html"
        artifact = self.artifact_store.create_pending(
            kind="webpage",
            source_type="web",
            file_name=file_name,
            remote_url=final_url,
            metadata={"original_url": url, "final_url": final_url, "untrusted": True},
        )
        artifact = self.artifact_store.import_bytes(artifact.artifact_id, content, file_name=file_name)
        return WebDocument(
            url=url,
            final_url=final_url,
            title=title[:500],
            text=text[: self.max_response_bytes],
            mime_type=mime_type,
            sha256=digest,
            artifact_id=artifact.artifact_id,
            fetched_at=int(time.time()),
        )

    async def download(self, url: str, *, file_name: str | None = None) -> dict[str, Any]:
        response, final_url, content = await self._request(url, allowed_mime=None)
        resolved_name = file_name or Path(urlparse(final_url).path).name or "download"
        artifact = self.artifact_store.create_pending(
            kind="file",
            source_type="web",
            file_name=resolved_name,
            remote_url=final_url,
            metadata={
                "original_url": url,
                "final_url": final_url,
                "content_type": response.headers.get("content-type", ""),
                "untrusted": True,
            },
        )
        return self.artifact_store.import_bytes(
            artifact.artifact_id, content, file_name=resolved_name
        ).to_dict()

    async def _request(
        self, url: str, *, allowed_mime: set[str] | None
    ) -> tuple[httpx.Response, str, bytes]:
        current = url
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            transport=self.transport,
            headers={"User-Agent": self.user_agent},
        ) as client:
            for redirect_count in range(self.max_redirects + 1):
                await self._validate_url(current)
                async with client.stream("GET", current) as response:
                    _validate_peer(response)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("redirect response has no location")
                        if redirect_count >= self.max_redirects:
                            raise ValueError("web redirect limit exceeded")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
                    if allowed_mime is not None and mime not in allowed_mime:
                        raise ValueError(f"web response MIME is not allowed: {mime}")
                    length = response.headers.get("content-length")
                    if length and int(length) > self.max_response_bytes:
                        raise ValueError("web response exceeds configured size limit")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self.max_response_bytes:
                            raise ValueError("web response exceeds configured size limit")
                    return response, str(response.url), bytes(content)
        raise RuntimeError("unreachable redirect state")

    async def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("web URL must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("web URL credentials are not allowed")
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
            raise ValueError("web URL targets a local hostname")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = [str(ipaddress.ip_address(hostname))]
        except ValueError:
            addresses = await self.resolver(hostname, port)
        if not addresses:
            raise ValueError("web hostname did not resolve")
        for raw in addresses:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
            if not address.is_global:
                raise ValueError(f"web URL resolves to non-public address: {address}")


async def _resolve(hostname: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.run_in_executor(None, socket.getaddrinfo, hostname, port)
    return list(dict.fromkeys(item[4][0] for item in records))


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self._ignored = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value or self._ignored:
            return
        if self._in_title:
            self.title = f"{self.title} {value}".strip()
        self._chunks.append(value)

    def text(self) -> str:
        return "\n".join(self._chunks)


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str, str]] = []
        self._href = ""
        self._title = ""
        self._snippet = ""
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._href = values.get("href") or ""
            self._title = ""
            self._capture_title = True
        elif "result__snippet" in classes:
            self._snippet = ""
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
            if self._href:
                self.results.append((self._title.strip(), self._href, self._snippet.strip()))
        if self._capture_snippet and tag in {"a", "div", "span"}:
            self._capture_snippet = False
            if self.results and self._snippet:
                title, href, _ = self.results[-1]
                self.results[-1] = (title, href, self._snippet.strip())

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title += data
        if self._capture_snippet:
            self._snippet += data


def _unwrap_result_url(url: str) -> str:
    parsed = urlparse(url)
    target = parse_qs(parsed.query).get("uddg")
    return unquote(target[0]) if target else url


def _validate_peer(response: httpx.Response) -> None:
    """Reject a private connected peer when the transport exposes its address."""
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        return
    peer = stream.get_extra_info("server_addr") or stream.get_extra_info("peername")
    if not peer:
        return
    raw = peer[0] if isinstance(peer, tuple) else str(peer)
    address = ipaddress.ip_address(str(raw).split("%", 1)[0])
    if not address.is_global:
        raise ValueError(f"web connection reached non-public peer: {address}")
