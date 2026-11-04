from typing import Callable, Optional

import flet as ft


# ==========================================
# FLET CONTROL FACTORIES & EVENT SYNC HELPERS (Flet 1.0)
# ==========================================
def sync_event_value(e, control: Optional[ft.Control] = None):
    """
    Ensures `control.value` is synchronized with the incoming event payload (`e.control.value` / `e.data`)
    without needlessly mutating active gesture controls.
    """
    if e is None:
        return
    target = control or getattr(e, "control", None)
    if target is None:
        return

    raw_data = getattr(e, "data", None)
    ctrl_val = getattr(target, "value", None)

    if isinstance(target, (ft.Checkbox, ft.Switch)):
        if isinstance(ctrl_val, bool):
            target.value = ctrl_val
        elif isinstance(raw_data, bool):
            target.value = raw_data
        elif isinstance(raw_data, str) and raw_data.lower() in ("true", "false"):
            target.value = raw_data.lower() == "true"
    elif isinstance(target, (ft.Dropdown, ft.TextField)):
        if ctrl_val is None and isinstance(raw_data, str):
            target.value = raw_data


def bind_change_handlers(
    control: ft.Control,
    handler: Optional[Callable],
    include_submit_and_blur: bool = False,
):
    """Binds the wrapped handler to whichever change/select (and optionally submit/blur) events the control has.
    Dropdown exposes `on_select`; Checkbox, Switch and TextField expose `on_change`."""
    if handler is None:
        return control

    def wrapped_handler(e):
        sync_event_value(e, control)
        handler(e)

    attrs = ["on_change", "on_select"]
    if include_submit_and_blur:
        attrs += ["on_submit", "on_blur"]
    for attr in attrs:
        if hasattr(control, attr):
            setattr(control, attr, wrapped_handler)

    return control


def create_checkbox(
    label: str,
    value: bool = False,
    on_change: Optional[Callable] = None,
    expand=None,
    label_size: Optional[int] = None,
    compact: bool = False,
) -> ft.Checkbox:
    """Creates a Checkbox with synchronized event handling and optional compact styling."""
    cb = ft.Checkbox(
        label=label,
        value=value,
        expand=expand,
        label_style=ft.TextStyle(size=label_size) if label_size is not None else None,
        visual_density=ft.VisualDensity.COMPACT if compact else None,
    )
    return bind_change_handlers(cb, on_change)


def create_switch(
    label: str,
    value: bool = False,
    on_change: Optional[Callable] = None,
) -> ft.Switch:
    """Creates a Switch with synchronized event handling."""
    sw = ft.Switch(label=label, value=value)
    return bind_change_handlers(sw, on_change)


def create_slider(
    min_val: float,
    max_val: float,
    divisions: int,
    value: float,
    on_drag_label: Optional[Callable] = None,
    on_release_commit: Optional[Callable] = None,
    expand: bool = True,
) -> ft.Slider:
    """
    Creates a Slider that separates live label updates (`on_change`) from heavy list filtering (`on_change_end`).
    This prevents Flutter/Flet from rebuilding the page or Slider widget mid-drag, which otherwise causes
    the mouse pointer to stay stuck to the slider thumb after releasing the mouse button.
    """
    sl = ft.Slider(
        min=min_val,
        max=max_val,
        divisions=divisions,
        value=value,
        expand=expand,
    )
    if on_drag_label is not None:
        sl.on_change = on_drag_label
    if on_release_commit is not None:
        sl.on_change_end = on_release_commit
    return sl


def get_padding_symmetric(horizontal: int = 0, vertical: int = 0):
    """Creates symmetric padding."""
    return ft.Padding.symmetric(horizontal=horizontal, vertical=vertical)


def get_padding_only(left: int = 0, top: int = 0, right: int = 0, bottom: int = 0):
    """Creates directional padding."""
    return ft.Padding.only(left=left, top=top, right=right, bottom=bottom)


