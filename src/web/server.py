"""ASGI web server: the Flet web UI plus a Steam image proxy and the Discord Activity handshake.

Run it with ``uvicorn src.web.server:app``. The module-level ``app`` is built at import time and
needs no running event loop.

Ported from the ``web`` branch (steam_friends_game_finder.py, "WEB SERVER" block). Changes from ``web``:

- No server-owned Steam API key and no ``--desktop`` mode.
- Env settings live in ``src.web.settings``.
- Flet 0.2x fallbacks are dropped: ``ft.run(..., export_asgi_app=True, no_cdn=True)`` is called
  directly instead of probing ``ft.run``/``ft.app`` and their kwargs (``_supported_kwargs``) and
  setting ``view`` (Flet 1.0 ignores ``view`` when it exports an ASGI app).
- ``_flet_runner`` is now the per-session Flet handler. It creates a ``WebPlatform`` for the page.
- The image proxy is stricter: it rejects ``.``/``..``/empty segments and encoded dots, re-encodes the
  path, drops the query string, follows redirects only to allowed hosts, streams with a size cap and
  accepts only raster image types (no SVG).
- The SDK proxy serves only ``@discord/embedded-app-sdk`` paths (no ``X-`` build-arg segments, query
  empty or ``bundle``), follows redirects only on
  ``https://esm.sh`` and streams with a size cap.
- ``/healthz`` and the image proxy also answer HEAD.
- The handshake script queues external links opened before the Discord SDK is ready. After 10 s, or
  on failure, it opens the queued links with the browser's own ``window.open``. A handshake that
  succeeds after the timeout installs the wrapper again.

All helper paths are root-relative (``/steam-img/...``, ``/discord-handshake.js``, ``/discord-sdk/...``).
Inside Discord the activity's root URL mapping (``/``) targets this server, so root-relative paths
reach it unchanged; no ``/.proxy/`` prefix is added (same as the ``web`` branch).
"""
import re
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit

import flet as ft
import httpx

from src.platform.web import STEAM_IMAGE_PROXY_PATH, WebPlatform, is_allowed_image_host
from src.ui.app import build_app
from src.web import settings

# ---------- Upstream HTTP client and image cache ----------
_proxy_http: Optional[httpx.AsyncClient] = None
_image_cache: "OrderedDict[str, Tuple[bytes, str]]" = OrderedDict()
_image_cache_bytes = 0
IMAGE_CACHE_MAX_BYTES = 48 * 1024 * 1024  # Keep RAM usage modest on free hosting tiers (512 MB)
IMAGE_MAX_BYTES = 3 * 1024 * 1024  # Larger upstream responses are rejected
IMAGE_MAX_REDIRECTS = 3
IMAGE_MAX_PATH_LEN = 512
# Raster types only: an SVG served from this origin could run script when opened directly.
ALLOWED_IMAGE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/avif",
    "image/x-icon", "image/vnd.microsoft.icon",
})

# ---------- Discord Activity ----------
_sdk_module_cache: Dict[str, Tuple[bytes, str]] = {}
SDK_MODULE_CACHE_MAX_ENTRIES = 64
SDK_MAX_BYTES = 4 * 1024 * 1024
SDK_MAX_REDIRECTS = 3
ESM_UPSTREAM = "https://esm.sh"
ESM_UPSTREAM_HOST = "esm.sh"
DISCORD_SDK_PROXY_PATH = "/discord-sdk/"
DISCORD_SDK_ENTRY = DISCORD_SDK_PROXY_PATH + "@discord/embedded-app-sdk@2?bundle"
DISCORD_SDK_LOCAL_PATH = "/discord-sdk-local.mjs"
DISCORD_HANDSHAKE_PATH = "/discord-handshake.js"
HEALTHZ_PATH = "/healthz"
# Paths the SDK proxy forwards to esm.sh: the SDK package itself, optionally under an esm.sh build
# prefix ("v135/"). The esm.sh bundle (?bundle) has no further imports; its entry module only
# re-exports "/@discord/embedded-app-sdk@<ver>/es2022/embedded-app-sdk.bundle.mjs".
_SDK_PATH_RE = re.compile(
    r"(?:v\d{1,4}/)?@discord/embedded-app-sdk@[0-9A-Za-z.^~+-]{1,40}(?:/[A-Za-z0-9@._/+~^,=-]{1,250})?"
)
# Optional self-hosted SDK bundle. If this file exists, the handshake imports it instead of the esm.sh proxy.
LOCAL_SDK_FILE = Path(__file__).with_name("discord-embedded-app-sdk.mjs")

