import pytest

from src.core.filtering import (
    SORT_DEFAULT_DESCENDING,
    SORT_OPTIONS,
    FilterSettings,
    filter_games,
    sort_games,
)


def names(games):
    return [g.name for g in games]


def no_owner_floor(**kwargs):
    """Settings that let every game through the owned/wishlist check (AND with both thresholds at 0)."""
    return FilterSettings(min_owned=0, min_wishlist=0, **kwargs)


# ------------------------------------------------------------------
# Owned / wishlist thresholds
# ------------------------------------------------------------------
def test_and_mode_needs_both_thresholds(players, make_game):
    p = players
    games = [
        make_game(1, "both", owners=p[:2], wishlisters=p[:1]),
        make_game(2, "owned only", owners=p[:2]),
        make_game(3, "wishlist only", wishlisters=p[:2]),
        make_game(4, "too few owners", owners=p[:1], wishlisters=p[:1]),
    ]
    s = FilterSettings(min_owned=2, min_wishlist=1, owned_wishlist_mode="AND")
    assert names(filter_games(games, s)) == ["both"]


def test_or_mode_matches_either_threshold(players, make_game):
    p = players
    games = [
        make_game(1, "owned", owners=p[:2]),
        make_game(2, "wishlisted", wishlisters=p[:1]),
        make_game(3, "neither", owners=p[:1]),
    ]
    s = FilterSettings(min_owned=2, min_wishlist=1, owned_wishlist_mode="OR")
    assert names(filter_games(games, s)) == ["owned", "wishlisted"]


def test_or_mode_leaves_zero_threshold_out(players, make_game):
    p = players
    games = [
        make_game(1, "owned by 2", owners=p[:2]),
        make_game(2, "owned by 1", owners=p[:1]),
        make_game(3, "nobody", wishlisters=p[:3]),
    ]
    s = FilterSettings(min_owned=2, min_wishlist=0, owned_wishlist_mode="OR")
    assert names(filter_games(games, s)) == ["owned by 2"]


# ------------------------------------------------------------------
# Text search
# ------------------------------------------------------------------
def test_title_search_is_case_insensitive_substring(make_game):
    games = [make_game(1, "Portal 2"), make_game(2, "Half-Life"), make_game(3, "PORTAL Stories")]
    s = no_owner_floor(search_query="portal")
    assert names(filter_games(games, s)) == ["Portal 2", "PORTAL Stories"]


def test_tag_search_requires_every_tag_as_substring_of_some_label(make_game):
    games = [
        make_game(1, "both", labels={"Online Co-op", "Survival"}),
        make_game(2, "only survival", labels={"Survival"}),
        make_game(3, "only coop", labels={"Co-op"}),
    ]
    s = no_owner_floor(required_tags=("co-op", "surv"))
    assert names(filter_games(games, s)) == ["both"]


def test_tag_search_matches_genres(make_game):
    games = [
        make_game(1, "genre rpg", genres={"RPG"}),
        make_game(2, "category rpg", labels={"RPG"}),
        make_game(3, "no rpg", labels={"Action"}, genres={"Strategy"}),
    ]
    s = no_owner_floor(required_tags=("rpg",))
    assert names(filter_games(games, s)) == ["genre rpg", "category rpg"]


# ------------------------------------------------------------------
# Review / discount / price
# ------------------------------------------------------------------
def test_min_review_excludes_unknown_reviews(make_game):
    games = [
        make_game(1, "good", review=90),
        make_game(2, "bad", review=50),
        make_game(3, "unknown", review=None),
        make_game(4, "exact", review=80),
    ]
    assert names(filter_games(games, no_owner_floor(min_review=80))) == ["good", "exact"]
    assert len(filter_games(games, no_owner_floor(min_review=0))) == 4


