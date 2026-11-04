import asyncio
import math
from typing import Dict, List, Optional, Set

import flet as ft

from src.core.export import format_game_titles
from src.core.filtering import (
    MAX_PRICE_SLIDER_CAP,
    SORT_DEFAULT_DESCENDING,
    SORT_OPTIONS,
    FilterSettings,
    filter_games,
    sort_games,
)
from src.core.models import GameItem, Player
from src.core.squad_code import (
    MAX_PLAYERS,
    decode_squad_code,
    encode_squad_code,
    merge_players,
    sanitize_player_entries,
)
from src.core.steam_client import SteamClient
from src.platform.base import Platform

BOLD = ft.FontWeight.BOLD
TEAL = "#00838F"
ORANGE = "#EF6C00"


def icon_button(icon, compact: bool = False, icon_color: Optional[str] = None, **kwargs) -> ft.IconButton:
    if compact:
        kwargs.update(width=28, height=28, padding=ft.Padding.symmetric(horizontal=2, vertical=2))
    return ft.IconButton(icon=icon, icon_color=icon_color, mouse_cursor=ft.MouseCursor.CLICK, **kwargs)


def link_text(text: str, size: int, link_props: dict) -> ft.Text:
    """Bold single-line link that underlines on hover."""
    style = ft.TextStyle(color="white")

    def set_hover(hovered: bool):
        style.decoration = ft.TextDecoration.UNDERLINE if hovered else ft.TextDecoration.NONE
        control.update()

    span = ft.TextSpan(
        text,
        style=style,
        on_enter=lambda _: set_hover(True),
        on_exit=lambda _: set_hover(False),
        **link_props,
    )
    control = ft.Text(
        spans=[span],
        size=size,
        weight=BOLD,
        overflow=ft.TextOverflow.ELLIPSIS,
        max_lines=1,
        expand=True,
        tooltip="Open in Steam Store",
    )
    return control


def tinted_box(color: str, **kwargs) -> ft.Container:
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.08, color),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.4, color)),
        **kwargs,
    )


def badge(text: str, bgcolor: str, size: int, padding, color: str = "white", bold: bool = False) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=size, color=color, weight=BOLD if bold else None),
        bgcolor=bgcolor,
        padding=padding,
        border_radius=4,
    )


def format_price(game: GameItem) -> str:
    if game.price_str:
        return game.price_str
    if game.is_effectively_unreleased:
        return "Unreleased / TBA"
    if game.is_effectively_unavailable:
        return "Unavailable / Delisted from Steam Store"
    return "Not listed" if game.store_info_fetched else "Fetching..."


def format_platforms(game: GameItem) -> str:
    names = [
        name
        for name, supported in (
            ("Windows", game.windows_supported),
            ("MacOS", game.mac_supported),
            ("Linux", game.linux_supported),
        )
        if supported
    ]
    if names:
        return ", ".join(names)
    return "N/A" if game.store_info_fetched else "Fetching..."


def format_reviews(game: GameItem) -> str:
    if game.review_percentage is not None:
        if game.review_desc:
            return f"{game.review_percentage}% ({game.review_desc})"
        return f"{game.review_percentage}%"
    return "No user reviews" if game.store_info_fetched else "Fetching..."


