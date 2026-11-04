from typing import Callable, Optional, Protocol


class Platform(Protocol):
    """What differs between the desktop app and the web app."""

    storage_description: str  # fits after "saved", e.g. "on disk in config.json"

    def link_props(self, url: str) -> dict:
        """Kwargs that make a clickable control open `url`. Callers must not set `on_click` themselves."""
        ...

    def copy_props(self, text: str, on_done: Optional[Callable[[bool], None]] = None) -> dict:
        """Kwargs that make a clickable control copy `text`, then call `on_done(ok)`."""
        ...

    async def load_config(self) -> dict:
        """The saved {"api_key": str, "players": list}, or empty defaults."""
        ...

    async def save_config(self, config: dict) -> None:
        ...

    def image_src(self, url: str) -> str:
        ...
