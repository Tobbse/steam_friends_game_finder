"""Runs build_app on a fake page: load gating at startup and the player cap in Add Player."""
import asyncio
import json

import flet as ft
import pytest
from flet.controls.context import _context_page

import src.ui.app as app_mod
from src.core.squad_code import MAX_PLAYERS, encode_squad_code
from src.platform.desktop import DesktopPlatform
from src.platform.web import STORAGE_KEY, WebPlatform

SEED = {
    "api_key": "FAKE-KEY",
    "players": [{"steam_id": "76561197960287930", "display_name": "Alice", "enabled": True}],
}


class _Services:
    def register_service(self, service):
        pass


class _Window:
    width = None
    height = None


class FakePage:
    def __init__(self):
        self.controls, self.overlay, self.dialogs, self.tasks = [], [], [], []
        self.window = _Window()
        self._services = _Services()

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self, *a):
        pass

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        pass

    def run_task(self, fn, *args):
        self.tasks.append(asyncio.get_running_loop().create_task(fn(*args)))


class GatedStorage:
    """Browser storage whose first read waits until the test opens the gate."""

    def __init__(self, data):
        self.data = dict(data)
        self.gate = asyncio.Event()
        self.sets = []

    async def get(self, key):
        await self.gate.wait()
        return self.data.get(key)

    async def set(self, key, value):
        self.sets.append(value)
        self.data[key] = value
        return True


class FailingStorage:
    """Browser storage whose read fails, e.g. blocked site data."""

    def __init__(self):
        self.sets = []

    async def get(self, key):
        raise RuntimeError("storage blocked")

    async def set(self, key, value):
        self.sets.append(value)
        return True


class NullClipboard:
    async def set(self, v):
        pass


def walk(ctrl, seen=None):
    seen = seen if seen is not None else set()
    if ctrl is None or id(ctrl) in seen:
        return
    seen.add(id(ctrl))
    yield ctrl
    for attr in ("content", "controls", "actions", "title", "spans"):
        val = getattr(ctrl, attr, None)
        for c in (val if isinstance(val, list) else [val]):
            if isinstance(c, ft.BaseControl):
                yield from walk(c, seen)


def find_button(root, text):
    return next(c for c in walk(root) if isinstance(c, ft.Button) and c.content == text)


def left_panel(page):
    return page.controls[0].controls[0]


def toast_text(page):
    return next(c.value for o in page.overlay for c in walk(o) if isinstance(c, ft.Text))


@pytest.fixture
def page():
    page = FakePage()
    token = _context_page.set(page)
    yield page
    _context_page.reset(token)


async def _settle(page):
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [t for t in page.tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending)


def _cancel_leftovers():
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task():
            t.cancel()


def test_web_controls_disabled_and_saves_skipped_until_config_loaded(page):
    async def run():
        storage = GatedStorage({STORAGE_KEY: json.dumps(SEED)})
        platform = WebPlatform(page, storage=storage, clipboard=NullClipboard(), url_launcher=object())
        build = asyncio.create_task(app_mod.build_app(page, platform))
        await asyncio.sleep(0.01)
        assert not build.done()
        assert left_panel(page).disabled is True

        # A config change that slips through before the load must not overwrite browser storage.
        find_button(left_panel(page), "Import Squad").on_click(None)
        dlg = page.dialogs[-1]
        next(c for c in walk(dlg) if isinstance(c, ft.TextField)).value = encode_squad_code(
            [{"steam_id": "76561197960287999", "display_name": "Early"}]
        )
        find_button(dlg, "Merge").on_click(None)
        await _settle(page)
        assert storage.sets == []

        storage.gate.set()
        await build
        assert not left_panel(page).disabled
        texts = [c.value for c in walk(left_panel(page)) if isinstance(c, ft.Text)]
        assert "Selected Players (1)" in texts
        assert json.loads(storage.data[STORAGE_KEY]) == SEED

        # After the load, saves go through.
        find_button(left_panel(page), "Import Squad").on_click(None)
        dlg = page.dialogs[-1]
        next(c for c in walk(dlg) if isinstance(c, ft.TextField)).value = encode_squad_code(
            [{"steam_id": "76561197960287999", "display_name": "Later"}]
        )
        find_button(dlg, "Merge").on_click(None)
        await _settle(page)
        saved = json.loads(storage.data[STORAGE_KEY])
        assert sorted(p["display_name"] for p in saved["players"]) == ["Alice", "Later"]
        _cancel_leftovers()

    asyncio.run(run())


