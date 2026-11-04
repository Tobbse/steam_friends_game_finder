import datetime

import httpx
import pytest

from src.core import steam_client as sc
from src.core.models import GameItem, Player
from src.core.steam_client import SteamClient

API = "api.steampowered.com"
STORE = "store.steampowered.com"
VANITY_PATH = "/ISteamUser/ResolveVanityURL/v1/"
SUMMARY_PATH = "/ISteamUser/GetPlayerSummaries/v2/"
OWNED_PATH = "/IPlayerService/GetOwnedGames/v1/"
WISHLIST_API_PATH = "/IWishlistService/GetWishlist/v1/"
CATEGORIES_PATH = "/IStoreBrowseService/GetStoreCategories/v1/"
TAGS_PATH = "/IStoreService/GetTagList/v1/"
ITEMS_PATH = "/IStoreBrowseService/GetItems/v1/"
APPDETAILS_PATH = "/api/appdetails"
STEAM_ID = "76561198000000001"


def store_wishlist_path(steam_id=STEAM_ID):
    return f"/wishlist/profiles/{steam_id}/wishlistdata/"


class FakeSteam:
    """
    Routes requests by (host, path) to canned responses. A route value is either an
    httpx.Response or a callable(request) -> httpx.Response (which may raise).
    Any request to an unregistered route fails the test immediately.
    """

    def __init__(self):
        self.routes = {}
        self.requests = []

    def add(self, host, path, response):
        self.routes[(host, path)] = response

    def calls(self, host, path):
        return [r for r in self.requests if (r.url.host, r.url.path) == (host, path)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.url.host, request.url.path))
        if route is None:
            # pytest.fail raises a BaseException, so SteamClient's `except Exception` can't swallow it.
            pytest.fail(f"Unexpected HTTP request: {request.method} {request.url}")
        return route(request) if callable(route) else route


@pytest.fixture
def fake():
    return FakeSteam()


@pytest.fixture
async def client(fake):
    c = SteamClient("TEST_KEY", transport=httpx.MockTransport(fake.handler))
    yield c
    await c.close()


@pytest.fixture(autouse=True)
def _clear_session_maps():
    sc.SESSION_CATEGORY_MAP.clear()
    sc.SESSION_TAG_MAP.clear()
    yield
    sc.SESSION_CATEGORY_MAP.clear()
    sc.SESSION_TAG_MAP.clear()


def raise_connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


# ---------- resolve_steam_id ----------

@pytest.mark.parametrize(
    "input_str",
    [
        f"https://steamcommunity.com/profiles/{STEAM_ID}",
        f"https://steamcommunity.com/profiles/{STEAM_ID}/",
        STEAM_ID,
        f"  {STEAM_ID}  ",
    ],
)
async def test_resolve_id_shortcuts_make_no_http_call(client, fake, input_str):
    assert await client.resolve_steam_id(input_str) == STEAM_ID
    assert fake.requests == []


@pytest.mark.parametrize("input_str", ["https://steamcommunity.com/id/gaben/", "gaben", "gaben/"])
async def test_resolve_vanity(client, fake, input_str):
    fake.add(API, VANITY_PATH, httpx.Response(200, json={"response": {"success": 1, "steamid": STEAM_ID}}))
    assert await client.resolve_steam_id(input_str) == STEAM_ID
    [req] = fake.calls(API, VANITY_PATH)
    assert req.url.params["vanityurl"] == "gaben"
    assert req.url.params["key"] == "TEST_KEY"


async def test_resolve_17_digit_vanity_url_uses_api(client, fake):
    other_id = "76561198000000099"
    fake.add(API, VANITY_PATH, httpx.Response(200, json={"response": {"success": 1, "steamid": other_id}}))
    assert await client.resolve_steam_id(f"https://steamcommunity.com/id/{STEAM_ID}") == other_id
    [req] = fake.calls(API, VANITY_PATH)
    assert req.url.params["vanityurl"] == STEAM_ID


async def test_resolve_vanity_not_found(client, fake):
    fake.add(API, VANITY_PATH, httpx.Response(200, json={"response": {"success": 42, "message": "No match"}}))
    assert await client.resolve_steam_id("nobody") is None


async def test_resolve_vanity_network_error_returns_none(client, fake):
    fake.add(API, VANITY_PATH, raise_connect_error)
    assert await client.resolve_steam_id("gaben") is None


# ---------- fetch_player_summary ----------

