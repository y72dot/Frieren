from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
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
        search_url: str = "https://search.yahoo.com/search?p={query}",
        news_search_url: str = (
            "https://www.bing.com/news/search?format=rss&setlang={lang}"
            "&cc={country}&mkt={market}&q={query}"
        ),
        search_fallback_urls: list[str] | None = None,
        user_agent: str = "qqbot-agent/1.0",
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_redirects = max_redirects
        self.search_url = search_url
        self.news_search_url = news_search_url
        self.search_fallback_urls = list(
            search_fallback_urls
            if search_fallback_urls is not None
            else [
                "https://www.bing.com/search?setlang={lang}&cc={country}"
                "&mkt={market}&q={query}",
                "https://html.duckduckgo.com/html/?q={query}",
            ]
        )
        self.user_agent = user_agent
        self.resolver = resolver or _resolve
        self.transport = transport

    async def search(self, query: str, *, limit: int = 10) -> list[WebSearchResult]:
        preferred = [self.news_search_url] if _is_news_query(query) else []
        templates = list(
            dict.fromkeys([*preferred, self.search_url, *self.search_fallback_urls])
        )
        failures: list[str] = []
        locale = _query_locale(query)
        for template in templates:
            url = template.format(query=quote_plus(query), **locale)
            try:
                response, final_url, content = await self._request(
                    url,
                    allowed_mime={
                        "text/html",
                        "text/xml",
                        "application/xml",
                        "application/rss+xml",
                    },
                )
            except Exception as exc:
                failures.append(f"{urlparse(url).hostname}: {exc}")
                continue
            mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if "xml" in mime or "format=rss" in final_url:
                raw_results = _parse_rss_results(content)
            else:
                hostname = urlparse(final_url).hostname or ""
                if hostname.endswith("yahoo.com"):
                    parser = _YahooSearchParser()
                elif hostname.endswith("bing.com"):
                    parser = _BingSearchParser()
                else:
                    parser = _SearchParser()
                parser.feed(content.decode(response.encoding or "utf-8", errors="replace"))
                raw_results = parser.results
            candidates = [
                WebSearchResult(
                    title=item[0],
                    url=_unwrap_result_url(item[1]),
                    snippet=item[2],
                    source=final_url,
                )
                for item in raw_results
                if item[0] and item[1]
            ]
            results = _rank_search_results(query, candidates, limit)
            if results:
                return results
            failures.append(f"{urlparse(final_url).hostname}: no relevant results")
        raise RuntimeError("web search providers unavailable: " + "; ".join(failures))

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


class _BingSearchParser(HTMLParser):
    """Extract organic results from Bing's ``li.b_algo`` markup."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str, str]] = []
        self._in_result = False
        self._in_heading = False
        self._capture_title = False
        self._capture_snippet = False
        self._href = ""
        self._title = ""
        self._snippet = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "li" and "b_algo" in classes:
            self._in_result = True
            self._href = self._title = self._snippet = ""
        elif self._in_result and tag == "h2":
            self._in_heading = True
        elif self._in_result and self._in_heading and tag == "a" and not self._href:
            self._href = values.get("href") or ""
            self._capture_title = bool(self._href)
        elif self._in_result and tag == "p":
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._capture_title = False
        elif tag == "h2":
            self._in_heading = False
        elif tag == "p":
            self._capture_snippet = False
        elif tag == "li" and self._in_result:
            if self._href and self._title.strip():
                self.results.append(
                    (self._title.strip(), self._href, self._snippet.strip())
                )
            self._in_result = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title += data
        if self._capture_snippet:
            self._snippet += data


class _YahooSearchParser(HTMLParser):
    """Extract Yahoo organic results without collecting sitelinks as results."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[str, str, str]] = []
        self._result_depth = 0
        self._capture_title = False
        self._capture_snippet = False
        self._href = ""
        self._title = ""
        self._snippet = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "div" and not self._result_depth and "algo" in classes:
            self._result_depth = 1
            self._href = self._title = self._snippet = ""
            return
        if not self._result_depth:
            return
        if tag == "div":
            self._result_depth += 1
        elif tag == "a" and not self._href and "mt-38" in classes:
            self._href = values.get("href") or ""
        elif tag == "h3" and self._href:
            self._capture_title = True
        elif tag == "p" and not self._snippet and "mah-44" in classes:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if not self._result_depth:
            return
        if tag == "h3":
            self._capture_title = False
        elif tag == "p":
            self._capture_snippet = False
        elif tag == "div":
            self._result_depth -= 1
            if not self._result_depth and self._href and self._title.strip():
                self.results.append(
                    (self._title.strip(), self._href, self._snippet.strip())
                )

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title += data
        if self._capture_snippet:
            self._snippet += data


