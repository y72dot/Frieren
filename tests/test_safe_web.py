from __future__ import annotations

import sqlite3

import httpx
import pytest

from src.core.artifacts import ArtifactStore
from src.core.web import SafeWebClient


async def _public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def _client(tmp_path, handler, **kwargs):
    store = ArtifactStore(
        tmp_path / "artifacts", connection=sqlite3.connect(":memory:")
    )
    return SafeWebClient(
        store,
        resolver=_public_resolver,
        transport=httpx.MockTransport(handler),
        **kwargs,
    ), store


@pytest.mark.asyncio
async def test_fetch_extracts_text_and_archives_untrusted_page(tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><title>Example</title><body>Hello <b>world</b><script>bad()</script></body></html>",
            request=request,
        )

    client, store = _client(tmp_path, handler)
    document = await client.fetch("https://example.com/page")
    assert document.title == "Example"
    assert "Hello" in document.text and "bad()" not in document.text
    assert document.untrusted is True
    artifact = store.get(document.artifact_id)
    assert artifact.status == "available"
    assert artifact.source_type == "web"
    assert artifact.remote_url == "https://example.com/page"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://localhost/",
    ],
)
async def test_ssrf_and_unsafe_urls_are_rejected_before_request(tmp_path, url):
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, text="no", request=request)

    client, _ = _client(tmp_path, handler)
    with pytest.raises(ValueError):
        await client.fetch(url)
    assert called is False


@pytest.mark.asyncio
async def test_redirect_target_is_resolved_and_revalidated(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/private"}, request=request
        )

    client, _ = _client(tmp_path, handler)
    with pytest.raises(ValueError, match="non-public"):
        await client.fetch("https://example.com/start")
    assert requests == ["https://example.com/start"]


@pytest.mark.asyncio
async def test_connected_peer_is_checked_again_after_dns_resolution(tmp_path):
    class PrivatePeer:
        def get_extra_info(self, key):
            return ("10.0.0.8", 443) if key == "server_addr" else None

    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="rebound",
            extensions={"network_stream": PrivatePeer()},
            request=request,
        )

    client, _ = _client(tmp_path, handler)
    with pytest.raises(ValueError, match="non-public peer"):
        await client.fetch("https://example.com/rebound")


@pytest.mark.asyncio
async def test_mime_size_and_redirect_limits_are_enforced(tmp_path):
    def binary(request):
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"x", request=request
        )

    client, _ = _client(tmp_path, binary)
    with pytest.raises(ValueError, match="MIME"):
        await client.fetch("https://example.com/data")

    def large(request):
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"12345", request=request
        )

    client, _ = _client(tmp_path, large, max_response_bytes=4)
    with pytest.raises(ValueError, match="size limit"):
        await client.fetch("https://example.com/large")

    def redirect(request):
        return httpx.Response(302, headers={"location": "/again"}, request=request)

    client, _ = _client(tmp_path, redirect, max_redirects=1)
    with pytest.raises(ValueError, match="redirect limit"):
        await client.fetch("https://example.com/start")


@pytest.mark.asyncio
async def test_search_only_parses_results_and_does_not_fetch_targets(tmp_path):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        html = """
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc">Result</a>
        <div class="result__snippet">Useful snippet</div>
        """
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text=html, request=request
        )

    client, _ = _client(tmp_path, handler)
    results = await client.search("durable runtime")
    assert len(requested) == 1
    assert results[0].url == "https://example.com/doc"
    assert results[0].snippet == "Useful snippet"


@pytest.mark.asyncio
async def test_search_falls_back_to_bing_when_primary_has_no_results(tmp_path):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if "duckduckgo.com" in str(request.url):
            return httpx.Response(
                202,
                headers={"content-type": "text/html"},
                text="<html><title>DuckDuckGo</title><body>challenge</body></html>",
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<ol><li class="b_algo"><a href="https://example.com/site">Site</a>'
                '<h2><a href="https://example.com/news">'
                "News result</a></h2><div class=\"b_caption\"><p>Latest details"
                "</p></div></li></ol>"
            ),
            request=request,
        )

    client, _ = _client(tmp_path, handler)
    results = await client.search("latest news")

    assert len(requested) == 2
    assert "bing.com" in requested[1]
    assert results[0].title == "News result"
    assert results[0].url == "https://example.com/news"
    assert results[0].snippet == "Latest details"
