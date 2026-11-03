from typing import Awaitable, Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class Platform(Protocol):
    """Everything the shared UI needs that differs between the desktop app and the web app."""

    name: str  # "desktop" or "web"

    def open_url(self, url: str) -> None:
        """Opens `url` in a browser on the user's device."""
        ...

    async def copy_to_clipboard(self, text: str) -> bool:
        """Copies `text` to the user's clipboard. Returns True on success."""
        ...

    async def load_config(self) -> dict:
        """Returns the saved config as {"api_key": str, "players": [...]}, or defaults if none is saved."""
        ...

    async def save_config(self, config: dict) -> None:
        """Persists the config. Errors are reported, not raised."""
        ...

    def image_src(self, url: str) -> str:
        """Maps a remote image URL to the src the UI should use (desktop returns it unchanged)."""
        ...

    def server_api_key(self) -> Optional[str]:
        """A Steam API key provided by the host, if any (desktop returns None)."""
        ...

    def spawn(self, coro_fn: Callable[..., Awaitable], *args) -> None:
        """Schedules `coro_fn(*args)` as a background task on the page's event loop.
        `coro_fn` must be an `async def` function. A lambda or `functools.partial` that returns a
        coroutine is rejected, because Flet's `page.run_task` requires a coroutine function."""
        ...
