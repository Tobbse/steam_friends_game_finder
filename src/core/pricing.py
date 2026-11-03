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


def is_release_text_unreleased(text: str, current_year: int) -> bool:
    """Checks if a custom release date string indicates an unreleased / upcoming game."""
    if not text:
        return False
    low = text.strip().lower()
    if any(kw in low for kw in UNRELEASED_KEYWORDS):
        return True
    # Check for quarter notation like "Q1 2026" or "Q4 2025"
    q_match = re.search(r"\bq[1-4]\s*(\d{4})\b", low)
    if q_match and int(q_match.group(1)) >= current_year:
        return True
    # If the release string is just a future year (e.g. "2026", "2027")
    if re.fullmatch(r"\d{4}", low) and int(low) > current_year:
        return True
    # Check if any 4-digit year in the string is strictly in the future
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", low)]
    if years and min(years) > current_year:
        return True
    return False


def detect_unreleased_from_store_item(item: dict, now_ts: float, current_year: int) -> Tuple[bool, str]:
    """
    Accurately determines `(is_unreleased, release_date_str)` from a Steam `IStoreBrowseService/GetItems`
    entry, covering all ways Steam represents upcoming / unreleased titles.
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
            is_release_text_unreleased(custom_msg, current_year),
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
