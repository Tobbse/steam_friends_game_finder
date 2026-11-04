import datetime
import re
from typing import Optional, Tuple

UNRELEASED_KEYWORDS = (
    "coming soon",
    "to be announced",
    "tba",
    "tbd",
    "unreleased",
    "when it's done",
    "when its done",
    "wishlist now",
)


def has_unreleased_keyword(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in UNRELEASED_KEYWORDS)


def parse_price_string(price_str: str) -> Optional[float]:
    """"$1,299.99" -> 1299.99, "Free to Play" -> 0.0. Prices are always fetched in US dollars."""
    if "free" in (price_str or "").lower():
        return 0.0
    match = re.search(r"\d[\d,]*(?:\.\d+)?", price_str or "")
    return float(match.group().replace(",", "")) if match else None


def detect_unreleased_from_store_item(item: dict, now_ts: float) -> Tuple[bool, str]:
    """Returns (is_unreleased, release_date_str) for an IStoreBrowseService/GetItems entry."""
    rel = item.get("release") or {}
    rd_ts = int(rel.get("steam_release_date") or 0)
    custom_msg = str(rel.get("custom_release_date_message") or "").strip()

    coming_soon_flag = any(
        (
            bool(item.get("is_coming_soon")),
            bool(rel.get("is_coming_soon")),
            bool(rel.get("coming_soon_display")),
            bool(rel.get("is_preload")),
            bool(rel.get("is_abridged_release_date")),
            rd_ts > now_ts,
            has_unreleased_keyword(custom_msg),
        )
    )

    release_date_str = ""
    if custom_msg:
        release_date_str = custom_msg
    elif rd_ts > 0:
        release_date_str = datetime.datetime.fromtimestamp(rd_ts, tz=datetime.timezone.utc).strftime("%b %d, %Y")
    elif coming_soon_flag:
        release_date_str = "Coming Soon"

    return coming_soon_flag, release_date_str
