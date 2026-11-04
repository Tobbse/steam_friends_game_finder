import importlib
import os
import shutil
import subprocess
from urllib.parse import unquote

import httpx
import pytest

import src.web.server as server
from src.web import settings

IMG_HOST = "shared.akamai.steamstatic.com"
IMG_PATH = "store_item_assets/steam/apps/440/header.jpg"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


@pytest.fixture(autouse=True)
def _clean_server_state(monkeypatch):
    server._clear_caches()
    monkeypatch.setattr(settings, "PROXY_STEAM_IMAGES", True)
    monkeypatch.setattr(settings, "DISCORD_CLIENT_ID", "")
    yield
    server._clear_caches()


@pytest.fixture
def upstream(monkeypatch):
    """Mocks the upstream HTTP client. Set `state["handler"]` to change the responses."""
    state = {"requests": [], "handler": lambda request: httpx.Response(
        200, content=PNG, headers={"content-type": "image/png"})}

    def handle(request):
        state["requests"].append(request)
        return state["handler"](request)

    monkeypatch.setattr(server, "_proxy_http", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    return state


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def client():
    async with _client(server.build_web_routes()) as c:
        yield c


async def _asgi_get(app, path, query=b""):
    return await _asgi_request(app, path, query)


async def _asgi_request(app, path, query=b"", method="GET"):
    """Sends one raw ASGI request. Unlike httpx, this keeps literal ".." and "." segments in the path."""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": unquote(path), "raw_path": path.encode(), "root_path": "",
        "query_string": query, "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


# ---------- /healthz ----------

async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_healthz_head(client):
    assert (await client.head("/healthz")).status_code == 200


# ---------- Steam image proxy ----------

async def test_image_proxy_fetches_allowed_host_over_https(client, upstream):
    resp = await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")
    assert resp.status_code == 200
    assert resp.content == PNG
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert [str(r.url) for r in upstream["requests"]] == [f"https://{IMG_HOST}/{IMG_PATH}"]


@pytest.mark.parametrize("host", ["evil.com", "steamstatic.com.evil.com", "127.0.0.1", "localhost"])
async def test_image_proxy_rejects_disallowed_host(client, upstream, host):
    resp = await client.get(f"/steam-img/{host}/{IMG_PATH}")
    assert resp.status_code == 403
    assert upstream["requests"] == []


@pytest.mark.parametrize("path", [
    f"/steam-img/{IMG_HOST}/a/../b.jpg",
    f"/steam-img/{IMG_HOST}/../x.jpg",
    f"/steam-img/{IMG_HOST}/a/%2e%2e/b.jpg",
    f"/steam-img/{IMG_HOST}/a/%2E%2E/b.jpg",
    f"/steam-img/{IMG_HOST}/a/.%2E/b.jpg",
    f"/steam-img/{IMG_HOST}/a/%252e%252e/b.jpg",
    f"/steam-img/{IMG_HOST}/a/%5c..%5cb.jpg",
    f"/steam-img/{IMG_HOST}/a/%5cb.jpg",
    f"/steam-img/{IMG_HOST}/a\\b.jpg",
    f"/steam-img/{IMG_HOST}/a/./b.jpg",
    f"/steam-img/{IMG_HOST}/./b.jpg",
    f"/steam-img/{IMG_HOST}/a/%2e/b.jpg",
    f"/steam-img/{IMG_HOST}/a//b.jpg",
    f"/steam-img/{IMG_HOST}//b.jpg",
    f"/steam-img/{IMG_HOST}/a/b.jpg/",
])
async def test_image_proxy_rejects_dot_segments(upstream, path):
    status, _ = await _asgi_get(server.build_web_routes(), path)
    assert status == 400
    assert upstream["requests"] == []


async def test_image_proxy_drops_query(client, upstream):
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}?t=1700000000&evil=1")).status_code == 200
    assert [str(r.url) for r in upstream["requests"]] == [f"https://{IMG_HOST}/{IMG_PATH}"]


async def test_image_proxy_different_t_values_share_one_cache_entry(client, upstream):
    for t in ("1", "2", "3"):
        assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}?t={t}")).content == PNG
    assert len(upstream["requests"]) == 1
    assert list(server._image_cache) == [f"https://{IMG_HOST}/{IMG_PATH}"]


