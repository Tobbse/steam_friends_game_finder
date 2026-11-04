import json
import webbrowser
from pathlib import Path
from typing import Awaitable, Callable, Optional

import flet as ft

DEFAULT_CONFIG_PATH = Path("steam_friends_game_finder_config.json")


class DesktopPlatform:
    """Platform for the native desktop app: system browser, local JSON config file."""

    name = "desktop"
    api_key_warning_extra = ""
    squad_backup_note = ""

    def __init__(self, page: ft.Page, config_path: Optional[Path] = None):
        self.page = page
        self.config_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH

    @property
    def storage_description(self) -> str:
        return f"on disk in {self.config_path.name}"

    @property
    def import_hint(self) -> str:
        return (
            "Paste a squad code (SSF1:...) or the contents of a config file "
            f"({self.config_path.name})"
        )

    def open_url(self, url: str) -> None:
        """Opens a URL in the user's default system web browser."""
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def link_props(self, url: str) -> dict:
        """An `on_click` handler that opens `url` in the system browser."""
        if not url:
            return {}
        return {"on_click": lambda _e: self.open_url(url)}

    def copy_props(self, text: str, on_done: Optional[Callable[[bool], None]] = None) -> dict:
        """An async `on_click` handler that copies `text`, then calls `on_done(ok)`."""
        if not text:
            return {}

        async def handler(_e):
            ok = await self.copy_to_clipboard(text)
            if on_done is not None:
                on_done(ok)

        return {"on_click": handler}

    async def copy_to_clipboard(self, text: str) -> bool:
        try:
            await ft.Clipboard().set(text)
            return True
        except Exception:
            return False

    async def load_config(self) -> dict:
        config = {"api_key": "", "players": []}
        path = self.config_path
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    config["api_key"] = str(loaded.get("api_key", ""))
                    if isinstance(loaded.get("players"), list):
                        config["players"] = loaded["players"]
            except Exception:
                pass
        return config

    async def save_config(self, config: dict) -> None:
        try:
            self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except OSError as err:
            print(f"Warning: Could not save config file: {err}")

    def image_src(self, url: Optional[str]) -> str:
        return url or ""

    def spawn(self, coro_fn: Callable[..., Awaitable], *args) -> None:
        self.page.run_task(coro_fn, *args)
