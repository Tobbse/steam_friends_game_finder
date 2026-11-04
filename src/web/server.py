"""ASGI app: the Flet web UI, a Steam image proxy and the Discord Activity handshake.

Inside a Discord Activity the browser can only reach this server, so Steam images and the
Discord SDK are fetched here and served from the same origin.
"""
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import quote

import flet as ft
import httpx
from fastapi import FastAPI, Response

from src.platform.web import STEAM_IMAGE_PROXY_PATH, WebPlatform, is_allowed_image_host
from src.ui.app import build_app

# favicon.png and icons/loading-animation.png here replace Flet's defaults
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

IMAGE_CACHE_MAX_BYTES = 48 * 1024 * 1024
IMAGE_MAX_BYTES = 3 * 1024 * 1024
# No SVG: served from this origin, it could run script
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"}

# Bump the version here to update the SDK
DISCORD_SDK_URL = "https://esm.sh/@discord/embedded-app-sdk@2.5.0/es2022/embedded-app-sdk.bundle.mjs"
DISCORD_SDK_PATH = "/discord-sdk.mjs"
DISCORD_HANDSHAKE_PATH = "/discord-handshake.js"

DISCORD_HANDSHAKE_JS = """
// Does nothing outside Discord
(async () => {
  if (!new URLSearchParams(location.search).has("frame_id")) return;
  const m = location.hostname.match(/^(\\d+)\\.discordsays\\.com$/i);
  if (!m) return;

  // Discord's sandbox blocks window.open, so external links go through the SDK.
  // Links clicked before the SDK is ready are queued.
  const nativeOpen = window.open.bind(window);
  const pending = [];
  let sdk = null;
  const openExternal = (url) => sdk.commands.openExternalLink({ url }).catch(() => {});
  window.open = (url, target, features) => {
    const u = new URL(String(url), location.href);
    if (u.origin === location.origin) return nativeOpen(url, target, features);
    if (sdk) openExternal(u.href);
    else if (pending.length < 10) pending.push(u.href);
    return null;
  };

  try {
    const { DiscordSDK } = await import("%s");
    const client = new DiscordSDK(m[1]);
    await client.ready();
    sdk = client;
    pending.splice(0).forEach(openExternal);
  } catch (err) {
    window.open = nativeOpen;
    console.error("Discord SDK handshake failed", err);
  }
})();
""" % DISCORD_SDK_PATH

HANDSHAKE_TAG = f'<script type="module" src="{DISCORD_HANDSHAKE_PATH}"></script>'.encode()

_http: Optional[httpx.AsyncClient] = None
_image_cache: "OrderedDict[str, Tuple[bytes, str]]" = OrderedDict()
_image_cache_bytes = 0
_discord_sdk: Optional[bytes] = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    return _http


def _cache_image(key: str, body: bytes, content_type: str):
    global _image_cache_bytes
    _image_cache[key] = (body, content_type)
    _image_cache_bytes += len(body)
    while _image_cache_bytes > IMAGE_CACHE_MAX_BYTES:
        _, (evicted, _) = _image_cache.popitem(last=False)
        _image_cache_bytes -= len(evicted)


def is_safe_image_path(path: str) -> bool:
    return 0 < len(path) <= 512 and ".." not in path and "\\" not in path and path.isprintable()


def build_routes() -> FastAPI:
    routes = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @routes.get(STEAM_IMAGE_PROXY_PATH + "{host}/{path:path}")
    async def steam_image(host: str, path: str):
        if not is_allowed_image_host(host) or not is_safe_image_path(path):
            return Response(status_code=404)
        url = f"https://{host.lower()}/{quote(path, safe='/._-~')}"
        cached = _image_cache.get(url)
        if cached:
            _image_cache.move_to_end(url)
        else:
            try:
                resp = await _client().get(url)
            except httpx.HTTPError:
                return Response(status_code=502)
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code != 200 or content_type not in IMAGE_TYPES or len(resp.content) > IMAGE_MAX_BYTES:
                return Response(status_code=404)
            cached = (resp.content, content_type)
            _cache_image(url, *cached)
        return Response(cached[0], media_type=cached[1], headers={"Cache-Control": "public, max-age=604800"})

    @routes.get(DISCORD_HANDSHAKE_PATH)
    async def discord_handshake():
        return Response(DISCORD_HANDSHAKE_JS, media_type="text/javascript")

    @routes.get(DISCORD_SDK_PATH)
    async def discord_sdk():
        global _discord_sdk
        if _discord_sdk is None:
            try:
                resp = await _client().get(DISCORD_SDK_URL)
                resp.raise_for_status()
            except httpx.HTTPError as err:
                print(f"Warning: Could not fetch the Discord SDK: {err}")
                return Response(status_code=502)
            _discord_sdk = resp.content
        return Response(_discord_sdk, media_type="text/javascript", headers={"Cache-Control": "public, max-age=86400"})

    return routes


class InjectHandshake:
    """Adds the Discord handshake <script> to Flet's index.html."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return

        start = None
        chunks = []

        async def wrapped_send(message):
            nonlocal start
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                if b"text/html" in headers.get(b"content-type", b"") and b"content-encoding" not in headers:
                    start = message
                    return
            elif message["type"] == "http.response.body" and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks).replace(b"</head>", HANDSHAKE_TAG + b"</head>", 1)
                headers = [(k, v) for k, v in start["headers"] if k != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        await self.app(scope, receive, wrapped_send)


def build_asgi_app():
    routes = build_routes()

    async def session(page: ft.Page):
        await build_app(page, WebPlatform())

    # no_cdn: Discord's sandbox blocks external CDNs, so Flet serves its own assets
    flet_app = InjectHandshake(ft.run(session, export_asgi_app=True, no_cdn=True, assets_dir=str(ASSETS_DIR)))
    helper_paths = (STEAM_IMAGE_PROXY_PATH, DISCORD_HANDSHAKE_PATH, DISCORD_SDK_PATH)

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(helper_paths):
            await routes(scope, receive, send)
        else:
            await flet_app(scope, receive, send)

    return app


app = build_asgi_app()