def test_import_dialog_has_no_replace_button(page, tmp_path):
    async def run():
        await app_mod.build_app(page, DesktopPlatform(page, config_path=tmp_path / "c.json"))
        find_button(left_panel(page), "Import Squad").on_click(None)
        labels = [c.content for c in walk(page.dialogs[-1]) if isinstance(c, ft.Button)]
        assert labels == ["Merge", "Cancel"]
        _cancel_leftovers()

    asyncio.run(run())


def test_desktop_controls_enabled_after_build(page, tmp_path):
    async def run():
        cfg = tmp_path / "c.json"
        cfg.write_text(json.dumps(SEED), encoding="utf-8")
        await app_mod.build_app(page, DesktopPlatform(page, config_path=cfg))
        assert not left_panel(page).disabled
        api_field = next(c for c in walk(left_panel(page)) if isinstance(c, ft.TextField) and c.label == "Your API key")
        assert api_field.value == "FAKE-KEY"
        _cancel_leftovers()

    asyncio.run(run())


def test_add_player_refuses_beyond_cap(page, tmp_path, monkeypatch):
    class NoClient:
        def __init__(self, *a, **k):
            raise AssertionError("must not contact Steam when the list is full")

    monkeypatch.setattr(app_mod, "SteamClient", NoClient)

    async def run():
        cfg = tmp_path / "c.json"
        players = [
            {"steam_id": f"7656119{i:010d}", "display_name": f"P{i}", "enabled": False}
            for i in range(MAX_PLAYERS)
        ]
        cfg.write_text(json.dumps({"api_key": "FAKE-KEY", "players": players}), encoding="utf-8")
        await app_mod.build_app(page, DesktopPlatform(page, config_path=cfg))
        new_input = next(
            c for c in walk(left_panel(page)) if isinstance(c, ft.TextField) and c.hint_text and "SteamID64" in c.hint_text
        )
        new_input.value = "76561190000099999"
        await find_button(left_panel(page), "Add Player").on_click(None)
        assert str(MAX_PLAYERS) in toast_text(page)
        assert len(json.loads(cfg.read_text(encoding="utf-8"))["players"]) == MAX_PLAYERS
        _cancel_leftovers()

    asyncio.run(run())


def test_web_storage_read_error_shows_dialog_and_never_saves(page):
    async def run():
        storage = FailingStorage()
        platform = WebPlatform(page, storage=storage, clipboard=NullClipboard(), url_launcher=object())
        await app_mod.build_app(page, platform)

        titles = [d.title.value for d in page.dialogs]
        assert titles == ["Settings not loaded"]
        assert page.dialogs[0].content.value == (
            "Your saved settings couldn't be read, so changes in this session won't be saved. "
            "Use Export Squad to keep any players you add."
        )
        # The panel is usable and rendered with the defaults.
        assert not left_panel(page).disabled
        texts = [c.value for c in walk(left_panel(page)) if isinstance(c, ft.Text)]
        assert "Selected Players (0)" in texts

        # Edits work in the session but are never written.
        find_button(left_panel(page), "Import Squad").on_click(None)
        dlg = page.dialogs[-1]
        next(c for c in walk(dlg) if isinstance(c, ft.TextField)).value = encode_squad_code(
            [{"steam_id": "76561197960287999", "display_name": "New"}]
        )
        find_button(dlg, "Merge").on_click(None)
        await _settle(page)
        texts = [c.value for c in walk(left_panel(page)) if isinstance(c, ft.Text)]
        assert "Selected Players (1)" in texts
        assert storage.sets == []
        _cancel_leftovers()

    asyncio.run(run())