async def test_image_proxy_head(client, upstream):
    resp = await client.head(f"/steam-img/{IMG_HOST}/{IMG_PATH}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


async def test_image_proxy_rejects_oversized_upstream(client, upstream):
    big = b"x" * (server.IMAGE_MAX_BYTES + 1)
    upstream["handler"] = lambda request: httpx.Response(200, content=big, headers={"content-type": "image/jpeg"})
    resp = await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")
    assert resp.status_code == 502
    assert server._image_cache_bytes == 0


async def test_image_proxy_rejects_oversized_stream_without_content_length(client, upstream):
    async def chunks():
        for _ in range(server.IMAGE_MAX_BYTES // 65536 + 2):
            yield b"x" * 65536

    upstream["handler"] = lambda request: httpx.Response(
        200, content=chunks(), headers={"content-type": "image/jpeg"})
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")).status_code == 502


@pytest.mark.parametrize("ctype", ["text/html", "image/svg+xml", "application/javascript", ""])
async def test_image_proxy_rejects_non_image_content_type(client, upstream, ctype):
    upstream["handler"] = lambda request: httpx.Response(200, content=b"<svg/>", headers={"content-type": ctype})
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")).status_code == 502


async def test_image_proxy_upstream_error_status(client, upstream):
    upstream["handler"] = lambda request: httpx.Response(404, content=b"nope")
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")).status_code == 404


async def test_image_proxy_serves_second_request_from_cache(client, upstream):
    url = f"/steam-img/{IMG_HOST}/{IMG_PATH}"
    assert (await client.get(url)).content == PNG
    assert (await client.get(url)).content == PNG
    assert len(upstream["requests"]) == 1


async def test_image_proxy_follows_redirect_only_to_allowed_host(client, upstream):
    def handler(request):
        if request.url.path.endswith("/old.jpg"):
            return httpx.Response(302, headers={"location": f"https://cdn.cloudflare.steamstatic.com/{IMG_PATH}"})
        if request.url.path.endswith("/bad.jpg"):
            return httpx.Response(302, headers={"location": "https://evil.com/x.png"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    upstream["handler"] = handler
    assert (await client.get(f"/steam-img/{IMG_HOST}/old.jpg")).status_code == 200
    assert (await client.get(f"/steam-img/{IMG_HOST}/bad.jpg")).status_code == 502
    assert [r.url.host for r in upstream["requests"]] == [IMG_HOST, "cdn.cloudflare.steamstatic.com", IMG_HOST]


@pytest.mark.parametrize("location", [
    f"http://{IMG_HOST}/x.png",  # not https
    f"https://user@{IMG_HOST}/x.png",  # userinfo
    f"https://{IMG_HOST}:8443/x.png",  # explicit port
    f"https://{IMG_HOST}:99999/x.png",  # invalid port (ValueError)
    f"https://{IMG_HOST}:abc/x.png",  # invalid port (ValueError)
    "https://[::1/x.png",  # invalid IPv6 netloc (ValueError)
])
async def test_image_proxy_rejects_bad_redirect_target(client, upstream, location):
    upstream["handler"] = lambda request: httpx.Response(302, headers={"location": location})
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")).status_code == 502
    assert len(upstream["requests"]) == 1


async def test_image_proxy_stops_after_max_redirects(client, upstream):
    upstream["handler"] = lambda request: httpx.Response(
        302, headers={"location": f"https://{IMG_HOST}/loop{len(upstream['requests'])}.jpg"})
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")).status_code == 502
    assert len(upstream["requests"]) == server.IMAGE_MAX_REDIRECTS + 1


async def test_image_proxy_off(client, upstream, monkeypatch):
    monkeypatch.setattr(settings, "PROXY_STEAM_IMAGES", False)
    assert (await client.get(f"/steam-img/{IMG_HOST}/{IMG_PATH}")).status_code == 404
    assert upstream["requests"] == []


def test_image_cache_evicts_oldest(monkeypatch):
    monkeypatch.setattr(server, "IMAGE_CACHE_MAX_BYTES", 80)
    for key in "abcdefghi":
        server._cache_image(key, b"x" * 10, "image/png")
    assert list(server._image_cache) == list("bcdefghi")
    assert server._image_cache_bytes == 80
    server._cache_image("b", b"y" * 10, "image/png")  # re-insert moves to the end, size unchanged
    assert list(server._image_cache)[-1] == "b"
    assert server._image_cache_bytes == 80


# ---------- Discord handshake and SDK proxy ----------

async def test_discord_handshake_contains_client_id_and_sdk_url(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DISCORD_CLIENT_ID", ' 12ab34";alert(1)//\u0661\u0662 ')
    monkeypatch.setattr(server, "LOCAL_SDK_FILE", tmp_path / "missing.mjs")
    resp = await client.get("/discord-handshake.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert 'const clientId = "12341" ||' in resp.text
    assert "alert" not in resp.text
    assert f'import("{server.DISCORD_SDK_ENTRY}")' in resp.text
    assert server.DISCORD_SDK_ENTRY.startswith("/discord-sdk/")


async def test_discord_handshake_ready_timeout_and_link_queue(client):
    js = (await client.get("/discord-handshake.js")).text
    assert "READY_TIMEOUT_MS = 10000" in js
    # timeout and failure both fall back: restore window.open and open the queued links natively
    assert "setTimeout(fallBack, READY_TIMEOUT_MS)" in js
    assert "window.open = nativeOpen;" in js
    assert 'pending.splice(0).forEach((href) => nativeOpen(href, "_blank"' in js
    assert "popup blocker" in js.lower()
    # after a timeout the handshake keeps going; on late success the wrapper is installed again
    assert "Promise.race" not in js
    assert "clearTimeout(timer);" in js
    assert js.count("window.open = wrappedOpen;") == 2
    # overflow of the queue is logged, not silently dropped
    assert "console.warn" in js and "MAX_PENDING_LINKS" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_discord_handshake_is_valid_js(client, tmp_path):
    script = tmp_path / "handshake.js"
    script.write_text((await client.get("/discord-handshake.js")).text)
    env = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}  # no preload hooks from the shell
    result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr


async def test_discord_handshake_uses_local_sdk_file(client, monkeypatch, tmp_path):
    local = tmp_path / "sdk.mjs"
    local.write_text("export const DiscordSDK = 1;")
    monkeypatch.setattr(server, "LOCAL_SDK_FILE", local)
    assert 'import("/discord-sdk-local.mjs")' in (await client.get("/discord-handshake.js")).text
    resp = await client.get("/discord-sdk-local.mjs")
    assert resp.status_code == 200
    assert resp.text == "export const DiscordSDK = 1;"


async def test_discord_sdk_local_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LOCAL_SDK_FILE", tmp_path / "missing.mjs")
    assert (await client.get("/discord-sdk-local.mjs")).status_code == 404


async def test_discord_sdk_proxy_fetches_esm_and_rewrites(client, upstream):
    upstream["handler"] = lambda request: httpx.Response(
        200, text='export * from "/@discord/embedded-app-sdk@2.4.0/es2022/x.mjs";')
    resp = await client.get("/discord-sdk/@discord/embedded-app-sdk@2?bundle")
    assert resp.status_code == 200
    assert resp.text == 'export * from "/discord-sdk/@discord/embedded-app-sdk@2.4.0/es2022/x.mjs";'
    assert str(upstream["requests"][0].url) == "https://esm.sh/@discord/embedded-app-sdk@2?bundle"
    await client.get("/discord-sdk/@discord/embedded-app-sdk@2?bundle")
    assert len(upstream["requests"]) == 1  # cached


async def test_discord_sdk_proxy_rejects_bad_path(client, upstream):
    assert (await client.get("/discord-sdk/a/%2e%2e/b")).status_code == 400
    assert upstream["requests"] == []


@pytest.mark.parametrize("path", [
    "@discord/embedded-app-sdk@2",
    "@discord/embedded-app-sdk@2.5.0/es2022/embedded-app-sdk.bundle.mjs",
    "v135/@discord/embedded-app-sdk@1.2.0/es2022/embedded-app-sdk.bundle.mjs",
])
async def test_discord_sdk_proxy_allows_sdk_paths(client, upstream, path):
    upstream["handler"] = lambda request: httpx.Response(200, text="export {};")
    assert (await client.get(f"/discord-sdk/{path}")).status_code == 200
    assert str(upstream["requests"][0].url) == f"https://esm.sh/{path}"


@pytest.mark.parametrize("path", [
    "react@18",
    "@discord/other-pkg@1",
    "@discord/embedded-app-sdk",  # no version
    "@discord/embedded-app-sdk-evil@1",
    "x/@discord/embedded-app-sdk@2",
    "v1/react@18",
    "node/process.mjs",
])
async def test_discord_sdk_proxy_rejects_other_packages(client, upstream, path):
    assert (await client.get(f"/discord-sdk/{path}")).status_code == 404
    assert upstream["requests"] == []


@pytest.mark.parametrize("query", ["bundle=1", "target=es2015", "bundle&x=1", "dev", "bundle=", "x" * 50])
async def test_discord_sdk_proxy_rejects_query_other_than_bundle(client, upstream, query):
    assert (await client.get(f"/discord-sdk/@discord/embedded-app-sdk@2?{query}")).status_code == 400
    assert upstream["requests"] == []


@pytest.mark.parametrize("path", [
    "@discord/embedded-app-sdk@2/X-ZHM/es2022/x.mjs",
    "@discord/embedded-app-sdk@2/x-ZHM/es2022/x.mjs",
    "v135/@discord/embedded-app-sdk@2/X-YS9i/x.mjs",
    "X-ZHM/@discord/embedded-app-sdk@2",
])
async def test_discord_sdk_proxy_rejects_build_arg_segments(client, upstream, path):
    assert (await client.get(f"/discord-sdk/{path}")).status_code == 400
    assert upstream["requests"] == []


async def test_discord_sdk_proxy_follows_redirect_on_esm(client, upstream):
    def handler(request):
        if request.url.path == "/@discord/embedded-app-sdk@2":
            return httpx.Response(302, headers={"location": "/@discord/embedded-app-sdk@2.5.0"})
        return httpx.Response(200, text="export {};")

    upstream["handler"] = handler
    assert (await client.get("/discord-sdk/@discord/embedded-app-sdk@2")).status_code == 200
    assert [str(r.url) for r in upstream["requests"]] == [
        "https://esm.sh/@discord/embedded-app-sdk@2", "https://esm.sh/@discord/embedded-app-sdk@2.5.0"]


@pytest.mark.parametrize("location", [
    "https://evil.com/@discord/embedded-app-sdk@2.5.0",
    "http://esm.sh/@discord/embedded-app-sdk@2.5.0",
    "https://user@esm.sh/@discord/embedded-app-sdk@2.5.0",
    "https://esm.sh:8443/@discord/embedded-app-sdk@2.5.0",
    "https://esm.sh:abc/@discord/embedded-app-sdk@2.5.0",
])
async def test_discord_sdk_proxy_rejects_bad_redirect(client, upstream, location):
    upstream["handler"] = lambda request: httpx.Response(302, headers={"location": location})
    assert (await client.get("/discord-sdk/@discord/embedded-app-sdk@2")).status_code == 502
    assert len(upstream["requests"]) == 1


async def test_discord_sdk_proxy_stops_after_max_redirects(client, upstream):
    upstream["handler"] = lambda request: httpx.Response(
        302, headers={"location": f"/@discord/embedded-app-sdk@2.{len(upstream['requests'])}"})
    assert (await client.get("/discord-sdk/@discord/embedded-app-sdk@2")).status_code == 502
    assert len(upstream["requests"]) == server.SDK_MAX_REDIRECTS + 1


async def test_discord_sdk_proxy_rejects_oversized(client, upstream):
    async def chunks():
        for _ in range(server.SDK_MAX_BYTES // 65536 + 2):
            yield b"x" * 65536

    upstream["handler"] = lambda request: httpx.Response(200, content=chunks())
    assert (await client.get("/discord-sdk/@discord/embedded-app-sdk@2")).status_code == 502
    assert server._sdk_module_cache == {}


async def test_discord_sdk_proxy_upstream_error(client, upstream):
    upstream["handler"] = lambda request: httpx.Response(500)
    assert (await client.get("/discord-sdk/@discord/embedded-app-sdk@2")).status_code == 502


def test_rewrite_esm_imports():
    js = (
        'import "/a.mjs";\n'
        "import {x} from '/b.mjs';\n"
        'export * from "/c.mjs";\n'
        'const m = import("/d.mjs");\n'
        'import y from "https://esm.sh/e.mjs";\n'
        'import z from "//cdn.example/f.mjs";\n'
        'import w from "./g.mjs";\n'
    )
    assert server._rewrite_esm_imports(js) == (
        'import "/discord-sdk/a.mjs";\n'
        "import {x} from '/discord-sdk/b.mjs';\n"
        'export * from "/discord-sdk/c.mjs";\n'
        'const m = import("/discord-sdk/d.mjs");\n'
        'import y from "https://esm.sh/e.mjs";\n'
        'import z from "//cdn.example/f.mjs";\n'
        'import w from "./g.mjs";\n'
    )


# ---------- HtmlInjectMiddleware ----------

def _static_app(body: bytes, content_type: str, extra_headers=()):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode()),
            *extra_headers]})
        await send({"type": "http.response.body", "body": body[:10], "more_body": True})
        await send({"type": "http.response.body", "body": body[10:], "more_body": False})
    return app


async def test_html_inject_middleware_injects_into_html():
    html = b"<html><head><title>x</title></head><body></body></html>"
    app = server.HtmlInjectMiddleware(_static_app(html, "text/html; charset=utf-8"), server.HANDSHAKE_TAG)
    async with _client(app) as client:
        resp = await client.get("/")
    assert resp.content == html.replace(b"</head>", server.HANDSHAKE_TAG + b"</head>")
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert server.HANDSHAKE_TAG == b'<script type="module" src="/discord-handshake.js"></script>'


async def test_html_inject_middleware_leaves_non_html_untouched():
    body = b'{"a": "</head>"}'
    app = server.HtmlInjectMiddleware(_static_app(body, "application/json"), server.HANDSHAKE_TAG)
    async with _client(app) as client:
        assert (await client.get("/x.json")).content == body


async def test_html_inject_middleware_skips_encoded_html():
    body = b"<html><head></head></html>"
    app = server.HtmlInjectMiddleware(
        _static_app(body, "text/html", [(b"content-encoding", b"identity")]), server.HANDSHAKE_TAG)
    async with _client(app) as client:
        assert (await client.get("/")).content == body


async def test_html_inject_middleware_passes_head_through():
    body = b"<html><head></head></html>"
    app = server.HtmlInjectMiddleware(_static_app(body, "text/html"), server.HANDSHAKE_TAG)
    status, sent = await _asgi_request(app, "/", method="HEAD")
    assert status == 200
    assert sent == body  # the dummy app sends a body; the middleware forwards it unchanged


# ---------- PrefixDispatcher ----------

@pytest.mark.parametrize("path, expected", [
    ("/healthz", "helper"),
    ("/discord-handshake.js", "helper"),
    ("/discord-sdk/@discord/x", "helper"),
    ("/discord-sdk-local.mjs", "helper"),
    (f"/steam-img/{IMG_HOST}/a.jpg", "helper"),
    ("/", "flet"),
    ("/index.html", "flet"),
    ("/ws", "flet"),
    ("/steam-imgx", "flet"),
])
async def test_prefix_dispatcher_routes(path, expected):
    def named(name):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": name.encode()})
        return app

    async with _client(server.PrefixDispatcher(named("flet"), named("helper"))) as client:
        assert (await client.get(path)).text == expected


async def test_prefix_dispatcher_sends_non_http_to_flet():
    seen = []

    async def flet(scope, receive, send):
        seen.append(("flet", scope["type"]))

    async def helper(scope, receive, send):
        seen.append(("helper", scope["type"]))

    dispatcher = server.PrefixDispatcher(flet, helper)
    await dispatcher({"type": "websocket", "path": "/healthz"}, None, None)
    await dispatcher({"type": "lifespan"}, None, None)
    assert seen == [("flet", "websocket"), ("flet", "lifespan")]


# ---------- settings ----------

@pytest.fixture
def reload_settings(monkeypatch):
    for name in ("HOST", "PORT", "DISCORD_CLIENT_ID", "PROXY_STEAM_IMAGES"):
        monkeypatch.delenv(name, raising=False)
    yield lambda: importlib.reload(settings)
    monkeypatch.undo()
    importlib.reload(settings)


def test_settings_defaults(reload_settings):
    s = reload_settings()
    assert (s.HOST, s.PORT, s.DISCORD_CLIENT_ID, s.PROXY_STEAM_IMAGES) == ("0.0.0.0", 8181, "", True)


def test_settings_from_env(reload_settings, monkeypatch):
    monkeypatch.setenv("HOST", " 127.0.0.1 ")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("DISCORD_CLIENT_ID", " 123456789 ")
    monkeypatch.setenv("PROXY_STEAM_IMAGES", "0")
    s = reload_settings()
    assert (s.HOST, s.PORT, s.DISCORD_CLIENT_ID, s.PROXY_STEAM_IMAGES) == ("127.0.0.1", 9000, "123456789", False)


@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("true", True), ("yes", True), ("On", True), ("", True),
    ("0", False), ("false", False), ("No", False), ("off", False),
])
def test_settings_proxy_flag(reload_settings, monkeypatch, raw, expected):
    monkeypatch.setenv("PROXY_STEAM_IMAGES", raw)
    assert reload_settings().PROXY_STEAM_IMAGES is expected


def test_settings_unrecognised_bool_warns_and_keeps_default(reload_settings, monkeypatch, capsys):
    monkeypatch.setenv("PROXY_STEAM_IMAGES", "maybe")
    assert reload_settings().PROXY_STEAM_IMAGES is True
    assert "PROXY_STEAM_IMAGES" in capsys.readouterr().out


@pytest.mark.parametrize("raw", ["abc", "0", "70000", "-1"])
def test_settings_invalid_port_falls_back(reload_settings, monkeypatch, raw):
    monkeypatch.setenv("PORT", raw)
    assert reload_settings().PORT == 8181


# ---------- module ----------

def test_module_exposes_asgi_app():
    module = importlib.import_module("src.web.server")
    assert isinstance(module.app, server.PrefixDispatcher)
    assert isinstance(module.app.flet_app, server.HtmlInjectMiddleware)
