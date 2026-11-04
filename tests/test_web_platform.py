import asyncio
import json

import flet as ft
import pytest

from src.platform.base import ConfigLoadError, Platform
from src.platform.web import (
    STORAGE_KEY,
    WebPlatform,
    is_allowed_image_host,
    proxied_image_url,
)

DEFAULTS = {"api_key": "", "players": []}
SAMPLE = {
    "api_key": "abc123",
    "players": [{"steam_id": "76561197960287930", "display_name": "Gabe", "enabled": True}],
}
IMG = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/440/header.jpg"


# ---------- is_allowed_image_host ----------

@pytest.mark.parametrize("host", [
    "shared.akamai.steamstatic.com",
    "cdn.cloudflare.steamstatic.com",
    "avatars.steamstatic.com",
    "SHARED.AKAMAI.STEAMSTATIC.COM",
    "steamcdn-a.akamaihd.net",
    "steamuserimages-a.akamaihd.net",
])
def test_allowed_image_hosts(host):
    assert is_allowed_image_host(host)


@pytest.mark.parametrize("host", [
    "steamstatic.com",  # bare domain, no subdomain
    "evil.com",
    "steamstatic.com.evil.com",
    "evilsteamstatic.com",
    "other.akamaihd.net",
    "shared.akamai.steamstatic.com:443",
    "a b.steamstatic.com",
    "shared.akamai.steamstatic.com/",
    ".steamstatic.com",
    "a..steamstatic.com",
    "-.steamstatic.com",
    "a-.steamstatic.com",
    "-a.steamstatic.com",
    "a.steamstatic.com.",
    "evil.com@avatars.steamstatic.com",
    "avatars.steamstatic.com\\evil.com",
    "",
    None,
])
def test_disallowed_image_hosts(host):
    assert not is_allowed_image_host(host)


# ---------- proxied_image_url ----------

def test_proxy_rewrites_steam_url_root_relative():
    assert proxied_image_url(IMG) == (
        "/steam-img/shared.akamai.steamstatic.com/store_item_assets/steam/apps/440/header.jpg"
    )


def test_proxy_drops_query():
    # The server ignores the query and caches by host + path; the browser URL matches that key.
    url = "https://steamcdn-a.akamaihd.net/a/b.jpg?t=123&x=1"
    assert proxied_image_url(url) == "/steam-img/steamcdn-a.akamaihd.net/a/b.jpg"


def test_proxy_encodes_path():
    url = "https://avatars.steamstatic.com/a dir/ü+x.jpg"
    assert proxied_image_url(url) == "/steam-img/avatars.steamstatic.com/a%20dir/%C3%BC%2Bx.jpg"


def test_proxy_keeps_existing_percent_escapes():
    url = "https://avatars.steamstatic.com/a%20b.jpg"
    assert proxied_image_url(url) == "/steam-img/avatars.steamstatic.com/a%20b.jpg"


def test_proxy_lowercases_host_and_handles_empty_path():
    assert proxied_image_url("https://AVATARS.steamstatic.com") == "/steam-img/avatars.steamstatic.com/"


def test_proxy_http_scheme_allowed():
    assert proxied_image_url("http://steamcdn-a.akamaihd.net/x.jpg") == "/steam-img/steamcdn-a.akamaihd.net/x.jpg"


@pytest.mark.parametrize("url", [
    "ftp://steamcdn-a.akamaihd.net/x.jpg",
    "data:image/png;base64,AAAA",
    "//steamcdn-a.akamaihd.net/x.jpg",
    "file:///etc/passwd",
])
def test_proxy_non_http_scheme_unchanged(url):
    assert proxied_image_url(url) == url


def test_proxy_disallowed_host_unchanged():
    url = "https://example.com/x.jpg"
    assert proxied_image_url(url) == url


@pytest.mark.parametrize("url", ["", None])
def test_proxy_empty_url_returns_empty_string(url):
    assert proxied_image_url(url) == ""


def test_proxy_off_returns_url_unchanged():
    assert proxied_image_url(IMG, proxy_images=False) == IMG


@pytest.mark.parametrize("url", [
    "https://evil.com@avatars.steamstatic.com/x.jpg",  # userinfo before a Steam host
    "https://avatars.steamstatic.com@evil.com/x.jpg",  # Steam name as userinfo, real host evil.com
    "https://avatars.steamstatic.com:8443/x.jpg",  # explicit port
    "https://avatars.steamstatic.com\\@evil.com/x.jpg",  # backslash: urlsplit sees host evil.com
])
def test_proxy_suspicious_netloc_unchanged(url):
    assert proxied_image_url(url) == url


