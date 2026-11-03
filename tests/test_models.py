import pytest

from src.core.models import GameItem


@pytest.mark.parametrize("discount, on_sale", [(0, False), (1, True), (75, True)])
def test_is_on_sale_follows_discount_pct(discount, on_sale):
    game = GameItem(1, "Game")
    game.discount_pct = discount
    assert game.is_on_sale is on_sale
