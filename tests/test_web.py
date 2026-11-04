import httpx
import pytest

from src.platform.web import is_allowed_image_host, proxied_image_url
from src.web import server


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://shared.akamai.steamstatic.com/a/b.jpg?t=1", "/steam-img/shared.akamai.steamstatic.com/a/b.jpg"),
        ("https://evil.com/a.jpg", "https://evil.com/a.jpg"),
        ("https://steamstatic.com.evil.com/a.jpg", "https://steamstatic.com.evil.com/a.jpg"),
        ("https://user@cdn.steamstatic.com/a.jpg", "https://user@cdn.steamstatic.com/a.jpg"),
        ("https://cdn.steamstatic.com:8080/a.jpg", "https://cdn.steamstatic.com:8080/a.jpg"),
    ],
)
def test_proxied_image_url(url, expected):
    assert proxied_image_url(url) == expected


def test_allowed_image_hosts():
    assert is_allowed_image_host("cdn.akamai.steamstatic.com")
    assert not is_allowed_image_host("steamstatic.com")
    assert not is_allowed_image_host("evil.com")


@pytest.mark.parametrize("path", ["a/b.jpg", "steam/apps/10/header.jpg"])
def test_safe_image_path(path):
    assert server.is_safe_image_path(path)


@pytest.mark.parametrize("path", ["", "../x.jpg", "a/../b.jpg", "a\\b.jpg", "a\nb.jpg", "a" * 513])
def test_unsafe_image_path(path):
    assert not server.is_safe_image_path(path)


@pytest.fixture
def upstream(monkeypatch):
    """Replaces the server's outgoing HTTP client. Set `responses[url]` before a request."""
    responses = {}
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return responses.get(str(request.url), httpx.Response(404))

    monkeypatch.setattr(server, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(server, "_image_cache", type(server._image_cache)())
    monkeypatch.setattr(server, "_image_cache_bytes", 0)
    monkeypatch.setattr(server, "_discord_sdk", None)
    return responses, requests


@pytest.fixture
async def routes():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(server.build_routes()), base_url="http://t") as c:
        yield c


async def test_image_proxy_fetches_once_and_caches(upstream, routes):
    responses, requests = upstream
    url = "https://cdn.steamstatic.com/a.jpg"
    responses[url] = httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})
    for _ in range(2):
        resp = await routes.get("/steam-img/cdn.steamstatic.com/a.jpg")
        assert resp.status_code == 200
        assert resp.content == b"img"
    assert requests == [url]


async def test_image_proxy_rejects_other_hosts_and_svg(upstream, routes):
    responses, requests = upstream
    responses["https://cdn.steamstatic.com/a.svg"] = httpx.Response(
        200, content=b"<svg/>", headers={"content-type": "image/svg+xml"}
    )
    assert (await routes.get("/steam-img/evil.com/a.jpg")).status_code == 404
    assert (await routes.get("/steam-img/cdn.steamstatic.com/a.svg")).status_code == 404
    assert requests == ["https://cdn.steamstatic.com/a.svg"]


async def test_discord_sdk_is_fetched_once(upstream, routes):
    responses, requests = upstream
    responses[server.DISCORD_SDK_URL] = httpx.Response(200, content=b"export class DiscordSDK {}")
    for _ in range(2):
        resp = await routes.get(server.DISCORD_SDK_PATH)
        assert resp.status_code == 200
        assert resp.content == b"export class DiscordSDK {}"
    assert requests == [server.DISCORD_SDK_URL]


async def test_handshake_script_is_injected_into_html():
    html = b"<html><head><title>x</title></head><body></body></html>"

    async def flet_app(scope, receive, send):
        content_type = b"text/html" if scope["path"] == "/" else b"text/javascript"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", content_type), (b"content-length", str(len(html)).encode())]})
        await send({"type": "http.response.body", "body": html})

    transport = httpx.ASGITransport(server.InjectHandshake(flet_app))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        page = await c.get("/")
        other = await c.get("/main.js")
    assert page.content == html.replace(b"</head>", server.HANDSHAKE_TAG + b"</head>")
    assert other.content == html