def test_min_discount_needs_sale_and_enough_discount(make_game):
    games = [
        make_game(1, "big sale", price=5.0, discount=50),
        make_game(2, "small sale", price=5.0, discount=10),
        make_game(3, "no sale", price=5.0),
    ]
    stale = make_game(4, "pct without sale flag", price=5.0)
    stale.discount_pct = 75
    games.append(stale)
    assert names(filter_games(games, no_owner_floor(min_discount=25))) == ["big sale"]


def test_max_price_none_means_no_limit(make_game):
    games = [make_game(1, "cheap", price=1.0), make_game(2, "pricey", price=60.0), make_game(3, "unknown")]
    assert len(filter_games(games, no_owner_floor(max_price=None))) == 3


def test_max_price_limit_excludes_unknown_and_over_limit(make_game):
    games = [
        make_game(1, "cheap", price=9.99),
        make_game(2, "exact", price=10.0),
        make_game(3, "pricey", price=10.01),
        make_game(4, "unknown"),
    ]
    assert names(filter_games(games, no_owner_floor(max_price=10))) == ["cheap", "exact"]


def test_free_game_passes_max_price_zero(make_game):
    games = [make_game(1, "free", price=0.0), make_game(2, "cheap", price=0.99)]
    assert names(filter_games(games, no_owner_floor(max_price=0))) == ["free"]


# ------------------------------------------------------------------
# Unreleased / unavailable
# ------------------------------------------------------------------
def test_unreleased_hidden_when_not_included(make_game):
    games = [make_game(1, "released"), make_game(2, "upcoming", unreleased=True)]
    assert names(filter_games(games, no_owner_floor(include_unreleased=False))) == ["released"]
    assert len(filter_games(games, no_owner_floor(include_unreleased=True))) == 2


def test_unreleased_detected_from_release_date_text(make_game):
    g = make_game(1, "coming")
    g.release_date = "Coming soon"
    assert filter_games([g], no_owner_floor(include_unreleased=False)) == []


def test_unavailable_hidden_by_default(make_game):
    games = [make_game(1, "available"), make_game(2, "delisted", unavailable=True)]
    assert FilterSettings().include_unavailable is False
    assert names(filter_games(games, no_owner_floor())) == ["available"]
    assert len(filter_games(games, no_owner_floor(include_unavailable=True))) == 2


# ------------------------------------------------------------------
# Common tags
# ------------------------------------------------------------------
def test_coop_matches_online_coop(make_game):
    games = [make_game(1, "coop", labels={"Online Co-op"}), make_game(2, "solo", labels={"Single-player"})]
    assert names(filter_games(games, no_owner_floor(require_coop=True))) == ["coop"]


def test_multiplayer_matches_pvp(make_game):
    games = [make_game(1, "pvp", labels={"PvP"}), make_game(2, "solo", labels={"Single-player"})]
    assert names(filter_games(games, no_owner_floor(require_multiplayer=True))) == ["pvp"]


def test_online_matches_online_pvp_and_mmo_but_not_split_screen(make_game):
    games = [
        make_game(1, "pvp", labels={"Online PvP"}),
        make_game(2, "mmo", labels={"MMO"}),
        make_game(3, "couch", labels={"Shared/Split Screen Co-op"}),
    ]
    assert names(filter_games(games, no_owner_floor(require_online=True))) == ["pvp", "mmo"]


def test_vr_matches_vr_supported(make_game):
    games = [make_game(1, "vr", labels={"VR Supported"}), make_game(2, "flat", labels={"Action"})]
    assert names(filter_games(games, no_owner_floor(require_vr=True))) == ["vr"]


@pytest.mark.parametrize("field", ["mac", "linux"])
def test_platform_requires_true(make_game, field):
    games = [
        make_game(1, "yes", **{field: True}),
        make_game(2, "no", **{field: False}),
        make_game(3, "unknown", **{field: None}),
    ]
    setting = {"mac": "require_macos", "linux": "require_linux"}[field]
    assert names(filter_games(games, no_owner_floor(**{setting: True}))) == ["yes"]


