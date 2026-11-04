import asyncio
import json
import re
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

import httpx

from src.core.models import GameItem, Player
from src.core.pricing import (
    detect_unreleased_from_store_item,
    has_unreleased_keyword,
    parse_price_string,
)

# Steam category/tag id -> name, fetched once per process
SESSION_CATEGORY_MAP: Dict[int, str] = {}
SESSION_TAG_MAP: Dict[int, str] = {}


class SteamClient:
    def __init__(self, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.api_key = api_key
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=15)
        self.client = httpx.AsyncClient(timeout=15.0, limits=limits, transport=transport)
        self._api_semaphore = asyncio.Semaphore(6)
        self._store_semaphore = asyncio.Semaphore(2)  # store.steampowered.com rate-limits harder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        await self.client.aclose()

    async def ensure_store_definitions(self):
        """Fetches the category and tag names. Retries on the next call if a fetch failed."""
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

        match_profile_url = re.search(r"steamcommunity\.com/profiles/(\d{17})", input_str)
        if match_profile_url:
            return match_profile_url.group(1)

        # /id/<vanity> before the bare 17-digit check: a vanity name can be 17 digits
        match_vanity = re.search(r"steamcommunity\.com/id/([^/?#]+)", input_str)
        if match_vanity:
            vanity_name = match_vanity.group(1)
        else:
            match_id64 = re.search(r"\b(\d{17})\b", input_str)
            if match_id64:
                return match_id64.group(1)
            vanity_name = input_str.rstrip("/")

        url = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
        params = {"key": self.api_key, "vanityurl": vanity_name}
        try:
            async with self._api_semaphore:
                resp = await self.client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("response", {}).get("success") == 1:
                    return data["response"]["steamid"]
        except Exception:
            pass
        return None

    async def fetch_player_summary(self, player: Player) -> Tuple[bool, Optional[str]]:
        """Fetches player summary and returns (found_profile, error_message)."""
        url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        params = {"key": self.api_key, "steamids": player.steam_id}
        try:
            async with self._api_semaphore:
                resp = await self.client.get(url, params=params)
            if resp.status_code in (401, 403):
                return False, "Invalid Steam Web API Key (HTTP 403/401). Please check your API key."
            if resp.status_code != 200:
                return False, f"Steam API returned HTTP {resp.status_code}."
            players = resp.json().get("response", {}).get("players", [])
            if players:
                player.display_name = players[0].get("personaname", player.steam_id)
                return True, None
            return False, None
        except Exception as exc:
            return False, f"Network error while contacting Steam: {exc}"

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
                player.fetch_error = "Invalid Steam Web API Key or unauthorized access (HTTP 403/401)."
                return
            if resp.status_code != 200:
                player.fetch_error = f"Steam API returned HTTP {resp.status_code}."
                return
            data = resp.json().get("response", {})
            # Private 'Game details' gives an empty response
            if "games" not in data and "game_count" not in data:
                player.is_private = True
                return
            for g in data.get("games", []):
                player.owned_games[g["appid"]] = g.get("name", f"App {g['appid']}")
        except Exception as exc:
            player.fetch_error = f"Network error: {exc}"

    async def fetch_wishlist(self, player: Player):
        url = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"
        try:
            async with self._api_semaphore:
                resp = await self.client.get(url, params={"key": self.api_key, "steamid": player.steam_id})
            if resp.status_code == 200:
                for item in resp.json().get("response", {}).get("items", []):
                    if item.get("appid"):
                        player.wishlist_games[int(item["appid"])] = f"App {item['appid']}"
        except Exception:
            pass

    async def fetch_bulk_store_items(
        self,
        games: List[GameItem],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ):
        """Fetches store data (price, reviews, tags, release status, ...) in chunks of 150 games."""
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
                        "include_reviews": True,
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

                            assets = item.get("assets") or {}
                            asset_fmt = assets.get("asset_url_format")
                            header_file = assets.get("header")
                            if asset_fmt and header_file:
                                rel_path = asset_fmt.replace("${FILENAME}", header_file).lstrip("/")
                                g.header_image_url = (
                                    f"https://shared.akamai.steamstatic.com/store_item_assets/{rel_path}"
                                )

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

                            tag_ids = item.get("tagids")
                            if isinstance(tag_ids, list):
                                for tid in tag_ids:
                                    tname = SESSION_TAG_MAP.get(int(tid))
                                    if tname:
                                        g.genres.add(tname)

                            basic = item.get("basic_info") or {}
                            if basic.get("short_description") and not g.short_description:
                                g.short_description = re.sub(r"<[^>]+>", "", basic["short_description"]).strip()
                            if basic.get("developers") and not g.developers:
                                g.developers = [d.get("name", "") for d in basic["developers"] if d.get("name")]

                            reviews_obj = item.get("reviews") or {}
                            summary_filtered = (
                                reviews_obj.get("summary_filtered")
                                or reviews_obj.get("summary_unfiltered")
                                or {}
                            )
                            pct_pos = summary_filtered.get("percent_positive")
                            review_cnt = int(summary_filtered.get("review_count") or 0)
                            if pct_pos is not None and review_cnt > 0:
                                g.review_percentage = int(pct_pos)
                                g.review_desc = str(summary_filtered.get("review_score_label") or "")

                            coming_soon, rel_date_str = detect_unreleased_from_store_item(
                                item, now_ts
                            )
                            g.is_unreleased = coming_soon
                            if rel_date_str and not g.release_date:
                                g.release_date = rel_date_str

                            platforms = item.get("platforms") or {}
                            if platforms:
                                g.windows_supported = bool(platforms.get("windows", False))
                                g.mac_supported = bool(platforms.get("mac", False))
                                g.linux_supported = bool(
                                    platforms.get("steamos_linux", False) or platforms.get("linux", False)
                                )

                            bpo = item.get("best_purchase_option")
                            if bpo:
                                formatted_price = str(bpo.get("formatted_final_price") or "").strip()
                                cents = bpo.get("final_price_in_cents")
                                parsed_price: Optional[float] = None
                                if cents is not None:
                                    try:
                                        parsed_price = int(cents) / 100.0
                                    except Exception:
                                        parsed_price = parse_price_string(formatted_price)
                                else:
                                    parsed_price = parse_price_string(formatted_price)

                                # Unreleased games can have a $0 placeholder price
                                if g.is_effectively_unreleased and (parsed_price is None or parsed_price == 0.0):
                                    g.price_str = ""
                                    g.final_price = None
                                    g.discount_pct = 0
                                    g.price_fetched = False
                                else:
                                    g.price_str = formatted_price
                                    g.final_price = parsed_price
                                    g.discount_pct = int(bpo.get("discount_pct", 0) or 0)
                                    g.price_fetched = parsed_price is not None or bool(formatted_price)
                            elif (item.get("is_free") or item.get("is_free_temporarily")) and not g.is_effectively_unreleased:
                                g.price_str = "Free to Play"
                                g.final_price = 0.0
                                g.discount_pct = 0
                                g.price_fetched = True

                            # Listed, but no release date, price or reviews, and nobody owns it: treat as upcoming
                            rel_obj = item.get("release") or {}
                            if (
                                not g.is_unreleased
                                and not g.price_fetched
                                and item.get("visible") is not False
                                and not item.get("unlisted")
                                and int(rel_obj.get("steam_release_date") or 0) == 0
                                and review_cnt == 0
                                and len(g.owners) == 0
                            ):
                                g.is_unreleased = True
                                if not g.release_date:
                                    g.release_date = "Coming Soon"

                            is_delisted = (
                                item.get("visible") is False
                                or item.get("unlisted") is True
                                or (item.get("success") not in (1, None))
                                or (not g.is_effectively_unreleased and not g.price_fetched)
                            )
                            g.is_unavailable = bool(is_delisted and not g.is_effectively_unreleased)
                            g.store_info_fetched = True

                        # Apps missing from the response are delisted
                        for g in chunk:
                            if g.appid not in returned_appids:
                                g.is_unavailable = not g.is_effectively_unreleased
                                g.store_info_fetched = True
                except Exception:
                    pass
                finally:
                    for g in chunk:
                        if not g.store_info_fetched:
                            g.store_info_fetched = True

            completed += len(chunk)
            if on_progress:
                on_progress(min(completed, total), total)

        # 2 chunks at a time, with a short pause, to stay under Steam's rate limit
        wave_size = 2
        for w_idx in range(0, len(chunks), wave_size):
            wave = chunks[w_idx : w_idx + wave_size]
            await asyncio.gather(*[process_chunk(c) for c in wave])
            if w_idx + wave_size < len(chunks):
                await asyncio.sleep(0.15)

    async def fetch_store_info(self, game: GameItem):
        """Single-game fallback for the details dialog, from the store's appdetails endpoint."""
        if game.details_fetched or (game.store_info_fetched and game.short_description):
            return

        async with self._store_semaphore:
            url = f"https://store.steampowered.com/api/appdetails?appids={game.appid}&cc=us"
            try:
                resp = await self.client.get(url)
                if resp.status_code != 200:
                    return
                game.details_fetched = True
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
                        game.release_date = str(rel_info["date"]).strip()
                    if rel_info.get("coming_soon") is True or has_unreleased_keyword(game.release_date):
                        game.is_unreleased = True
                        game.is_unavailable = False

                    game.categories.update(
                        c["description"] for c in game_data.get("categories", []) if c.get("description")
                    )
                    game.genres.update(
                        g["description"] for g in game_data.get("genres", []) if g.get("description")
                    )

                    platforms = game_data.get("platforms", {})
                    game.mac_supported = bool(platforms.get("mac", False))
                    game.windows_supported = bool(platforms.get("windows", False))
                    game.linux_supported = bool(platforms.get("linux", False))

                    if game_data.get("header_image"):
                        game.header_image_url = game_data["header_image"]

                    price_overview = game_data.get("price_overview")
                    if price_overview:
                        game.discount_pct = int(price_overview.get("discount_percent", 0) or 0)
                        game.price_str = str(price_overview.get("final_formatted") or "").strip()
                        if price_overview.get("final") is not None:
                            try:
                                game.final_price = int(price_overview["final"]) / 100.0
                            except Exception:
                                game.final_price = parse_price_string(game.price_str)
                        else:
                            game.final_price = parse_price_string(game.price_str)
                        game.price_fetched = True
                    elif game_data.get("is_free") and not game.is_effectively_unreleased:
                        game.price_str = "Free to Play"
                        game.final_price = 0.0
                        game.price_fetched = True

                    game.is_unavailable = bool(not game.is_effectively_unreleased and not game.price_fetched)
                    game.store_info_fetched = True
                else:
                    game.is_unavailable = not game.is_effectively_unreleased
                    game.store_info_fetched = True
            except Exception:
                pass