async def build_app(page: ft.Page, platform: Platform):
    page.title = "Steam Friends Game Finder"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = ft.Padding.symmetric(horizontal=20, vertical=8)
    scrollbars = ft.ScrollbarTheme(thumb_visibility=True, track_visibility=True)
    page.theme = ft.Theme(scrollbar_theme=scrollbars)
    page.dark_theme = ft.Theme(scrollbar_theme=scrollbars)
    page.window.width = 1330
    page.window.height = 960

    config = {"api_key": "", "players": []}
    players: List[Player] = []
    last_fetched_player_ids: Set[str] = set()
    all_games_map: Dict[int, GameItem] = {}
    session_players_cache: Dict[str, Player] = {}
    session_games_cache: Dict[int, GameItem] = {}
    cached_filtered_games: List[GameItem] = []
    is_fetching = False
    loading_title = "Fetching game data..."
    has_loaded_once = False
    current_page = 1
    page_size = 50

    # ---------- Toast & dialogs ----------
    toast_text = ft.Text("", color="white", weight=BOLD, size=13)
    toast = ft.Container(
        visible=False,
        top=10,
        left=0,
        right=0,
        content=ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    bgcolor="#1565C0",
                    border=ft.Border.all(1, "#42A5F5"),
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=18, vertical=10),
                    content=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[ft.Icon(ft.Icons.INFO_OUTLINE, color="cyan200", size=18), toast_text],
                    ),
                )
            ],
        ),
    )
    page.overlay.append(toast)
    toast_task: Optional[asyncio.Task] = None

    def notify(msg: str):
        nonlocal toast_task
        if toast_task and not toast_task.done():
            toast_task.cancel()
        toast_text.value = msg
        toast.visible = True
        page.update()

        async def hide():
            await asyncio.sleep(3.5)
            toast.visible = False
            page.update()

        toast_task = asyncio.create_task(hide())

    def close_dialog(dlg: ft.AlertDialog):
        dlg.open = False
        page.pop_dialog()

    def show_dialog(title: str, message: str):
        dlg = ft.AlertDialog(
            title=ft.Text(title, weight=BOLD),
            content=ft.Text(message),
            actions=[ft.Button(content="OK", on_click=lambda _: close_dialog(dlg))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)

    # ---------- Game details dialog ----------
    def open_game_details_dialog(game: GameItem):
        needs_fetch = (not game.store_info_fetched or not game.short_description) and not game.details_fetched
        pending = "Fetching..." if needs_fetch else None

        desc_text = ft.Text(size=12)
        price_text = ft.Text(size=14, weight=BOLD)
        review_text = ft.Text(size=12)
        dev_text = ft.Text(size=12)
        release_text = ft.Text(size=12)
        platform_text = ft.Text(size=12)
        tags_text = ft.Text(size=12)

        def fill(pending: Optional[str]):
            desc_text.value = game.short_description or (
                "Fetching store description..." if pending else "No description available."
            )
            price_text.value = f"Price: {format_price(game)}"
            if game.is_on_sale:
                price_text.color = "green400"
            elif game.is_effectively_unavailable:
                price_text.color = "red300"
            else:
                price_text.color = "white"
            review_text.value = f"• User Reviews: {format_reviews(game)}"
            review_text.color = "blue300" if game.review_percentage is not None else None
            dev_text.value = f"• Developer: {', '.join(game.developers) or pending or 'N/A'}"

            if game.is_effectively_unreleased:
                suffix, release_text.color = " (Unreleased)", "amber300"
            elif game.is_effectively_unavailable:
                suffix, release_text.color = " (Unavailable in Store)", "red300"
            else:
                suffix, release_text.color = "", None
            release_text.value = f"• Release Date: {game.release_date or pending or 'N/A'}{suffix}"
            platform_text.value = f"• Platforms: {format_platforms(game)}"
            tags = ", ".join(sorted(game.categories | game.genres)[:12])
            tags_text.value = f"• Tags & Categories: {tags or pending or 'None listed'}"

        fill(pending)
        total_players = len(players) or 1

        def player_line(icon, color: str, label: str, members: List[Player]) -> ft.Row:
            return ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Icon(icon, size=18, color=color),
                    ft.Text(f"{label} ({len(members)}/{total_players})", size=12, weight=BOLD, color=color, width=120),
                    ft.Text(", ".join(p.display_name for p in members) or "None", size=12, expand=True),
                ],
            )

        players_section = tinted_box(
            TEAL,
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Column(
                spacing=6,
                tight=True,
                controls=[
                    ft.Text("Players", weight=BOLD, size=13, color="cyan200"),
                    player_line(ft.Icons.CHECK_CIRCLE, "green400", "Owned", game.owners),
                    player_line(ft.Icons.BOOKMARK, "amber400", "Wishlisted", game.wishlisters),
                ],
            ),
        )

        dlg = ft.AlertDialog(
            title=ft.Text(game.name, weight=BOLD, size=18),
            content=ft.Container(
                width=480,
                content=ft.Column(
                    tight=True,
                    scroll=ft.ScrollMode.ALWAYS,
                    spacing=6,
                    controls=[
                        ft.Image(
                            src=platform.image_src(game.header_image_url),
                            width=460,
                            height=215,
                            fit=ft.BoxFit.COVER,
                            border_radius=6,
                            error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=50, color="grey500"),
                        ),
                        price_text,
                        players_section,
                        ft.Divider(),
                        ft.Text("Short Description:", weight=BOLD, size=13),
                        desc_text,
                        ft.Divider(),
                        review_text,
                        dev_text,
                        release_text,
                        platform_text,
                        tags_text,
                    ],
                ),
            ),
            actions=[
                ft.Button(
                    content="Steam Store",
                    icon=ft.Icons.OPEN_IN_NEW,
                    **platform.link_props(f"https://store.steampowered.com/app/{game.appid}/"),
                ),
                ft.Button(content="Close", on_click=lambda _: close_dialog(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)

        if needs_fetch:
            async def fetch_details():
                try:
                    async with SteamClient((api_key_field.value or "").strip()) as client:
                        await client.fetch_store_info(game)
                    fill(None)
                    # The fetch can change release, availability, tags or price
                    filter_and_render_games()
                except Exception as err:
                    print(f"Background fetch error: {err}")

            page.run_task(fetch_details)

    # ---------- Config ----------
    def sort_config_players():
        """Enabled players first, then alphabetically."""
        config["players"].sort(key=lambda p: (not p.get("enabled", True), p.get("display_name", "").lower()))

    async def save_config():
        sort_config_players()
        await platform.save_config({**config, "players": [dict(p) for p in config["players"]]})

    def enabled_players() -> List[dict]:
        return [p for p in config["players"] if p.get("enabled", True)]

    # ---------- Left panel ----------
    api_key_field = ft.TextField(label="Your API key", password=True, can_reveal_password=True, expand=True)

    api_key_header_row = ft.Row(
        spacing=6,
        controls=[
            ft.Text("Steam Web API Key", size=18, weight=BOLD),
            ft.Container(
                content=ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="orange400", size=20),
                tooltip=f"Warning: Your API key is stored in plain text {platform.storage_description}.",
                padding=2,
            ),
        ],
    )
    api_key_row = ft.Row(
        [
            api_key_field,
            icon_button(
                ft.Icons.HELP_OUTLINE,
                tooltip="Get your Steam API Key from the official Steam website",
                **platform.link_props("https://steamcommunity.com/dev/apikey"),
            ),
        ]
    )

    add_player_help_text = (
        "How Adding Players Works:\n"
        "• Accepted inputs:\n"
        "  1. 17-digit SteamID64 (e.g. 76561197960287930)\n"
        "  2. Full Steam Profile URL (steamcommunity.com/id/... or /profiles/...)\n"
        "  3. Custom Vanity URL Name (e.g. gabelogannewell)\n"
        "• Privacy requirement: Each player's Steam Profile AND 'Game details' privacy setting must be set to Public.\n"
        f"• Storage: Your player list is saved {platform.storage_description}.\n"
        "• Backup/share: Use 'Export Squad' / 'Import Squad' below the player list."
    )
    add_player_header_row = ft.Row(
        spacing=6,
        controls=[
            ft.Text("Add New Player", size=16, weight=BOLD),
            ft.Container(
                content=ft.Icon(ft.Icons.INFO_OUTLINE, color="cyan300", size=19),
                tooltip=add_player_help_text,
                padding=2,
                on_click=lambda _: show_dialog("Adding Steam Players", add_player_help_text),
            ),
        ],
    )

    new_player_input = ft.TextField(hint_text="Enter SteamID64, profile URL, or vanity name", expand=True)
    player_list_view = ft.ListView(expand=True, spacing=5, scroll=ft.ScrollMode.ALWAYS)
    submit_btn = ft.Button(content="Find Common Games", icon=ft.Icons.SEARCH)

    def update_submit_button():
        """Turns the submit button into "Refresh Games" when the ticked players differ from the last fetch."""
        selected_ids = {p["steam_id"] for p in enabled_players()}
        outdated = bool(last_fetched_player_ids or all_games_map) and last_fetched_player_ids != selected_ids
        submit_btn.content = "Refresh Games" if outdated else "Find Common Games"
        submit_btn.icon = ft.Icons.REFRESH if outdated else ft.Icons.SEARCH
        submit_btn.style = ft.ButtonStyle(
            bgcolor="amber700" if outdated else "blue700",
            color="black" if outdated else "white",
        )

    update_submit_button()

    loading_subtitle = ft.Text("", size=14, color="cyan200", text_align=ft.TextAlign.CENTER)
    progress_bar = ft.ProgressBar(width=360, value=None, color="cyan400", bgcolor="bluegrey800")

    def update_loading_progress(message: str, progress: Optional[float] = None):
        loading_subtitle.value = message
        progress_bar.value = progress
        page.update()

    # ---------- Sliders ----------
    def slider_value(slider: ft.Slider) -> int:
        return int(round(slider.value or 0))

    min_owned_label = ft.Text(size=12, width=102)
    min_wishlist_label = ft.Text(size=12, width=102)
    discount_label = ft.Text(size=12, width=102)
    max_price_label = ft.Text(size=12, width=102)
    min_review_label = ft.Text(size=12, width=102)

    def sync_slider_labels():
        # Only the labels change here: updating a Slider mid-drag breaks the drag
        # A count of 0 matches every game, so it's shown as "off"
        for label, name, slider in (
            (min_owned_label, "Min Owned", min_owned_slider),
            (min_wishlist_label, "Min Wishlist", min_wishlist_slider),
        ):
            count = slider_value(slider)
            label.value = f"{name}: {count or 'off'}"
            label.color = None if count else "grey500"
        discount_label.value = f"Min Discount: {slider_value(discount_slider)}%"
        min_review_label.value = f"Min Review: {slider_value(min_review_slider)}%"
        price = slider_value(max_price_slider)
        max_price_label.value = "Max Price: ∞" if price >= MAX_PRICE_SLIDER_CAP else f"Max Price: ${price}"

    def on_slider_drag(_=None):
        sync_slider_labels()
        for label in (min_owned_label, min_wishlist_label, discount_label, max_price_label, min_review_label):
            label.update()

    def on_filter_change(_=None):
        nonlocal current_page
        sync_slider_labels()
        current_page = 1
        filter_and_render_games()

    def make_count_release(other):
        # Min Owned and Min Wishlist are never both 0
        def handler(e):
            if slider_value(e.control) == 0 and slider_value(other()) == 0:
                other().value = 1
            on_filter_change()
        return handler

    def make_slider(max_val: int, divisions: int, value: int, on_release) -> ft.Slider:
        # on_change only updates labels; filtering waits for on_change_end
        return ft.Slider(
            min=0, max=max_val, divisions=divisions, value=value, expand=True,
            on_change=on_slider_drag, on_change_end=on_release,
        )

    min_owned_slider = make_slider(5, 5, 1, make_count_release(lambda: min_wishlist_slider))
    min_wishlist_slider = make_slider(5, 5, 0, make_count_release(lambda: min_owned_slider))
    discount_slider = make_slider(90, 18, 0, on_filter_change)
    max_price_slider = make_slider(MAX_PRICE_SLIDER_CAP, MAX_PRICE_SLIDER_CAP, MAX_PRICE_SLIDER_CAP, on_filter_change)
    min_review_slider = make_slider(100, 20, 0, on_filter_change)
    sync_slider_labels()

    # AND / OR chip between Min Owned and Min Wishlist
    owned_wishlist_mode = "AND"
    mode_text = ft.Text("AND", size=11, weight=BOLD, color="white")

    def apply_mode_style():
        is_or = owned_wishlist_mode == "OR"
        color = ORANGE if is_or else TEAL
        mode_text.value = owned_wishlist_mode
        mode_chip.bgcolor = color
        mode_chip.border = ft.Border.all(1.5, "#FFA726" if is_or else "#26C6DA")
        mode_chip.tooltip = (
            "OR: games matching Min Owned or Min Wishlist (a filter at 0 is ignored). Click to switch to AND."
            if is_or
            else "AND: games must match both Min Owned and Min Wishlist. Click to switch to OR."
        )
        owned_wishlist_group.bgcolor = ft.Colors.with_opacity(0.08, color)
        owned_wishlist_group.border = ft.Border.all(1, ft.Colors.with_opacity(0.4, color))

    def toggle_mode(_=None):
        nonlocal owned_wishlist_mode
        owned_wishlist_mode = "OR" if owned_wishlist_mode == "AND" else "AND"
        apply_mode_style()
        on_filter_change()

    # A Slider has empty space at the end of its track, so the chip gets extra right padding to look centred
    chip_width = 40
    slider_edge_inset = 20
    mode_chip = ft.Container(
        content=mode_text,
        width=chip_width,
        alignment=ft.Alignment.CENTER,
        border_radius=10,
        padding=ft.Padding.symmetric(vertical=3),
        on_click=toggle_mode,
    )
    owned_wishlist_group = ft.Container(
        padding=ft.Padding.symmetric(horizontal=9, vertical=3),
        content=ft.Row(
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row([min_owned_label, min_owned_slider], spacing=2, expand=1),
                ft.Container(content=mode_chip, padding=ft.Padding.only(right=slider_edge_inset)),
                ft.Row([min_wishlist_label, min_wishlist_slider], spacing=2, expand=1),
            ],
        ),
    )
    apply_mode_style()

    def update_count_sliders():
        """Caps Min Owned / Min Wishlist at the number of loaded (or else ticked) players."""
        count = len(players) if has_loaded_once and players else len(enabled_players())
        max_players = max(1, count)
        for slider in (min_owned_slider, min_wishlist_slider):
            slider.max = max_players
            slider.divisions = max_players
            slider.disabled = count == 0
            slider.value = min(slider.value, max_players)
        if slider_value(min_owned_slider) == 0 and slider_value(min_wishlist_slider) == 0:
            min_owned_slider.value = 1
        sync_slider_labels()

    # ---------- Player list ----------
    def render_player_list():
        sort_config_players()
        selected = enabled_players()
        available = [p for p in config["players"] if not p.get("enabled", True)]

        def on_toggle(p: dict):
            def handler(e):
                p["enabled"] = bool(e.control.value)
                page.run_task(save_config)
                render_player_list()
                filter_and_render_games()
            return handler

        def on_delete(p: dict):
            def handler(_):
                config["players"].remove(p)
                session_players_cache.pop(p["steam_id"], None)
                page.run_task(save_config)
                render_player_list()
                filter_and_render_games()
                notify(f"Removed '{p['display_name']}' from player list.")
            return handler

        def player_row(p: dict, idx: int, enabled: bool) -> ft.Container:
            return ft.Container(
                padding=ft.Padding.only(right=14),
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Row(
                            spacing=6,
                            expand=True,
                            controls=[
                                badge(
                                    f"#{idx}", "bluegrey900", 11, ft.Padding.symmetric(horizontal=6, vertical=3),
                                    color="cyan200" if enabled else "grey500", bold=True,
                                ),
                                ft.Checkbox(label=p["display_name"], value=enabled, on_change=on_toggle(p), expand=True),
                            ],
                        ),
                        icon_button(ft.Icons.DELETE, icon_color="red400", tooltip="Delete player", on_click=on_delete(p)),
                    ],
                ),
            )

        def section(title: str, color: str, entries: List[dict], enabled: bool, empty_text: str) -> List[ft.Control]:
            controls: List[ft.Control] = [ft.Text(f"{title} ({len(entries)})", size=14, weight=BOLD, color=color)]
            controls += [player_row(p, i, enabled) for i, p in enumerate(entries, start=1)]
            if not entries:
                controls.append(
                    ft.Container(
                        padding=4,
                        content=ft.Text(empty_text, size=12, italic=True, color="grey500"),
                    )
                )
            return controls

        player_list_view.controls = [
            *section("Selected Players", "cyan200", selected, True, "No players selected."),
            ft.Divider(height=12),
            *section("Available Players", "grey400", available, False, "No unselected players."),
        ]
        update_count_sliders()
        update_submit_button()
        page.update()

    async def on_add_player(_):
        nonlocal is_fetching, loading_title
        if is_fetching:
            return
        api_key = (api_key_field.value or "").strip()
        if not api_key:
            show_dialog("API Key Required", "Please enter your Steam Web API Key before adding players.")
            return
        val = (new_player_input.value or "").strip()
        if not val:
            return
        if len(config["players"]) >= MAX_PLAYERS:
            notify(f"Your player list is full ({MAX_PLAYERS} players). Delete a player first.")
            return

        config["api_key"] = api_key
        await save_config()

        is_fetching = True
        loading_title = "Resolving Steam Profile..."
        update_loading_progress(f"Looking up '{val}' on Steam...")
        filter_and_render_games(recompute=False)

        try:
            async with SteamClient(api_key) as client:
                resolved_id = await client.resolve_steam_id(val)
                if not resolved_id:
                    show_dialog(
                        "Player Not Found",
                        f"Could not find a Steam profile for:\n'{val}'\n\nPlease verify the SteamID64, URL, or API Key.",
                    )
                    return
                if any(p["steam_id"] == resolved_id for p in config["players"]):
                    show_dialog("Duplicate Player", "This player is already in your player list.")
                    return

                update_loading_progress("Fetching player profile details...")
                player = Player(steam_id=resolved_id)
                found, err_msg = await client.fetch_player_summary(player)
                if err_msg:
                    show_dialog("Steam API Error", err_msg)
                    return
                if not found:
                    show_dialog("Player Not Found", f"No Steam account exists for SteamID64:\n'{resolved_id}'")
                    return

            config["players"].append({"steam_id": resolved_id, "display_name": player.display_name, "enabled": True})
            await save_config()
            new_player_input.value = ""
            notify(f"Added player '{player.display_name}'!")
        finally:
            is_fetching = False
            loading_title = "Fetching game data..."
            render_player_list()
            filter_and_render_games()

    new_player_input.on_submit = on_add_player

    # ---------- Squad export / import (never includes the API key) ----------
    def open_export_dialog(_=None):
        if not config["players"]:
            show_dialog("Nothing to Export", "Add at least one player first.")
            return
        code = encode_squad_code(config["players"])

        def on_copied(ok: bool):
            notify("Squad code copied to clipboard!" if ok else "Copy failed - select the text and copy it manually.")

        dlg = ft.AlertDialog(
            title=ft.Text("Export Squad", weight=BOLD),
            content=ft.Container(
                width=460,
                content=ft.Column(
                    tight=True,
                    spacing=10,
                    controls=[
                        ft.Text(
                            f"This code contains your {len(config['players'])} player(s) and their checkbox state. "
                            "It does NOT contain your API key, so it's safe to share with friends.",
                            size=12,
                        ),
                        ft.TextField(value=code, read_only=True, multiline=True, min_lines=3, max_lines=6),
                    ],
                ),
            ),
            actions=[
                ft.Button(content="Copy", icon=ft.Icons.CONTENT_COPY, **platform.copy_props(code, on_done=on_copied)),
                ft.Button(content="Close", on_click=lambda _: close_dialog(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)

    def open_import_dialog(_=None):
        input_field = ft.TextField(
            hint_text="Paste a squad code (SSF1:...) or the contents of a config file",
            multiline=True,
            min_lines=3,
            max_lines=8,
        )
        error_text = ft.Text("", size=12, color="red300")

        def apply_import(_=None):
            try:
                imported = decode_squad_code(input_field.value or "")
            except ValueError as err:
                error_text.value = str(err)
                page.update()
                return
            config["players"], added, skipped = merge_players(config["players"], imported)
            page.run_task(save_config)
            close_dialog(dlg)
            render_player_list()
            filter_and_render_games()
            msg = f"Added {added} player(s)."
            if skipped:
                msg += f" {skipped} skipped: the list holds at most {MAX_PLAYERS} players."
            notify(msg)

        dlg = ft.AlertDialog(
            title=ft.Text("Import Squad", weight=BOLD),
            content=ft.Container(
                width=460,
                content=ft.Column(
                    tight=True,
                    spacing=10,
                    controls=[
                        ft.Text(
                            "New players are added to your list; players already on it are kept as they are. "
                            "API keys in the pasted text are ignored.",
                            size=12,
                        ),
                        input_field,
                        error_text,
                    ],
                ),
            ),
            actions=[
                ft.Button(content="Merge", icon=ft.Icons.CALL_MERGE, on_click=apply_import),
                ft.Button(content="Cancel", on_click=lambda _: close_dialog(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)

    # ---------- Sort menu ----------
    sort_by = "Owned"
    sort_descending = True
    sort_button_text = ft.Text("Owned", size=12, weight=BOLD, color="white")
    sort_direction_icon = ft.Icon(ft.Icons.ARROW_DOWNWARD, size=16, color="white")

    def refresh_sort_menu():
        sort_button_text.value = sort_by
        sort_direction_icon.icon = ft.Icons.ARROW_DOWNWARD if sort_descending else ft.Icons.ARROW_UPWARD
        for name, item in sort_field_items.items():
            item.checked = name == sort_by
        sort_ascending_item.checked = not sort_descending
        sort_descending_item.checked = sort_descending

    def set_sort(field: Optional[str] = None, descending: Optional[bool] = None):
        def handler(_=None):
            nonlocal sort_by, sort_descending
            if field is not None:
                # A new field starts in its natural direction (e.g. cheapest price first)
                sort_by = field
                sort_descending = SORT_DEFAULT_DESCENDING[field]
            if descending is not None:
                sort_descending = descending
            refresh_sort_menu()
            on_filter_change()
        return handler

    sort_icons = {
        "Owned": ft.Icons.TAG,
        "Wishlisted": ft.Icons.TAG,
        "User Reviews": ft.Icons.PERCENT,
        "Price": ft.Icons.ATTACH_MONEY,
        "Discount": ft.Icons.PERCENT,
    }
    sort_field_items = {
        name: ft.PopupMenuItem(content=name, icon=sort_icons[name], on_click=set_sort(field=name))
        for name in SORT_OPTIONS
    }
    sort_ascending_item = ft.PopupMenuItem(content="Ascending", icon=ft.Icons.ARROW_UPWARD, on_click=set_sort(descending=False))
    sort_descending_item = ft.PopupMenuItem(content="Descending", icon=ft.Icons.ARROW_DOWNWARD, on_click=set_sort(descending=True))
    sort_menu_button = ft.PopupMenuButton(
        tooltip="Sort games",
        items=[*sort_field_items.values(), ft.PopupMenuItem(), sort_ascending_item, sort_descending_item],
        popup_animation_style=ft.AnimationStyle(duration=150, reverse_duration=150),
        content=ft.Container(
            bgcolor="bluegrey800",
            border=ft.Border.all(1, "bluegrey600"),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                tight=True,
                spacing=6,
                controls=[
                    ft.Icon(ft.Icons.SORT, size=16, color="white"),
                    ft.Text("Sort:", size=12, color="grey300"),
                    sort_button_text,
                    sort_direction_icon,
                ],
            ),
        ),
    )
    refresh_sort_menu()

    # ---------- Copy titles ----------
    # On web the browser copies inside the click, so the text must be on the button before it's clicked
    copy_titles_btn = ft.IconButton(
        icon=ft.Icons.CONTENT_COPY,
        icon_size=16,
        width=30,
        height=30,
        padding=2,
        disabled=True,
        bgcolor="bluegrey800",
        style=ft.ButtonStyle(
            icon_color={ft.ControlState.DEFAULT: "white", ft.ControlState.DISABLED: "grey600"},
            shape=ft.RoundedRectangleBorder(radius=6),
            side=ft.BorderSide(1, "bluegrey600"),
        ),
    )
    copy_titles_text = None

    def sync_copy_titles_button(games: List[GameItem], disabled_reason: str = "no games match the current filters"):
        nonlocal copy_titles_text
        count = len(games)
        copy_titles_btn.disabled = count == 0
        if count == 0:
            copy_titles_btn.tooltip = f"Nothing to copy: {disabled_reason}"
        else:
            copy_titles_btn.tooltip = f"Copy titles of all {count} matching game{'' if count == 1 else 's'}"
        text = format_game_titles(games)
        if text == copy_titles_text:
            return  # Don't resend the same text to the client on every page change
        copy_titles_text = text

        def on_copied(ok: bool):
            if ok:
                notify(f"Copied {count} game title{'' if count == 1 else 's'}.")
            else:
                notify("Copy failed - the clipboard couldn't be written.")

        props = platform.copy_props(text, on_done=on_copied)
        copy_titles_btn.on_click = props.get("on_click")
        copy_titles_btn.action = props.get("action")

    # ---------- Filters ----------
    list_mode_switch = ft.Switch(label="List Mode", value=False, on_change=lambda _: filter_and_render_games())
    show_unreleased_switch = ft.Switch(label="Unreleased", value=True, on_change=on_filter_change)
    show_unavailable_switch = ft.Switch(label="Unavailable", value=False, on_change=on_filter_change)
    search_filter = ft.TextField(
        label="Search title...", prefix_icon=ft.Icons.SEARCH, expand=1, on_change=on_filter_change
    )
    tags_filter = ft.TextField(
        label="Search tags (comma-separated)...",
        hint_text="e.g. RPG, Survival, Roguelike",
        prefix_icon=ft.Icons.SELL,
        expand=1,
        on_change=on_filter_change,
    )

    def tag_checkbox(label: str) -> ft.Checkbox:
        return ft.Checkbox(
            label=label,
            value=False,
            on_change=on_filter_change,
            label_style=ft.TextStyle(size=12),
            visual_density=ft.VisualDensity.COMPACT,
        )

    coop_cb = tag_checkbox("Co-op")
    multiplayer_cb = tag_checkbox("Multiplayer")
    online_cb = tag_checkbox("Online")
    vr_cb = tag_checkbox("VR")
    macos_cb = tag_checkbox("MacOS")
    linux_cb = tag_checkbox("Linux")

    def compute_filtered_games() -> List[GameItem]:
        max_price = slider_value(max_price_slider)
        settings = FilterSettings(
            search_query=(search_filter.value or "").lower().strip(),
            required_tags=tuple(t.strip().lower() for t in (tags_filter.value or "").split(",") if t.strip()),
            min_owned=slider_value(min_owned_slider),
            min_wishlist=slider_value(min_wishlist_slider),
            owned_wishlist_mode=owned_wishlist_mode,
            min_discount=slider_value(discount_slider),
            min_review=slider_value(min_review_slider),
            max_price=max_price if max_price < MAX_PRICE_SLIDER_CAP else None,
            include_unreleased=bool(show_unreleased_switch.value),
            include_unavailable=bool(show_unavailable_switch.value),
            require_coop=bool(coop_cb.value),
            require_multiplayer=bool(multiplayer_cb.value),
            require_online=bool(online_cb.value),
            require_vr=bool(vr_cb.value),
            require_macos=bool(macos_cb.value),
            require_linux=bool(linux_cb.value),
        )
        return sort_games(filter_games(all_games_map.values(), settings), sort_by, sort_descending)

    # ---------- Fetch & aggregate ----------
    async def on_submit(_):
        nonlocal current_page, is_fetching, loading_title, has_loaded_once, last_fetched_player_ids
        if is_fetching:
            return
        api_key = (api_key_field.value or "").strip()
        if not api_key:
            show_dialog("API Key Required", "Please enter your Steam Web API Key first.")
            return
        config["api_key"] = api_key
        await save_config()

        active = enabled_players()
        if not active:
            show_dialog("No Players Selected", "Please select at least 1 active player with a checkmark.")
            return

        is_fetching = True
        loading_title = "Fetching game data..."
        submit_btn.disabled = True
        current_page = 1
        update_loading_progress("Preparing to fetch libraries & store metadata...")
        filter_and_render_games(recompute=False)

        try:
            # Reuse libraries fetched earlier in this session
            players.clear()
            to_fetch: List[Player] = []
            for p_data in active:
                cached = session_players_cache.get(p_data["steam_id"])
                if cached and cached.library_fetched and not cached.is_private and not cached.fetch_error:
                    players.append(cached)
                else:
                    player = Player(steam_id=p_data["steam_id"], display_name=p_data["display_name"])
                    players.append(player)
                    to_fetch.append(player)

            async with SteamClient(api_key) as client:
                if to_fetch:
                    update_loading_progress(f"Fetching libraries & wishlists for {len(to_fetch)} player(s)...")
                    await asyncio.gather(
                        *(task for p in to_fetch for task in (
                            client.fetch_player_summary(p), client.fetch_owned_games(p), client.fetch_wishlist(p)
                        ))
                    )

                    # Keep stored names in sync with current Steam names
                    by_id = {p["steam_id"]: p for p in config["players"]}
                    names_changed = False
                    for p in to_fetch:
                        if not p.fetch_error:
                            p.library_fetched = True
                            if not p.is_private:
                                session_players_cache[p.steam_id] = p
                        entry = by_id.get(p.steam_id)
                        if entry and entry.get("display_name") != p.display_name:
                            entry["display_name"] = p.display_name
                            names_changed = True
                    if names_changed:
                        await save_config()

                    errors = [f"{p.display_name}: {p.fetch_error}" for p in to_fetch if p.fetch_error]
                    private = [p.display_name for p in to_fetch if p.is_private and not p.fetch_error]
                    if errors:
                        show_dialog(
                            "Steam API / Network Error",
                            "Failed to fetch library data for the following players:\n\n• " + "\n• ".join(errors),
                        )
                    elif private:
                        show_dialog(
                            "Private Libraries Detected",
                            "The following players have private libraries or restricted privacy settings:\n\n• "
                            + "\n• ".join(private)
                            + "\n\nSteam's privacy settings prevent fetching their games. "
                            "Games for accessible profiles will still be displayed.",
                        )

                for game in session_games_cache.values():
                    game.owners.clear()
                    game.wishlisters.clear()
                all_games_map.clear()

                def add_game(appid: int, name: str) -> GameItem:
                    game = session_games_cache.get(appid)
                    if game is None:
                        game = session_games_cache[appid] = GameItem(appid, name)
                    elif game.name.startswith("App ") and not name.startswith("App "):
                        game.name = name
                    all_games_map[appid] = game
                    return game

                for p in players:
                    for appid, name in p.owned_games.items():
                        add_game(appid, name).owners.append(p)
                    for appid, name in p.wishlist_games.items():
                        add_game(appid, name).wishlisters.append(p)

                unfetched = sorted(
                    (g for g in all_games_map.values() if not g.store_info_fetched),
                    key=lambda g: (-len(g.owners), -len(g.wishlisters), g.name.lower()),
                )
                if unfetched:
                    def on_progress(done: int, total: int):
                        update_loading_progress(f"Fetching store prices, reviews & tags ({done}/{total})...", done / total)

                    on_progress(0, len(unfetched))
                    await client.fetch_bulk_store_items(unfetched, on_progress=on_progress)

            has_loaded_once = True
            last_fetched_player_ids = {p.steam_id for p in players}
            notify(f"Data loaded! Found {len(all_games_map)} unique games across players.")
        finally:
            is_fetching = False
            submit_btn.disabled = False
            render_player_list()
            filter_and_render_games()

    submit_btn.on_click = on_submit

    # ---------- Summary bar ----------
    matching_text = ft.Text("Matching Filters: 0 / 0", size=12, weight=BOLD, color="white")
    matching_badge = ft.Container(
        content=matching_text,
        bgcolor="blue800",
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        border_radius=6,
    )

    filters_collapsed = False
    collapse_text = ft.Text("Hide Filters", size=12, weight=BOLD, color="white")
    collapse_arrow = ft.Icon(ft.Icons.KEYBOARD_ARROW_UP, size=18, color="white")

    def toggle_filters(_=None):
        nonlocal filters_collapsed
        filters_collapsed = not filters_collapsed
        top_filters_area.visible = not filters_collapsed
        collapse_text.value = "Show Filters" if filters_collapsed else "Hide Filters"
        collapse_arrow.icon = ft.Icons.KEYBOARD_ARROW_DOWN if filters_collapsed else ft.Icons.KEYBOARD_ARROW_UP
        collapse_chip.bgcolor = ORANGE if filters_collapsed else TEAL
        collapse_chip.border = ft.Border.all(1.5, "#FFA726" if filters_collapsed else "#26C6DA")
        page.update()

    collapse_chip = ft.Container(
        bgcolor=TEAL,
        border=ft.Border.all(1.5, "#26C6DA"),
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        tooltip="Collapse or expand the filter sections",
        on_click=toggle_filters,
        content=ft.Row(
            tight=True,
            spacing=6,
            controls=[ft.Icon(ft.Icons.TUNE, size=16, color="white"), collapse_text, collapse_arrow],
        ),
    )

    top_summary_bar = ft.Container(
        padding=ft.Padding.symmetric(horizontal=4),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Row([collapse_chip, matching_badge, list_mode_switch], spacing=12),
                ft.Row([copy_titles_btn, sort_menu_button], spacing=8),
            ],
        ),
    )

    # ---------- Pagination ----------
    page_info_text = ft.Text("Showing 0-0 (Page 0 of 0)", size=12, weight=BOLD, color="grey300")

    def go_to_page(delta: int):
        def handler(_):
            nonlocal current_page
            current_page += delta
            filter_and_render_games(recompute=False)
        return handler

    prev_page_btn = ft.Button(content="< Prev", disabled=True, on_click=go_to_page(-1))
    next_page_btn = ft.Button(content="Next >", disabled=True, on_click=go_to_page(1))

    def on_page_size_change(e):
        nonlocal page_size, current_page
        page_size = 999999 if e.control.value == "All" else int(e.control.value)
        current_page = 1
        filter_and_render_games(recompute=False)

    page_size_dropdown = ft.Dropdown(
        options=[ft.DropdownOption(key=v, text=v) for v in ("25", "50", "100", "All")],
        value="50",
        width=90,
        on_select=on_page_size_change,
    )

    bottom_pagination_bar = ft.Container(
        # Fixed height so the left panel's bottom divider lines up with the one above this bar
        height=60,
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                page_info_text,
                ft.Row([prev_page_btn, next_page_btn, ft.Text("Per page:", size=12), page_size_dropdown], spacing=8),
            ],
        ),
    )

    # ---------- Games list ----------
    # Both stay mounted; only `visible` flips, so the ListView is never detached mid-update
    games_list_view = ft.ListView(expand=True, spacing=6, visible=False, scroll=ft.ScrollMode.ALWAYS)
    games_state_container = ft.Container(expand=True)
    games_container = ft.Column(expand=True, spacing=0, controls=[games_state_container, games_list_view])

    def show_placeholder(icon, color: str, title: str, subtitle: str = "", spinner: bool = False):
        top = (
            ft.ProgressRing(width=64, height=64, stroke_width=5, color=color)
            if spinner
            else ft.Container(
                content=ft.Icon(icon, size=76, color=color),
                padding=18,
                bgcolor="bluegrey900",
                border_radius=50,
            )
        )
        controls = [top, ft.Text(title, size=22, weight=BOLD, color="white", text_align=ft.TextAlign.CENTER)]
        if spinner:
            controls += [loading_subtitle, progress_bar]
        elif subtitle:
            controls.append(ft.Text(subtitle, size=14, color="grey400", text_align=ft.TextAlign.CENTER))

        games_list_view.visible = False
        games_state_container.visible = True
        games_state_container.content = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=30,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
                spacing=14,
                controls=controls,
            ),
        )
        page_info_text.value = "Showing 0-0 (Page 0 of 0)"
        prev_page_btn.disabled = True
        next_page_btn.disabled = True
        page.update()

    def build_game_card(game: GameItem, rank: int, total_players: int, small: bool) -> ft.Card:
        unreleased = game.is_effectively_unreleased
        unavailable = game.is_effectively_unavailable
        tags = sorted(game.categories | game.genres)
        tag_list = ", ".join(tags[:5]) or ("None listed" if game.store_info_fetched else "Not fetched")

        tooltip = (
            f"🎮 #{rank} - {game.name}\n"
            f"• Owners ({len(game.owners)}): {', '.join(p.display_name for p in game.owners) or 'None'}\n"
            f"• Wishlist ({len(game.wishlisters)}): {', '.join(p.display_name for p in game.wishlisters) or 'None'}\n"
            f"• User Reviews: {format_reviews(game)}\n"
            f"• Tags: {tag_list}"
        )
        if unreleased:
            tooltip += f"\n• Status: Unreleased ({game.release_date or 'Coming Soon'})"
        elif unavailable:
            tooltip += "\n• Status: Unavailable / Delisted in Steam Store"
        if game.mac_supported is not None:
            tooltip += f"\n• MacOS: {'Supported' if game.mac_supported else 'No'}"

        font = 10 if small else 11
        pad = ft.Padding.symmetric(horizontal=5, vertical=2) if small else ft.Padding.symmetric(horizontal=6, vertical=3)
        rank_badge = badge(
            f"#{rank}", "bluegrey900", 10 if small else 12,
            ft.Padding.symmetric(horizontal=6, vertical=2) if small else ft.Padding.symmetric(horizontal=8, vertical=4),
            color="cyan200", bold=True,
        )

        owned_all = len(game.owners) == total_players
        badges = [
            badge(f"Owned: {len(game.owners)}/{total_players}", "green700" if owned_all else "bluegrey700", font, pad),
            badge(
                f"Wishlisted: {len(game.wishlisters)}/{total_players}",
                "amber800" if game.wishlisters else "bluegrey900", font, pad,
            ),
        ]
        if game.price_str:
            price = f"{game.price_str} (-{game.discount_pct}%)" if game.is_on_sale else game.price_str
            badges.append(badge(price, "green400" if game.is_on_sale else "grey400", font, pad, color="black", bold=True))
        if unavailable:
            badges.append(badge("Unavailable", "#B71C1C", font, pad, bold=True))
        elif unreleased:
            badges.append(badge("Unreleased", "#BF360C", font, pad, bold=True))

        actions: List[ft.Control] = []
        if game.review_percentage is not None:
            actions.append(
                ft.Container(
                    bgcolor="blue700",
                    padding=pad,
                    border_radius=4,
                    tooltip=f"Steam User Reviews: {format_reviews(game)}",
                    content=ft.Row(
                        tight=True,
                        spacing=4,
                        controls=[
                            ft.Icon(ft.Icons.THUMB_UP, size=11 if small else 13, color="white"),
                            ft.Text(f"{game.review_percentage}%", size=font, weight=BOLD, color="white"),
                        ],
                    ),
                )
            )
        store_link = platform.link_props(f"https://store.steampowered.com/app/{game.appid}/")
        icon_size = 16 if small else None
        actions += [
            icon_button(
                ft.Icons.INFO_OUTLINE,
                compact=small,
                icon_color="cyan300",
                icon_size=icon_size,
                tooltip=ft.Tooltip(message=tooltip, wait_duration=400),
                on_click=lambda _: open_game_details_dialog(game),
            ),
            icon_button(
                ft.Icons.OPEN_IN_NEW, compact=small, icon_color="blue400", icon_size=icon_size,
                tooltip="Open in Steam Store", **store_link,
            ),
        ]

        title: List[ft.Control] = []
        if unavailable:
            title.append(
                ft.Container(
                    content=ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="amber300", size=15 if small else 18),
                    tooltip="Unavailable / No longer available in the Steam Store",
                )
            )
        title.append(link_text(game.name, 12 if small else 15, store_link))

        if unavailable:
            row_bgcolor = ft.Colors.with_opacity(0.5, "red900")
        elif unreleased:
            row_bgcolor = ft.Colors.with_opacity(0.5, "amber900")
        else:
            row_bgcolor = None

        if small:
            row = ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                spacing=8,
                controls=[
                    rank_badge,
                    ft.Row(title, spacing=5, expand=True),
                    ft.Row(badges, spacing=4),
                    ft.Row(actions, spacing=4),
                ],
            )
            padding = ft.Padding.only(left=6, top=2, right=14, bottom=2)
        else:
            thumbnail = ft.Container(
                content=ft.Image(
                    src=platform.image_src(game.header_image_url),
                    width=110,
                    height=50,
                    fit=ft.BoxFit.COVER,
                    border_radius=4,
                    error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=30, color="grey500"),
                ),
                width=110,
                height=50,
                bgcolor="bluegrey900",
                border_radius=4,
                tooltip=ft.Tooltip(message=f"{tooltip}\n\nClick to view details", wait_duration=400),
                on_click=lambda _: open_game_details_dialog(game),
            )
            row = ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Row(
                        spacing=10,
                        expand=True,
                        controls=[
                            rank_badge,
                            thumbnail,
                            ft.Column(
                                spacing=4,
                                expand=True,
                                controls=[ft.Row(title, spacing=6), ft.Row(badges, spacing=6)],
                            ),
                        ],
                    ),
                    ft.Row(actions, spacing=6),
                ],
            )
            padding = ft.Padding.only(left=8, top=8, right=16, bottom=8)

        return ft.Card(content=ft.Container(bgcolor=row_bgcolor, border_radius=8, padding=padding, content=row))

    def filter_and_render_games(recompute: bool = True):
        nonlocal current_page, cached_filtered_games
        total_loaded = len(all_games_map)

        if is_fetching or not has_loaded_once:
            matching_text.value = "Matching Filters: 0 / 0"
            matching_badge.bgcolor = "bluegrey800"
            if is_fetching:
                sync_copy_titles_button([], "wait until the games are loaded")
                show_placeholder(None, "cyan400", loading_title, spinner=True)
            elif not enabled_players():
                sync_copy_titles_button([], "no games loaded yet")
                show_placeholder(
                    ft.Icons.GROUP_ADD,
                    "amber400",
                    "No players selected.",
                    "Add at least one Steam player on the left and make sure their checkbox is ticked,\n"
                    "then click 'Find Common Games' to load all player's libraries.",
                )
            else:
                sync_copy_titles_button([], "no games loaded yet")
                show_placeholder(
                    ft.Icons.SPORTS_ESPORTS,
                    "cyan300",
                    "No game data has been loaded yet.",
                    "Click 'Find Common Games' on the bottom-left to scan your selected players' libraries.",
                )
            return

        if recompute:
            cached_filtered_games = compute_filtered_games()
        games = cached_filtered_games
        total_count = len(games)
        sync_copy_titles_button(games)
        matching_text.value = f"Matching Filters: {total_count} / {total_loaded}"
        matching_badge.bgcolor = "blue800" if total_count else "bluegrey800"

        if not total_count:
            show_placeholder(
                ft.Icons.SEARCH_OFF,
                "grey500",
                "No games match the current filters.",
                "Try relaxing your search query, tags, review score, price, or ownership sliders above.",
            )
            return

        small = bool(list_mode_switch.value)
        total_pages = math.ceil(total_count / page_size)
        current_page = max(1, min(current_page, total_pages))
        start = (current_page - 1) * page_size
        end = min(start + page_size, total_count)
        page_info_text.value = f"Showing {start + 1}-{end} of {total_count} (Page {current_page} of {total_pages})"
        prev_page_btn.disabled = current_page == 1
        next_page_btn.disabled = current_page >= total_pages

        total_players = len(players) or 1
        games_list_view.spacing = 2 if small else 6
        # Assign the whole list at once so the ListView never renders an empty frame
        games_list_view.controls = [
            build_game_card(game, start + i + 1, total_players, small) for i, game in enumerate(games[start:end])
        ]
        games_state_container.visible = False
        games_list_view.visible = True
        page.update()

    # ---------- Layout ----------
    left_panel = ft.Card(
        content=ft.Container(
            padding=ft.Padding.only(left=15, top=8, right=15),
            width=380,
            expand=True,
            content=ft.Column(
                expand=True,
                controls=[
                    api_key_header_row,
                    api_key_row,
                    ft.Divider(),
                    add_player_header_row,
                    ft.Row([new_player_input, ft.Button(content="Add Player", icon=ft.Icons.ADD, on_click=on_add_player)]),
                    ft.Divider(),
                    player_list_view,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=8,
                        controls=[
                            ft.Button(content="Export Squad", icon=ft.Icons.UPLOAD, on_click=open_export_dialog),
                            ft.Button(content="Import Squad", icon=ft.Icons.DOWNLOAD, on_click=open_import_dialog),
                        ],
                    ),
                    # Same height as the pagination bar on the right, so the dividers and the button line up
                    ft.Column(
                        spacing=0,
                        controls=[
                            ft.Divider(height=4),
                            ft.Container(
                                height=60,
                                padding=ft.Padding.only(top=8),
                                alignment=ft.Alignment.CENTER,
                                content=submit_btn,
                            ),
                        ],
                    ),
                ],
            ),
        )
    )

    def section_title(text: str) -> ft.Text:
        return ft.Text(text, size=13, weight=BOLD, color="cyan200")

    filters_card = ft.Card(
        margin=ft.Margin.only(left=4, top=4, right=4, bottom=1),
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Column(
                spacing=4,
                controls=[section_title("Filters"), ft.Row([search_filter, tags_filter], spacing=12)],
            ),
        ),
    )

    common_tags_card = ft.Card(
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            width=140,
            content=ft.Column(
                spacing=0,
                tight=True,
                controls=[
                    ft.Container(padding=ft.Padding.only(bottom=2), content=section_title("Common Tags")),
                    *(ft.Container(content=cb, height=26) for cb in (coop_cb, multiplayer_cb, online_cb, vr_cb, macos_cb, linux_cb)),
                ],
            ),
        )
    )

    parameters_card = ft.Card(
        expand=True,
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=1, vertical=4),
            content=ft.Column(
                spacing=4,
                tight=True,
                controls=[
                    ft.Container(padding=ft.Padding.symmetric(horizontal=10), content=section_title("Parameters")),
                    owned_wishlist_group,
                    tinted_box(
                        TEAL,
                        padding=ft.Padding.symmetric(horizontal=9, vertical=3),
                        content=ft.Column(
                            spacing=0,
                            tight=True,
                            controls=[
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        ft.Row([discount_label, discount_slider], spacing=2, expand=1),
                                        ft.Container(width=chip_width + slider_edge_inset),
                                        ft.Row([max_price_label, max_price_slider], spacing=2, expand=1),
                                    ],
                                ),
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        ft.Row([min_review_label, min_review_slider], spacing=2, expand=1),
                                        ft.Container(width=chip_width + slider_edge_inset),
                                        ft.Container(expand=1),
                                    ],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        ),
    )

    special_games_card = ft.Card(
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            width=155,
            content=ft.Column(
                spacing=6,
                tight=True,
                controls=[section_title("Special Games"), show_unreleased_switch, show_unavailable_switch],
            ),
        )
    )

    top_filters_area = ft.Column(
        spacing=0,
        controls=[
            filters_card,
            ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[common_tags_card, parameters_card, special_games_card],
            ),
        ],
    )

    right_panel = ft.Column(
        expand=True,
        spacing=4,
        controls=[
            top_filters_area,
            top_summary_bar,
            ft.Divider(height=4),
            games_container,
            ft.Divider(height=4),
            bottom_pagination_bar,
        ],
    )

    page.add(ft.Row(expand=True, spacing=15, controls=[left_panel, right_panel]))

    # Load after page.add: browser storage on web needs a mounted page
    loaded = await platform.load_config()
    config["api_key"] = loaded["api_key"]
    config["players"] = sanitize_player_entries(loaded["players"])
    api_key_field.value = config["api_key"]
    render_player_list()
    filter_and_render_games()