async def test_player_summary_success(client, fake):
    fake.add(API, SUMMARY_PATH, httpx.Response(200, json={
        "response": {"players": [{"personaname": "Gabe", "avatarmedium": "http://a.png"}]}
    }))
    player = Player(STEAM_ID)
    assert await client.fetch_player_summary(player) == (True, None)
    assert player.display_name == "Gabe"
    assert player.avatar_url == "http://a.png"


async def test_player_summary_not_found(client, fake):
    fake.add(API, SUMMARY_PATH, httpx.Response(200, json={"response": {"players": []}}))
    assert await client.fetch_player_summary(Player(STEAM_ID)) == (False, None)


@pytest.mark.parametrize("status", [401, 403])
async def test_player_summary_auth_error(client, fake, status):
    fake.add(API, SUMMARY_PATH, httpx.Response(status))
    found, err = await client.fetch_player_summary(Player(STEAM_ID))
    assert found is False
    assert "Invalid Steam Web API Key" in err


# ---------- fetch_owned_games ----------

async def test_owned_games_parsed(client, fake):
    fake.add(API, OWNED_PATH, httpx.Response(200, json={"response": {
        "game_count": 2,
        "games": [{"appid": 10, "name": "Counter-Strike"}, {"appid": 20}],
    }}))
    player = Player(STEAM_ID)
    await client.fetch_owned_games(player)
    assert player.owned_games == {10: "Counter-Strike", 20: "App 20"}
    assert player.is_private is False
    assert player.fetch_error is None
    [req] = fake.calls(API, OWNED_PATH)
    assert req.url.params["steamid"] == STEAM_ID


async def test_owned_games_zero_count_is_public_and_empty(client, fake):
    fake.add(API, OWNED_PATH, httpx.Response(200, json={"response": {"game_count": 0}}))
    player = Player(STEAM_ID)
    await client.fetch_owned_games(player)
    assert player.owned_games == {}
    assert player.is_private is False


@pytest.mark.parametrize("body", [{"response": {}}, {}])
async def test_owned_games_private_profile(client, fake, body):
    fake.add(API, OWNED_PATH, httpx.Response(200, json=body))
    player = Player(STEAM_ID)
    await client.fetch_owned_games(player)
    assert player.is_private is True
    assert player.owned_games == {}
    assert player.fetch_error is None


@pytest.mark.parametrize("status", [401, 403])
async def test_owned_games_auth_error_sets_fetch_error(client, fake, status):
    fake.add(API, OWNED_PATH, httpx.Response(status))
    player = Player(STEAM_ID)
    await client.fetch_owned_games(player)
    assert "HTTP 403/401" in player.fetch_error
    assert player.is_private is False


async def test_owned_games_other_http_error_sets_fetch_error(client, fake):
    fake.add(API, OWNED_PATH, httpx.Response(500))
    player = Player(STEAM_ID)
    await client.fetch_owned_games(player)
    assert player.fetch_error == "Steam API returned HTTP 500."


async def test_owned_games_network_error_sets_fetch_error(client, fake):
    fake.add(API, OWNED_PATH, raise_connect_error)
    player = Player(STEAM_ID)
    await client.fetch_owned_games(player)
    assert player.fetch_error.startswith("Network error:")
    assert "connection refused" in player.fetch_error


# ---------- fetch_wishlist ----------

async def test_wishlist_api_success_skips_store(client, fake):
    fake.add(API, WISHLIST_API_PATH, httpx.Response(200, json={"response": {
        "items": [{"appid": 10, "priority": 1}, {"appid": 20}, {"priority": 3}]
    }}))
    player = Player(STEAM_ID)
    await client.fetch_wishlist(player)
    assert player.wishlist_games == {10: "App 10", 20: "App 20"}
    # The store route is not registered, so any fallback call would fail the test.
    assert len(fake.requests) == 1


async def test_wishlist_api_empty_is_authoritative(client, fake):
    fake.add(API, WISHLIST_API_PATH, httpx.Response(200, json={"response": {}}))
    player = Player(STEAM_ID)
    await client.fetch_wishlist(player)
    assert player.wishlist_games == {}
    assert len(fake.requests) == 1