def test_proxy_dot_dot_path_is_passed_through_for_server_to_reject():
    # The client does not normalize the path; the server's /steam-img/ route rejects "..".
    url = "https://avatars.steamstatic.com/../../x.jpg"
    assert proxied_image_url(url) == "/steam-img/avatars.steamstatic.com/../../x.jpg"


def test_proxy_invalid_url_unchanged():
    url = "https://[invalid/x.jpg"
    assert proxied_image_url(url) == url


# ---------- WebPlatform ----------

class FakePage:
    def __init__(self, url="https://app.example.com/"):
        self.url = url
        self.tasks = []

    def run_task(self, handler, *args):
        self.tasks.append((handler, args))


class FakeStorage:
    def __init__(self, data=None, fail=False):
        self.data = dict(data or {})
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise RuntimeError("storage unavailable")
        return self.data.get(key)

    async def set(self, key, value):
        if self.fail:
            raise RuntimeError("storage unavailable")
        assert isinstance(value, str)
        self.data[key] = value
        return True


class FakeClipboard:
    def __init__(self, fail=False):
        self.fail = fail
        self.value = None

    async def set(self, value):
        if self.fail:
            raise RuntimeError("no clipboard")
        self.value = value


class FakeLauncher:
    def __init__(self):
        self.launched = []

    async def launch_url(self, url):
        self.launched.append(url)


def make(page=None, storage=None, **kwargs):
    return WebPlatform(
        page if page is not None else FakePage(),
        storage=storage if storage is not None else FakeStorage(),
        clipboard=kwargs.pop("clipboard", FakeClipboard()),
        url_launcher=kwargs.pop("url_launcher", FakeLauncher()),
        **kwargs,
    )


def test_web_platform_satisfies_protocol():
    platform = make()
    assert isinstance(platform, Platform)
    assert platform.name == "web"


def test_default_services_are_flet_services():
    platform = WebPlatform(FakePage())
    assert isinstance(platform._storage, ft.SharedPreferences)
    assert isinstance(platform._clipboard, ft.Clipboard)
    assert isinstance(platform._url_launcher, ft.UrlLauncher)


def test_spawn_calls_page_run_task():
    page = FakePage()

    async def job(a, b):
        pass

    make(page).spawn(job, 1, "two")
    assert page.tasks == [(job, (1, "two"))]


def test_open_url_spawns_launch_on_client():
    page = FakePage()
    launcher = FakeLauncher()
    platform = make(page, url_launcher=launcher)
    platform.open_url("https://store.steampowered.com/app/440")
    assert len(page.tasks) == 1
    handler, args = page.tasks[0]
    asyncio.run(handler(*args))
    assert launcher.launched == ["https://store.steampowered.com/app/440"]


def test_open_url_ignores_empty():
    page = FakePage()
    make(page).open_url("")
    assert page.tasks == []


def test_link_props_returns_url_with_blank_target_and_no_on_click():
    props = make().link_props("https://store.steampowered.com/app/440")
    assert props == {"url": ft.Url("https://store.steampowered.com/app/440", target=ft.UrlTarget.BLANK)}
    assert "on_click" not in props
    # The props are accepted by the controls Task 4.5 uses.
    ft.TextSpan(text="x", **props)
    ft.IconButton(icon=ft.Icons.OPEN_IN_NEW, **props)
    ft.Button(content="x", **props)


def test_link_props_empty_url():
    assert make().link_props("") == {}


def test_copy_to_clipboard():
    clipboard = FakeClipboard()
    assert asyncio.run(make(clipboard=clipboard).copy_to_clipboard("SSF1:abc")) is True
    assert clipboard.value == "SSF1:abc"


def test_copy_to_clipboard_failure_returns_false():
    assert asyncio.run(make(clipboard=FakeClipboard(fail=True)).copy_to_clipboard("x")) is False


@pytest.mark.parametrize("page_url", [
    "https://app.example.com/path",
    "wss://123.discordsays.com",
    "ws://backend:8181",
    None,
])
def test_image_src_is_always_root_relative(page_url):
    assert make(FakePage(url=page_url)).image_src(IMG) == (
        "/steam-img/shared.akamai.steamstatic.com/store_item_assets/steam/apps/440/header.jpg"
    )


def test_image_src_does_not_read_page_url():
    class GonePage(FakePage):
        @property
        def url(self):
            raise RuntimeError("An attempt to fetch destroyed session.")

        @url.setter
        def url(self, value):
            pass

    assert make(GonePage()).image_src(IMG).startswith("/steam-img/")


@pytest.mark.parametrize("url", ["", None])
def test_image_src_empty_returns_empty_string(url):
    assert make().image_src(url) == ""


def test_image_src_proxy_off():
    assert make(proxy_images=False).image_src(IMG) == IMG