DISCORD_HANDSHAKE_JS = r"""
// Discord Activity handshake. Does nothing when opened as a normal website.
(async () => {
  const qs = new URLSearchParams(window.location.search);
  if (!qs.has("frame_id")) return;  // Discord always passes frame_id to Activities
  const m = window.location.hostname.match(/^(\d+)\.discordsays\.com$/i);
  const clientId = "__CLIENT_ID__" || (m ? m[1] : "");
  if (!clientId) { console.warn("[SteamSquadFinder] Discord client id not found"); return; }

  // External links (Steam Store, API key page) must go through Discord's link prompt.
  // Links opened before the SDK is ready are queued and sent once it is.
  const READY_TIMEOUT_MS = 10000;
  const MAX_PENDING_LINKS = 10;
  const nativeOpen = window.open.bind(window);
  const pending = [];
  let sdk = null;
  const externalHref = (url) => {
    try {
      const u = new URL(String(url), window.location.href);
      return u.origin !== window.location.origin ? u.href : null;
    } catch (e) {
      return null;
    }
  };
  const openExternal = (href) => sdk.commands.openExternalLink({ url: href }).catch(() => {});
  const wrappedOpen = (url, target, features) => {
    const href = externalHref(url);
    if (href === null) return nativeOpen(url, target, features);
    if (sdk) openExternal(href);
    else if (pending.length < MAX_PENDING_LINKS) pending.push(href);
    else console.warn("[SteamSquadFinder] Discord SDK not ready, link dropped:", href);
    return null;
  };
  // Timeout or failure: restore window.open and open the queued links natively. Popup blockers will
  // usually stop these calls, because they no longer run inside the user's click.
  const fallBack = () => {
    window.open = nativeOpen;
    pending.splice(0).forEach((href) => nativeOpen(href, "_blank", "noopener"));
  };
  window.open = wrappedOpen;

  // After the timeout the handshake keeps going; if it succeeds later, the wrapper is installed again.
  const timer = setTimeout(fallBack, READY_TIMEOUT_MS);
  try {
    const mod = await import("__SDK_URL__");
    const client = new mod.DiscordSDK(clientId);
    await client.ready();
    clearTimeout(timer);
    sdk = client;
    window.__discordSdk = client;
    window.open = wrappedOpen;
    pending.splice(0).forEach(openExternal);
    console.log("[SteamSquadFinder] Discord SDK ready");
  } catch (err) {
    clearTimeout(timer);
    fallBack();
    console.error("[SteamSquadFinder] Discord SDK handshake failed", err);
  }
})();
"""

HANDSHAKE_TAG = f'<script type="module" src="{DISCORD_HANDSHAKE_PATH}"></script>'.encode("ascii")


def _get_proxy_http() -> httpx.AsyncClient:
    """Shared upstream client. Redirects are off: callers decide which redirects to follow."""
    global _proxy_http
    if _proxy_http is None:
        _proxy_http = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"User-Agent": "SteamSquadFinder/1.0 (+image proxy)"},
        )
    return _proxy_http


def _cache_image(key: str, body: bytes, content_type: str):
    """Stores an image in the in-memory LRU cache and evicts the oldest entries above the size limit."""
    global _image_cache_bytes
    old = _image_cache.pop(key, None)
    if old is not None:
        _image_cache_bytes -= len(old[0])
    _image_cache[key] = (body, content_type)
    _image_cache_bytes += len(body)
    while _image_cache_bytes > IMAGE_CACHE_MAX_BYTES and _image_cache:
        _, (evicted, _) = _image_cache.popitem(last=False)
        _image_cache_bytes -= len(evicted)


def _clear_caches():
    """Empties the image and SDK caches (for tests)."""
    global _image_cache_bytes
    _image_cache.clear()
    _image_cache_bytes = 0
    _sdk_module_cache.clear()


def _rewrite_esm_imports(js: str) -> str:
    """Rewrites absolute esm.sh import paths ("/x") to go through this server's /discord-sdk/ proxy."""
    return re.sub(r"((?:\bfrom|\bimport)\s*\(?\s*)([\"'])/(?!/)", r"\1\2" + DISCORD_SDK_PROXY_PATH, js)


def _is_safe_image_path(path: str) -> bool:
    """False for empty or long paths, ``.``/``..``/empty segments, encoded dots, backslashes and control chars.

    `path` is already percent-decoded once by the server, so a remaining "%2e" means double encoding.
    """
    if not path or len(path) > IMAGE_MAX_PATH_LEN:
        return False
    lowered = path.lower()
    if ".." in lowered or "%2e" in lowered or "\\" in path or "%5c" in lowered:
        return False
    if any(segment in ("", ".") for segment in path.split("/")):
        return False
    return not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path)


