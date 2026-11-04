import re
from typing import Iterable

from src.core.models import GameItem

_BREAKS = re.compile(r"\s*[\r\n\t\v\f\x1c-\x1e\x85  ]+\s*")


def format_game_title(name: str) -> str:
    """The title on one line: line breaks and tabs become a single space."""
    return _BREAKS.sub(" ", name or "").strip()


def format_game_titles(games: Iterable[GameItem]) -> str:
    """One title per line. A blank title becomes "App <appid>"."""
    return "\n".join(format_game_title(game.name) or f"App {game.appid}" for game in games)