@pytest.mark.parametrize("api_route", [httpx.Response(500), httpx.Response(403), raise_connect_error])
async def test_wishlist_falls_back_to_store_when_api_fails(client, fake, api_route):
    fake.add(API, WISHLIST_API_PATH, api_route)
    fake.add(STORE, store_wishlist_path(), httpx.Response(200, json={
        "30": {"name": "Day of Defeat"}, "40": {}, "not_an_id": {"name": "x"}, "50": "junk",
    }))
    player = Player(STEAM_ID)
    await client.fetch_wishlist(player)
    assert player.wishlist_games == {30: "Day of Defeat", 40: "App 40"}
    [store_req] = fake.calls(STORE, store_wishlist_path())
    assert store_req.url.params["p"] == "0"
    assert "Mozilla" in store_req.headers["User-Agent"]


async def test_wishlist_store_fallback_paginates(client, fake):
    fake.add(API, WISHLIST_API_PATH, httpx.Response(500))

    def store_page(request):
        page = int(request.url.params["p"])
        if page == 0:
            return httpx.Response(200, json={str(1000 + i): {"name": f"G{i}"} for i in range(50)})
        if page == 1:
            return httpx.Response(200, json={"2000": {"name": "Last"}})
        pytest.fail(f"Unexpected page {page}")

    fake.add(STORE, store_wishlist_path(), store_page)
    player = Player(STEAM_ID)
    await client.fetch_wishlist(player)
    assert len(player.wishlist_games) == 51
    assert player.wishlist_games[2000] == "Last"
    assert [r.url.params["p"] for r in fake.calls(STORE, store_wishlist_path())] == ["0", "1"]


@pytest.mark.parametrize("store_body", [{"success": 2}, [], {}])
async def test_wishlist_store_fallback_private_or_empty(client, fake, store_body):
    fake.add(API, WISHLIST_API_PATH, httpx.Response(500))
    fake.add(STORE, store_wishlist_path(), httpx.Response(200, json=store_body))
    player = Player(STEAM_ID)
    await client.fetch_wishlist(player)
    assert player.wishlist_games == {}
    assert len(fake.calls(STORE, store_wishlist_path())) == 1


async def test_wishlist_without_api_key_goes_straight_to_store(fake):
    fake.add(STORE, store_wishlist_path(), httpx.Response(200, json={"30": {"name": "Day of Defeat"}}))
    async with SteamClient("", transport=httpx.MockTransport(fake.handler)) as c:
        player = Player(STEAM_ID)
        await c.fetch_wishlist(player)
    assert player.wishlist_games == {30: "Day of Defeat"}
    assert fake.calls(API, WISHLIST_API_PATH) == []


# ---------- session maps / store definitions ----------

def _add_definitions(fake):
    fake.add(API, CATEGORIES_PATH, httpx.Response(200, json={"response": {"categories": [
        {"categoryid": 9, "display_name": "Co-op"},
        {"categoryid": 49, "internal_name": "pvp"},
        {"categoryid": 99},
    ]}}))
    fake.add(API, TAGS_PATH, httpx.Response(200, json={"response": {"tags": [
        {"tagid": 19, "name": "Action"},
        {"tagid": 21, "name": ""},
    ]}}))


async def test_ensure_store_definitions_fills_session_maps(client, fake):
    _add_definitions(fake)
    await client.ensure_store_definitions()
    assert sc.SESSION_CATEGORY_MAP == {9: "Co-op", 49: "pvp"}
    assert sc.SESSION_TAG_MAP == {19: "Action"}

    # Second call is a no-op because both maps are filled.
    await client.ensure_store_definitions()
    assert len(fake.requests) == 2


async def test_ensure_store_definitions_retries_only_missing_map(client, fake):
    sc.SESSION_CATEGORY_MAP[1] = "Already Loaded"
    _add_definitions(fake)
    await client.ensure_store_definitions()
    assert fake.calls(API, CATEGORIES_PATH) == []
    assert sc.SESSION_TAG_MAP == {19: "Action"}


async def test_ensure_store_definitions_skipped_without_api_key(fake):
    async with SteamClient("", transport=httpx.MockTransport(fake.handler)) as c:
        await c.ensure_store_definitions()
    assert fake.requests == []
    assert sc.SESSION_CATEGORY_MAP == {}


async def test_session_maps_start_empty_in_each_test():
    assert sc.SESSION_CATEGORY_MAP == {}
    assert sc.SESSION_TAG_MAP == {}


# ---------- fetch_bulk_store_items (smoke) ----------

