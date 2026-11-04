"""Platform for the Flet web app: the user's browser opens links, holds the clipboard and stores the config.

Ported from the ``web`` branch (steam_friends_game_finder.py). Changes from ``web``:

- Flet 0.2x fallbacks are dropped (``page.launch_url``, ``page.client_storage``,
  ``page.shared_preferences``, ``page.set_clipboard[_async]``, ``control.url_target``,
  ``*_async`` storage methods, ``spawn``'s ``asyncio.create_task`` fallback). Only the Flet 1.0
  services ``ft.UrlLauncher``, ``ft.SharedPreferences`` and ``ft.Clipboard`` are used.
- There is no server-owned API key. The user's own key is stored in browser storage with the players.
- ``attach_url``/``link_control`` became ``link_props(url)``, which returns kwargs for a control.
- ``PROXY_STEAM_IMAGES`` is not read here; the caller passes ``proxy_images``.
- ``get_page_origin`` is gone: proxied image URLs are always root-relative (``/steam-img/...``).
  ``page.url`` can name the backend host behind Discord's proxy or a reverse proxy.
- ``is_allowed_image_host`` accepts only well-formed DNS labels before ``.steamstatic.com``.
  ``proxied_image_url`` leaves URLs with userinfo or an explicit port unchanged, and drops the query
  string (the server's cache key is host + path).

The module-level helpers (``is_allowed_image_host``, ``proxied_image_url``) are pure functions and
work without a running page.
"""
import json
import re
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote, urlsplit

import flet as ft

from src.platform.base import ConfigLoadError

STORAGE_KEY = "steam_squad_finder.config.v1"  # Key inside the user's browser localStorage

# Route of the same-origin image proxy served by the web server (Task 4.4). Must match its route.
STEAM_IMAGE_PROXY_PATH = "/steam-img/"
STEAM_IMAGE_EXACT_HOSTS = {"steamcdn-a.akamaihd.net", "steamuserimages-a.akamaihd.net"}
_STEAMSTATIC_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+steamstatic\.com")


def is_allowed_image_host(host: Optional[str]) -> bool:
    """True if `host` is a Steam image CDN host that the image proxy may fetch from."""
    host = (host or "").lower()
    return host in STEAM_IMAGE_EXACT_HOSTS or bool(_STEAMSTATIC_HOST_RE.fullmatch(host))


def proxied_image_url(url: Optional[str], proxy_images: bool = True) -> str:
    """Rewrites a Steam CDN URL to the root-relative image proxy route ``/steam-img/{host}{path}``.

    Returns "" for an empty URL. Returns `url` unchanged if proxying is off, the scheme is not
    http(s), the URL has userinfo or a port, or the host is not a Steam CDN host. The query string
    (e.g. Steam's ``?t=`` cache buster) is dropped, because the server ignores it and caches by host
    and path. The path is not normalized: the server route must reject ``..``.
    """
    if not url:
        return ""
    if not proxy_images:
        return url
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return url
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or port is not None:
        return url
    if not is_allowed_image_host(host):
        return url
    return f"{STEAM_IMAGE_PROXY_PATH}{host}{quote(parts.path or '/', safe='/%._-~')}"