# ------------------------------------------------------------------
# Sorting
# ------------------------------------------------------------------
@pytest.fixture
def sortable_games(players, make_game):
    p = players
    return [
        make_game(1, "A", owners=p[:1], wishlisters=p[:3], price=20.0, discount=10, review=70),
        make_game(2, "B", owners=p[:3], wishlisters=p[:1], price=5.0, discount=50, review=95),
        make_game(3, "C", owners=p[:2], wishlisters=p[:2], price=10.0, discount=0, review=80),
    ]


@pytest.mark.parametrize(
    "sort_by, descending, expected",
    [
        ("Owned", True, ["B", "C", "A"]),
        ("Owned", False, ["A", "C", "B"]),
        ("Wishlisted", True, ["A", "C", "B"]),
        ("Wishlisted", False, ["B", "C", "A"]),
        ("User Reviews", True, ["B", "C", "A"]),
        ("User Reviews", False, ["A", "C", "B"]),
        ("Price", True, ["A", "C", "B"]),
        ("Price", False, ["B", "C", "A"]),
        ("Discount", True, ["B", "A", "C"]),
        ("Discount", False, ["C", "A", "B"]),
    ],
)
def test_sort_by_each_field(sortable_games, sort_by, descending, expected):
    assert names(sort_games(list(sortable_games), sort_by, descending)) == expected


@pytest.mark.parametrize(
    "descending, expected", [(True, ["dear", "cheap", "none"]), (False, ["cheap", "dear", "none"])]
)
def test_sort_puts_missing_price_last(make_game, descending, expected):
    games = [make_game(1, "none"), make_game(2, "cheap", price=1.0), make_game(3, "dear", price=9.0)]
    assert names(sort_games(games, "Price", descending)) == expected


@pytest.mark.parametrize(
    "descending, expected", [(True, ["high", "low", "none"]), (False, ["low", "high", "none"])]
)
def test_sort_puts_missing_reviews_last(make_game, descending, expected):
    games = [make_game(1, "none"), make_game(2, "low", review=10), make_game(3, "high", review=99)]
    assert names(sort_games(games, "User Reviews", descending)) == expected


@pytest.mark.parametrize("descending", [True, False])
def test_sort_ties_owners_desc_wishlisters_desc_name_asc(players, make_game, descending):
    p = players
    # All share price 5.0, so the primary key ties everywhere
    games = [
        make_game(1, "zeta", owners=p[:1], price=5.0),
        make_game(2, "beta", owners=p[:1], wishlisters=p[:2], price=5.0),
        make_game(3, "Alpha", owners=p[:1], wishlisters=p[:2], price=5.0),
        make_game(4, "omega", owners=p[:3], price=5.0),
    ]
    assert names(sort_games(games, "Price", descending)) == ["omega", "Alpha", "beta", "zeta"]


def test_sort_does_not_change_input_list(sortable_games):
    original = list(sortable_games)
    result = sort_games(sortable_games, "Owned", True)
    assert sortable_games == original
    assert result is not sortable_games


@pytest.mark.parametrize("descending", [True, False])
def test_unknown_sort_by_falls_back_to_owned(sortable_games, descending):
    assert names(sort_games(sortable_games, "Bogus", descending)) == names(
        sort_games(sortable_games, "Owned", descending)
    )
    assert names(sort_games(sortable_games, "Bogus", True)) == ["B", "C", "A"]


def test_sort_default_directions():
    assert SORT_OPTIONS == ["Owned", "Wishlisted", "User Reviews", "Price", "Discount"]
    assert SORT_DEFAULT_DESCENDING["Price"] is False
    for name in SORT_OPTIONS:
        if name != "Price":
            assert SORT_DEFAULT_DESCENDING[name] is True


def test_filter_settings_defaults():
    s = FilterSettings()
    assert (s.min_owned, s.min_wishlist, s.owned_wishlist_mode) == (1, 0, "AND")
    assert (s.min_discount, s.min_review, s.max_price) == (0, 0, None)
    assert s.include_unreleased is True and s.include_unavailable is False