def _unwrap_result_url(url: str) -> str:
    parsed = urlparse(url)
    target = parse_qs(parsed.query).get("uddg")
    if target:
        return unquote(target[0])
    if parsed.hostname and parsed.hostname.endswith("bing.com"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                payload = encoded[2:]
                payload += "=" * (-len(payload) % 4)
                decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
                if decoded.startswith(("http://", "https://")):
                    return decoded
            except (ValueError, UnicodeDecodeError):
                pass
    if parsed.hostname and parsed.hostname.endswith("search.yahoo.com"):
        match = re.search(r"/RU=(.*?)/RK=", parsed.path)
        if match:
            decoded = unquote(match.group(1))
            if decoded.startswith(("http://", "https://")):
                return decoded
    return url


def _parse_rss_results(content: bytes) -> list[tuple[str, str, str]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    return [
        (
            html.unescape(item.findtext("title") or "").strip(),
            (item.findtext("link") or "").strip(),
            html.unescape(item.findtext("description") or "").strip(),
        )
        for item in root.findall(".//item")
    ]


def _query_locale(query: str) -> dict[str, str]:
    if re.search(r"[\u3040-\u30ff]", query):
        return {"lang": "ja-JP", "country": "JP", "market": "ja-JP"}
    if re.search(r"[\u3400-\u9fff]", query):
        return {"lang": "zh-CN", "country": "CN", "market": "zh-CN"}
    return {"lang": "en-US", "country": "US", "market": "en-US"}


def _is_news_query(query: str) -> bool:
    normalized = query.casefold()
    markers = (
        "latest", "news", "today", "current", "recent", "release date",
        "announcement", "schedule", "最新", "新闻", "今日", "近期", "发布",
        "发布日期", "播出", "档期", "什么时候", "いつ", "最新情報", "放送",
    )
    return any(marker in normalized for marker in markers)


def _rank_search_results(
    query: str, results: list[WebSearchResult], limit: int
) -> list[WebSearchResult]:
    terms = _search_terms(query)
    ranked: list[tuple[int, int, WebSearchResult]] = []
    seen_urls: set[str] = set()
    host_counts: dict[str, int] = {}
    for index, result in enumerate(results):
        canonical = result.url.rstrip("/")
        if not canonical or canonical in seen_urls:
            continue
        title = result.title.casefold()
        snippet = result.snippet.casefold()
        url = result.url.casefold()
        host = (urlparse(result.url).hostname or "").casefold()
        matched = {term for term in terms if term in title or term in snippet or term in url}
        if terms and not matched:
            continue
        score = sum(
            (4 if term in title else 0)
            + (2 if term in snippet else 0)
            + (1 if term in url else 0)
            for term in matched
        )
        # Prefer the entity's own domain over mirrors and API directories.  The
        # path may repeat arbitrary query words, so only the hostname receives
        # this authority signal.
        score += sum(5 for term in matched if len(term) >= 3 and term in host)
        if host.endswith((".gov", ".gov.cn", ".edu", ".edu.cn")):
            score += 2
        ranked.append((score, -index, result))
        seen_urls.add(canonical)
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected: list[WebSearchResult] = []
    for _, _, result in ranked:
        host = (urlparse(result.url).hostname or "").lower()
        if host_counts.get(host, 0) >= 2:
            continue
        host_counts[host] = host_counts.get(host, 0) + 1
        selected.append(result)
        if len(selected) >= limit:
            break
    return selected


def _search_terms(query: str) -> set[str]:
    ignored = {
        "and", "or", "the", "a", "an", "of", "for", "to", "in",
        "official", "latest", "news", "release", "date", "update",
        "season", "information", "search", "2025", "2026",
        "最新", "官方", "信息", "搜索", "播出", "第二季", "第2期", "2期",
    }
    normalized = query.casefold()
    latin = {
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", normalized)
        if token not in ignored
    }
    cjk = {
        token for token in re.findall(r"[\u3400-\u9fff\u3040-\u30ff]+", normalized)
        if token not in ignored and len(token) >= 2
    }
    return latin | cjk


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
