import asyncio
import datetime
import json
import math
import re
import time
import webbrowser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import flet as ft
import httpx

CONFIG_FILE = Path("steam_finder_config.json")
MAX_PRICE_SLIDER_CAP = 70  # Values 0..69 represent $0..$69; 70 represents "Unlimited"

# Dynamic in-memory maps populated once per session from Steam's Web API
SESSION_CATEGORY_MAP: Dict[int, str] = {}
SESSION_TAG_MAP: Dict[int, str] = {}


# ==========================================
# FLET CROSS-VERSION COMPATIBILITY HELPERS
# ==========================================
def open_url(url: str):
    """Opens a URL in the user's default system web browser."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def get_padding_symmetric(horizontal: int = 0, vertical: int = 0):
    """Creates symmetric padding safely across Flet versions."""
    for attr in ("Padding", "padding"):
        cls = getattr(ft, attr, None)
        if cls and hasattr(cls, "symmetric"):
            return cls.symmetric(horizontal=horizontal, vertical=vertical)
    return None


def get_padding_only(left: int = 0, top: int = 0, right: int = 0, bottom: int = 0):
    """Creates directional padding safely across Flet versions."""
    for attr in ("Padding", "padding"):
        cls = getattr(ft, attr, None)
        if cls and hasattr(cls, "only"):
            return cls.only(left=left, top=top, right=right, bottom=bottom)
    return get_padding_symmetric(horizontal=max(left, right), vertical=max(top, bottom))


def get_always_scroll():
    """Returns ALWAYS scroll mode safely across Flet versions."""
    if hasattr(ft, "ScrollMode") and hasattr(ft.ScrollMode, "ALWAYS"):
        return ft.ScrollMode.ALWAYS
    return "always"


def get_click_cursor():
    """Returns the clickable hand MouseCursor across Flet versions."""
    if hasattr(ft, "MouseCursor") and hasattr(ft.MouseCursor, "CLICK"):
        return ft.MouseCursor.CLICK
    return None


def configure_always_visible_scrollbars(page: ft.Page):
    """Configures page themes so scrollbars are always visible on all scrollable containers."""
    sb_theme_cls = getattr(ft, "ScrollBarTheme", getattr(ft, "ScrollbarTheme", None))
    if sb_theme_cls and hasattr(ft, "Theme"):
        try:
            sb_theme = sb_theme_cls(thumb_visibility=True, track_visibility=True)
            page.theme = ft.Theme(scrollbar_theme=sb_theme)
            page.dark_theme = ft.Theme(scrollbar_theme=sb_theme)
        except Exception:
            pass


def get_icon(name: str):
    """Resolves icon enums safely across Flet versions."""
    if not isinstance(name, str):
        return name
    name_upper = name.upper()
    for attr in ("Icons", "icons"):
        cls = getattr(ft, attr, None)
        if cls and hasattr(cls, name_upper):
            return getattr(cls, name_upper)
    return name.lower()


def get_color(name: str):
    """Resolves color enums safely across Flet versions."""
    if not isinstance(name, str):
        return name
    name_upper = name.upper()
    for attr in ("Colors", "colors"):
        cls = getattr(ft, attr, None)
        if cls and hasattr(cls, name_upper):
            return getattr(cls, name_upper)
    return name.lower()


def get_center_alignment():
    """Returns center alignment safely across all Flet versions."""
    if hasattr(ft, "alignment") and hasattr(ft.alignment, "center"):
        return ft.alignment.center
    if hasattr(ft, "Alignment"):
        return ft.Alignment(0, 0)
    return None


def create_card(content: ft.Control, bgcolor: Optional[str] = None) -> ft.Card:
    """Creates a Card safely across Flet versions (handling bgcolor vs color differences)."""
    if bgcolor is not None:
        try:
            return ft.Card(content=content, bgcolor=bgcolor)
        except TypeError:
            pass
    return ft.Card(content=content)


def create_button(
    text: str,
    icon: Optional[str] = None,
    on_click=None,
    style=None,
    visible: bool = True,
    disabled: bool = False,
):
    """Creates a button compatible with both Flet 1.0+ and older versions, with a hand cursor on hover."""
    btn_cls = getattr(ft, "Button", getattr(ft, "ElevatedButton", None))
    kwargs = {"visible": visible, "disabled": disabled}
    if icon:
        kwargs["icon"] = get_icon(icon)

    click_cursor = get_click_cursor()
    if style is not None:
        if click_cursor is not None and getattr(style, "mouse_cursor", None) is None:
            try:
                style.mouse_cursor = click_cursor
            except Exception:
                pass
        kwargs["style"] = style
    elif click_cursor is not None and hasattr(ft, "ButtonStyle"):
        try:
            kwargs["style"] = ft.ButtonStyle(mouse_cursor=click_cursor)
        except Exception:
            pass

    btn = btn_cls(text, **kwargs)
    if on_click:
        btn.on_click = on_click
    return btn


def create_icon_button(
    icon_name: str,
    on_click=None,
    tooltip: Optional[str] = None,
    icon_color: Optional[str] = None,
    icon_size: Optional[int] = None,
    compact: bool = False,
):
    """Creates an IconButton guaranteed to work across Flet releases, with a hand cursor on hover."""
    icon_val = get_icon(icon_name)
    kwargs = {}
    if tooltip:
        kwargs["tooltip"] = tooltip
    if icon_color:
        kwargs["icon_color"] = get_color(icon_color)
    if icon_size is not None:
        kwargs["icon_size"] = icon_size
    if compact:
        kwargs["width"] = 28
        kwargs["height"] = 28
        kwargs["padding"] = get_padding_symmetric(horizontal=2, vertical=2)

    click_cursor = get_click_cursor()
    if click_cursor is not None:
        kwargs["mouse_cursor"] = click_cursor

    try:
        btn = ft.IconButton(icon=icon_val, **kwargs)
    except Exception:
        safe_kwargs = {k: v for k, v in kwargs.items() if k in ("tooltip", "icon_color", "icon_size")}
        btn = ft.IconButton(icon=icon_val, **safe_kwargs)
    if on_click:
        btn.on_click = on_click
    return btn


def create_option(text: str, key: Optional[str] = None):
    """Creates a dropdown option compatible across Flet releases."""
    val_key = key if key is not None else text
    if hasattr(ft, "dropdown") and hasattr(ft.dropdown, "Option"):
        return ft.dropdown.Option(key=str(val_key), text=text)
    for attr in ("Option", "DropdownOption"):
        cls = getattr(ft, attr, None)
        if cls:
            return cls(key=str(val_key), text=text)
    return text


def create_dropdown(options, value=None, label=None, width=None, on_change=None):
    """Creates a Dropdown control safely without passing event handlers to __init__."""
    kwargs = {k: v for k, v in (("label", label), ("value", value), ("width", width)) if v is not None}
    dd = ft.Dropdown(options=options, **kwargs)
    if on_change:
        if hasattr(dd, "on_change"):
            dd.on_change = on_change
        elif hasattr(dd, "on_select"):
            dd.on_select = on_change
    return dd


def create_badge_layout(controls):
    """Creates a Wrap layout if supported by Flet, falling back to Row."""
    if hasattr(ft, "Wrap"):
        try:
            return ft.Wrap(controls=controls, spacing=6, run_spacing=4)
        except Exception:
            pass
    return ft.Row(controls=controls, spacing=6)


def parse_price_string(price_str: str) -> Optional[float]:
    """Extracts a numeric float price from a formatted price string as a fallback."""
    if not price_str:
        return None
    if "free" in price_str.lower():
        return 0.0
    cleaned = price_str.replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


# ==========================================
# DATA MODELS
# ==========================================
class Player:
    def __init__(self, steam_id: str, display_name: str = "", avatar_url: str = ""):
        self.steam_id = steam_id
        self.display_name = display_name or steam_id
        self.avatar_url = avatar_url
        self.owned_games: Dict[int, str] = {}     # appid -> game_name
        self.wishlist_games: Dict[int, str] = {}  # appid -> game_name
        self.is_private: bool = False
        self.library_fetched: bool = False


class GameItem:
    def __init__(self, appid: int, name: str):
        self.appid = appid
        self.name = name
        self.owners: List[Player] = []
        self.wishlisters: List[Player] = []
        self.categories: Set[str] = set()
        self.genres: Set[str] = set()
        self.mac_supported: Optional[bool] = None
        self.windows_supported: Optional[bool] = None
        self.linux_supported: Optional[bool] = None
        self.short_description: str = ""
        self.developers: List[str] = []
        self.release_date: str = ""
        self.is_unreleased: bool = False
        self.is_unavailable: bool = False
        self.header_image_url: str = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
        self.price_str: str = ""
        self.final_price: Optional[float] = None  # Numeric price in dollars AFTER applied discounts
        self.discount_pct: int = 0
        self.is_on_sale: bool = False
        self.price_fetched: bool = False
        self.store_info_fetched: bool = False

    @property
    def is_free(self) -> bool:
        """Returns True if the game is Free to Play ($0.00)."""
        return (self.final_price is not None and self.final_price == 0.0) or ("free" in self.price_str.lower())

    @property
    def is_coop(self) -> bool:
        """Checks dynamically resolved categories & tags for Co-op support."""
        all_labels = self.categories | self.genres
        return any("co-op" in c.lower() or "coop" in c.lower() for c in all_labels)

    @property
    def is_multiplayer(self) -> bool:
        """Checks dynamically resolved categories & tags for Multiplayer / PvP support."""
        all_labels = self.categories | self.genres
        return any(
            "multiplayer" in c.lower() or "multi-player" in c.lower() or "pvp" in c.lower()
            for c in all_labels
        )


# ==========================================
# ASYNC STEAM API CLIENT (BULK & RATE-SAFE)
# ==========================================
class SteamClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=15)
        self.client = httpx.AsyncClient(timeout=15.0, limits=limits)
        self._api_semaphore = asyncio.Semaphore(6)    # Official Web API concurrency
        self._store_semaphore = asyncio.Semaphore(2)  # Gentle throttle for store.steampowered.com

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        await self.client.aclose()

    async def ensure_store_definitions(self):
        """
        Dynamically fetches Steam's official Category ID and Tag ID definitions
        once per session so no hardcoded tag strings are needed.
        """
        if not self.api_key:
            return

        async def _fetch_categories():
            if SESSION_CATEGORY_MAP:
                return
            url = "https://api.steampowered.com/IStoreBrowseService/GetStoreCategories/v1/"
            try:
                async with self._api_semaphore:
                    resp = await self.client.get(url, params={"key": self.api_key, "language": "english"})
                if resp.status_code == 200:
                    cats = resp.json().get("response", {}).get("categories", [])
                    for c in cats:
                        cid = c.get("categoryid")
                        cname = c.get("display_name") or c.get("internal_name")
                        if cid is not None and cname:
                            SESSION_CATEGORY_MAP[int(cid)] = str(cname)
            except Exception:
                pass

        async def _fetch_tags():
            if SESSION_TAG_MAP:
                return
            url = "https://api.steampowered.com/IStoreService/GetTagList/v1/"
            try:
                async with self._api_semaphore:
                    resp = await self.client.get(url, params={"key": self.api_key, "language": "english"})
                if resp.status_code == 200:
                    tags = resp.json().get("response", {}).get("tags", [])
                    for t in tags:
                        tid = t.get("tagid")
                        tname = t.get("name")
                        if tid is not None and tname:
                            SESSION_TAG_MAP[int(tid)] = str(tname)
            except Exception:
                pass

        await asyncio.gather(_fetch_categories(), _fetch_tags())

    async def resolve_steam_id(self, input_str: str) -> Optional[str]:
        input_str = input_str.strip()

        # 1. 17-digit SteamID64
        match_id64 = re.search(r"\b(\d{17})\b", input_str)
        if match_id64:
            return match_id64.group(1)

        # 2. Extract custom vanity name or profile path
        match_vanity = re.search(r"steamcommunity\.com/id/([^/?#]+)", input_str)
        vanity_name = match_vanity.group(1) if match_vanity else input_str.rstrip("/")

        url = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
        params = {"key": self.api_key, "vanityurl": vanity_name}
        try:
            async with self._api_semaphore:
                resp = await self.client.get(url, params=params)
            data = resp.json()
            if data.get("response", {}).get("success") == 1:
                return data["response"]["steamid"]
        except Exception:
            pass
        return None

    async def fetch_player_summary(self, player: Player):
        url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        params = {"key": self.api_key, "steamids": player.steam_id}
        try:
            async with self._api_semaphore:
                resp = await self.client.get(url, params=params)
            players = resp.json().get("response", {}).get("players", [])
            if players:
                player.display_name = players[0].get("personaname", player.steam_id)
                player.avatar_url = players[0].get("avatarmedium", "")
        except Exception:
            pass

    async def fetch_owned_games(self, player: Player):
        url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
        params = {
            "key": self.api_key,
            "steamid": player.steam_id,
            "include_appinfo": True,
            "include_played_free_games": True,
        }
        try:
            async with self._api_semaphore:
                resp = await self.client.get(url, params=params)
            if resp.status_code in (401, 403):
                player.is_private = True
                return
            data = resp.json().get("response", {})
            # Steam returns an empty dict {} when 'Game details' are set to Private
            if "games" not in data and "game_count" not in data:
                player.is_private = True
                return
            for g in data.get("games", []):
                player.owned_games[g["appid"]] = g.get("name", f"App {g['appid']}")
        except Exception:
            player.is_private = True

    async def fetch_wishlist(self, player: Player):
        # Method 1: Official Steam Web API (IWishlistService)
        if self.api_key:
            url_api = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"
            params = {"key": self.api_key, "steamid": player.steam_id}
            try:
                async with self._api_semaphore:
                    resp = await self.client.get(url_api, params=params)
                if resp.status_code == 200:
                    data = resp.json().get("response", {})
                    items = data.get("items", [])
                    if items:
                        for item in items:
                            appid = item.get("appid")
                            if appid:
                                player.wishlist_games[int(appid)] = f"App {appid}"
                        return
            except Exception:
                pass

        # Method 2: Fallback to Steam Store wishlistdata endpoint if Web API returned empty
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        for page_num in range(4):
            url_store = f"https://store.steampowered.com/wishlist/profiles/{player.steam_id}/wishlistdata/"
            try:
                async with self._store_semaphore:
                    resp = await self.client.get(url_store, params={"p": page_num}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and "success" not in data and len(data) > 0:
                        for appid_str, info in data.items():
                            if isinstance(info, dict) and appid_str.isdigit():
                                player.wishlist_games[int(appid_str)] = info.get("name", f"App {appid_str}")
                        if len(data) < 50:
                            break
                    else:
                        break
                else:
                    break
            except Exception:
                break

    async def fetch_bulk_store_items(
        self,
        games: List[GameItem],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ):
        """
        Fetches prices, discounts, titles, platforms, descriptions, release/availability status,
        categories, and tags in 150-game chunks using paced waves.
        """
        if not games:
            return

        await self.ensure_store_definitions()

        total = len(games)
        completed = 0
        chunk_size = 150
        chunks = [games[i : i + chunk_size] for i in range(0, total, chunk_size)]
        now_ts = time.time()

        async def process_chunk(chunk: List[GameItem]):
            nonlocal completed
            chunk_map = {g.appid: g for g in chunk}
            returned_appids: Set[int] = set()

            if self.api_key:
                url_browse = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
                input_payload = {
                    "ids": [{"appid": g.appid} for g in chunk],
                    "context": {"language": "english", "country_code": "US", "steam_realm": 1},
                    "data_request": {
                        "include_basic_info": True,
                        "include_assets": True,
                        "include_best_purchase_option": True,
                        "include_release": True,
                        "include_platforms": True,
                        "include_tag_count": 20,
                    },
                }
                try:
                    async with self._api_semaphore:
                        resp = await self.client.get(
                            url_browse,
                            params={"key": self.api_key, "input_json": json.dumps(input_payload)},
                        )
                    if resp.status_code == 200:
                        store_items = resp.json().get("response", {}).get("store_items", [])
                        for item in store_items:
                            appid = item.get("appid")
                            g = chunk_map.get(appid)
                            if not g:
                                continue
                            returned_appids.add(appid)

                            if item.get("name"):
                                g.name = item["name"]

                            # Dynamically resolve Store Categories
                            cats_obj = item.get("categories") or {}
                            for key in (
                                "supported_player_categoryids",
                                "feature_categoryids",
                                "controller_categoryids",
                            ):
                                ids_list = cats_obj.get(key)
                                if isinstance(ids_list, list):
                                    for cid in ids_list:
                                        cname = SESSION_CATEGORY_MAP.get(int(cid))
                                        if cname:
                                            g.categories.add(cname)

                            # Dynamically resolve Store Tags / Genres
                            tag_ids = item.get("tagids")
                            if isinstance(tag_ids, list):
                                for tid in tag_ids:
                                    tname = SESSION_TAG_MAP.get(int(tid))
                                    if tname:
                                        g.genres.add(tname)

                            # Extract Basic Info (Short description & Developers)
                            basic = item.get("basic_info") or {}
                            if basic.get("short_description") and not g.short_description:
                                g.short_description = re.sub(r"<[^>]+>", "", basic["short_description"]).strip()
                            if basic.get("developers") and not g.developers:
                                g.developers = [d.get("name", "") for d in basic["developers"] if d.get("name")]

                            # Extract Release Status & Unreleased Detection
                            rel = item.get("release") or {}
                            rd_ts = int(rel.get("steam_release_date") or 0)
                            coming_soon = bool(
                                item.get("is_coming_soon") is True
                                or rel.get("is_coming_soon") is True
                                or (rd_ts > now_ts)
                            )
                            g.is_unreleased = coming_soon

                            if not g.release_date:
                                if rel.get("custom_release_date_message"):
                                    g.release_date = str(rel["custom_release_date_message"])
                                elif rd_ts > 0:
                                    try:
                                        dt = datetime.datetime.fromtimestamp(rd_ts, tz=datetime.timezone.utc)
                                        g.release_date = dt.strftime("%b %d, %Y")
                                    except Exception:
                                        pass
                                elif coming_soon:
                                    g.release_date = "Coming Soon"

                            # Extract Platforms
                            platforms = item.get("platforms") or {}
                            if platforms:
                                g.windows_supported = bool(platforms.get("windows", False))
                                g.mac_supported = bool(platforms.get("mac", False))
                                g.linux_supported = bool(
                                    platforms.get("steamos_linux", False) or platforms.get("linux", False)
                                )

                            # Extract Pricing & Discounts (after applied discount)
                            bpo = item.get("best_purchase_option")
                            if bpo:
                                g.price_str = bpo.get("formatted_final_price", "")
                                g.discount_pct = int(bpo.get("discount_pct", 0) or 0)
                                g.is_on_sale = g.discount_pct > 0
                                cents = bpo.get("final_price_in_cents")
                                if cents is not None:
                                    try:
                                        g.final_price = int(cents) / 100.0
                                    except Exception:
                                        g.final_price = parse_price_string(g.price_str)
                                else:
                                    g.final_price = parse_price_string(g.price_str)
                                g.price_fetched = True
                            elif item.get("is_free") and not g.is_unreleased:
                                g.price_str = "Free to Play"
                                g.final_price = 0.0
                                g.discount_pct = 0
                                g.is_on_sale = False
                                g.price_fetched = True

                            # Detect Unavailable / Delisted store status
                            is_delisted = (
                                item.get("visible") is False
                                or item.get("unlisted") is True
                                or (item.get("success") not in (1, None))
                                or (not g.is_unreleased and not g.price_fetched)
                            )
                            g.is_unavailable = bool(is_delisted and not g.is_unreleased)
                            g.store_info_fetched = True

                        # Any requested appid completely omitted by Steam's store response is unavailable/removed
                        for g in chunk:
                            if g.appid not in returned_appids:
                                g.is_unavailable = True
                                g.store_info_fetched = True
                except Exception:
                    pass

            completed += len(chunk)
            if on_progress:
                on_progress(min(completed, total), total)

        # Paced wave execution (2 chunks per wave with a short pause between waves)
        wave_size = 2
        for w_idx in range(0, len(chunks), wave_size):
            wave = chunks[w_idx : w_idx + wave_size]
            await asyncio.gather(*[process_chunk(c) for c in wave])
            if w_idx + wave_size < len(chunks):
                await asyncio.sleep(0.15)

    async def fetch_store_info(self, game: GameItem):
        """On-demand single-game fallback (used when opening Game Details popup for an unfetched item)."""
        if game.store_info_fetched and game.short_description:
            return

        async with self._store_semaphore:
            url = f"https://store.steampowered.com/api/appdetails?appids={game.appid}&cc=us"
            try:
                resp = await self.client.get(url)
                if resp.status_code != 200:
                    return
                data = resp.json().get(str(game.appid), {})
                if data.get("success"):
                    game_data = data.get("data", {})

                    if game_data.get("name"):
                        game.name = game_data["name"]

                    raw_desc = game_data.get("short_description", "")
                    game.short_description = re.sub(r"<[^>]+>", "", raw_desc).strip()

                    game.developers = game_data.get("developers", [])
                    rel_info = game_data.get("release_date") or {}
                    if rel_info.get("date"):
                        game.release_date = rel_info["date"]
                    if rel_info.get("coming_soon") is True:
                        game.is_unreleased = True

                    game.categories.update(
                        c["description"] for c in game_data.get("categories", []) if c.get("description")
                    )
                    game.genres.update(
                        g["description"] for g in game_data.get("genres", []) if g.get("description")
                    )

                    platforms = game_data.get("platforms", {})
                    game.mac_supported = platforms.get("mac", False)
                    game.windows_supported = platforms.get("windows", False)
                    game.linux_supported = platforms.get("linux", False)

                    if game_data.get("header_image"):
                        game.header_image_url = game_data["header_image"]

                    price_overview = game_data.get("price_overview")
                    if price_overview:
                        game.discount_pct = price_overview.get("discount_percent", 0)
                        game.price_str = price_overview.get("final_formatted", "")
                        game.is_on_sale = game.discount_pct > 0
                        if price_overview.get("final") is not None:
                            try:
                                game.final_price = int(price_overview["final"]) / 100.0
                            except Exception:
                                game.final_price = parse_price_string(game.price_str)
                        else:
                            game.final_price = parse_price_string(game.price_str)
                        game.price_fetched = True
                    elif game_data.get("is_free") and not game.is_unreleased:
                        game.price_str = "Free to Play"
                        game.final_price = 0.0
                        game.price_fetched = True

                    if not game.is_unreleased and not game.price_fetched:
                        game.is_unavailable = True

                    game.store_info_fetched = True
                else:
                    game.is_unavailable = True
                    game.store_info_fetched = True
            except Exception:
                pass


# ==========================================
# MAIN FLET GUI APPLICATION
# ==========================================
async def main(page: ft.Page):
    page.title = "Steam Squad Game Finder"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 20
    configure_always_visible_scrollbars(page)

    try:
        page.window.width = 1180
        page.window.height = 880
    except Exception:
        try:
            page.window_width = 1180
            page.window_height = 880
        except Exception:
            pass

    # Background task & state flags
    status_clear_task: Optional[asyncio.Task] = None
    is_fetching: bool = False
    has_loaded_once: bool = False

    # Reusable SnackBar to prevent overlay memory leaks
    shared_snackbar = ft.SnackBar(ft.Text(""))

    # Safe multi-tier dialog display helpers (Flet 1.0 + Flet 0.2x compatible)
    def open_dialog(dlg: ft.AlertDialog):
        dlg.open = True
        if hasattr(page, "show_dialog"):
            try:
                page.show_dialog(dlg)
                return
            except Exception:
                pass
        if hasattr(page, "open"):
            try:
                page.open(dlg)
                return
            except Exception:
                pass
        if hasattr(page, "overlay"):
            try:
                if dlg not in page.overlay:
                    page.overlay.append(dlg)
                dlg.open = True
                page.update()
                return
            except Exception:
                pass
        try:
            page.dialog = dlg
            dlg.open = True
            page.update()
        except Exception:
            pass

    def close_dialog(dlg: ft.AlertDialog):
        dlg.open = False
        if hasattr(page, "pop_dialog"):
            try:
                page.pop_dialog()
                return
            except Exception:
                pass
        if hasattr(page, "close"):
            try:
                page.close(dlg)
                return
            except Exception:
                pass
        try:
            dlg.open = False
            if hasattr(page, "overlay") and dlg in page.overlay:
                page.overlay.remove(dlg)
            page.update()
        except Exception:
            pass

    def notify(msg: str):
        shared_snackbar.content = ft.Text(msg)
        if hasattr(page, "show_dialog"):
            try:
                page.show_dialog(shared_snackbar)
                return
            except Exception:
                pass
        if hasattr(page, "open"):
            try:
                page.open(shared_snackbar)
                return
            except Exception:
                pass
        if hasattr(page, "overlay"):
            try:
                if shared_snackbar not in page.overlay:
                    page.overlay.append(shared_snackbar)
                shared_snackbar.open = True
                page.update()
                return
            except Exception:
                pass
        page.snack_bar = shared_snackbar
        page.snack_bar.open = True
        page.update()

    def show_dialog(title: str, message: str):
        dlg = ft.AlertDialog(
            title=ft.Text(title, weight="bold"),
            content=ft.Text(message),
            actions=[create_button("OK", on_click=lambda _: close_dialog(dlg))],
            actions_alignment="end",
        )
        open_dialog(dlg)

    def format_game_price_label(game: GameItem) -> str:
        if game.price_str:
            return game.price_str
        if game.is_unreleased:
            return "Unreleased / TBA"
        if game.is_unavailable:
            return "Unavailable / Delisted from Steam Store"
        if not game.store_info_fetched:
            return "Fetching..."
        return "Not listed"

    def format_game_platforms(game: GameItem) -> str:
        platforms = []
        if game.windows_supported:
            platforms.append("Windows")
        if game.mac_supported:
            platforms.append("macOS")
        if game.linux_supported:
            platforms.append("Linux")
        if platforms:
            return ", ".join(platforms)
        return "Fetching..." if not game.store_info_fetched else "N/A"

    # Modal Popup Dialog for Game Details
    def open_game_details_dialog(game: GameItem):
        try:
            owner_names = ", ".join(p.display_name for p in game.owners) or "None"
            wishlist_names = ", ".join(p.display_name for p in game.wishlisters) or "None"

            desc_text = ft.Text(
                game.short_description
                or ("Fetching store description..." if not game.store_info_fetched else "No description available."),
                size=12,
            )

            price_text = ft.Text(
                f"Price: {format_game_price_label(game)}",
                size=14,
                weight="bold",
                color=(
                    get_color("green400")
                    if game.is_on_sale
                    else (get_color("red300") if game.is_unavailable else get_color("white"))
                ),
            )
            dev_str = (
                ", ".join(game.developers)
                if game.developers
                else ("Fetching..." if not game.store_info_fetched else "N/A")
            )
            dev_text = ft.Text(f"• Developer: {dev_str}", size=12)

            rel_suffix = (
                " (Unreleased)"
                if game.is_unreleased
                else (" (Unavailable in Store)" if game.is_unavailable else "")
            )
            rel_base = game.release_date or ("Fetching..." if not game.store_info_fetched else "N/A")
            release_text = ft.Text(
                f"• Release Date: {rel_base}{rel_suffix}",
                size=12,
                color=(
                    get_color("amber300")
                    if game.is_unreleased
                    else (get_color("red300") if game.is_unavailable else None)
                ),
            )

            platform_text = ft.Text(f"• Platforms: {format_game_platforms(game)}", size=12)

            all_tags_str = (
                ", ".join(sorted(game.categories | game.genres)[:12])
                if (game.categories or game.genres)
                else ("Fetching..." if not game.store_info_fetched else "None listed")
            )
            tags_text = ft.Text(f"• Tags & Categories: {all_tags_str}", size=12)

            dlg = ft.AlertDialog(
                title=ft.Text(game.name, weight="bold", size=18),
                content=ft.Container(
                    width=480,
                    content=ft.Column(
                        controls=[
                            ft.Image(
                                src=game.header_image_url,
                                width=460,
                                height=215,
                                fit="cover",
                                border_radius=6,
                                error_content=ft.Icon(
                                    get_icon("videogame_asset"), size=50, color=get_color("grey500")
                                ),
                            ),
                            price_text,
                            ft.Divider(),
                            ft.Text("Short Description:", weight="bold", size=13),
                            desc_text,
                            ft.Divider(),
                            ft.Text(f"• Owners ({len(game.owners)}): {owner_names}", size=12),
                            ft.Text(f"• Wishlisted ({len(game.wishlisters)}): {wishlist_names}", size=12),
                            dev_text,
                            release_text,
                            platform_text,
                            tags_text,
                        ],
                        tight=True,
                        scroll=get_always_scroll(),
                        spacing=6,
                    ),
                ),
                actions=[
                    create_button(
                        "Steam Store",
                        icon="open_in_new",
                        on_click=lambda _: open_url(f"https://store.steampowered.com/app/{game.appid}/"),
                    ),
                    create_button("Close", on_click=lambda _: close_dialog(dlg)),
                ],
                actions_alignment="end",
            )

            open_dialog(dlg)

            if (not game.store_info_fetched or not game.short_description) and api_key_field.value.strip():
                async def fetch_background():
                    try:
                        async with SteamClient(api_key_field.value.strip()) as client:
                            await client.fetch_store_info(game)

                        desc_text.value = game.short_description or "No description available."
                        price_text.value = f"Price: {format_game_price_label(game)}"
                        if game.is_on_sale:
                            price_text.color = get_color("green400")
                        elif game.is_unavailable:
                            price_text.color = get_color("red300")

                        dev_text.value = f"• Developer: {', '.join(game.developers) if game.developers else 'N/A'}"
                        r_suffix = (
                            " (Unreleased)"
                            if game.is_unreleased
                            else (" (Unavailable in Store)" if game.is_unavailable else "")
                        )
                        release_text.value = f"• Release Date: {(game.release_date or 'N/A')}{r_suffix}"
                        platform_text.value = f"• Platforms: {format_game_platforms(game)}"

                        t_list = (
                            ", ".join(sorted(game.categories | game.genres)[:12])
                            if (game.categories or game.genres)
                            else "None listed"
                        )
                        tags_text.value = f"• Tags & Categories: {t_list}"
                        page.update()
                    except Exception as err_bg:
                        print(f"Background fetch error: {err_bg}")

                asyncio.create_task(fetch_background())

        except Exception as err:
            print(f"Error displaying details: {err}")
            notify(f"Error displaying details: {err}")

    # Load / Save Config
    config = {"api_key": "", "players": []}
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config["api_key"] = str(loaded.get("api_key", ""))
                if isinstance(loaded.get("players"), list):
                    config["players"] = loaded["players"]
        except Exception:
            pass

    def sort_config_players():
        """Sorts players so enabled players are at the top, and alphabetically within each group."""
        config.setdefault("players", []).sort(
            key=lambda p: (not p.get("enabled", True), p.get("display_name", "").lower())
        )

    def save_config():
        sort_config_players()
        CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

    # App State & In-Memory Session Caches
    players: List[Player] = []
    last_fetched_player_ids: Set[str] = set()
    all_games_map: Dict[int, GameItem] = {}
    session_players_cache: Dict[str, Player] = {}   # steam_id -> fetched Player
    session_games_cache: Dict[int, GameItem] = {}   # appid -> GameItem with fetched store metadata
    cached_filtered_games: List[GameItem] = []

    # Pagination State
    current_page = 1
    page_size = 50

    # UI Controls - Left Panel
    api_key_warning_icon = ft.Container(
        content=ft.Icon(
            get_icon("warning_amber_rounded"),
            color=get_color("orange400"),
            size=20,
        ),
        tooltip="Warning: Your API key is stored on disk in plain text (steam_finder_config.json).",
        padding=get_padding_symmetric(horizontal=2, vertical=2),
    )

    api_key_header_row = ft.Row(
        controls=[
            ft.Text("Steam Web API Key", size=18, weight="bold"),
            api_key_warning_icon,
        ],
        spacing=6,
    )

    api_key_field = ft.TextField(
        label="Your API key",
        value=config.get("api_key", ""),
        password=True,
        can_reveal_password=True,
        expand=True,
    )

    api_key_help_btn = create_icon_button(
        "help_outline",
        tooltip="Get your Steam API Key from the official Steam website",
        on_click=lambda _: open_url("https://steamcommunity.com/dev/apikey"),
    )

    api_key_row = ft.Row([api_key_field, api_key_help_btn])

    add_player_help_text = (
        "How Adding Players Works:\n"
        "• Accepted inputs:\n"
        "  1. 17-digit SteamID64 (e.g. 76561197960287930)\n"
        "  2. Full Steam Profile URL (steamcommunity.com/id/... or /profiles/...)\n"
        "  3. Custom Vanity URL Name (e.g. gabelogannewell)\n"
        "• Privacy requirement: Each player's Steam Profile AND 'Game details' privacy setting must be set to Public.\n"
        "• Storage: Your player list is saved locally on disk in steam_finder_config.json."
    )

    add_player_info_icon = ft.Container(
        content=ft.Icon(
            get_icon("info_outline"),
            color=get_color("cyan300"),
            size=19,
        ),
        tooltip=add_player_help_text,
        padding=get_padding_symmetric(horizontal=2, vertical=2),
        on_click=lambda _: show_dialog("Adding Steam Players", add_player_help_text),
    )

    add_player_header_row = ft.Row(
        controls=[
            ft.Text("Add New Player", size=16, weight="bold"),
            add_player_info_icon,
        ],
        spacing=6,
    )

    new_player_input = ft.TextField(
        hint_text="Enter SteamID64, profile URL, or vanity name",
        expand=True,
    )

    player_list_view = ft.ListView(height=280, spacing=5)
    try:
        player_list_view.scroll = get_always_scroll()
    except Exception:
        pass

    # Main Submit Button
    submit_btn = create_button(
        "Find Common Games",
        icon="search",
        style=ft.ButtonStyle(
            bgcolor=get_color("blue700"),
            color=get_color("white"),
            mouse_cursor=get_click_cursor(),
        ),
    )

    def check_outdated_state():
        """Morphs the submit button when ticked players differ from the last fetched player set."""
        if not last_fetched_player_ids and not all_games_map:
            is_outdated = False
        else:
            selected_ids = {
                p["steam_id"] for p in config.get("players", []) if p.get("enabled", True)
            }
            is_outdated = (last_fetched_player_ids != selected_ids)

        label = "Refresh Games (Outdated)" if is_outdated else "Find Common Games"
        icon_val = get_icon("refresh" if is_outdated else "search")
        click_cursor = get_click_cursor()
        style_kwargs = {
            "bgcolor": get_color("amber700") if is_outdated else get_color("blue700"),
            "color": get_color("black") if is_outdated else get_color("white"),
        }
        if click_cursor is not None:
            style_kwargs["mouse_cursor"] = click_cursor

        btn_style = ft.ButtonStyle(**style_kwargs)

        try:
            if hasattr(submit_btn, "text"):
                submit_btn.text = label
            if hasattr(submit_btn, "content") and isinstance(getattr(submit_btn, "content", None), str):
                submit_btn.content = label
        except Exception:
            pass
        submit_btn.icon = icon_val
        submit_btn.style = btn_style

    # Status, Progress Bar & Centered Loading Spinner Controls
    status_text = ft.Text("", size=14, color=get_color("cyan200"), visible=False)
    fetch_progress_bar = ft.ProgressBar(
        value=0,
        visible=False,
        color=get_color("cyan400"),
        bgcolor=get_color("bluegrey800"),
    )
    center_loading_subtitle = ft.Text(
        "Connecting to Steam Web API...",
        size=14,
        color=get_color("cyan200"),
        text_align="center",
    )

    def set_progress_state(
        visible: bool,
        message: Optional[str] = None,
        progress: Optional[float] = None,
        auto_clear_seconds: Optional[float] = None,
    ):
        """Updates the status message and progress bar safely, with optional auto-clear timer."""
        nonlocal status_clear_task
        if status_clear_task and not status_clear_task.done():
            status_clear_task.cancel()

        if message is not None:
            status_text.value = message
            if is_fetching and message:
                center_loading_subtitle.value = message
        status_text.visible = bool(status_text.value)
        fetch_progress_bar.visible = visible
        fetch_progress_bar.value = progress
        page.update()

        if auto_clear_seconds and status_text.value:
            expected_msg = status_text.value

            async def _clear_after_delay():
                try:
                    await asyncio.sleep(auto_clear_seconds)
                    if status_text.value == expected_msg and not fetch_progress_bar.visible:
                        status_text.value = ""
                        status_text.visible = False
                        page.update()
                except asyncio.CancelledError:
                    pass

            status_clear_task = asyncio.create_task(_clear_after_delay())

    # ------------------------------------------
    # TOP FILTER & SORT CONTROLS
    # ------------------------------------------
    min_owned_label = ft.Text("Min Owned: 1", size=12, width=110)
    min_owned_slider = ft.Slider(
        min=0,
        max=5,
        divisions=5,
        value=1,
        label="Owned: {value}",
        width=170,
    )

    min_wishlist_label = ft.Text("Min Wishlist: 0", size=12, width=110)
    min_wishlist_slider = ft.Slider(
        min=0,
        max=5,
        divisions=5,
        value=0,
        label="Wishlist: {value}",
        width=170,
    )

    discount_label = ft.Text("Min Discount: 0%", size=12, width=110)
    discount_slider = ft.Slider(
        min=0,
        max=90,
        divisions=18,
        value=0,
        label="{value}%",
        width=170,
    )

    max_price_label = ft.Text("Max Price: Unlimited", size=12, width=130)
    max_price_slider = ft.Slider(
        min=0,
        max=MAX_PRICE_SLIDER_CAP,
        divisions=MAX_PRICE_SLIDER_CAP,
        value=MAX_PRICE_SLIDER_CAP,
        label="Unlimited",
        width=170,
    )

    def sync_slider_labels():
        min_owned_label.value = f"Min Owned: {int(round(min_owned_slider.value or 0))}"
        min_wishlist_label.value = f"Min Wishlist: {int(round(min_wishlist_slider.value or 0))}"
        discount_label.value = f"Min Discount: {int(round(discount_slider.value or 0))}%"

        price_val = int(
            round(max_price_slider.value if max_price_slider.value is not None else MAX_PRICE_SLIDER_CAP)
        )
        if price_val >= MAX_PRICE_SLIDER_CAP:
            max_price_label.value = "Max Price: Unlimited"
            max_price_slider.label = "Unlimited"
        elif price_val == 0:
            max_price_label.value = "Max Price: $0 (Free)"
            max_price_slider.label = "$0 (Free)"
        else:
            max_price_label.value = f"Max Price: ${price_val}"
            max_price_slider.label = f"${price_val}"

    def update_match_sliders():
        """Updates owned and wishlist slider max values to equal the count of checked players."""
        ticked_count = sum(1 for p in config.get("players", []) if p.get("enabled", True))
        max_players = max(1, ticked_count)
        is_disabled = ticked_count == 0

        for slider in (min_owned_slider, min_wishlist_slider):
            slider.min = 0
            slider.max = max_players
            slider.divisions = max_players
            slider.disabled = is_disabled
            if slider.value > max_players:
                slider.value = max_players
            elif slider.value < 0:
                slider.value = 0

        # Ensure min owned and min wishlist are never both 0
        if int(round(min_owned_slider.value or 0)) == 0 and int(round(min_wishlist_slider.value or 0)) == 0:
            min_owned_slider.value = 1

        sync_slider_labels()

    def on_owned_slider_change(e):
        nonlocal current_page
        if int(round(min_owned_slider.value or 0)) == 0 and int(round(min_wishlist_slider.value or 0)) == 0:
            min_wishlist_slider.value = 1
        sync_slider_labels()
        current_page = 1
        filter_and_render_games()

    def on_wishlist_slider_change(e):
        nonlocal current_page
        if int(round(min_wishlist_slider.value or 0)) == 0 and int(round(min_owned_slider.value or 0)) == 0:
            min_owned_slider.value = 1
        sync_slider_labels()
        current_page = 1
        filter_and_render_games()

    def on_filter_change(e=None):
        nonlocal current_page
        sync_slider_labels()
        current_page = 1
        filter_and_render_games()

    min_owned_slider.on_change = on_owned_slider_change
    min_wishlist_slider.on_change = on_wishlist_slider_change
    discount_slider.on_change = on_filter_change
    max_price_slider.on_change = on_filter_change

    def render_player_list():
        sort_config_players()
        player_list_view.controls.clear()

        for idx, p_data in enumerate(config.get("players", []), start=1):
            def make_toggle(p):
                def toggle_handler(e):
                    p["enabled"] = e.control.value
                    save_config()
                    render_player_list()
                    filter_and_render_games(recompute_filters=False)
                return toggle_handler

            def make_delete(p):
                def delete_handler(e):
                    config["players"].remove(p)
                    session_players_cache.pop(p.get("steam_id", ""), None)
                    save_config()
                    render_player_list()
                    filter_and_render_games(recompute_filters=False)
                    notify(f"Removed '{p['display_name']}' from player list.")
                return delete_handler

            is_enabled = p_data.get("enabled", True)
            player_num_badge = ft.Container(
                content=ft.Text(
                    f"#{idx}",
                    size=11,
                    weight="bold",
                    color=get_color("cyan200") if is_enabled else get_color("grey500"),
                ),
                bgcolor=get_color("bluegrey900"),
                padding=get_padding_symmetric(horizontal=6, vertical=3),
                border_radius=4,
            )

            cb = ft.Checkbox(
                label=p_data["display_name"],
                value=is_enabled,
                expand=True,
            )
            cb.on_change = make_toggle(p_data)

            del_btn = create_icon_button(
                "delete",
                icon_color="red400",
                tooltip="Delete player",
                on_click=make_delete(p_data),
            )

            row_container = ft.Container(
                padding=get_padding_only(right=14),
                content=ft.Row(
                    [
                        ft.Row([player_num_badge, cb], spacing=6, expand=True),
                        del_btn,
                    ],
                    alignment="spaceBetween",
                ),
            )
            player_list_view.controls.append(row_container)

        update_match_sliders()
        check_outdated_state()
        page.update()

    async def on_add_player(e):
        api_key = api_key_field.value.strip()
        if not api_key:
            show_dialog("API Key Required", "Please enter your Steam Web API Key before adding players.")
            return

        val = new_player_input.value.strip()
        if not val:
            return

        config["api_key"] = api_key
        save_config()

        set_progress_state(True, "Resolving Steam user profile...", None)

        async with SteamClient(api_key) as client:
            resolved_id = await client.resolve_steam_id(val)

            if not resolved_id:
                set_progress_state(False, "")
                show_dialog(
                    "Player Not Found",
                    f"Could not find a Steam profile for:\n'{val}'\n\nPlease verify the SteamID64 or URL.",
                )
                return

            if any(p["steam_id"] == resolved_id for p in config.get("players", [])):
                set_progress_state(False, "")
                show_dialog("Duplicate Player", "This player is already in your player list.")
                return

            temp_player = Player(steam_id=resolved_id)
            await client.fetch_player_summary(temp_player)

        new_entry = {
            "steam_id": resolved_id,
            "display_name": temp_player.display_name,
            "enabled": True,
        }
        config.setdefault("players", []).append(new_entry)
        save_config()

        new_player_input.value = ""
        set_progress_state(
            False,
            f"Added player '{temp_player.display_name}'!",
            auto_clear_seconds=4.0,
        )
        notify(f"Added player '{temp_player.display_name}'!")
        render_player_list()
        filter_and_render_games(recompute_filters=False)

    new_player_input.on_submit = on_add_player

    add_player_btn = create_button("Add Player", icon="add", on_click=on_add_player)
    add_player_row = ft.Row([new_player_input, add_player_btn])

    sort_dropdown = create_dropdown(
        label="Sort By",
        value="Owned",
        options=[
            create_option("Owned"),
            create_option("Wishlisted"),
            create_option("Price"),
            create_option("Discount"),
        ],
        width=155,
        on_change=on_filter_change,
    )

    list_mode_switch = ft.Switch(
        label="List Mode",
        value=False,
    )
    list_mode_switch.on_change = lambda _: filter_and_render_games(recompute_filters=False)

    show_unreleased_switch = ft.Switch(
        label="Show Unreleased",
        value=True,
    )
    show_unreleased_switch.on_change = on_filter_change

    show_unavailable_switch = ft.Switch(
        label="Show Unavailable",
        value=False,
    )
    show_unavailable_switch.on_change = on_filter_change

    search_filter = ft.TextField(
        label="Search title...",
        prefix_icon=get_icon("search"),
        expand=True,
    )
    search_filter.on_change = on_filter_change

    custom_tags_filter = ft.TextField(
        hint_text="Filter by tags - enter multiple, comma-separated (e.g. RPG, Survival, Roguelike)",
        prefix_icon=get_icon("sell"),
        expand=True,
    )
    custom_tags_filter.on_change = on_filter_change

    coop_only_cb = ft.Checkbox(label="Co-op")
    coop_only_cb.on_change = on_filter_change

    multiplayer_only_cb = ft.Checkbox(label="Multiplayer")
    multiplayer_only_cb.on_change = on_filter_change

    macos_only_cb = ft.Checkbox(label="macOS")
    macos_only_cb.on_change = on_filter_change

    games_list_view = ft.ListView(expand=True, spacing=6)
    try:
        games_list_view.scroll = get_always_scroll()
    except Exception:
        pass

    games_container = ft.Container(content=games_list_view, expand=True)

    def build_centered_state(
        icon_name: str,
        icon_color: str,
        title: str,
        subtitle: str = "",
        show_spinner: bool = False,
        subtitle_control: Optional[ft.Control] = None,
    ) -> ft.Container:
        """Creates a polished, vertically and horizontally centered placeholder state for the games panel."""
        top_visual = (
            ft.ProgressRing(width=64, height=64, stroke_width=5, color=get_color(icon_color))
            if show_spinner
            else ft.Container(
                content=ft.Icon(get_icon(icon_name), size=76, color=get_color(icon_color)),
                padding=18,
                bgcolor=get_color("bluegrey900"),
                border_radius=50,
            )
        )

        col_controls = [
            top_visual,
            ft.Text(
                title,
                size=22,
                weight="bold",
                color=get_color("white"),
                text_align="center",
            ),
        ]

        if subtitle_control is not None:
            col_controls.append(subtitle_control)
        elif subtitle:
            col_controls.append(
                ft.Text(
                    subtitle,
                    size=14,
                    color=get_color("grey400"),
                    text_align="center",
                )
            )

        return ft.Container(
            expand=True,
            alignment=get_center_alignment(),
            padding=30,
            content=ft.Column(
                alignment="center",
                horizontal_alignment="center",
                tight=True,
                spacing=14,
                controls=col_controls,
            ),
        )

    # Helper to compute filtered GameItems
    def compute_filtered_games() -> List[GameItem]:
        search_query = search_filter.value.lower().strip() if search_filter.value else ""
        raw_tags_query = custom_tags_filter.value or ""
        required_tags = [t.strip().lower() for t in raw_tags_query.split(",") if t.strip()]

        sort_by = sort_dropdown.value or "Owned"
        min_owned = int(round(min_owned_slider.value or 0))
        min_wishlist = int(round(min_wishlist_slider.value or 0))
        min_discount = int(round(discount_slider.value or 0))
        max_price_val = int(
            round(max_price_slider.value if max_price_slider.value is not None else MAX_PRICE_SLIDER_CAP)
        )
        is_price_limited = max_price_val < MAX_PRICE_SLIDER_CAP
        include_unreleased = bool(show_unreleased_switch.value)
        include_unavailable = bool(show_unavailable_switch.value)

        filtered: List[GameItem] = []
        for g in all_games_map.values():
            if search_query and search_query not in g.name.lower():
                continue

            # Unreleased & Unavailable Store Filters
            if not include_unreleased and g.is_unreleased:
                continue
            if not include_unavailable and g.is_unavailable:
                continue

            # Owned & Wishlist Count Threshold Filters
            if len(g.owners) < min_owned:
                continue
            if len(g.wishlisters) < min_wishlist:
                continue

            # Category & Platform Filters
            if coop_only_cb.value and not g.is_coop:
                continue
            if multiplayer_only_cb.value and not g.is_multiplayer:
                continue
            if macos_only_cb.value and g.mac_supported is not True:
                continue

            # Custom Comma-Separated Tags Filter
            if required_tags:
                game_tags_lower = [lbl.lower() for lbl in (g.categories | g.genres)]
                if not all(any(req_t in gt for gt in game_tags_lower) for req_t in required_tags):
                    continue

            # Discount Filter (applies to all games when min_discount > 0)
            if min_discount > 0:
                if not g.is_on_sale or g.discount_pct < min_discount:
                    continue

            # Max Price Filter (uses final_price after applied discounts; free games have final_price == 0.0)
            if is_price_limited:
                if g.final_price is None or g.final_price > float(max_price_val):
                    continue

            filtered.append(g)

        if sort_by == "Wishlisted":
            filtered.sort(key=lambda g: (-len(g.wishlisters), -len(g.owners), g.name.lower()))
        elif sort_by == "Price":
            filtered.sort(
                key=lambda g: (
                    g.final_price is None,
                    g.final_price if g.final_price is not None else float("inf"),
                    -len(g.owners),
                    -len(g.wishlisters),
                    g.name.lower(),
                )
            )
        elif sort_by == "Discount":
            filtered.sort(
                key=lambda g: (
                    -g.discount_pct,
                    g.final_price if g.final_price is not None else float("inf"),
                    -len(g.owners),
                    -len(g.wishlisters),
                    g.name.lower(),
                )
            )
        else:
            filtered.sort(key=lambda g: (-len(g.owners), -len(g.wishlisters), g.name.lower()))

        return filtered

    # ------------------------------------------
    # CORE LOGIC: DELTA FETCH & AGGREGATE GAMES
    # ------------------------------------------
    async def on_submit(e):
        nonlocal current_page, is_fetching, has_loaded_once, last_fetched_player_ids
        if is_fetching:
            return

        api_key = api_key_field.value.strip()
        if not api_key:
            show_dialog("API Key Required", "Please enter your Steam Web API Key first.")
            return

        config["api_key"] = api_key
        save_config()

        active_players_data = [
            p for p in config.get("players", []) if p.get("enabled", True)
        ]

        if not active_players_data:
            show_dialog(
                "No Players Selected",
                "Please select at least 1 active player with a checkmark.",
            )
            return

        is_fetching = True
        submit_btn.disabled = True
        update_match_sliders()
        current_page = 1
        center_loading_subtitle.value = "Preparing to fetch libraries & store metadata..."
        filter_and_render_games(recompute_filters=False)

        try:
            # Reuse in-memory Player libraries; only fetch players not yet in session_players_cache
            players.clear()
            players_to_fetch: List[Player] = []

            for p_data in active_players_data:
                sid = p_data["steam_id"]
                cached_p = session_players_cache.get(sid)
                if cached_p and cached_p.library_fetched and not cached_p.is_private:
                    players.append(cached_p)
                else:
                    new_p = Player(steam_id=sid, display_name=p_data["display_name"])
                    players.append(new_p)
                    players_to_fetch.append(new_p)

            page.update()

            async with SteamClient(api_key) as client:
                if players_to_fetch:
                    set_progress_state(
                        True,
                        f"Fetching libraries & wishlists for {len(players_to_fetch)} player(s)...",
                        None,
                    )
                    tasks = []
                    for p in players_to_fetch:
                        tasks.append(client.fetch_player_summary(p))
                        tasks.append(client.fetch_owned_games(p))
                        tasks.append(client.fetch_wishlist(p))

                    await asyncio.gather(*tasks)

                    # Sync updated persona names back to config
                    config_updated = False
                    config_map = {p["steam_id"]: p for p in config.get("players", [])}
                    for p in players_to_fetch:
                        p.library_fetched = True
                        if not p.is_private:
                            session_players_cache[p.steam_id] = p
                        if p.steam_id in config_map and p.display_name != config_map[p.steam_id].get("display_name"):
                            config_map[p.steam_id]["display_name"] = p.display_name
                            config_updated = True

                    if config_updated:
                        save_config()

                    # Alert for private libraries among newly fetched players
                    private_players = [p.display_name for p in players_to_fetch if p.is_private]
                    if private_players:
                        show_dialog(
                            "Private Libraries Detected",
                            "The following players have private libraries or restricted privacy settings:\n\n• "
                            + "\n• ".join(private_players)
                            + "\n\nSteam's privacy settings prevent fetching their games. "
                            "Games for accessible profiles will still be displayed.",
                        )

                # Reset ownership/wishlist pointers on cached GameItems before re-aggregating active players
                for g_cached in session_games_cache.values():
                    g_cached.owners.clear()
                    g_cached.wishlisters.clear()
                all_games_map.clear()

                for p in players:
                    for appid, name in p.owned_games.items():
                        if appid not in session_games_cache:
                            session_games_cache[appid] = GameItem(appid, name)
                        elif session_games_cache[appid].name.startswith("App ") and not name.startswith("App "):
                            session_games_cache[appid].name = name
                        game_obj = session_games_cache[appid]
                        all_games_map[appid] = game_obj
                        if p not in game_obj.owners:
                            game_obj.owners.append(p)

                    for appid, name in p.wishlist_games.items():
                        if appid not in session_games_cache:
                            session_games_cache[appid] = GameItem(appid, name)
                        elif session_games_cache[appid].name.startswith("App ") and not name.startswith("App "):
                            session_games_cache[appid].name = name
                        game_obj = session_games_cache[appid]
                        all_games_map[appid] = game_obj
                        if p not in game_obj.wishlisters:
                            game_obj.wishlisters.append(p)

                # Only fetch store metadata for games not yet fetched in this session
                unfetched_games = sorted(
                    [g for g in all_games_map.values() if not g.store_info_fetched],
                    key=lambda g: (-len(g.owners), -len(g.wishlisters), g.name.lower()),
                )

                if unfetched_games:
                    set_progress_state(
                        True,
                        f"Fetching store prices, tags & deals for {len(unfetched_games)} games (0/{len(unfetched_games)})...",
                        0.0,
                    )

                    def on_bulk_progress(done: int, total: int):
                        set_progress_state(
                            True,
                            f"Fetching store prices, tags & deals ({done}/{total})...",
                            done / total if total > 0 else 1.0,
                        )

                    await client.fetch_bulk_store_items(unfetched_games, on_progress=on_bulk_progress)

            has_loaded_once = True
            last_fetched_player_ids = {p.steam_id for p in players}
            set_progress_state(
                False,
                f"Loaded {len(all_games_map)} unique games with live store prices & tags!",
                1.0,
                auto_clear_seconds=4.0,
            )
        finally:
            is_fetching = False
            submit_btn.disabled = False
            render_player_list()
            filter_and_render_games()

    submit_btn.on_click = on_submit

    # ------------------------------------------
    # TOP SUMMARY + TOGGLES + SORT BAR & BOTTOM PAGINATION BAR
    # ------------------------------------------
    matching_filters_text = ft.Text("Matching Filters: 0 / 0", size=12, weight="bold", color=get_color("white"))
    matching_filters_badge = ft.Container(
        content=matching_filters_text,
        bgcolor=get_color("blue800"),
        padding=get_padding_symmetric(horizontal=10, vertical=6),
        border_radius=6,
    )

    top_summary_bar = ft.Container(
        padding=get_padding_symmetric(horizontal=4, vertical=2),
        content=ft.Row(
            alignment="spaceBetween",
            controls=[
                matching_filters_badge,
                ft.Row(
                    [
                        show_unreleased_switch,
                        show_unavailable_switch,
                    ],
                    spacing=10,
                ),
                sort_dropdown,
            ],
        ),
    )

    page_info_text = ft.Text("Showing 0-0 (Page 0 of 0)", size=12, weight="bold", color=get_color("grey300"))
    prev_page_btn = create_button("< Prev", disabled=True)
    next_page_btn = create_button("Next >", disabled=True)

    def on_prev_page(e):
        nonlocal current_page
        if current_page > 1:
            current_page -= 1
            filter_and_render_games(recompute_filters=False)

    def on_next_page(e):
        nonlocal current_page
        max_p = math.ceil(len(cached_filtered_games) / page_size) if page_size > 0 else 1
        if current_page < max_p:
            current_page += 1
            filter_and_render_games(recompute_filters=False)

    def on_page_size_change(e):
        nonlocal page_size, current_page
        val = page_size_dropdown.value
        page_size = 999999 if val == "All" else int(val)
        current_page = 1
        filter_and_render_games(recompute_filters=False)

    prev_page_btn.on_click = on_prev_page
    next_page_btn.on_click = on_next_page

    page_size_dropdown = create_dropdown(
        options=[
            create_option("25", "25"),
            create_option("50", "50"),
            create_option("100", "100"),
            create_option("All", "All"),
        ],
        value="50",
        width=90,
        on_change=on_page_size_change,
    )

    bottom_pagination_bar = ft.Container(
        padding=get_padding_symmetric(horizontal=8, vertical=6),
        content=ft.Row(
            alignment="spaceBetween",
            controls=[
                ft.Row(
                    [
                        list_mode_switch,
                        page_info_text,
                    ],
                    spacing=16,
                ),
                ft.Row(
                    [
                        prev_page_btn,
                        next_page_btn,
                        ft.Text("Per page:", size=12),
                        page_size_dropdown,
                    ],
                    spacing=8,
                ),
            ],
        ),
    )

    # ------------------------------------------
    # RENDER & FILTER GAMES LIST
    # ------------------------------------------
    def filter_and_render_games(recompute_filters: bool = True):
        nonlocal current_page, cached_filtered_games

        ticked_count = sum(1 for p in config.get("players", []) if p.get("enabled", True))
        total_loaded = len(all_games_map)

        # State 1: Actively fetching game data -> Centered Spinner
        if is_fetching:
            matching_filters_text.value = "Matching Filters: 0 / 0"
            matching_filters_badge.bgcolor = get_color("bluegrey800")
            page_info_text.value = "Showing 0-0 (Page 0 of 0)"
            prev_page_btn.disabled = True
            next_page_btn.disabled = True
            games_container.content = build_centered_state(
                icon_name="sync",
                icon_color="cyan400",
                title="Fetching game data...",
                show_spinner=True,
                subtitle_control=center_loading_subtitle,
            )
            page.update()
            return

        # State 2: No players added or selected -> Centered Player Prompt
        if ticked_count == 0:
            matching_filters_text.value = "Matching Filters: 0 / 0"
            matching_filters_badge.bgcolor = get_color("bluegrey800")
            page_info_text.value = "Showing 0-0 (Page 0 of 0)"
            prev_page_btn.disabled = True
            next_page_btn.disabled = True
            games_container.content = build_centered_state(
                icon_name="group_add",
                icon_color="amber400",
                title="No players selected.",
                subtitle=(
                    "Add at least one Steam player on the left and make sure their checkbox is ticked,\n"
                    "then click 'Find Common Games' to load your squad's libraries."
                ),
            )
            page.update()
            return

        # State 3: Players are selected, but no game data has been loaded yet
        if not has_loaded_once and total_loaded == 0:
            matching_filters_text.value = "Matching Filters: 0 / 0"
            matching_filters_badge.bgcolor = get_color("bluegrey800")
            page_info_text.value = "Showing 0-0 (Page 0 of 0)"
            prev_page_btn.disabled = True
            next_page_btn.disabled = True
            games_container.content = build_centered_state(
                icon_name="sports_esports",
                icon_color="cyan300",
                title="No game data has been loaded yet.",
                subtitle="Click 'Find Common Games' on the bottom-left to scan your selected players' libraries.",
            )
            page.update()
            return

        if recompute_filters:
            cached_filtered_games = compute_filtered_games()

        filtered_games = cached_filtered_games
        total_count = len(filtered_games)
        total_players = len(players) or 1

        # Update persistent Filtered / Total count badge
        matching_filters_text.value = f"Matching Filters: {total_count} / {total_loaded}"
        matching_filters_badge.bgcolor = (
            get_color("blue800") if total_count > 0 else get_color("bluegrey800")
        )

        # State 4: Data loaded, but no games match current filters
        if total_count == 0:
            page_info_text.value = "Showing 0-0 (Page 0 of 0)"
            prev_page_btn.disabled = True
            next_page_btn.disabled = True
            games_container.content = build_centered_state(
                icon_name="search_off",
                icon_color="grey500",
                title="No games match the current filters.",
                subtitle="Try relaxing your search query, tags, price, or ownership sliders above.",
            )
            page.update()
            return

        # State 5: Render the scrollable list of matching games
        games_container.content = games_list_view
        is_small = bool(list_mode_switch.value)
        games_list_view.spacing = 2 if is_small else 6
        games_list_view.controls.clear()

        total_pages = math.ceil(total_count / page_size) if page_size > 0 else 1
        current_page = max(1, min(current_page, total_pages))

        start_idx = (current_page - 1) * page_size
        end_idx = min(start_idx + page_size, total_count)
        page_games = filtered_games[start_idx:end_idx]

        page_info_text.value = (
            f"Showing {start_idx + 1}-{end_idx} of {total_count} (Page {current_page} of {total_pages})"
        )
        prev_page_btn.disabled = current_page == 1
        next_page_btn.disabled = current_page >= total_pages

        # Build game card list for active page with # Rank Numbering
        for idx, game in enumerate(page_games):
            global_rank = start_idx + idx + 1
            owner_names = ", ".join(p.display_name for p in game.owners)
            wishlist_names = ", ".join(p.display_name for p in game.wishlisters)
            combined_tags = sorted(game.categories | game.genres)
            if combined_tags:
                tag_list = ", ".join(combined_tags[:5])
            elif game.store_info_fetched:
                tag_list = "None listed"
            else:
                tag_list = "Not fetched"

            info_tooltip_text = (
                f"🎮 #{global_rank} - {game.name}\n"
                f"• Owners ({len(game.owners)}): {owner_names or 'None'}\n"
                f"• Wishlist ({len(game.wishlisters)}): {wishlist_names or 'None'}\n"
                f"• Tags: {tag_list}"
            )
            if game.is_unreleased:
                info_tooltip_text += f"\n• Status: Unreleased ({game.release_date or 'Coming Soon'})"
            elif game.is_unavailable:
                info_tooltip_text += "\n• Status: Unavailable / Delisted in Steam Store"
            if game.mac_supported is not None:
                info_tooltip_text += f"\n• macOS: {'Supported' if game.mac_supported else 'No'}"

            badge_font_size = 10 if is_small else 11
            badge_pad = (
                get_padding_symmetric(horizontal=5, vertical=2)
                if is_small
                else get_padding_symmetric(horizontal=6, vertical=3)
            )

            rank_badge = ft.Container(
                content=ft.Text(
                    f"#{global_rank}",
                    size=10 if is_small else 12,
                    weight="bold",
                    color=get_color("cyan200"),
                ),
                bgcolor=get_color("bluegrey900"),
                padding=(
                    get_padding_symmetric(horizontal=6, vertical=2)
                    if is_small
                    else get_padding_symmetric(horizontal=8, vertical=4)
                ),
                border_radius=4,
            )

            owner_badge = ft.Container(
                content=ft.Text(
                    f"Owned: {len(game.owners)}/{total_players}",
                    size=badge_font_size,
                    color=get_color("white"),
                ),
                bgcolor=get_color("green700") if len(game.owners) == total_players else get_color("bluegrey700"),
                padding=badge_pad,
                border_radius=4,
            )

            wishlist_badge = ft.Container(
                content=ft.Text(
                    f"Wishlisted: {len(game.wishlisters)}/{total_players}",
                    size=badge_font_size,
                    color=get_color("white"),
                ),
                bgcolor=get_color("amber800") if len(game.wishlisters) > 0 else get_color("bluegrey900"),
                padding=badge_pad,
                border_radius=4,
            )

            badges = [owner_badge, wishlist_badge]

            if game.price_str:
                price_color = get_color("green400") if game.is_on_sale else get_color("grey400")
                price_text = f"{game.price_str} (-{game.discount_pct}%)" if game.is_on_sale else game.price_str
                badges.append(
                    ft.Container(
                        content=ft.Text(price_text, size=badge_font_size, color=get_color("black"), weight="bold"),
                        bgcolor=price_color,
                        padding=badge_pad,
                        border_radius=4,
                    )
                )

            unavailable_warning_icon = ft.Container(
                content=ft.Icon(
                    get_icon("warning_amber_rounded"),
                    color=get_color("amber300"),
                    size=15 if is_small else 18,
                ),
                tooltip="Unavailable / No longer available in the Steam Store",
            )

            if game.is_unavailable:
                row_bgcolor = get_color("red900")
            elif game.is_unreleased:
                row_bgcolor = get_color("amber900")
            else:
                row_bgcolor = None

            info_btn = create_icon_button(
                "info_outline",
                tooltip=info_tooltip_text,
                icon_color="cyan300",
                icon_size=16 if is_small else None,
                compact=is_small,
                on_click=lambda _, g=game: open_game_details_dialog(g),
            )

            store_url = f"https://store.steampowered.com/app/{game.appid}/"
            store_btn = create_icon_button(
                "open_in_new",
                tooltip="Open in Steam Store",
                icon_color="blue400",
                icon_size=16 if is_small else None,
                compact=is_small,
                on_click=lambda _, url=store_url: open_url(url),
            )

            title_controls = [unavailable_warning_icon] if game.is_unavailable else []
            title_controls.append(
                ft.Text(
                    game.name,
                    size=12 if is_small else 15,
                    weight="bold",
                    overflow="ellipsis",
                    max_lines=1,
                    expand=True,
                )
            )

            if is_small:
                card = create_card(
                    bgcolor=row_bgcolor,
                    content=ft.Container(
                        bgcolor=row_bgcolor,
                        border_radius=8,
                        padding=get_padding_only(left=6, top=2, right=14, bottom=2),
                        content=ft.Row(
                            alignment="spaceBetween",
                            spacing=8,
                            controls=[
                                rank_badge,
                                ft.Row(controls=title_controls, spacing=5, expand=True),
                                ft.Row(controls=badges, spacing=4),
                                ft.Row(controls=[info_btn, store_btn], spacing=2),
                            ],
                        ),
                    ),
                )
            else:
                thumb_img = ft.Image(
                    src=game.header_image_url,
                    width=110,
                    height=50,
                    fit="cover",
                    border_radius=4,
                    error_content=ft.Icon(get_icon("videogame_asset"), size=30, color=get_color("grey500")),
                )

                thumb_container = ft.Container(
                    content=thumb_img,
                    width=110,
                    height=50,
                    bgcolor=get_color("bluegrey900"),
                    border_radius=4,
                    tooltip=f"Click to view details for #{global_rank} {game.name}",
                    on_click=lambda _, g=game: open_game_details_dialog(g),
                )

                card = create_card(
                    bgcolor=row_bgcolor,
                    content=ft.Container(
                        bgcolor=row_bgcolor,
                        border_radius=8,
                        padding=get_padding_only(left=8, top=8, right=16, bottom=8),
                        content=ft.Row(
                            alignment="spaceBetween",
                            controls=[
                                ft.Row(
                                    controls=[
                                        rank_badge,
                                        thumb_container,
                                        ft.Column(
                                            spacing=4,
                                            expand=True,
                                            controls=[
                                                ft.Row(controls=title_controls, spacing=6),
                                                create_badge_layout(badges),
                                            ],
                                        ),
                                    ],
                                    spacing=10,
                                    expand=True,
                                ),
                                ft.Row(
                                    controls=[info_btn, store_btn],
                                    spacing=4,
                                ),
                            ],
                        ),
                    ),
                )

            games_list_view.controls.append(card)

        page.update()

    # Initial Render of Player List & Counters
    render_player_list()
    filter_and_render_games()

    # ------------------------------------------
    # LAYOUT BUILD
    # ------------------------------------------
    left_panel = create_card(
        content=ft.Container(
            padding=15,
            width=380,
            content=ft.Column(
                controls=[
                    api_key_header_row,
                    api_key_row,
                    ft.Divider(),
                    add_player_header_row,
                    add_player_row,
                    ft.Divider(),
                    ft.Text("Players", size=16, weight="bold"),
                    player_list_view,
                    ft.Divider(),
                    submit_btn,
                ]
            ),
        )
    )

    top_filter_bar = create_card(
        content=ft.Container(
            padding=10,
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment="spaceBetween",
                        spacing=20,
                        controls=[
                            search_filter,
                            ft.Row([coop_only_cb, multiplayer_only_cb, macos_only_cb], spacing=10),
                        ],
                    ),
                    ft.Row(controls=[custom_tags_filter]),
                    ft.Row(
                        alignment="spaceBetween",
                        controls=[
                            ft.Row([min_owned_label, min_owned_slider], spacing=4),
                            ft.Row([min_wishlist_label, min_wishlist_slider], spacing=4),
                        ],
                    ),
                    ft.Row(
                        alignment="spaceBetween",
                        controls=[
                            ft.Row([discount_label, discount_slider], spacing=4),
                            ft.Row([max_price_label, max_price_slider], spacing=4),
                        ],
                    ),
                ],
            ),
        )
    )

    status_section = ft.Column(
        spacing=4,
        controls=[
            status_text,
            fetch_progress_bar,
        ],
    )

    right_panel = ft.Column(
        expand=True,
        controls=[
            top_filter_bar,
            status_section,
            top_summary_bar,
            ft.Divider(height=4),
            games_container,
            ft.Divider(height=4),
            bottom_pagination_bar,
        ],
    )

    page.add(
        ft.Row(
            expand=True,
            controls=[left_panel, right_panel],
            spacing=15,
        )
    )


if __name__ == "__main__":
    if hasattr(ft, "run"):
        ft.run(main)
    elif hasattr(ft, "app"):
        ft.app(main)
    else:
        from flet import run
        run(main)
