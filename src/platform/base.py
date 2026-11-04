from typing import Awaitable, Callable, Optional, Protocol, runtime_checkable


class ConfigLoadError(Exception):
    """The saved config exists (or may exist) but couldn't be read, e.g. browser storage failed.

    Distinct from "nothing saved": the app must not save over a config it couldn't read.
    """


@runtime_checkable
class Platform(Protocol):
    """Everything the shared UI needs that differs between the desktop app and the web app."""

    name: str  # "desktop" or "web"

    # Where the config (API key + players) is kept, as a phrase that fits after "saved"/"stored",
    # e.g. "on disk in steam_friends_game_finder_config.json". Only used for UI wording.
    storage_description: str
    # Extra sentence after the API-key storage warning, or "".
    api_key_warning_extra: str
    # Extra sentence after the "Backup/share" line in the add-player help, or "".
    squad_backup_note: str
    # Hint text in the Import Squad field: what can be pasted.
    import_hint: str

    def open_url(self, url: str) -> None:
        """Opens `url` in a browser on the user's device."""
        ...

    def link_props(self, url: str) -> dict:
        """All kwargs a clickable control (`TextSpan`, `IconButton`, `Button`, `Container`, ...) needs to
        open `url`, e.g. `ft.IconButton(..., **platform.link_props(url))`. Call sites must not set
        `on_click` themselves. Desktop returns `{"on_click": handler}` (the handler takes the event and
        calls `open_url`); web returns `{"url": ft.Url(url, target=BLANK)}` and no `on_click`, so the
        client opens the link and it can't open twice. An empty `url` returns {}."""
        ...

    def copy_props(self, text: str, on_done: Optional[Callable[[bool], None]] = None) -> dict:
        """All kwargs a clickable control (`Button`, `IconButton`, `Container`, ...) needs to copy
        `text` to the clipboard when clicked, e.g. `ft.Button(..., **platform.copy_props(code))`.
        Call sites must not set `on_click` themselves. `on_done(ok)` runs after the click, e.g. to show
        a toast. Desktop returns `{"on_click": handler}` (the handler awaits `copy_to_clipboard`); web
        returns `{"action": ft.CopyToClipboard(text)}` so the browser copies inside the click (iOS
        Safari ignores clipboard writes from the server), plus an `on_click` that calls `on_done(True)`
        if `on_done` is given. Web must be called inside a Flet session. An empty `text` returns {}."""
        ...

    async def copy_to_clipboard(self, text: str) -> bool:
        """Copies `text` to the user's clipboard. Returns True on success."""
        ...

    async def load_config(self) -> dict:
        """Returns the saved config as {"api_key": str, "players": [...]}, or defaults if none is saved
        (or the saved data is unusable). May raise `ConfigLoadError` if the storage itself can't be
        read; the UI then doesn't save in that session."""
        ...

    async def save_config(self, config: dict) -> None:
        """Persists the config. Errors are reported, not raised."""
        ...

    def image_src(self, url: Optional[str]) -> str:
        """Maps a remote image URL to the src the UI should use (desktop returns it unchanged).
        Never returns None: an empty or None `url` gives ""."""
        ...

    def spawn(self, coro_fn: Callable[..., Awaitable], *args) -> None:
        """Schedules `coro_fn(*args)` as a background task on the page's event loop.
        `coro_fn` must be an `async def` function. A lambda or `functools.partial` that returns a
        coroutine is rejected, because Flet's `page.run_task` requires a coroutine function."""
        ...
