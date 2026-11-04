import pytest

from src.core.filtering import (
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


@pytest.mark.parametrize("mode", ["AND", "OR"])
def test_both_thresholds_zero_means_no_constraint(players, make_game, mode):
    p = players
    games = [
        make_game(1, "nobody"),
        make_game(2, "owned", owners=p[:1]),
        make_game(3, "wishlisted", wishlisters=p[:1]),
    ]
    s = FilterSettings(min_owned=0, min_wishlist=0, owned_wishlist_mode=mode)
    assert names(filter_games(games, s)) == ["nobody", "owned", "wishlisted"]


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


def test_min_discount_needs_enough_discount(make_game):
    games = [
        make_game(1, "big sale", price=5.0, discount=50),
        make_game(2, "small sale", price=5.0, discount=10),
        make_game(3, "no sale", price=5.0),
    ]
    assert names(filter_games(games, no_owner_floor(min_discount=25))) == ["big sale"]


def test_max_price_limit_excludes_unknown_and_over_limit(make_game):
    games = [
        make_game(1, "cheap", price=9.99),
        make_game(2, "exact", price=10.0),
        make_game(3, "pricey", price=10.01),
        make_game(4, "unknown"),
    ]
    assert names(filter_games(games, no_owner_floor(max_price=10))) == ["cheap", "exact"]


# ------------------------------------------------------------------
# Unreleased / unavailable
# ------------------------------------------------------------------
def test_unreleased_hidden_when_not_included(make_game):
    games = [make_game(1, "released"), make_game(2, "upcoming", unreleased=True)]
    assert names(filter_games(games, no_owner_floor(include_unreleased=False))) == ["released"]
    assert len(filter_games(games, no_owner_floor(include_unreleased=True))) == 2


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