class WebPlatform:
    """Platform for the Flet web app (browser tab or Discord activity).

    Create it inside the Flet session (in the page's main handler): the Flet services it uses
    register with the current page when they are constructed. It keeps references to them so
    Flet does not unregister them.
    """

    name = "web"
    storage_description = "in this browser's local storage (not on the server)"
    api_key_warning_extra = (
        "It is sent to the app server only to make Steam requests. Don't use this on shared computers."
    )
    squad_backup_note = "Discord may occasionally clear Activity storage, so keep a squad code handy."
    import_hint = (
        "Paste a squad code (SSF1:...) or the contents of the desktop app's config file "
        "(steam_friends_game_finder_config.json)"
    )

    def __init__(
        self,
        page: ft.Page,
        proxy_images: bool = True,
        *,
        storage: Optional[Any] = None,
        clipboard: Optional[Any] = None,
        url_launcher: Optional[Any] = None,
    ):
        self.page = page
        self.proxy_images = proxy_images
        self._storage = storage if storage is not None else ft.SharedPreferences()
        self._clipboard = clipboard if clipboard is not None else ft.Clipboard()
        # Created on the first open_url (inside the session's task, so it registers with this page).
        # The UI opens links with link_props, so most sessions never need a launcher service.
        self._url_launcher = url_launcher

    def open_url(self, url: str) -> None:
        """Opens `url` on the USER'S device (browser tab or Discord client), not on the server.

        The launch is a server round-trip, so browsers may block it as a popup. Prefer
        `link_props(url)` on the clicked control, which opens the link on the client.
        """
        if url:
            self.spawn(self._launch_url, url)

    async def _launch_url(self, url: str) -> None:
        try:
            if self._url_launcher is None:
                self._url_launcher = ft.UrlLauncher()
            await self._url_launcher.launch_url(url)
        except Exception as err:
            print(f"Warning: Could not open URL: {err}")

    def link_props(self, url: str) -> dict:
        """Kwargs that make a Flet control a real link opened by the client in a new tab.

        No `on_click`: with both set, the link could open twice."""
        if not url:
            return {}
        return {"url": ft.Url(url, target=ft.UrlTarget.BLANK)}

    def copy_props(self, text: str, on_done: Optional[Callable[[bool], None]] = None) -> dict:
        """Kwargs that make the client copy `text` inside the click itself (`ft.CopyToClipboard`).

        iOS Safari ignores clipboard writes that arrive later from the server. The client runs the
        action before it sends the click to Python, so `on_done` is called with True: the server
        can't see whether the browser's copy worked. Must be called inside a Flet session, because
        `ft.CopyToClipboard` binds to the current page's clipboard service."""
        if not text:
            return {}
        props: dict = {"action": ft.CopyToClipboard(text)}
        if on_done is not None:
            props["on_click"] = lambda _e: on_done(True)
        return props

    async def copy_to_clipboard(self, text: str) -> bool:
        try:
            await self._clipboard.set(text)
            return True
        except Exception:
            return False

    async def load_config(self) -> dict:
        """Returns the config saved in the browser, or defaults.

        Call it only after the page is mounted (after `page.add`): browser storage is a client
        service and needs a connected page.

        Raises `ConfigLoadError` if the storage read itself fails: returning defaults then would let
        the next save overwrite a config that may still be there. Missing, corrupt or invalid data
        returns the defaults.
        """
        config = {"api_key": "", "players": []}
        try:
            raw = await self._storage.get(STORAGE_KEY)
        except Exception as err:
            raise ConfigLoadError(f"Could not read browser storage: {err}") from err
        if not raw:
            return config
        try:
            loaded = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, RecursionError):
            return config
        if isinstance(loaded, dict):
            config["api_key"] = str(loaded.get("api_key", "") or "").strip()
            # Returned as stored, like the desktop platform; build_app sanitizes on both platforms.
            if isinstance(loaded.get("players"), list):
                config["players"] = loaded["players"]
        return config

    async def save_config(self, config: dict) -> None:
        """Writes `api_key` and `players` to browser storage. Errors are reported, not raised.

        A plain write: callers serialize concurrent saves (`build_app` keeps the latest snapshot).
        """
        try:
            payload = json.dumps(
                {"api_key": config.get("api_key", ""), "players": config.get("players", [])},
                separators=(",", ":"),
            )
            saved = await self._storage.set(STORAGE_KEY, payload)
        except Exception as err:
            print(f"Warning: Could not save config to browser storage: {err}")
            return
        if saved is False:
            print("Warning: Could not save config to browser storage: SharedPreferences.set returned False")

    def image_src(self, url: Optional[str]) -> str:
        return proxied_image_url(url, self.proxy_images)

    def spawn(self, coro_fn: Callable[..., Awaitable], *args) -> None:
        self.page.run_task(coro_fn, *args)
