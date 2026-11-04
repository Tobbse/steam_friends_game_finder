import pytest

from src.core.models import GameItem, Player


@pytest.mark.parametrize("discount, on_sale", [(0, False), (1, True), (75, True)])
def test_is_on_sale_follows_discount_pct(discount, on_sale):
    game = GameItem(1, "Game")
    game.discount_pct = discount
    assert game.is_on_sale is on_sale


def _game(labels=(), genres=()):
    game = GameItem(1, "Game")
    game.categories = set(labels)
    game.genres = set(genres)
    return game


# ---------- label-based properties ----------

@pytest.mark.parametrize(
    "labels, expected",
    [
        (["Online Co-op"], True),
        (["Local Co-op"], True),
        (["Cooperative"], True),
        (["Single-player"], False),
        ([], False),
    ],
)
def test_is_coop(labels, expected):
    assert _game(labels).is_coop is expected


@pytest.mark.parametrize(
    "labels, expected",
    [
        (["Multi-player"], True),
        (["Massively Multiplayer"], True),
        (["Online PvP"], True),
        (["Single-player"], False),
        (["Online Co-op"], False),
    ],
)
def test_is_multiplayer(labels, expected):
    assert _game(labels).is_multiplayer is expected


@pytest.mark.parametrize(
    "labels, expected",
    [
        (["Online Co-op"], True),
        (["Online PvP"], True),
        (["MMORPG"], True),
        (["Massively Multiplayer"], True),
        (["Local Co-op"], False),
        (["Shared/Split Screen PvP"], False),
    ],
)
def test_is_online(labels, expected):
    assert _game(labels).is_online is expected


@pytest.mark.parametrize(
    "labels, expected",
    [
        (["VR"], True),
        (["VR Supported"], True),
        (["Tracked Motion Controller Support", "Full VR"], True),
        (["Virtual Reality"], True),
        (["Survival"], False),
        (["Driving"], False),
    ],
)
def test_is_vr(labels, expected):
    assert _game(labels).is_vr is expected


# ---------- is_free ----------


# ---------- unreleased vs unavailable ----------

def test_unavailable_flag_alone_makes_game_unavailable():
    game = GameItem(1, "Game")
    game.is_unavailable = True
    assert game.is_effectively_unreleased is False
    assert game.is_effectively_unavailable is True


@pytest.mark.parametrize(
    "field, value",
    [
        ("is_unreleased", True),
        ("release_date", "Coming soon"),
        ("release_date", "To be announced"),
        ("price_str", "Coming Soon"),
    ],
)
def test_unreleased_game_is_never_unavailable(field, value):
    game = GameItem(1, "Game")
    game.is_unavailable = True
    setattr(game, field, value)
    assert game.is_effectively_unreleased is True
    assert game.is_effectively_unavailable is False


@pytest.mark.parametrize("release_date", ["2027", "Q4 2026", "Dec 20, 2099", "December 2099", "Jan 15, 2020"])
def test_release_date_text_without_keyword_is_released(release_date):
    # Date text is no longer parsed: Steam's flags set is_unreleased.
    game = GameItem(1, "Game")
    game.release_date = release_date
    assert game.is_effectively_unreleased is False


# ---------- Player ----------

def test_player_display_name_falls_back_to_steam_id():
    assert Player("76561198000000001").display_name == "76561198000000001"
    assert Player("76561198000000001", "").display_name == "76561198000000001"


# ---------- price text is checked for keywords only ----------