def _image_upstream_url(host: str, path: str) -> str:
    """Builds ``https://{host}/{path}``. The query string is dropped: it is also the cache key, and
    a free ``?t=`` would let anyone fill the cache with copies of one image."""
    return f"https://{host.lower()}/{quote(path, safe='/._-~')}"


def _redirect_target(url: str, location: str, host_ok: Callable[[Optional[str]], bool]) -> Optional[str]:
    """Absolute redirect target if it is https, without userinfo or port, on a host `host_ok` accepts."""
    try:
        target = urljoin(url, location)
        parts = urlsplit(target)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or "@" in parts.netloc or port is not None or not host_ok(parts.hostname):
        return None
    return target


class _UpstreamRejected(Exception):
    def __init__(self, status_code: int):
        super().__init__(status_code)
        self.status_code = status_code


async def _fetch_image(url: str) -> Tuple[bytes, str]:
    """Fetches an image. Follows redirects only to allowed https hosts and stops reading at IMAGE_MAX_BYTES.

    Raises `_UpstreamRejected` with the status code to return, or httpx errors.
    """
    client = _get_proxy_http()
    for _ in range(IMAGE_MAX_REDIRECTS + 1):
        async with client.stream("GET", url) as resp:
            if resp.is_redirect:
                target = _redirect_target(url, resp.headers.get("location", ""), is_allowed_image_host)
                if target is None:
                    raise _UpstreamRejected(502)
                url = target
                continue
            if resp.status_code != 200:
                raise _UpstreamRejected(404)
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in ALLOWED_IMAGE_TYPES:
                raise _UpstreamRejected(502)
            length = resp.headers.get("content-length", "")
            if length.isdigit() and int(length) > IMAGE_MAX_BYTES:
                raise _UpstreamRejected(502)
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > IMAGE_MAX_BYTES:
                    raise _UpstreamRejected(502)
            return bytes(body), content_type
    raise _UpstreamRejected(502)


def _is_esm_host(host: Optional[str]) -> bool:
    return (host or "").lower() == ESM_UPSTREAM_HOST


async def _fetch_sdk_module(url: str) -> bytes:
    """Fetches an esm.sh module. Follows redirects only on https://esm.sh and stops reading at SDK_MAX_BYTES.

    Raises `_UpstreamRejected` or httpx errors.
    """
    client = _get_proxy_http()
    for _ in range(SDK_MAX_REDIRECTS + 1):
        async with client.stream("GET", url) as resp:
            if resp.is_redirect:
                target = _redirect_target(url, resp.headers.get("location", ""), _is_esm_host)
                if target is None:
                    raise _UpstreamRejected(502)
                url = target
                continue
            if resp.status_code != 200:
                raise _UpstreamRejected(502)
            length = resp.headers.get("content-length", "")
            if length.isdigit() and int(length) > SDK_MAX_BYTES:
                raise _UpstreamRejected(502)
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > SDK_MAX_BYTES:
                    raise _UpstreamRejected(502)
            return bytes(body)
    raise _UpstreamRejected(502)


