import asyncio
import json

import pytest

from src.platform.base import Platform
from src.platform.desktop import LEGACY_CONFIG_NAME, DesktopPlatform

DEFAULTS = {"api_key": "", "players": []}
SAMPLE = {
    "api_key": "abc123",
    "players": [{"steam_id": "76561197960287930", "display_name": "Gabe", "enabled": True}],
}


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "steam_friends_game_finder_config.json"


@pytest.fixture
def platform(config_path):
    return DesktopPlatform(page=None, config_path=config_path)


def load(platform):
    return asyncio.run(platform.load_config())


def save(platform, config):
    asyncio.run(platform.save_config(config))


def test_default_config_path_is_relative_new_name():
    assert str(DesktopPlatform(page=None).config_path) == "steam_friends_game_finder_config.json"


def test_load_missing_returns_defaults(platform):
    assert load(platform) == DEFAULTS


def test_save_then_load_roundtrip(platform, config_path):
    save(platform, SAMPLE)
    assert config_path.read_text(encoding="utf-8") == json.dumps(SAMPLE, indent=2)
    assert load(platform) == SAMPLE


def test_load_invalid_json_returns_defaults(platform, config_path):
    config_path.write_text("{not json", encoding="utf-8")
    assert load(platform) == DEFAULTS


def test_load_validates_fields(platform, config_path):
    config_path.write_text(json.dumps({"api_key": 42, "players": "nope", "extra": 1}), encoding="utf-8")
    assert load(platform) == {"api_key": "42", "players": []}


def test_load_non_dict_returns_defaults(platform, config_path):
    config_path.write_text("[1, 2]", encoding="utf-8")
    assert load(platform) == DEFAULTS


def test_fallback_to_legacy_file(platform, config_path):
    legacy = config_path.with_name(LEGACY_CONFIG_NAME)
    legacy.write_text(json.dumps(SAMPLE), encoding="utf-8")
    assert load(platform) == SAMPLE
    assert not config_path.exists()


def test_new_file_wins_over_legacy(platform, config_path):
    config_path.with_name(LEGACY_CONFIG_NAME).write_text(json.dumps(SAMPLE), encoding="utf-8")
    config_path.write_text(json.dumps({"api_key": "new", "players": []}), encoding="utf-8")
    assert load(platform) == {"api_key": "new", "players": []}


def test_save_writes_new_name_and_keeps_legacy(platform, config_path):
    legacy = config_path.with_name(LEGACY_CONFIG_NAME)
    legacy_text = json.dumps(SAMPLE)
    legacy.write_text(legacy_text, encoding="utf-8")

    config = load(platform)
    config["api_key"] = "changed"
    save(platform, config)

    assert json.loads(config_path.read_text(encoding="utf-8"))["api_key"] == "changed"
    assert legacy.read_text(encoding="utf-8") == legacy_text
    assert load(platform)["api_key"] == "changed"


def test_save_error_is_reported_not_raised(tmp_path, capsys):
    platform = DesktopPlatform(page=None, config_path=tmp_path / "missing_dir" / "cfg.json")
    save(platform, SAMPLE)
    assert "Warning: Could not save config file" in capsys.readouterr().out


def test_name_and_image_src(platform):
    assert platform.name == "desktop"
    assert platform.image_src("https://example.com/a.jpg") == "https://example.com/a.jpg"


def test_desktop_platform_satisfies_protocol(platform):
    assert isinstance(platform, Platform)


def test_spawn_calls_page_run_task():
    calls = []

    class FakePage:
        def run_task(self, handler, *args):
            calls.append((handler, args))

    async def job(a, b):
        pass

    DesktopPlatform(page=FakePage()).spawn(job, 1, "two")
    assert calls == [(job, (1, "two"))]


def test_open_url_calls_webbrowser_open(platform, monkeypatch):
    opened = []
    monkeypatch.setattr("src.platform.desktop.webbrowser.open", lambda url: opened.append(url))
    platform.open_url("https://example.com/")
    assert opened == ["https://example.com/"]


def test_link_props_on_click_opens_url(platform, monkeypatch):
    opened = []
    monkeypatch.setattr("src.platform.desktop.webbrowser.open", lambda url: opened.append(url))
    props = platform.link_props("https://example.com/")
    assert set(props) == {"on_click"}
    props["on_click"](object())  # Flet passes an event argument
    assert opened == ["https://example.com/"]


def test_link_props_empty_url(platform):
    assert platform.link_props("") == {}


@pytest.mark.parametrize("url", ["", None])
def test_image_src_empty_returns_empty_string(platform, url):
    assert platform.image_src(url) == ""
