import json
import webbrowser
from pathlib import Path
from typing import Callable, Optional

import flet as ft

CONFIG_PATH = Path("steam_friends_game_finder_config.json")


class DesktopPlatform:
    storage_description = f"on disk in {CONFIG_PATH.name}"

    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path

    def link_props(self, url: str) -> dict:
        return {"on_click": lambda _: webbrowser.open(url)}

    def copy_props(self, text: str, on_done: Optional[Callable[[bool], None]] = None) -> dict:
        async def handler(_):
            try:
                await ft.Clipboard().set(text)
                ok = True
            except Exception:
                ok = False
            if on_done:
                on_done(ok)

        return {"on_click": handler} if text else {}

    async def load_config(self) -> dict:
        try:
            loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
            return {"api_key": str(loaded.get("api_key", "")), "players": loaded.get("players", [])}
        except Exception:
            return {"api_key": "", "players": []}

    async def save_config(self, config: dict) -> None:
        try:
            self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except OSError as err:
            print(f"Warning: Could not save config file: {err}")

    def image_src(self, url: str) -> str:
        return url