def build_web_routes():
    """Small FastAPI app with the helper routes; everything else is handled by Flet."""
    from fastapi import FastAPI, Request, Response

    routes = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @routes.api_route(HEALTHZ_PATH, methods=["GET", "HEAD"])
    async def healthz():
        return {"ok": True}

    @routes.get(DISCORD_HANDSHAKE_PATH)
    async def discord_handshake():
        sdk_url = DISCORD_SDK_LOCAL_PATH if LOCAL_SDK_FILE.is_file() else DISCORD_SDK_ENTRY
        js = DISCORD_HANDSHAKE_JS.replace("__CLIENT_ID__", re.sub(r"[^0-9]", "", settings.DISCORD_CLIENT_ID))
        js = js.replace("__SDK_URL__", sdk_url)
        return Response(js, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @routes.get(DISCORD_SDK_LOCAL_PATH)
    async def discord_sdk_local():
        if not LOCAL_SDK_FILE.is_file():
            return Response(status_code=404)
        return Response(LOCAL_SDK_FILE.read_bytes(), media_type="text/javascript")

    @routes.get(DISCORD_SDK_PROXY_PATH + "{path:path}")
    async def discord_sdk_proxy(path: str, request: Request):
        # Discord's sandbox blocks third-party CDNs, so the SDK is fetched server-side and served same-origin
        query = request.url.query
        if not re.fullmatch(r"[A-Za-z0-9@._/+~^,=-]{1,300}", path) or ".." in path or query not in ("", "bundle"):
            return Response(status_code=400)
        if any(segment.lower().startswith("x-") for segment in path.split("/")):
            return Response(status_code=400)  # esm.sh build-arg segments
        if not _SDK_PATH_RE.fullmatch(path):
            return Response(status_code=404)
        cache_key = f"{path}?{query}"
        cached = _sdk_module_cache.get(cache_key)
        if cached is None:
            try:
                raw = await _fetch_sdk_module(f"{ESM_UPSTREAM}/{path}" + (f"?{query}" if query else ""))
            except _UpstreamRejected as rejected:
                return Response(status_code=rejected.status_code)
            except httpx.HTTPError:
                return Response(status_code=502)
            body = _rewrite_esm_imports(raw.decode("utf-8", errors="replace")).encode("utf-8")
            cached = (body, "text/javascript")
            if len(_sdk_module_cache) < SDK_MODULE_CACHE_MAX_ENTRIES:
                _sdk_module_cache[cache_key] = cached
        return Response(cached[0], media_type=cached[1], headers={"Cache-Control": "public, max-age=86400"})

    @routes.api_route(STEAM_IMAGE_PROXY_PATH + "{host}/{path:path}", methods=["GET", "HEAD"])
    async def steam_image_proxy(host: str, path: str):
        if not settings.PROXY_STEAM_IMAGES:
            return Response(status_code=404)
        if not is_allowed_image_host(host):
            return Response(status_code=403)
        if not _is_safe_image_path(path):
            return Response(status_code=400)
        upstream = _image_upstream_url(host, path)
        cached = _image_cache.get(upstream)
        if cached is not None:
            _image_cache.move_to_end(upstream)
        else:
            try:
                cached = await _fetch_image(upstream)
            except _UpstreamRejected as rejected:
                return Response(status_code=rejected.status_code)
            except httpx.HTTPError:
                return Response(status_code=502)
            _cache_image(upstream, *cached)
        return Response(
            cached[0],
            media_type=cached[1],
            headers={
                "Cache-Control": "public, max-age=604800",
                "Access-Control-Allow-Origin": "*",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return routes


class HtmlInjectMiddleware:
    """Injects the Discord handshake <script> into Flet's index.html responses (pure ASGI, no buffering otherwise)."""

    def __init__(self, app, tag: bytes):
        self.app = app
        self.tag = tag

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        start_message = None
        body_chunks: List[bytes] = []
        passthrough = False

        async def wrapped_send(message):
            nonlocal start_message, passthrough
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                ctype = headers.get(b"content-type", b"").lower()
                if b"text/html" in ctype and b"content-encoding" not in headers and scope.get("method") == "GET":
                    start_message = message
                else:
                    passthrough = True
                    await send(message)
                return
            if message["type"] == "http.response.body" and not passthrough and start_message is not None:
                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(body_chunks)
                if self.tag not in body:
                    idx = body.lower().find(b"</head>")
                    body = body[:idx] + self.tag + body[idx:] if idx != -1 else self.tag + body
                headers = [(k, v) for k, v in start_message.get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode("latin-1")))
                await send({**start_message, "headers": headers})
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, wrapped_send)


class PrefixDispatcher:
    """Routes helper paths to the FastAPI helper app and everything else (incl. lifespan/WebSocket) to Flet."""

    PREFIXES = (
        STEAM_IMAGE_PROXY_PATH,
        DISCORD_SDK_PROXY_PATH,
        DISCORD_SDK_LOCAL_PATH,
        DISCORD_HANDSHAKE_PATH,
        HEALTHZ_PATH,
    )

    def __init__(self, flet_app, helper_app):
        self.flet_app = flet_app
        self.helper_app = helper_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith(self.PREFIXES):
            await self.helper_app(scope, receive, send)
        else:
            await self.flet_app(scope, receive, send)


async def _flet_runner(page: ft.Page):
    """Flet session handler: one WebPlatform per browser session."""
    await build_app(page, WebPlatform(page, proxy_images=settings.PROXY_STEAM_IMAGES))


def build_asgi_app():
    """Creates the full ASGI app: Flet web UI + helper routes + Discord handshake injection."""
    # no_cdn: serve CanvasKit and fonts from THIS server, because Discord's sandbox blocks external CDNs
    flet_asgi = ft.run(_flet_runner, export_asgi_app=True, no_cdn=True)
    return PrefixDispatcher(HtmlInjectMiddleware(flet_asgi, HANDSHAKE_TAG), build_web_routes())


# Module-level ASGI app:  uvicorn src.web.server:app --host 0.0.0.0 --port $PORT
app = build_asgi_app()
