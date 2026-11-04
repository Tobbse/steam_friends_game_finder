from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from src.core.models import GameItem

MAX_PRICE_SLIDER_CAP = 70  # slider values 0..69 = $0..$69, 70 = no limit

SORT_OPTIONS = ["Owned", "Wishlisted", "User Reviews", "Price", "Discount"]
SORT_DEFAULT_DESCENDING = {"Owned": True, "Wishlisted": True, "User Reviews": True, "Price": False, "Discount": True}


@dataclass(frozen=True)
class FilterSettings:
    search_query: str = ""  # lower-cased
    required_tags: Tuple[str, ...] = ()  # lower-cased
    min_owned: int = 1
    min_wishlist: int = 0
    owned_wishlist_mode: str = "AND"  # "AND" or "OR"
    min_discount: int = 0
    min_review: int = 0
    max_price: Optional[int] = None  # None = no limit
    include_unreleased: bool = True
    include_unavailable: bool = False
    require_coop: bool = False
    require_multiplayer: bool = False
    require_online: bool = False
    require_vr: bool = False
    require_macos: bool = False
    require_linux: bool = False


def matches(game: GameItem, settings: FilterSettings) -> bool:
    if settings.search_query and settings.search_query not in game.name.lower():
        return False

    if not settings.include_unreleased and game.is_effectively_unreleased:
        return False
    if not settings.include_unavailable and game.is_effectively_unavailable:
        return False

    if settings.owned_wishlist_mode == "OR":
        # A threshold of 0 would match everything, so it's left out of the OR
        no_constraint = settings.min_owned <= 0 and settings.min_wishlist <= 0
        owned_ok = settings.min_owned > 0 and len(game.owners) >= settings.min_owned
        wishlist_ok = settings.min_wishlist > 0 and len(game.wishlisters) >= settings.min_wishlist
        if not (no_constraint or owned_ok or wishlist_ok):
            return False
    else:
        if len(game.owners) < settings.min_owned:
            return False
        if len(game.wishlisters) < settings.min_wishlist:
            return False

    if settings.require_coop and not game.is_coop:
        return False
    if settings.require_multiplayer and not game.is_multiplayer:
        return False
    if settings.require_online and not game.is_online:
        return False
    if settings.require_vr and not game.is_vr:
        return False
    if settings.require_macos and game.mac_supported is not True:
        return False
    if settings.require_linux and game.linux_supported is not True:
        return False

    if settings.required_tags:
        game_tags_lower = [lbl.lower() for lbl in (game.categories | game.genres)]
        if not all(any(req_t in gt for gt in game_tags_lower) for req_t in settings.required_tags):
            return False

    if settings.min_review > 0:
        if game.review_percentage is None or game.review_percentage < settings.min_review:
            return False

    if settings.min_discount > 0:
        if not game.is_on_sale or game.discount_pct < settings.min_discount:
            return False

    if settings.max_price is not None:
        if game.final_price is None or game.final_price > float(settings.max_price):
            return False

    return True


def filter_games(games: Iterable[GameItem], settings: FilterSettings) -> List[GameItem]:
    return [game for game in games if matches(game, settings)]


def sort_games(games: Iterable[GameItem], sort_by: str, descending: bool) -> List[GameItem]:
    """Unknown sort_by sorts by "Owned". Games without a value (no reviews, no price) go last."""
    primary_value_by_field = {
        "Wishlisted": lambda game: len(game.wishlisters),
        "User Reviews": lambda game: game.review_percentage,
        "Price": lambda game: game.final_price,
        "Discount": lambda game: game.discount_pct,
    }
    primary_value = primary_value_by_field.get(sort_by, lambda game: len(game.owners))

    # Tie-break first; the stable sort below keeps that order for equal values
    ordered = sorted(games, key=lambda game: (-len(game.owners), -len(game.wishlisters), game.name.lower()))
    with_value = [game for game in ordered if primary_value(game) is not None]
    without_value = [game for game in ordered if primary_value(game) is None]
    with_value.sort(key=primary_value, reverse=descending)
    return with_value + without_value