# ------------------------------------------
# Copy Titles button (next to Sort)
# ------------------------------------------
# One player owns all of these; reviews make the "User Reviews" sort differ from the default name order.
FAKE_LIBRARY = {
    10: ("Beta Quest", 70),
    11: ("alpha strike", 90),
    12: ("Gamma\tDeluxe\nEdition", 80),
    13: ("Café ☕ Story", 60),
}


class FakeSteamClient:
    """Stands in for SteamClient: every player owns FAKE_LIBRARY plus `extra` filler games."""

    extra = 0

    def __init__(self, api_key):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_player_summary(self, p):
        pass

    async def fetch_owned_games(self, p):
        p.owned_games = {appid: name for appid, (name, _) in FAKE_LIBRARY.items()}
        p.owned_games.update({1000 + i: f"Filler {i:03d}" for i in range(self.extra)})

    async def fetch_wishlist(self, p):
        pass

    async def fetch_bulk_store_items(self, games, on_progress=None):
        for g in games:
            g.store_info_fetched = True
            g.review_percentage = FAKE_LIBRARY.get(g.appid, ("", 50))[1]


def copy_button(page):
    sort_btn = next(c for c in walk(page.controls[0]) if isinstance(c, ft.PopupMenuButton) and c.tooltip == "Sort games")
    row = next(c for c in walk(page.controls[0]) if isinstance(c, ft.Row) and sort_btn in c.controls)
    idx = row.controls.index(sort_btn)
    assert idx > 0, "copy button must sit left of Sort"
    btn = row.controls[idx - 1]
    assert isinstance(btn, ft.IconButton) and btn.icon == ft.Icons.CONTENT_COPY
    return btn, sort_btn


def search_field(page):
    return next(c for c in walk(page.controls[0]) if isinstance(c, ft.TextField) and c.label == "Search title...")


async def _load_games(page):
    await find_button(page.controls[0], "Find Common Games").on_click(None)
    await _settle(page)


DEFAULT_ORDER = "alpha strike\nBeta Quest\nCafé ☕ Story\nGamma Deluxe Edition"


@pytest.fixture
def steam(monkeypatch):
    FakeSteamClient.extra = 0
    monkeypatch.setattr(app_mod, "SteamClient", FakeSteamClient)
    return FakeSteamClient


def test_copy_button_sits_left_of_sort_and_is_disabled_without_games(page, tmp_path, steam):
    async def run():
        cfg = tmp_path / "c.json"
        cfg.write_text(json.dumps(SEED), encoding="utf-8")
        await app_mod.build_app(page, DesktopPlatform(page, config_path=cfg))
        btn, _ = copy_button(page)
        assert btn.disabled is True
        assert btn.tooltip == "Nothing to copy: no games loaded yet"
        assert btn.on_click is None and btn.action is None

        await _load_games(page)
        assert btn.disabled is False
        assert btn.tooltip == "Copy titles of all 4 matching games"

        # No matches: disabled again, with the reason in the tooltip
        search_field(page).value = "zzz"
        search_field(page).on_change(None)
        assert btn.disabled is True
        assert btn.tooltip == "Nothing to copy: no games match the current filters"
        assert btn.on_click is None
        _cancel_leftovers()

    asyncio.run(run())