def get_margin_only(left: int = 0, top: int = 0, right: int = 0, bottom: int = 0):
    """Creates directional margin."""
    return ft.Margin.only(left=left, top=top, right=right, bottom=bottom)


def get_always_scroll():
    """Returns ALWAYS scroll mode."""
    return ft.ScrollMode.ALWAYS


def get_click_cursor():
    """Returns the clickable hand MouseCursor."""
    return ft.MouseCursor.CLICK


def get_image_fit_cover():
    """Returns COVER image fit."""
    return ft.BoxFit.COVER


def get_font_weight_bold():
    """Returns BOLD font weight."""
    return ft.FontWeight.BOLD


def get_text_align_center():
    """Returns CENTER text alignment."""
    return ft.TextAlign.CENTER


def get_text_overflow_ellipsis():
    """Returns ELLIPSIS text overflow."""
    return ft.TextOverflow.ELLIPSIS


_MAIN_ALIGNMENT_NAMES = {
    "spaceBetween": "SPACE_BETWEEN",
    "center": "CENTER",
    "end": "END",
    "start": "START",
}


def get_main_alignment(name: str):
    """Resolves a MainAxisAlignment from a short name like "spaceBetween" or "center"."""
    return getattr(ft.MainAxisAlignment, _MAIN_ALIGNMENT_NAMES.get(name, name.upper()), name)


def get_cross_alignment(name: str):
    """Resolves a CrossAxisAlignment from a short name like "start" or "center"."""
    return getattr(ft.CrossAxisAlignment, name.upper(), name)


def configure_always_visible_scrollbars(page: ft.Page):
    """Configures page themes so scrollbars are always visible on all scrollable containers."""
    sb_theme = ft.ScrollbarTheme(thumb_visibility=True, track_visibility=True)
    page.theme = ft.Theme(scrollbar_theme=sb_theme)
    page.dark_theme = ft.Theme(scrollbar_theme=sb_theme)


def get_icon(name: str):
    """Resolves an icon name like "search" to `ft.Icons.SEARCH`. Non-strings pass through;
    unknown names fall back to the lowercase string."""
    if not isinstance(name, str):
        return name
    return getattr(ft.Icons, name.upper(), name.lower())


def get_color(name: str):
    """Resolves a color name like "white" to `ft.Colors.WHITE`. Hex strings and non-strings pass through.
    Names without a matching enum member (Flet 1.0 spells shades as e.g. `CYAN_200`) fall back to the
    lowercase string, which is the same value Flet uses for that color (e.g. "cyan200")."""
    if not isinstance(name, str) or name.startswith("#"):
        return name
    return getattr(ft.Colors, name.upper(), name.lower())


def get_color_with_opacity(opacity: float, color_name: str, fallback_hex: str) -> str:
    """Returns `color_name` at the given opacity as a Flet "color,opacity" string.
    `fallback_hex` (#AARRGGBB) is only used if the opacity is out of range: Flet 1.0's `with_opacity`
    validates the opacity but not the color."""
    try:
        return ft.Colors.with_opacity(opacity, get_color(color_name))
    except (TypeError, ValueError):
        return fallback_hex


def get_center_alignment():
    """Returns center alignment."""
    return ft.Alignment.CENTER


def get_border_all(width: float = 1.0, color: str = "#42A5F5"):
    """Creates a uniform border."""
    return ft.Border.all(width, get_color(color))


def create_card(content: ft.Control, bgcolor: Optional[str] = None, expand: bool = False) -> ft.Card:
    """Creates a Card with an optional background color."""
    return ft.Card(content=content, bgcolor=bgcolor, expand=expand or None)