def test_image_src_non_steam_unchanged():
    assert make().image_src("https://example.com/a.jpg") == "https://example.com/a.jpg"


def test_save_then_load_roundtrip():
    storage = FakeStorage()
    platform = make(storage=storage)
    asyncio.run(platform.save_config(SAMPLE))
    assert json.loads(storage.data[STORAGE_KEY]) == SAMPLE
    assert asyncio.run(platform.load_config()) == SAMPLE


def test_save_writes_only_api_key_and_players():
    storage = FakeStorage()
    asyncio.run(make(storage=storage).save_config({**SAMPLE, "extra": 1}))
    assert storage.data[STORAGE_KEY] == json.dumps(SAMPLE, separators=(",", ":"))


def test_save_set_returning_false_is_reported(capsys):
    class RefusingStorage(FakeStorage):
        async def set(self, key, value):
            return False

    asyncio.run(make(storage=RefusingStorage()).save_config(SAMPLE))
    assert "Warning: Could not save config to browser storage" in capsys.readouterr().out


def test_save_success_prints_nothing(capsys):
    asyncio.run(make().save_config(SAMPLE))
    assert capsys.readouterr().out == ""


def test_save_error_is_reported_not_raised(capsys):
    asyncio.run(make(storage=FakeStorage(fail=True)).save_config(SAMPLE))
    assert "Warning: Could not save config to browser storage" in capsys.readouterr().out


def test_load_missing_returns_defaults():
    assert asyncio.run(make().load_config()) == DEFAULTS


@pytest.mark.parametrize("raw", ["", "{not json", "[1, 2]", "42", "null", '"text"'])
def test_load_garbage_returns_defaults(raw):
    platform = make(storage=FakeStorage({STORAGE_KEY: raw}))
    assert asyncio.run(platform.load_config()) == DEFAULTS


def test_load_deeply_nested_json_returns_defaults():
    raw = "[" * 100_000 + "]" * 100_000
    platform = make(storage=FakeStorage({STORAGE_KEY: raw}))
    assert asyncio.run(platform.load_config()) == DEFAULTS


def test_load_storage_read_error_raises_config_load_error():
    # A failed read must not look like "nothing saved": the app would then overwrite the saved config.
    with pytest.raises(ConfigLoadError, match="storage unavailable"):
        asyncio.run(make(storage=FakeStorage(fail=True)).load_config())


def test_load_validates_fields():
    raw = json.dumps({
        "api_key": "  key  ",
        "players": [
            {"steam_id": "76561197960287930", "display_name": "Gabe"},
            {"steam_id": "not-an-id"},
            "junk",
        ],
        "extra": 1,
    })
    platform = make(storage=FakeStorage({STORAGE_KEY: raw}))
    assert asyncio.run(platform.load_config()) == {
        "api_key": "key",
        "players": [{"steam_id": "76561197960287930", "display_name": "Gabe", "enabled": True}],
    }


def test_load_players_not_a_list():
    raw = json.dumps({"api_key": 42, "players": "nope"})
    platform = make(storage=FakeStorage({STORAGE_KEY: raw}))
    assert asyncio.run(platform.load_config()) == {"api_key": "42", "players": []}


# ---------- storage_description / copy_props ----------

@pytest.fixture
def flet_page_context():
    """ft.CopyToClipboard binds to the current page's clipboard service, so it needs a page context."""
    from flet.controls.context import _context_page

    class _Services:
        def register_service(self, service):
            pass

    page = FakePage()
    page._services = _Services()  # Flet services register with the context page on construction
    token = _context_page.set(page)
    yield page
    _context_page.reset(token)


def test_storage_description_mentions_browser():
    assert "browser" in make().storage_description
    assert ".json" not in make().storage_description


def test_copy_props_uses_client_side_copy_action(flet_page_context):
    clipboard = FakeClipboard()
    props = make(clipboard=clipboard).copy_props("SSF1:abc")
    assert set(props) == {"action"}
    assert isinstance(props["action"], ft.CopyToClipboard)
    assert props["action"].data == "SSF1:abc"
    # The copy happens on the client, never through the server-side clipboard service.
    assert clipboard.value is None
    # The props are accepted by the controls Task 4.5 uses.
    ft.Button(content="Copy", **props)
    ft.IconButton(icon=ft.Icons.CONTENT_COPY, **props)


def test_copy_props_on_done_runs_on_click(flet_page_context):
    done = []
    props = make().copy_props("x", on_done=done.append)
    assert set(props) == {"action", "on_click"}
    props["on_click"](object())
    assert done == [True]
    ft.Button(content="Copy", **props)


def test_copy_props_empty_text():
    assert make().copy_props("") == {}