async def test_bulk_store_items_resolves_labels_and_price(client, fake):
    _add_definitions(fake)
    fake.add(API, ITEMS_PATH, httpx.Response(200, json={"response": {"store_items": [{
        "appid": 10,
        "name": "Counter-Strike",
        "categories": {"supported_player_categoryids": [9], "feature_categoryids": [49, 12345]},
        "tagids": [19, 777],
        "release": {"steam_release_date": 1579089600},
        "platforms": {"windows": True, "mac": False, "steamos_linux": True},
        "best_purchase_option": {"formatted_final_price": "$9.99", "final_price_in_cents": 999, "discount_pct": 50},
        "reviews": {"summary_filtered": {"percent_positive": 90, "review_count": 100, "review_score_label": "Very Positive"}},
    }]}}))
    game, missing = GameItem(10, "App 10"), GameItem(20, "App 20")
    progress = []
    await client.fetch_bulk_store_items([game, missing], on_progress=lambda d, t: progress.append((d, t)))

    assert game.name == "Counter-Strike"
    assert game.categories == {"Co-op", "pvp"}
    assert game.genres == {"Action"}
    assert game.final_price == 9.99
    assert game.discount_pct == 50
    assert game.review_percentage == 90
    assert (game.windows_supported, game.mac_supported, game.linux_supported) == (True, False, True)
    assert game.release_date == "Jan 15, 2020"
    assert game.is_unreleased is False
    assert game.is_unavailable is False
    # An appid Steam omits from the response is marked unavailable.
    assert missing.is_unavailable is True
    assert missing.store_info_fetched is True
    assert progress == [(2, 2)]


# ---------- unreleased detection in the store fetchers ----------

RELEASE_TS = int(datetime.datetime(2026, 12, 20, 12, tzinfo=datetime.timezone.utc).timestamp())


@pytest.mark.parametrize("now_ts, unreleased", [(RELEASE_TS - 3600, True), (RELEASE_TS + 3600, False)])
async def test_bulk_store_items_compare_release_timestamp_with_now(monkeypatch, client, fake, now_ts, unreleased):
    monkeypatch.setattr(sc.time, "time", lambda: now_ts)
    _add_definitions(fake)
    fake.add(API, ITEMS_PATH, httpx.Response(200, json={"response": {"store_items": [{
        "appid": 10,
        "release": {"steam_release_date": RELEASE_TS},
        "best_purchase_option": {"formatted_final_price": "$9.99", "final_price_in_cents": 999},
    }]}}))
    game = GameItem(10, "Game")
    game.owners = [Player(STEAM_ID)]
    await client.fetch_bulk_store_items([game])
    assert game.is_unreleased is unreleased
    assert game.is_effectively_unreleased is unreleased
    assert game.release_date == "Dec 20, 2026"
    assert game.final_price == 9.99


async def test_bulk_store_items_custom_date_text_without_flag_is_released(client, fake):
    _add_definitions(fake)
    fake.add(API, ITEMS_PATH, httpx.Response(200, json={"response": {"store_items": [{
        "appid": 10,
        "release": {"custom_release_date_message": "Q4 2099"},
        "best_purchase_option": {"formatted_final_price": "$9.99", "final_price_in_cents": 999},
    }]}}))
    game = GameItem(10, "Game")
    game.owners = [Player(STEAM_ID)]
    await client.fetch_bulk_store_items([game])
    assert game.is_unreleased is False
    assert game.release_date == "Q4 2099"


def _appdetails(release_date):
    return httpx.Response(200, json={"10": {"success": True, "data": {
        "name": "Game",
        "release_date": release_date,
        "price_overview": {"final": 999, "final_formatted": "$9.99", "discount_percent": 0},
    }}})


@pytest.mark.parametrize(
    "release_date, unreleased",
    [
        ({"coming_soon": True, "date": "Dec 20, 2099"}, True),
        ({"coming_soon": False, "date": "Coming soon"}, True),  # keyword match
        ({"coming_soon": False, "date": "Q4 2099"}, False),  # date text is not parsed
        ({"coming_soon": False, "date": "Jan 15, 2020"}, False),
    ],
)
async def test_store_info_uses_coming_soon_flag_or_keyword(client, fake, release_date, unreleased):
    fake.add(STORE, APPDETAILS_PATH, _appdetails(release_date))
    game = GameItem(10, "Game")
    await client.fetch_store_info(game)
    assert game.details_fetched is True
    assert game.release_date == release_date["date"]
    assert game.is_unreleased is unreleased
    assert game.is_effectively_unreleased is unreleased
    assert game.is_effectively_unavailable is False
    assert game.final_price == 9.99
