import json
import webbrowser
from pathlib import Path
from typing import Awaitable, Callable, Optional

import flet as ft

DEFAULT_CONFIG_PATH = Path("steam_friends_game_finder_config.json")
# The config file's name before it was renamed. Only read as a fallback; never written or removed.
LEGACY_CONFIG_NAME = "steam_finder_config.json"


class DesktopPlatform:
    """Platform for the native desktop app: system browser, local JSON config file."""

    name = "desktop"

    def __init__(self, page: ft.Page, config_path: Optional[Path] = None):
        self.page = page
        self.config_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH

    def open_url(self, url: str) -> None:
        """Opens a URL in the user's default system web browser."""
        try:
            webbrowser.open(url)
        except Exception:
            pass

    async def copy_to_clipboard(self, text: str) -> bool:
        try:
            await ft.Clipboard().set(text)
            return True
        except Exception:
            return False

    async def load_config(self) -> dict:
        config = {"api_key": "", "players": []}
        path = self.config_path
        if not path.exists():
            legacy_path = path.with_name(LEGACY_CONFIG_NAME)
            if legacy_path.exists():
                path = legacy_path
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

    def image_src(self, url: str) -> str:
        return url

    def server_api_key(self) -> Optional[str]:
        return None

    def spawn(self, coro_fn: Callable[..., Awaitable], *args) -> None:
        self.page.run_task(coro_fn, *args)
