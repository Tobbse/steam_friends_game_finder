from src.core.export import format_game_titles
from src.core.models import GameItem


def games(*names):
    return [GameItem(i + 1, name) for i, name in enumerate(names)]


def test_one_title_per_line_in_given_order():
    assert format_game_titles(games("Portal 2", "Celeste", "Among Us")) == "Portal 2\nCeleste\nAmong Us"


def test_empty_list_gives_empty_string():
    assert format_game_titles([]) == ""


def test_single_game_has_no_trailing_newline():
    assert format_game_titles(games("Hades")) == "Hades"


def test_strips_and_collapses_newlines_and_tabs():
    text = format_game_titles(games("  Padded  ", "Two\nLines", "Tab\tbed", "Mixed \r\n\t Break"))
    assert text.split("\n") == ["Padded", "Two Lines", "Tab bed", "Mixed Break"]


def test_keeps_unicode():
    assert format_game_titles(games("ダークソウル", "Café Ünïcode ™", "🎮 Game")) == "ダークソウル\nCafé Ünïcode ™\n🎮 Game"


def test_accepts_any_iterable():
    assert format_game_titles(iter(games("A", "B"))) == "A\nB"


def test_blank_title_falls_back_to_app_id():
    items = [GameItem(440, "   "), GameItem(620, "\n\t"), GameItem(730, "")]
    assert format_game_titles(items) == "App 440\nApp 620\nApp 730"
