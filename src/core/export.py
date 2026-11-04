"""Plain-text export of game lists. No UI imports."""
import re
from typing import Iterable

from src.core.models import GameItem

# Line breaks (incl. Unicode ones) and tabs, plus any spaces around them
_BREAKS = re.compile(r"\s*[\r\n\t\v\f\x1c-\x1e\x85  ]+\s*")


def format_game_title(name: str) -> str:
    """One game title as a single line: stripped, with line breaks and tabs collapsed to one space."""
    return _BREAKS.sub(" ", name or "").strip()


def format_game_titles(games: Iterable[GameItem]) -> str:
    """The titles of `games`, one per line in the given order, with no trailing newline.
    A title that is blank after cleanup becomes "App <appid>", so every game has a non-empty line."""
    return "\n".join(format_game_title(game.name) or f"App {game.appid}" for game in games)
