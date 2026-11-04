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


def test_is_coop_reads_genres_too():
    assert _game(genres=["Co-op"]).is_coop is True


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

def test_is_free_with_zero_price():
    game = GameItem(1, "Game")
    game.final_price = 0.0
    assert game.is_free is True


def test_is_free_with_free_text():
    game = GameItem(1, "Game")
    game.price_str = "Free to Play"
    assert game.is_free is True


def test_is_free_false_for_paid_game():
    game = GameItem(1, "Game")
    game.final_price = 19.99
    game.price_str = "$19.99"
    assert game.is_free is False


def test_is_free_false_without_price_info():
    assert GameItem(1, "Game").is_free is False


def test_unreleased_game_is_never_free():
    game = GameItem(1, "Game")
    game.final_price = 0.0
    game.is_unreleased = True
    assert game.is_free is False


# ---------- unreleased vs unavailable ----------

def test_unavailable_flag_alone_makes_game_unavailable():
    game = GameItem(1, "Game")
    game.is_unavailable = True
    assert game.is_effectively_unreleased is False
    assert game.is_effectively_unavailable is True


def test_default_game_is_neither_unreleased_nor_unavailable():
    game = GameItem(1, "Game")
    assert game.is_effectively_unreleased is False
    assert game.is_effectively_unavailable is False


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


def test_player_display_name_kept_when_given():
    player = Player("76561198000000001", "Alice", "http://avatar")
    assert player.display_name == "Alice"
    assert player.avatar_url == "http://avatar"


def test_player_defaults():
    player = Player("1")
    assert player.owned_games == {}
    assert player.wishlist_games == {}
    assert player.is_private is False
    assert player.fetch_error is None
    assert player.library_fetched is False


# ---------- price text is checked for keywords only ----------

@pytest.mark.parametrize("price_str", ["2031,00 kr", "$2099.00", "Q1 2099", "Dec 20, 2099"])
def test_price_text_with_year_like_number_is_not_unreleased(price_str):
    game = GameItem(1, "Game")
    game.price_str = price_str
    game.final_price = 2031.0
    assert game.is_effectively_unreleased is False
    assert game.is_free is False
    assert game.is_effectively_unavailable is False


def test_unavailable_game_with_year_like_price_stays_unavailable():
    game = GameItem(1, "Game")
    game.price_str = "2031,00 kr"
    game.final_price = 2031.0
    game.is_unavailable = True
    assert game.is_effectively_unavailable is True


def test_free_price_text_with_year_like_number_is_free():
    game = GameItem(1, "Game")
    game.price_str = "Free until Dec 20, 2099"
    assert game.is_effectively_unreleased is False
    assert game.is_free is True
