import datetime

import pytest

from src.core.pricing import (
    detect_unreleased_from_store_item,
    has_unreleased_keyword,
    parse_price_string,
)

NOW_TS = datetime.datetime(2026, 11, 4, 12, tzinfo=datetime.timezone.utc).timestamp()
DAY = 24 * 3600


# ---------- parse_price_string ----------

@pytest.mark.parametrize(
    "price_str, expected",
    [
        ("$19.99", 19.99),
        ("$1,299.00", 1299.0),
        ("$1,299", 1299.0),
        ("$0.99", 0.99),
        ("$10", 10.0),
        ("Free", 0.0),
        ("Free to Play", 0.0),
        ("", None),
        ("N/A", None),
    ],
)
def test_parse_price_string(price_str, expected):
    assert parse_price_string(price_str) == expected


# ---------- has_unreleased_keyword ----------

@pytest.mark.parametrize(
    "text",
    [
        "Coming soon", "COMING SOON", "  coming soon  ", "To be announced", "TBA", "TBD",
        "Unreleased", "When it's done", "When its done", "Wishlist Now", "Coming soon - Q2 2027",
    ],
)
def test_has_unreleased_keyword_true(text):
    assert has_unreleased_keyword(text) is True


# ---------- detect_unreleased_from_store_item ----------

def test_detect_empty_item_is_released_without_date():
    assert detect_unreleased_from_store_item({}, NOW_TS) == (False, "")


def test_detect_top_level_coming_soon_flag():
    assert detect_unreleased_from_store_item({"is_coming_soon": True}, NOW_TS) == (True, "Coming Soon")


@pytest.mark.parametrize("flag", ["is_coming_soon", "coming_soon_display", "is_preload", "is_abridged_release_date"])
def test_detect_release_flags(flag):
    assert detect_unreleased_from_store_item({"release": {flag: True}}, NOW_TS) == (True, "Coming Soon")


def test_detect_past_timestamp_is_released_and_formats_date():
    ts = int(datetime.datetime(2020, 1, 15, 12, tzinfo=datetime.timezone.utc).timestamp())
    item = {"release": {"steam_release_date": ts}}
    assert detect_unreleased_from_store_item(item, NOW_TS) == (False, "Jan 15, 2020")


def test_detect_future_timestamp_is_unreleased():
    ts = int(NOW_TS) + 90 * DAY
    expected_str = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%b %d, %Y")
    item = {"release": {"steam_release_date": ts}}
    assert detect_unreleased_from_store_item(item, NOW_TS) == (True, expected_str)


def test_detect_custom_keyword_message_wins_over_timestamp():
    item = {"release": {"steam_release_date": 1579089600, "custom_release_date_message": " To be announced "}}
    assert detect_unreleased_from_store_item(item, NOW_TS) == (True, "To be announced")


@pytest.mark.parametrize("message", ["Q4 2026", "2027", "Dec 20, 2026", "December 2027"])
def test_detect_custom_date_message_without_flag_is_released(message):
    # Only Steam's flags, the timestamp and keywords count; date text is shown but not parsed.
    item = {"release": {"custom_release_date_message": message}}
    assert detect_unreleased_from_store_item(item, NOW_TS) == (False, message)