def test_desktop_copy_titles_copies_all_matching_games_and_toasts(page, tmp_path, steam):
    steam.extra = 30  # 34 games: more than one page of 25

    async def run():
        cfg = tmp_path / "c.json"
        cfg.write_text(json.dumps(SEED), encoding="utf-8")
        platform = DesktopPlatform(page, config_path=cfg)
        copied = []
        result = {"ok": True}

        async def fake_copy(text):
            copied.append(text)
            return result["ok"]

        platform.copy_to_clipboard = fake_copy
        await app_mod.build_app(page, platform)
        await _load_games(page)
        btn, sort_btn = copy_button(page)

        # Smaller pages, then go to page 2: the copy still covers every page
        dd = next(c for c in walk(page.controls[0]) if isinstance(c, ft.Dropdown) and c.value == "50")
        dd.value = "25"
        dd.on_select(None)
        await btn.on_click(None)  # page 1 of 2: still every page
        expected = ["alpha strike", "Beta Quest", "Café ☕ Story"] + [f"Filler {i:03d}" for i in range(30)]
        all_titles = "\n".join(expected + ["Gamma Deluxe Edition"])
        assert copied[-1] == all_titles
        find_button(page.controls[0], "Next >").on_click(None)
        await btn.on_click(None)
        page_info = [c.value for c in walk(page.controls[0]) if isinstance(c, ft.Text) and "Page" in str(c.value)]
        assert page_info == ["Showing 26-34 of 34 (Page 2 of 2)"]
        assert copied[-1] == all_titles
        assert toast_text(page) == "Copied 34 game titles."

        # Filter down to one game: singular toast
        search_field(page).value = "beta"
        search_field(page).on_change(None)
        assert btn.tooltip == "Copy the title of the 1 matching game"
        page.overlay.clear()
        await btn.on_click(None)
        assert copied[-1] == "Beta Quest"
        assert toast_text(page) == "Copied 1 game title."

        # Sort change re-orders the copy
        search_field(page).value = ""
        search_field(page).on_change(None)
        next(i for i in sort_btn.items if i.content == "User Reviews").on_click(None)
        await btn.on_click(None)
        assert copied[-1].split("\n")[:4] == ["alpha strike", "Gamma Deluxe Edition", "Beta Quest", "Café ☕ Story"]

        # Clipboard failure -> error toast
        result["ok"] = False
        page.overlay.clear()
        await btn.on_click(None)
        assert toast_text(page) == "Copy failed - the clipboard couldn't be written."
        _cancel_leftovers()

    asyncio.run(run())


def test_web_copy_titles_action_holds_current_text(page, steam):
    async def run():
        storage = GatedStorage({STORAGE_KEY: json.dumps(SEED)})
        storage.gate.set()
        platform = WebPlatform(page, storage=storage, clipboard=NullClipboard(), url_launcher=object())
        await app_mod.build_app(page, platform)
        btn, _ = copy_button(page)
        assert btn.disabled is True and btn.action is None

        await _load_games(page)
        assert isinstance(btn.action, ft.CopyToClipboard)
        assert btn.action.data == DEFAULT_ORDER

        search_field(page).value = "quest"
        search_field(page).on_change(None)
        assert btn.action.data == "Beta Quest"

        # Zero matches: disabled, both props cleared; clearing the filter brings the text back
        search_field(page).value = "zzz"
        search_field(page).on_change(None)
        assert btn.disabled is True
        assert btn.action is None and btn.on_click is None
        search_field(page).value = ""
        search_field(page).on_change(None)
        assert btn.disabled is False
        assert isinstance(btn.action, ft.CopyToClipboard) and btn.action.data == DEFAULT_ORDER
        search_field(page).value = "quest"
        search_field(page).on_change(None)

        # The click handler only toasts; the browser did the copy already
        btn.on_click(None)
        assert toast_text(page) == "Copied 1 game title."
        _cancel_leftovers()

    asyncio.run(run())


def test_copy_button_enabled_for_game_with_blank_title(page, tmp_path, monkeypatch):
    class BlankNameClient(FakeSteamClient):
        async def fetch_owned_games(self, p):
            p.owned_games = {440: "   "}

    monkeypatch.setattr(app_mod, "SteamClient", BlankNameClient)

    async def run():
        cfg = tmp_path / "c.json"
        cfg.write_text(json.dumps(SEED), encoding="utf-8")
        platform = DesktopPlatform(page, config_path=cfg)
        copied = []

        async def fake_copy(text):
            copied.append(text)
            return True

        platform.copy_to_clipboard = fake_copy
        await app_mod.build_app(page, platform)
        await _load_games(page)
        btn, _ = copy_button(page)
        assert btn.disabled is False
        assert btn.tooltip == "Copy the title of the 1 matching game"
        await btn.on_click(None)
        assert copied == ["App 440"]
        _cancel_leftovers()

    asyncio.run(run())
