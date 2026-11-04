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
    """
    Keyword-only check ("Coming soon", "TBA", ...) for release or price text.
    Dates, quarters and years are not parsed: Steam's own flags say whether a game is released.
    """
    if not text:
        return False
    low = text.strip().lower()
    return any(kw in low for kw in UNRELEASED_KEYWORDS)


def parse_price_string(price_str: str) -> Optional[float]:
    """Extracts a numeric float price from a formatted price string, handling thousands & decimal separators."""
    if not price_str:
        return None
    if "free" in price_str.lower():
        return 0.0
    match = re.search(r"(\d[\d.,]*)", price_str)
    if not match:
        return None
    num_str = match.group(1).strip(".,")
    if "," in num_str and "." in num_str:
        if num_str.rfind(",") > num_str.rfind("."):
            num_str = num_str.replace(".", "").replace(",", ".")
        else:
            num_str = num_str.replace(",", "")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts[-1]) == 2:
            num_str = "".join(parts[:-1]) + "." + parts[-1]
        else:
            num_str = num_str.replace(",", "")
    try:
        return float(num_str)
    except ValueError:
        return None


def detect_unreleased_from_store_item(item: dict, now_ts: float) -> Tuple[bool, str]:
    """
    Determines `(is_unreleased, release_date_str)` from a Steam `IStoreBrowseService/GetItems` entry.
    Unreleased means: one of Steam's coming-soon flags is set, the release timestamp is after `now_ts`,
    or the custom release message contains an unreleased keyword.
    """
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
        try:
            dt = datetime.datetime.fromtimestamp(rd_ts, tz=datetime.timezone.utc)
            release_date_str = dt.strftime("%b %d, %Y")
        except Exception:
            pass
    elif coming_soon_flag:
        release_date_str = "Coming Soon"

    return coming_soon_flag, release_date_str
