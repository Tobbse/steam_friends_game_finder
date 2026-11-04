from src.core.export import format_game_titles
from src.core.models import GameItem


def games(*names):
    return [GameItem(i + 1, name) for i, name in enumerate(names)]


def test_one_title_per_line_in_given_order():
    assert format_game_titles(games("Portal 2", "Celeste", "Among Us")) == "Portal 2\nCeleste\nAmong Us"


def test_strips_and_collapses_newlines_and_tabs():
    text = format_game_titles(games("  Padded  ", "Two\nLines", "Tab\tbed", "Mixed \r\n\t Break"))
    assert text.split("\n") == ["Padded", "Two Lines", "Tab bed", "Mixed Break"]


def test_blank_title_falls_back_to_app_id():
    items = [GameItem(440, "   "), GameItem(620, "\n\t"), GameItem(730, "")]
    assert format_game_titles(items) == "App 440\nApp 620\nApp 730"
