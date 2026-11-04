import json
import re
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

import flet as ft

STORAGE_KEY = "steam_squad_finder.config.v1"
STEAM_IMAGE_PROXY_PATH = "/steam-img/"
_STEAM_IMAGE_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+steamstatic\.com")


def is_allowed_image_host(host: Optional[str]) -> bool:
    return bool(_STEAM_IMAGE_HOST_RE.fullmatch((host or "").lower()))


def proxied_image_url(url: str) -> str:
    """Rewrites a Steam CDN URL to the server's image proxy (`/steam-img/{host}{path}`).
    Other URLs are returned unchanged. The query string is dropped."""
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
    """Runs in the user's browser (or a Discord Activity). Create it inside the Flet session."""

    storage_description = "in this browser's local storage (not on the server)"

    def __init__(self):
        self._storage = ft.SharedPreferences()

    def link_props(self, url: str) -> dict:
        # The browser opens the link itself, so popup blockers don't stop it
        return {"url": ft.Url(url, target=ft.UrlTarget.BLANK)}

    def copy_props(self, text: str, on_done: Optional[Callable[[bool], None]] = None) -> dict:
        # The browser copies inside the click; iOS Safari ignores clipboard writes that come later from the server
        if not text:
            return {}
        props: dict = {"action": ft.CopyToClipboard(text)}
        if on_done:
            props["on_click"] = lambda _: on_done(True)
        return props

    async def load_config(self) -> dict:
        try:
            loaded = json.loads(await self._storage.get(STORAGE_KEY))
            return {"api_key": str(loaded.get("api_key", "")).strip(), "players": loaded.get("players", [])}
        except Exception:
            return {"api_key": "", "players": []}

    async def save_config(self, config: dict) -> None:
        try:
            await self._storage.set(STORAGE_KEY, json.dumps(config, separators=(",", ":")))
        except Exception as err:
            print(f"Warning: Could not save config to browser storage: {err}")

    def image_src(self, url: str) -> str:
        return proxied_image_url(url)
