import pytest

from src.core.models import GameItem, Player


@pytest.fixture
def players():
    return [Player(str(i), f"P{i}") for i in range(1, 5)]


@pytest.fixture
def make_game():
    def _make(appid, name, owners=(), wishlisters=(), price=None, discount=0,
              review=None, labels=(), genres=(), mac=None, linux=None, unreleased=False, unavailable=False):
        g = GameItem(appid, name)
        g.owners = list(owners)
        g.wishlisters = list(wishlisters)
        g.final_price = price
        g.price_str = "" if price is None else ("Free" if price == 0 else f"${price:.2f}")
        g.discount_pct = discount
        g.is_on_sale = discount > 0
        g.review_percentage = review
        g.categories = set(labels)
        g.genres = set(genres)
        g.mac_supported = mac
        g.linux_supported = linux
        g.is_unreleased = unreleased
        g.is_unavailable = unavailable
        return g
    return _make
