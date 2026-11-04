import flet as ft

from src.platform.desktop import DesktopPlatform
from src.ui.widgets import create_button, create_icon_button, create_link_text


def link_span(link_text: ft.Text) -> ft.TextSpan:
    return link_text.spans[0]


def test_link_text_with_desktop_props_opens_url(monkeypatch):
    opened = []
    monkeypatch.setattr("src.platform.desktop.webbrowser.open", lambda url: opened.append(url))
    text = create_link_text("Portal 2", size=15, **DesktopPlatform(page=None).link_props("https://x.test/"))
    span = link_span(text)
    span.on_click(object())
    assert opened == ["https://x.test/"]
    assert span.url is None


def test_link_text_with_url_props():
    url = ft.Url("https://x.test/", target=ft.UrlTarget.BLANK)
    span = link_span(create_link_text("Portal 2", size=15, url=url))
    assert span.url == url
    assert span.on_click is None


def test_link_text_hover_underlines():
    span = link_span(create_link_text("Portal 2", size=12))
    span.on_enter(None)
    assert span.style.decoration == ft.TextDecoration.UNDERLINE
    span.on_exit(None)
    assert span.style.decoration == ft.TextDecoration.NONE


def test_buttons_accept_link_props_without_duplicate_on_click():
    props = DesktopPlatform(page=None).link_props("https://x.test/")
    assert create_button("Store", icon="open_in_new", **props).on_click is props["on_click"]
    assert create_icon_button("open_in_new", tooltip="t", **props).on_click is props["on_click"]


def test_buttons_pass_extra_kwargs():
    url = ft.Url("https://x.test/")
    assert create_button("Store", url=url).url == url
    assert create_icon_button("open_in_new", url=url).url == url