def create_button(
    text: str,
    icon: Optional[str] = None,
    on_click=None,
    style=None,
    visible: bool = True,
    disabled: bool = False,
    **extra,
):
    """Creates a Button with a hand cursor on hover (added to `style` unless it already sets one).
    `extra` is passed to `ft.Button`, e.g. `**platform.link_props(url)` or `**platform.copy_props(text)`;
    an `on_click` in those props binds to the `on_click` parameter."""
    if style is None:
        style = ft.ButtonStyle(mouse_cursor=ft.MouseCursor.CLICK)
    elif style.mouse_cursor is None:
        style.mouse_cursor = ft.MouseCursor.CLICK

    return ft.Button(
        content=text,
        icon=get_icon(icon) if icon else None,
        style=style,
        visible=visible,
        disabled=disabled,
        on_click=on_click,
        **extra,
    )


def create_icon_button(
    icon_name: str,
    on_click=None,
    tooltip=None,  # str or ft.Tooltip
    icon_color: Optional[str] = None,
    icon_size: Optional[int] = None,
    compact: bool = False,
    **extra,
):
    """Creates an IconButton with a hand cursor on hover. `extra` is passed to `ft.IconButton`,
    e.g. `**platform.link_props(url)`."""
    return ft.IconButton(
        icon=get_icon(icon_name),
        tooltip=tooltip or None,
        icon_color=get_color(icon_color) if icon_color else None,
        icon_size=icon_size,
        width=28 if compact else None,
        height=28 if compact else None,
        padding=get_padding_symmetric(horizontal=2, vertical=2) if compact else None,
        mouse_cursor=ft.MouseCursor.CLICK,
        on_click=on_click,
        **extra,
    )


def create_option(text: str, key: Optional[str] = None):
    """Creates a dropdown option; the key defaults to the text."""
    return ft.DropdownOption(key=str(key if key is not None else text), text=text)


def create_tooltip(message: str, wait_ms: int):
    """Creates a Tooltip with a custom hover delay in milliseconds."""
    return ft.Tooltip(message=message, wait_duration=wait_ms)


def create_link_text(text: str, size: int, color: str = "white", **link_props) -> ft.Text:
    """Creates a single-line bold Text whose content is a clickable link (underlined on hover).
    `link_props` are passed to the `ft.TextSpan` and make it open the link: pass
    `**platform.link_props(url)` (desktop gives an `on_click`, web a `url`)."""
    style = ft.TextStyle(color=get_color(color))

    def set_hover(hovered: bool):
        style.decoration = ft.TextDecoration.UNDERLINE if hovered else ft.TextDecoration.NONE
        try:
            link_text.update()
        except Exception:
            pass  # Not yet mounted on a page.

    span = ft.TextSpan(
        text,
        style=style,
        on_enter=lambda _: set_hover(True),
        on_exit=lambda _: set_hover(False),
        **link_props,
    )

    link_text = ft.Text(
        spans=[span],
        size=size,
        weight=get_font_weight_bold(),
        overflow=get_text_overflow_ellipsis(),
        max_lines=1,
        expand=True,
        tooltip="Open in Steam Store",
    )
    return link_text


def create_menu_item(text: Optional[str] = None, icon: Optional[str] = None, checked: Optional[bool] = None, on_click=None):
    """Creates a PopupMenuItem. Calling it without text creates a divider item."""
    if text is None:
        return ft.PopupMenuItem()
    return ft.PopupMenuItem(
        content=text,
        icon=get_icon(icon) if icon else None,
        checked=checked,
        on_click=on_click,
    )


def create_dropdown(options, value=None, label=None, width=None, on_change=None):
    """Creates a Dropdown control with synchronized event handling."""
    kwargs = {k: v for k, v in (("label", label), ("value", value), ("width", width)) if v is not None}
    dd = ft.Dropdown(options=options, **kwargs)
    return bind_change_handlers(dd, on_change)


def create_badge_layout(controls):
    """Lays out badges in a single Row.
    Flet 1.0 has no `ft.Wrap`, so badges have always rendered in a non-wrapping Row."""
    return ft.Row(controls=controls, spacing=6)
