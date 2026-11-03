import asyncio
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Set

import flet as ft

from src.core.models import GameItem, Player
from src.core.steam_client import SteamClient
from src.ui.widgets import (
    bind_change_handlers,
    configure_always_visible_scrollbars,
    create_badge_layout,
    create_button,
    create_card,
    create_checkbox,
    create_dropdown,
    create_icon_button,
    create_link_text,
    create_menu_item,
    create_option,
    create_slider,
    create_switch,
    create_tooltip,
    get_always_scroll,
    get_border_all,
    get_center_alignment,
    get_click_cursor,
    get_color,
    get_color_with_opacity,
    get_cross_alignment,
    get_font_weight_bold,
    get_icon,
    get_image_fit_cover,
    get_main_alignment,
    get_margin_only,
    get_padding_only,
    get_padding_symmetric,
    get_text_align_center,
    open_url,
)

CONFIG_FILE = Path("steam_friends_game_finder_config.json")
MAX_PRICE_SLIDER_CAP = 70  # Values 0..69 represent $0..$69; 70 represents "∞"


# ==========================================
# MAIN FLET GUI APPLICATION
# ==========================================
async def build_app(page: ft.Page):
    page.title = "Steam Friends Game Finder"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = get_padding_symmetric(horizontal=20, vertical=8) or 20
    configure_always_visible_scrollbars(page)

    # Default window dimensions: 1330 x 960
    try:
        page.window.width = 1330
        page.window.height = 960
    except Exception:
        try:
            page.window_width = 1330
            page.window_height = 960
        except Exception:
            pass

    # State flags
    is_fetching: bool = False
    loading_title_str: str = "Fetching game data..."
    has_loaded_once: bool = False
    notify_clear_task: Optional[asyncio.Task] = None

    # Top-anchored, non-fullscreen notification popup
    top_toast_text = ft.Text("", color=get_color("white"), weight=get_font_weight_bold(), size=13)
    top_toast_card = ft.Container(
        bgcolor="#1565C0",
        border=get_border_all(1, "#42A5F5"),
        border_radius=8,
        padding=get_padding_symmetric(horizontal=18, vertical=10),
        content=ft.Row(
            tight=True,
            spacing=8,
            controls=[
                ft.Icon(get_icon("info_outline"), color=get_color("cyan200"), size=18),
                top_toast_text,
            ],
        ),
    )
    top_toast_wrapper = ft.Container(
        visible=False,
        top=10,
        left=0,
        right=0,
        content=ft.Row(
            alignment=get_main_alignment("center"),
            controls=[top_toast_card],
        ),
    )
    if hasattr(page, "overlay"):
        try:
            page.overlay.append(top_toast_wrapper)
        except Exception:
            pass

    def notify(msg: str):
        """Displays a slightly blue popup banner at the very top of the window."""
        nonlocal notify_clear_task
        if notify_clear_task and not notify_clear_task.done():
            notify_clear_task.cancel()

        top_toast_text.value = msg
        top_toast_wrapper.visible = True
        if hasattr(page, "overlay") and top_toast_wrapper not in page.overlay:
            try:
                page.overlay.append(top_toast_wrapper)
            except Exception:
                pass
        page.update()

        async def _hide_toast():
            try:
                await asyncio.sleep(3.5)
                top_toast_wrapper.visible = False
                page.update()
            except asyncio.CancelledError:
                pass

        notify_clear_task = asyncio.create_task(_hide_toast())

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

    def show_dialog(title: str, message: str):
        dlg = ft.AlertDialog(
            title=ft.Text(title, weight=get_font_weight_bold()),
            content=ft.Text(message),
            actions=[create_button("OK", on_click=lambda _: close_dialog(dlg))],
            actions_alignment=get_main_alignment("end"),
        )
        open_dialog(dlg)

    def format_game_price_label(game: GameItem) -> str:
        if game.price_str:
            return game.price_str
        if game.is_effectively_unreleased:
            return "Unreleased / TBA"
        if game.is_effectively_unavailable:
            return "Unavailable / Delisted from Steam Store"
        if not game.store_info_fetched:
            return "Fetching..."
        return "Not listed"

    def format_game_platforms(game: GameItem) -> str:
        platforms = []
        if game.windows_supported:
            platforms.append("Windows")
        if game.mac_supported:
            platforms.append("MacOS")
        if game.linux_supported:
            platforms.append("Linux")
        if platforms:
            return ", ".join(platforms)
        return "Fetching..." if not game.store_info_fetched else "N/A"

    def format_game_reviews(game: GameItem) -> str:
        if game.review_percentage is not None:
            if game.review_desc:
                return f"{game.review_percentage}% ({game.review_desc})"
            return f"{game.review_percentage}%"
        return "No user reviews" if game.store_info_fetched else "Fetching..."

    # Modal Popup Dialog for Game Details
    def open_game_details_dialog(game: GameItem):
        try:
            owner_names = ", ".join(p.display_name for p in game.owners) or "None"
            wishlist_names = ", ".join(p.display_name for p in game.wishlisters) or "None"

            needs_fallback_fetch = (not game.store_info_fetched or not game.short_description) and not game.details_fetched

            desc_text = ft.Text(
                game.short_description
                or ("Fetching store description..." if needs_fallback_fetch else "No description available."),
                size=12,
            )

            price_text = ft.Text(
                f"Price: {format_game_price_label(game)}",
                size=14,
                weight=get_font_weight_bold(),
                color=(
                    get_color("green400")
                    if game.is_on_sale
                    else (get_color("red300") if game.is_effectively_unavailable else get_color("white"))
                ),
            )
            review_text = ft.Text(
                f"• User Reviews: {format_game_reviews(game)}",
                size=12,
                color=get_color("blue300") if game.review_percentage is not None else None,
            )
            dev_str = (
                ", ".join(game.developers)
                if game.developers
                else ("Fetching..." if needs_fallback_fetch else "N/A")
            )
            dev_text = ft.Text(f"• Developer: {dev_str}", size=12)

            rel_suffix = (
                " (Unreleased)"
                if game.is_effectively_unreleased
                else (" (Unavailable in Store)" if game.is_effectively_unavailable else "")
            )
            rel_base = game.release_date or ("Fetching..." if needs_fallback_fetch else "N/A")
            release_text = ft.Text(
                f"• Release Date: {rel_base}{rel_suffix}",
                size=12,
                color=(
                    get_color("amber300")
                    if game.is_effectively_unreleased
                    else (get_color("red300") if game.is_effectively_unavailable else None)
                ),
            )

            platform_text = ft.Text(f"• Platforms: {format_game_platforms(game)}", size=12)

            all_tags_str = (
                ", ".join(sorted(game.categories | game.genres)[:12])
                if (game.categories or game.genres)
                else ("Fetching..." if needs_fallback_fetch else "None listed")
            )
            tags_text = ft.Text(f"• Tags & Categories: {all_tags_str}", size=12)

            # Owners / Wishlisted in their own tinted section, coloured like the Owned / Wishlisted badges
            total_players = len(players) or 1

            def player_line(icon_name: str, icon_color: str, label: str, count: int, names: str) -> ft.Row:
                return ft.Row(
                    spacing=8,
                    vertical_alignment=get_cross_alignment("start"),
                    controls=[
                        ft.Icon(get_icon(icon_name), size=18, color=get_color(icon_color)),
                        ft.Text(
                            f"{label} ({count}/{total_players})",
                            size=12,
                            weight=get_font_weight_bold(),
                            color=get_color(icon_color),
                            width=120,
                        ),
                        ft.Text(names, size=12, expand=True),
                    ],
                )

            players_section = ft.Container(
                bgcolor=get_color_with_opacity(0.08, "#00838F", "#1400838F"),
                border=get_border_all(1, get_color_with_opacity(0.4, "#00838F", "#6600838F")),
                border_radius=6,
                padding=get_padding_symmetric(horizontal=10, vertical=8),
                content=ft.Column(
                    spacing=6,
                    tight=True,
                    controls=[
                        ft.Text("Players", weight=get_font_weight_bold(), size=13, color=get_color("cyan200")),
                        player_line("check_circle", "green400", "Owned", len(game.owners), owner_names),
                        player_line("bookmark", "amber400", "Wishlisted", len(game.wishlisters), wishlist_names),
                    ],
                ),
            )

            dlg = ft.AlertDialog(
                title=ft.Text(game.name, weight=get_font_weight_bold(), size=18),
                content=ft.Container(
                    width=480,
                    content=ft.Column(
                        controls=[
                            ft.Image(
                                src=game.header_image_url,
                                width=460,
                                height=215,
                                fit=get_image_fit_cover(),
                                border_radius=6,
                                error_content=ft.Icon(
                                    get_icon("videogame_asset"), size=50, color=get_color("grey500")
                                ),
                            ),
                            price_text,
                            players_section,
                            ft.Divider(),
                            ft.Text("Short Description:", weight=get_font_weight_bold(), size=13),
                            desc_text,
                            ft.Divider(),
                            review_text,
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
                actions_alignment=get_main_alignment("end"),
            )

            open_dialog(dlg)

            if needs_fallback_fetch:
                async def fetch_background():
                    try:
                        async with SteamClient(api_key_field.value.strip()) as client:
                            await client.fetch_store_info(game)

                        desc_text.value = game.short_description or "No description available."
                        price_text.value = f"Price: {format_game_price_label(game)}"
                        if game.is_on_sale:
                            price_text.color = get_color("green400")
                        elif game.is_effectively_unavailable:
                            price_text.color = get_color("red300")
                        else:
                            price_text.color = get_color("white")

                        review_text.value = f"• User Reviews: {format_game_reviews(game)}"
                        dev_text.value = f"• Developer: {', '.join(game.developers) if game.developers else 'N/A'}"
                        r_suffix = (
                            " (Unreleased)"
                            if game.is_effectively_unreleased
                            else (" (Unavailable in Store)" if game.is_effectively_unavailable else "")
                        )
                        release_text.value = f"• Release Date: {(game.release_date or 'N/A')}{r_suffix}"
                        platform_text.value = f"• Platforms: {format_game_platforms(game)}"

                        t_list = (
                            ", ".join(sorted(game.categories | game.genres)[:12])
                            if (game.categories or game.genres)
                            else "None listed"
                        )
                        tags_text.value = f"• Tags & Categories: {t_list}"

                        # Always re-evaluate filters in case fallback metadata updated release/availability/tags/price
                        filter_and_render_games(recompute_filters=True)
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
        try:
            CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except OSError as err:
            print(f"Warning: Could not save config file: {err}")

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
        tooltip="Warning: Your API key is stored on disk in plain text (steam_friends_game_finder_config.json).",
        padding=get_padding_symmetric(horizontal=2, vertical=2),
    )

    api_key_header_row = ft.Row(
        controls=[
            ft.Text("Steam Web API Key", size=18, weight=get_font_weight_bold()),
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
        "• Storage: Your player list is saved locally on disk in steam_friends_game_finder_config.json."
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
            ft.Text("Add New Player", size=16, weight=get_font_weight_bold()),
            add_player_info_icon,
        ],
        spacing=6,
    )

    new_player_input = ft.TextField(
        hint_text="Enter SteamID64, profile URL, or vanity name",
        expand=True,
    )

    # Expandable player list so it stretches down to the bottom button
    player_list_view = ft.ListView(expand=True, spacing=5)
    try:
        player_list_view.scroll = get_always_scroll()
    except Exception:
        pass

    # Main Submit Button (anchored at the bottom of the left panel)
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

        label = "Refresh Games" if is_outdated else "Find Common Games"
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

    # Centered Loading View Controls (Subtitle + ProgressBar)
    center_loading_subtitle = ft.Text(
        "Connecting to Steam Web API...",
        size=14,
        color=get_color("cyan200"),
        text_align=get_text_align_center(),
    )
    center_progress_bar = ft.ProgressBar(
        width=360,
        value=None,
        color=get_color("cyan400"),
        bgcolor=get_color("bluegrey800"),
    )

    def update_loading_progress(message: str, progress: Optional[float] = None):
        """Updates the text and progress bar inside the centered loading spinner view."""
        center_loading_subtitle.value = message
        center_progress_bar.value = progress
        page.update()

    # ------------------------------------------
    # TOP FILTER & SORT CONTROLS
    # ------------------------------------------
    min_owned_label = ft.Text("Min Owned: 1", size=12, width=102)
    min_wishlist_label = ft.Text("Min Wishlist: 0", size=12, width=102)
    discount_label = ft.Text("Min Discount: 0%", size=12, width=102)
    max_price_label = ft.Text("Max Price: ∞", size=12, width=102)
    min_review_label = ft.Text("Min Review: 0%", size=12, width=102)

    def sync_slider_labels(update_only_labels: bool = False):
        """
        Updates the Text labels next to the sliders.
        Never mutates the Slider controls themselves so active mouse drags are never interrupted.
        """
        min_owned_label.value = f"Min Owned: {int(round(min_owned_slider.value or 0))}"
        min_wishlist_label.value = f"Min Wishlist: {int(round(min_wishlist_slider.value or 0))}"
        discount_label.value = f"Min Discount: {int(round(discount_slider.value or 0))}%"
        min_review_label.value = f"Min Review: {int(round(min_review_slider.value or 0))}%"

        price_val = int(
            round(max_price_slider.value if max_price_slider.value is not None else MAX_PRICE_SLIDER_CAP)
        )
        if price_val >= MAX_PRICE_SLIDER_CAP:
            max_price_label.value = "Max Price: ∞"
        elif price_val == 0:
            max_price_label.value = "Max Price: $0"
        else:
            max_price_label.value = f"Max Price: ${price_val}"

        if update_only_labels:
            for lbl in (
                min_owned_label,
                min_wishlist_label,
                discount_label,
                max_price_label,
                min_review_label,
            ):
                try:
                    lbl.update()
                except Exception:
                    pass

    def on_slider_drag(e=None):
        """Lightweight handler during slider dragging: updates only the text label next to the slider."""
        sync_slider_labels(update_only_labels=True)

    def on_owned_slider_release(e=None):
        """Commits Min Owned slider change when the mouse button is released."""
        nonlocal current_page
        if int(round(min_owned_slider.value or 0)) == 0 and int(round(min_wishlist_slider.value or 0)) == 0:
            min_wishlist_slider.value = 1
        sync_slider_labels(update_only_labels=False)
        current_page = 1
        filter_and_render_games(recompute_filters=True)

    def on_wishlist_slider_release(e=None):
        """Commits Min Wishlist slider change when the mouse button is released."""
        nonlocal current_page
        if int(round(min_wishlist_slider.value or 0)) == 0 and int(round(min_owned_slider.value or 0)) == 0:
            min_owned_slider.value = 1
        sync_slider_labels(update_only_labels=False)
        current_page = 1
        filter_and_render_games(recompute_filters=True)

    def on_filter_change(e=None):
        """Commits filter changes and re-renders the filtered games list."""
        nonlocal current_page
        sync_slider_labels(update_only_labels=False)
        current_page = 1
        filter_and_render_games(recompute_filters=True)

    min_owned_slider = create_slider(
        min_val=0,
        max_val=5,
        divisions=5,
        value=1,
        on_drag_label=on_slider_drag,
        on_release_commit=on_owned_slider_release,
    )

    min_wishlist_slider = create_slider(
        min_val=0,
        max_val=5,
        divisions=5,
        value=0,
        on_drag_label=on_slider_drag,
        on_release_commit=on_wishlist_slider_release,
    )

    discount_slider = create_slider(
        min_val=0,
        max_val=90,
        divisions=18,
        value=0,
        on_drag_label=on_slider_drag,
        on_release_commit=on_filter_change,
    )

    max_price_slider = create_slider(
        min_val=0,
        max_val=MAX_PRICE_SLIDER_CAP,
        divisions=MAX_PRICE_SLIDER_CAP,
        value=MAX_PRICE_SLIDER_CAP,
        on_drag_label=on_slider_drag,
        on_release_commit=on_filter_change,
    )

    min_review_slider = create_slider(
        min_val=0,
        max_val=100,
        divisions=20,
        value=0,
        on_drag_label=on_slider_drag,
        on_release_commit=on_filter_change,
    )

    # AND / OR connector between the Min Owned and Min Wishlist filters
    owned_wishlist_mode = "AND"
    owned_wishlist_mode_text = ft.Text(
        "AND", size=11, weight=get_font_weight_bold(), color=get_color("white")
    )

    def apply_owned_wishlist_mode_style():
        is_or = owned_wishlist_mode == "OR"
        owned_wishlist_mode_text.value = owned_wishlist_mode
        owned_wishlist_mode_chip.bgcolor = "#EF6C00" if is_or else "#00838F"
        owned_wishlist_mode_chip.border = get_border_all(1.5, "#FFA726" if is_or else "#26C6DA")
        owned_wishlist_mode_chip.tooltip = (
            "OR: games matching Min Owned or Min Wishlist (a filter at 0 is ignored). Click to switch to AND."
            if is_or
            else "AND: games must match both Min Owned and Min Wishlist. Click to switch to OR."
        )
        # Tint the linked group in the mode colour so it stands apart from the other sliders
        owned_wishlist_group.bgcolor = (
            get_color_with_opacity(0.08, "#EF6C00", "#14EF6C00")
            if is_or
            else get_color_with_opacity(0.08, "#00838F", "#1400838F")
        )
        owned_wishlist_group.border = get_border_all(
            1,
            get_color_with_opacity(0.4, "#EF6C00", "#66EF6C00")
            if is_or
            else get_color_with_opacity(0.4, "#00838F", "#6600838F"),
        )

    def toggle_owned_wishlist_mode(e=None):
        nonlocal owned_wishlist_mode, current_page
        owned_wishlist_mode = "OR" if owned_wishlist_mode == "AND" else "AND"
        apply_owned_wishlist_mode_style()
        current_page = 1
        filter_and_render_games(recompute_filters=True)

    # A Slider leaves empty space at the end of its track, a Text label does not. This extra gap
    # after the chip balances that, so the chip looks centred between the two filters.
    and_or_chip_width = 40
    slider_edge_inset = 20
    owned_wishlist_mode_chip = ft.Container(
        content=owned_wishlist_mode_text,
        width=and_or_chip_width,
        alignment=get_center_alignment(),
        border_radius=10,
        padding=get_padding_symmetric(horizontal=0, vertical=3),
        on_click=toggle_owned_wishlist_mode,
    )
    if get_click_cursor() is not None:
        try:
            owned_wishlist_mode_chip.mouse_cursor = get_click_cursor()
        except Exception:
            pass

    # Linked Min Owned / Min Wishlist pair with the AND/OR connector between them
    owned_wishlist_group = ft.Container(
        # 9px + the 1px border keeps the labels aligned with the 10px padding of the other sections
        padding=get_padding_symmetric(horizontal=9, vertical=3),
        content=ft.Row(
            spacing=6,
            vertical_alignment=get_cross_alignment("center"),
            controls=[
                ft.Row([min_owned_label, min_owned_slider], spacing=2, expand=1),
                ft.Container(
                    content=owned_wishlist_mode_chip,
                    padding=get_padding_only(right=slider_edge_inset),
                ),
                ft.Row([min_wishlist_label, min_wishlist_slider], spacing=2, expand=1),
            ],
        ),
    )
    apply_owned_wishlist_mode_style()

    def update_match_sliders() -> bool:
        """
        Updates owned and wishlist slider max values.
        Uses loaded player count when results are already loaded, or ticked player count otherwise.
        Returns True if any slider value was clamped/changed.
        """
        ticked_count = sum(1 for p in config.get("players", []) if p.get("enabled", True))
        reference_count = len(players) if (has_loaded_once and players) else ticked_count
        max_players = max(1, reference_count)
        is_disabled = reference_count == 0

        prev_owned = int(round(min_owned_slider.value or 0))
        prev_wish = int(round(min_wishlist_slider.value or 0))

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

        sync_slider_labels(update_only_labels=False)
        new_owned = int(round(min_owned_slider.value or 0))
        new_wish = int(round(min_wishlist_slider.value or 0))
        return (prev_owned != new_owned) or (prev_wish != new_wish)

    def render_player_list() -> bool:
        """Renders the left-hand player list and returns True if slider bounds caused value clamping."""
        sort_config_players()
        player_list_view.controls.clear()

        all_players = config.get("players", [])
        selected_players = [p for p in all_players if p.get("enabled", True)]
        available_players = [p for p in all_players if not p.get("enabled", True)]

        def build_player_row(p_data: dict, idx: int, is_enabled: bool) -> ft.Container:
            def make_toggle(p):
                def toggle_handler(e):
                    p["enabled"] = bool(e.control.value)
                    save_config()
                    render_player_list()
                    filter_and_render_games(recompute_filters=True)
                return toggle_handler

            def make_delete(p):
                def delete_handler(e):
                    config["players"].remove(p)
                    session_players_cache.pop(p.get("steam_id", ""), None)
                    save_config()
                    render_player_list()
                    filter_and_render_games(recompute_filters=True)
                    notify(f"Removed '{p['display_name']}' from player list.")
                return delete_handler

            player_num_badge = ft.Container(
                content=ft.Text(
                    f"#{idx}",
                    size=11,
                    weight=get_font_weight_bold(),
                    color=get_color("cyan200") if is_enabled else get_color("grey500"),
                ),
                bgcolor=get_color("bluegrey900"),
                padding=get_padding_symmetric(horizontal=6, vertical=3),
                border_radius=4,
            )

            cb = create_checkbox(
                label=p_data["display_name"],
                value=is_enabled,
                on_change=make_toggle(p_data),
                expand=True,
            )

            del_btn = create_icon_button(
                "delete",
                icon_color="red400",
                tooltip="Delete player",
                on_click=make_delete(p_data),
            )

            return ft.Container(
                padding=get_padding_only(right=14),
                content=ft.Row(
                    [
                        ft.Row([player_num_badge, cb], spacing=6, expand=True),
                        del_btn,
                    ],
                    alignment=get_main_alignment("spaceBetween"),
                ),
            )

        # Selected Players Section
        player_list_view.controls.append(
            ft.Text(
                f"Selected Players ({len(selected_players)})",
                size=14,
                weight=get_font_weight_bold(),
                color=get_color("cyan200"),
            )
        )
        if selected_players:
            for idx, p_data in enumerate(selected_players, start=1):
                player_list_view.controls.append(build_player_row(p_data, idx, True))
        else:
            player_list_view.controls.append(
                ft.Container(
                    padding=get_padding_symmetric(horizontal=4, vertical=4),
                    content=ft.Text("No players selected.", size=12, italic=True, color=get_color("grey500")),
                )
            )

        player_list_view.controls.append(ft.Divider(height=12))

        # Available Players Section
        player_list_view.controls.append(
            ft.Text(
                f"Available Players ({len(available_players)})",
                size=14,
                weight=get_font_weight_bold(),
                color=get_color("grey400"),
            )
        )
        if available_players:
            for idx, p_data in enumerate(available_players, start=1):
                player_list_view.controls.append(build_player_row(p_data, idx, False))
        else:
            player_list_view.controls.append(
                ft.Container(
                    padding=get_padding_symmetric(horizontal=4, vertical=4),
                    content=ft.Text("No unselected players.", size=12, italic=True, color=get_color("grey500")),
                )
            )

        sliders_changed = update_match_sliders()
        check_outdated_state()
        page.update()
        return sliders_changed

    async def on_add_player(e):
        nonlocal is_fetching, loading_title_str
        if is_fetching:
            return

        api_key = api_key_field.value.strip()
        if not api_key:
            show_dialog("API Key Required", "Please enter your Steam Web API Key before adding players.")
            return

        val = new_player_input.value.strip()
        if not val:
            return

        config["api_key"] = api_key
        save_config()

        is_fetching = True
        loading_title_str = "Resolving Steam Profile..."
        update_loading_progress(f"Looking up '{val}' on Steam...", None)
        filter_and_render_games(recompute_filters=False)

        try:
            async with SteamClient(api_key) as client:
                resolved_id = await client.resolve_steam_id(val)

                if not resolved_id:
                    show_dialog(
                        "Player Not Found",
                        f"Could not find a Steam profile for:\n'{val}'\n\nPlease verify the SteamID64, URL, or API Key.",
                    )
                    return

                if any(p["steam_id"] == resolved_id for p in config.get("players", [])):
                    show_dialog("Duplicate Player", "This player is already in your player list.")
                    return

                update_loading_progress("Fetching player profile details...", None)
                temp_player = Player(steam_id=resolved_id)
                found_profile, err_msg = await client.fetch_player_summary(temp_player)
                if err_msg:
                    show_dialog("Steam API Error", err_msg)
                    return
                if not found_profile:
                    show_dialog(
                        "Player Not Found",
                        f"No Steam account exists for SteamID64:\n'{resolved_id}'",
                    )
                    return

            new_entry = {
                "steam_id": resolved_id,
                "display_name": temp_player.display_name,
                "enabled": True,
            }
            config.setdefault("players", []).append(new_entry)
            save_config()

            new_player_input.value = ""
            notify(f"Added player '{temp_player.display_name}'!")
        finally:
            is_fetching = False
            loading_title_str = "Fetching game data..."
            render_player_list()
            filter_and_render_games(recompute_filters=True)

    new_player_input.on_submit = on_add_player

    add_player_btn = create_button("Add Player", icon="add", on_click=on_add_player)
    add_player_row = ft.Row([new_player_input, add_player_btn])

    # Sort menu: sort field + ascending/descending, opened from a compact button in the summary bar
    # Picking a field resets the direction to that field's natural default (cheapest price first, highest
    # count/review/discount first); the direction items flip it afterwards.
    SORT_OPTIONS = ["Owned", "Wishlisted", "User Reviews", "Price", "Discount"]
    SORT_DEFAULT_DESCENDING = {"Owned": True, "Wishlisted": True, "User Reviews": True, "Price": False, "Discount": True}
    sort_by_value = "Owned"
    sort_descending = True

    sort_button_text = ft.Text("Owned", size=12, weight=get_font_weight_bold(), color=get_color("white"))
    sort_direction_icon = ft.Icon(get_icon("arrow_downward"), size=16, color=get_color("white"))

    def refresh_sort_menu():
        sort_button_text.value = sort_by_value
        sort_direction_icon.name = get_icon("arrow_downward" if sort_descending else "arrow_upward")
        for name, item in sort_field_items.items():
            item.checked = name == sort_by_value
        sort_ascending_item.checked = not sort_descending
        sort_descending_item.checked = sort_descending

    def make_sort_field_handler(name: str):
        def handler(e=None):
            nonlocal sort_by_value, sort_descending
            sort_by_value = name
            sort_descending = SORT_DEFAULT_DESCENDING[name]
            refresh_sort_menu()
            on_filter_change()
        return handler

    def make_sort_direction_handler(descending: bool):
        def handler(e=None):
            nonlocal sort_descending
            sort_descending = descending
            refresh_sort_menu()
            on_filter_change()
        return handler

    SORT_ICONS = {
        "Owned": "tag",
        "Wishlisted": "tag",
        "User Reviews": "percent",
        "Price": "attach_money",
        "Discount": "percent",
    }
    sort_field_items = {
        name: create_menu_item(name, icon=SORT_ICONS[name], on_click=make_sort_field_handler(name))
        for name in SORT_OPTIONS
    }
    sort_ascending_item = create_menu_item(
        "Ascending", icon="arrow_upward", on_click=make_sort_direction_handler(False)
    )
    sort_descending_item = create_menu_item(
        "Descending", icon="arrow_downward", on_click=make_sort_direction_handler(True)
    )

    sort_menu_button = ft.PopupMenuButton(
        tooltip="Sort games",
        items=[
            *sort_field_items.values(),
            create_menu_item(),
            sort_ascending_item,
            sort_descending_item,
        ],
        content=ft.Container(
            bgcolor=get_color("bluegrey800"),
            border=get_border_all(1, "bluegrey600"),
            border_radius=6,
            padding=get_padding_symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                tight=True,
                spacing=6,
                controls=[
                    ft.Icon(get_icon("sort"), size=16, color=get_color("white")),
                    ft.Text("Sort:", size=12, color=get_color("grey300")),
                    sort_button_text,
                    sort_direction_icon,
                ],
            ),
        ),
    )
    # Open/close animation 2x faster than Flutter's 300ms popup menu default
    if hasattr(ft, "AnimationStyle"):
        try:
            sort_menu_button.popup_animation_style = ft.AnimationStyle(duration=150, reverse_duration=150)
        except Exception:
            pass
    refresh_sort_menu()

    list_mode_switch = create_switch(
        label="List Mode",
        value=False,
        on_change=lambda _: filter_and_render_games(recompute_filters=True),
    )

    # Special Game Switches (Show logic: Unreleased ON by default, Unavailable OFF by default)
    show_unreleased_switch = create_switch(
        label="Unreleased",
        value=True,
        on_change=on_filter_change,
    )

    show_unavailable_switch = create_switch(
        label="Unavailable",
        value=False,
        on_change=on_filter_change,
    )

    search_filter = bind_change_handlers(
        ft.TextField(
            label="Search title...",
            prefix_icon=get_icon("search"),
            expand=1,
        ),
        on_filter_change,
        include_submit_and_blur=True,
    )

    custom_tags_filter = bind_change_handlers(
        ft.TextField(
            label="Search tags (comma-separated)...",
            hint_text="e.g. RPG, Survival, Roguelike",
            prefix_icon=get_icon("sell"),
            expand=1,
        ),
        on_filter_change,
        include_submit_and_blur=True,
    )

    # Compact single-column Common Tags checkboxes with smaller text (size=12)
    coop_only_cb = create_checkbox(
        label="Co-op", value=False, on_change=on_filter_change, label_size=12, compact=True
    )
    multiplayer_only_cb = create_checkbox(
        label="Multiplayer", value=False, on_change=on_filter_change, label_size=12, compact=True
    )
    vr_only_cb = create_checkbox(
        label="VR", value=False, on_change=on_filter_change, label_size=12, compact=True
    )
    macos_only_cb = create_checkbox(
        label="MacOS", value=False, on_change=on_filter_change, label_size=12, compact=True
    )
    online_only_cb = create_checkbox(
        label="Online", value=False, on_change=on_filter_change, label_size=12, compact=True
    )
    linux_only_cb = create_checkbox(
        label="Linux", value=False, on_change=on_filter_change, label_size=12, compact=True
    )

    # Keep BOTH games_list_view and games_state_container permanently mounted inside games_container
    # and toggle their `.visible` property so Flet never detaches/re-attaches the ListView mid-update.
    games_list_view = ft.ListView(expand=True, spacing=6, visible=False)
    try:
        games_list_view.scroll = get_always_scroll()
    except Exception:
        pass

    games_state_container = ft.Container(expand=True, visible=True)

    games_container = ft.Column(
        expand=True,
        spacing=0,
        controls=[
            games_state_container,
            games_list_view,
        ],
    )

    def build_centered_state(
        icon_name: str,
        icon_color: str,
        title: str,
        subtitle: str = "",
        show_spinner: bool = False,
        subtitle_control: Optional[ft.Control] = None,
        extra_control: Optional[ft.Control] = None,
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
                weight=get_font_weight_bold(),
                color=get_color("white"),
                text_align=get_text_align_center(),
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
                    text_align=get_text_align_center(),
                )
            )

        if extra_control is not None:
            col_controls.append(extra_control)

        return ft.Container(
            expand=True,
            alignment=get_center_alignment(),
            padding=30,
            content=ft.Column(
                alignment=get_main_alignment("center"),
                horizontal_alignment=get_cross_alignment("center"),
                tight=True,
                spacing=14,
                controls=col_controls,
            ),
        )

    def show_games_placeholder(placeholder_control: ft.Control):
        """Displays the centered state container and hides the ListView without unmounting it."""
        games_list_view.visible = False
        games_state_container.content = placeholder_control
        games_state_container.visible = True

    def show_games_list():
        """Displays the ListView and hides the centered state container without unmounting it."""
        games_state_container.visible = False
        games_list_view.visible = True

    # Helper to compute filtered GameItems
    def compute_filtered_games() -> List[GameItem]:
        search_query = (search_filter.value or "").lower().strip()
        raw_tags_query = custom_tags_filter.value or ""
        required_tags = [t.strip().lower() for t in raw_tags_query.split(",") if t.strip()]

        sort_by = sort_by_value
        min_owned = int(round(min_owned_slider.value or 0))
        min_wishlist = int(round(min_wishlist_slider.value or 0))
        min_discount = int(round(discount_slider.value or 0))
        min_review = int(round(min_review_slider.value or 0))
        max_price_val = int(
            round(max_price_slider.value if max_price_slider.value is not None else MAX_PRICE_SLIDER_CAP)
        )
        is_price_limited = max_price_val < MAX_PRICE_SLIDER_CAP
        include_unreleased = bool(show_unreleased_switch.value)
        include_unavailable = bool(show_unavailable_switch.value)
        require_coop = bool(coop_only_cb.value)
        require_multiplayer = bool(multiplayer_only_cb.value)
        require_vr = bool(vr_only_cb.value)
        require_macos = bool(macos_only_cb.value)
        require_online = bool(online_only_cb.value)
        require_linux = bool(linux_only_cb.value)

        filtered: List[GameItem] = []
        for g in all_games_map.values():
            if search_query and search_query not in g.name.lower():
                continue

            # Unreleased & Unavailable Store Filters (Show logic)
            if not include_unreleased and g.is_effectively_unreleased:
                continue
            if not include_unavailable and g.is_effectively_unavailable:
                continue

            # Owned & Wishlist Count Threshold Filters
            if owned_wishlist_mode == "OR":
                # A threshold of 0 matches everything, so it is left out of the OR
                owned_ok = min_owned > 0 and len(g.owners) >= min_owned
                wishlist_ok = min_wishlist > 0 and len(g.wishlisters) >= min_wishlist
                if not (owned_ok or wishlist_ok):
                    continue
            else:
                if len(g.owners) < min_owned:
                    continue
                if len(g.wishlisters) < min_wishlist:
                    continue

            # Category & Platform Filters
            if require_coop and not g.is_coop:
                continue
            if require_multiplayer and not g.is_multiplayer:
                continue
            if require_online and not g.is_online:
                continue
            if require_vr and not g.is_vr:
                continue
            if require_macos and g.mac_supported is not True:
                continue
            if require_linux and g.linux_supported is not True:
                continue

            # Custom Comma-Separated Tags Filter
            if required_tags:
                game_tags_lower = [lbl.lower() for lbl in (g.categories | g.genres)]
                if not all(any(req_t in gt for gt in game_tags_lower) for req_t in required_tags):
                    continue

            # User Review Percentage Filter
            if min_review > 0:
                if g.review_percentage is None or g.review_percentage < min_review:
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

        # Primary sort value per field; None (no reviews / no price) always goes to the end
        primary_value_by_field = {
            "Wishlisted": lambda g: len(g.wishlisters),
            "User Reviews": lambda g: g.review_percentage,
            "Price": lambda g: g.final_price,
            "Discount": lambda g: g.discount_pct,
        }
        primary_value = primary_value_by_field.get(sort_by, lambda g: len(g.owners))

        # Tie-break first, then a stable sort on the primary value (reverse keeps ties in order)
        filtered.sort(key=lambda g: (-len(g.owners), -len(g.wishlisters), g.name.lower()))
        with_value = [g for g in filtered if primary_value(g) is not None]
        without_value = [g for g in filtered if primary_value(g) is None]
        with_value.sort(key=primary_value, reverse=sort_descending)
        filtered = with_value + without_value

        return filtered

    # ------------------------------------------
    # CORE LOGIC: DELTA FETCH & AGGREGATE GAMES
    # ------------------------------------------
    async def on_submit(e):
        nonlocal current_page, is_fetching, loading_title_str, has_loaded_once, last_fetched_player_ids
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
        loading_title_str = "Fetching game data..."
        submit_btn.disabled = True
        current_page = 1
        update_loading_progress("Preparing to fetch libraries & store metadata...", None)
        filter_and_render_games(recompute_filters=False)

        try:
            # Reuse in-memory Player libraries; only fetch players not yet in session_players_cache
            players.clear()
            players_to_fetch: List[Player] = []

            for p_data in active_players_data:
                sid = p_data["steam_id"]
                cached_p = session_players_cache.get(sid)
                if cached_p and cached_p.library_fetched and not cached_p.is_private and not cached_p.fetch_error:
                    players.append(cached_p)
                else:
                    new_p = Player(steam_id=sid, display_name=p_data["display_name"])
                    players.append(new_p)
                    players_to_fetch.append(new_p)

            page.update()

            async with SteamClient(api_key) as client:
                if players_to_fetch:
                    update_loading_progress(
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
                        if not p.fetch_error:
                            p.library_fetched = True
                            if not p.is_private:
                                session_players_cache[p.steam_id] = p
                        if p.steam_id in config_map and p.display_name != config_map[p.steam_id].get("display_name"):
                            config_map[p.steam_id]["display_name"] = p.display_name
                            config_updated = True

                    if config_updated:
                        save_config()

                    # Check for API / Network errors vs Private libraries
                    errored_players = [
                        f"{p.display_name}: {p.fetch_error}" for p in players_to_fetch if p.fetch_error
                    ]
                    private_players = [
                        p.display_name for p in players_to_fetch if p.is_private and not p.fetch_error
                    ]

                    if errored_players:
                        show_dialog(
                            "Steam API / Network Error",
                            "Failed to fetch library data for the following players:\n\n• "
                            + "\n• ".join(errored_players),
                        )
                    elif private_players:
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

                # Fetch store metadata for games not yet fetched in this session
                unfetched_games = sorted(
                    [
                        g
                        for g in all_games_map.values()
                        if not g.store_info_fetched
                    ],
                    key=lambda g: (-len(g.owners), -len(g.wishlisters), g.name.lower()),
                )

                if unfetched_games:
                    update_loading_progress(
                        f"Fetching store prices, reviews & tags for {len(unfetched_games)} games (0/{len(unfetched_games)})...",
                        0.0,
                    )

                    def on_bulk_progress(done: int, total: int):
                        update_loading_progress(
                            f"Fetching store prices, reviews & tags ({done}/{total})...",
                            done / total if total > 0 else 1.0,
                        )

                    await client.fetch_bulk_store_items(unfetched_games, on_progress=on_bulk_progress)

            has_loaded_once = True
            last_fetched_player_ids = {p.steam_id for p in players}
            notify(f"Data loaded! Found {len(all_games_map)} unique games across players.")
        finally:
            is_fetching = False
            submit_btn.disabled = False
            render_player_list()
            filter_and_render_games(recompute_filters=True)

    submit_btn.on_click = on_submit

    # ------------------------------------------
    # TOP SUMMARY + PROMINENT LEFT-ANCHORED COLLAPSE BUTTON + LIST MODE + SORT BAR
    # ------------------------------------------
    matching_filters_text = ft.Text(
        "Matching Filters: 0 / 0", size=12, weight=get_font_weight_bold(), color=get_color("white")
    )
    matching_filters_badge = ft.Container(
        content=matching_filters_text,
        bgcolor=get_color("blue800"),
        padding=get_padding_symmetric(horizontal=10, vertical=6),
        border_radius=6,
    )

    # Prominent Collapse/Expand button positioned at the very left of the summary row
    filters_collapsed = False
    collapse_filter_icon = ft.Icon(get_icon("tune"), size=16, color=get_color("white"))
    collapse_arrow_icon = ft.Icon(get_icon("keyboard_arrow_up"), size=18, color=get_color("white"))
    collapse_btn_text = ft.Text(
        "Hide Filters", size=12, weight=get_font_weight_bold(), color=get_color("white")
    )

    def toggle_filters_collapse(e=None):
        nonlocal filters_collapsed
        filters_collapsed = not filters_collapsed
        top_filters_area.visible = not filters_collapsed
        if filters_collapsed:
            collapse_btn_text.value = "Show Filters"
            collapse_arrow_icon.name = get_icon("keyboard_arrow_down")
            collapse_filters_chip.bgcolor = "#EF6C00"  # Prominent Amber-Orange when collapsed
            collapse_filters_chip.border = get_border_all(1.5, "#FFA726")
        else:
            collapse_btn_text.value = "Hide Filters"
            collapse_arrow_icon.name = get_icon("keyboard_arrow_up")
            collapse_filters_chip.bgcolor = "#00838F"  # Prominent Cyan-Teal when expanded
            collapse_filters_chip.border = get_border_all(1.5, "#26C6DA")
        page.update()

    collapse_filters_chip = ft.Container(
        bgcolor="#00838F",
        border=get_border_all(1.5, "#26C6DA"),
        border_radius=6,
        padding=get_padding_symmetric(horizontal=12, vertical=6),
        tooltip="Collapse or expand the Filters, Common Tags, Parameters, and Special Games sections",
        on_click=toggle_filters_collapse,
        content=ft.Row(
            tight=True,
            spacing=6,
            controls=[collapse_filter_icon, collapse_btn_text, collapse_arrow_icon],
        ),
    )
    if get_click_cursor() is not None:
        try:
            collapse_filters_chip.mouse_cursor = get_click_cursor()
        except Exception:
            pass

    top_summary_bar = ft.Container(
        padding=get_padding_symmetric(horizontal=4, vertical=0),
        content=ft.Row(
            alignment=get_main_alignment("spaceBetween"),
            controls=[
                ft.Row(
                    [
                        collapse_filters_chip,
                        matching_filters_badge,
                        list_mode_switch,
                    ],
                    spacing=12,
                ),
                sort_menu_button,
            ],
        ),
    )

    page_info_text = ft.Text(
        "Showing 0-0 (Page 0 of 0)", size=12, weight=get_font_weight_bold(), color=get_color("grey300")
    )
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
        page_size = 999999 if val == "All" else int(val or 50)
        current_page = 1
        filter_and_render_games(recompute_filters=True)

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
        # Fixed height so the left panel's bottom divider can line up with the divider above this bar
        height=60,
        padding=get_padding_symmetric(horizontal=8, vertical=6),
        content=ft.Row(
            alignment=get_main_alignment("spaceBetween"),
            controls=[
                page_info_text,
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

        # State 1: Actively fetching data -> Centered Spinner + Subtitle + ProgressBar
        if is_fetching:
            matching_filters_text.value = "Matching Filters: 0 / 0"
            matching_filters_badge.bgcolor = get_color("bluegrey800")
            page_info_text.value = "Showing 0-0 (Page 0 of 0)"
            prev_page_btn.disabled = True
            next_page_btn.disabled = True
            show_games_placeholder(
                build_centered_state(
                    icon_name="sync",
                    icon_color="cyan400",
                    title=loading_title_str,
                    show_spinner=True,
                    subtitle_control=center_loading_subtitle,
                    extra_control=center_progress_bar,
                )
            )
            page.update()
            return

        # State 2: No data loaded yet AND no players selected -> Centered Player Prompt
        if not has_loaded_once and ticked_count == 0:
            matching_filters_text.value = "Matching Filters: 0 / 0"
            matching_filters_badge.bgcolor = get_color("bluegrey800")
            page_info_text.value = "Showing 0-0 (Page 0 of 0)"
            prev_page_btn.disabled = True
            next_page_btn.disabled = True
            show_games_placeholder(
                build_centered_state(
                    icon_name="group_add",
                    icon_color="amber400",
                    title="No players selected.",
                    subtitle=(
                        "Add at least one Steam player on the left and make sure their checkbox is ticked,\n"
                        "then click 'Find Common Games' to load all player's libraries."
                    ),
                )
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
            show_games_placeholder(
                build_centered_state(
                    icon_name="sports_esports",
                    icon_color="cyan300",
                    title="No game data has been loaded yet.",
                    subtitle="Click 'Find Common Games' on the bottom-left to scan your selected players' libraries.",
                )
            )
            page.update()
            return

        if recompute_filters or not cached_filtered_games:
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
            show_games_placeholder(
                build_centered_state(
                    icon_name="search_off",
                    icon_color="grey500",
                    title="No games match the current filters.",
                    subtitle="Try relaxing your search query, tags, review score, price, or ownership sliders above.",
                )
            )
            page.update()
            return

        # State 5: Render the scrollable list of matching games
        is_small = bool(list_mode_switch.value)
        games_list_view.spacing = 2 if is_small else 6

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

        # Build new controls list and assign atomically so ListView never renders an empty intermediate frame
        new_cards: List[ft.Control] = []

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

            is_unrel = game.is_effectively_unreleased
            is_unavail = game.is_effectively_unavailable

            info_tooltip_text = (
                f"🎮 #{global_rank} - {game.name}\n"
                f"• Owners ({len(game.owners)}): {owner_names or 'None'}\n"
                f"• Wishlist ({len(game.wishlisters)}): {wishlist_names or 'None'}\n"
                f"• User Reviews: {format_game_reviews(game)}\n"
                f"• Tags: {tag_list}"
            )
            if is_unrel:
                info_tooltip_text += f"\n• Status: Unreleased ({game.release_date or 'Coming Soon'})"
            elif is_unavail:
                info_tooltip_text += "\n• Status: Unavailable / Delisted in Steam Store"
            if game.mac_supported is not None:
                info_tooltip_text += f"\n• MacOS: {'Supported' if game.mac_supported else 'No'}"

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
                    weight=get_font_weight_bold(),
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
                        content=ft.Text(
                            price_text,
                            size=badge_font_size,
                            color=get_color("black"),
                            weight=get_font_weight_bold(),
                        ),
                        bgcolor=price_color,
                        padding=badge_pad,
                        border_radius=4,
                    )
                )

            # Dark Red "Unavailable" Badge & Dark Orange "Unreleased" Badge
            if is_unavail:
                badges.append(
                    ft.Container(
                        content=ft.Text(
                            "Unavailable",
                            size=badge_font_size,
                            color=get_color("white"),
                            weight=get_font_weight_bold(),
                        ),
                        bgcolor="#B71C1C",
                        padding=badge_pad,
                        border_radius=4,
                    )
                )
            elif is_unrel:
                badges.append(
                    ft.Container(
                        content=ft.Text(
                            "Unreleased",
                            size=badge_font_size,
                            color=get_color("white"),
                            weight=get_font_weight_bold(),
                        ),
                        bgcolor="#BF360C",
                        padding=badge_pad,
                        border_radius=4,
                    )
                )

            # Right-anchored Blue User Review Badge (shows only review icon + percentage)
            review_badge = None
            if game.review_percentage is not None:
                review_badge = ft.Container(
                    bgcolor=get_color("blue700"),
                    padding=badge_pad,
                    border_radius=4,
                    tooltip=f"Steam User Reviews: {format_game_reviews(game)}",
                    content=ft.Row(
                        tight=True,
                        spacing=4,
                        controls=[
                            ft.Icon(
                                get_icon("thumb_up"),
                                size=11 if is_small else 13,
                                color=get_color("white"),
                            ),
                            ft.Text(
                                f"{game.review_percentage}%",
                                size=badge_font_size,
                                weight=get_font_weight_bold(),
                                color=get_color("white"),
                            ),
                        ],
                    ),
                )

            unavailable_warning_icon = ft.Container(
                content=ft.Icon(
                    get_icon("warning_amber_rounded"),
                    color=get_color("amber300"),
                    size=15 if is_small else 18,
                ),
                tooltip="Unavailable / No longer available in the Steam Store",
            )

            # Row background color at 0.5 opacity for Unavailable (dark red) and Unreleased (orange)
            if is_unavail:
                row_bgcolor = get_color_with_opacity(0.5, "red900", "#80B71C1C")
            elif is_unrel:
                row_bgcolor = get_color_with_opacity(0.5, "amber900", "#80FF6F00")
            else:
                row_bgcolor = None

            info_btn = create_icon_button(
                "info_outline",
                # Half of Flet's default 800ms hover delay
                tooltip=create_tooltip(info_tooltip_text, wait_ms=400),
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

            right_action_controls = []
            if review_badge is not None:
                right_action_controls.append(review_badge)
            right_action_controls.extend([info_btn, store_btn])

            title_controls = [unavailable_warning_icon] if is_unavail else []
            title_controls.append(create_link_text(game.name, store_url, size=12 if is_small else 15))

            if is_small:
                card = create_card(
                    content=ft.Container(
                        bgcolor=row_bgcolor,
                        border_radius=8,
                        padding=get_padding_only(left=6, top=2, right=14, bottom=2),
                        content=ft.Row(
                            alignment=get_main_alignment("spaceBetween"),
                            spacing=8,
                            controls=[
                                rank_badge,
                                ft.Row(controls=title_controls, spacing=5, expand=True),
                                ft.Row(controls=badges, spacing=4),
                                ft.Row(controls=right_action_controls, spacing=4),
                            ],
                        ),
                    ),
                )
            else:
                thumb_img = ft.Image(
                    src=game.header_image_url,
                    width=110,
                    height=50,
                    fit=get_image_fit_cover(),
                    border_radius=4,
                    error_content=ft.Icon(get_icon("videogame_asset"), size=30, color=get_color("grey500")),
                )

                thumb_container = ft.Container(
                    content=thumb_img,
                    width=110,
                    height=50,
                    bgcolor=get_color("bluegrey900"),
                    border_radius=4,
                    tooltip=create_tooltip(f"{info_tooltip_text}\n\nClick to view details", wait_ms=400),
                    on_click=lambda _, g=game: open_game_details_dialog(g),
                )

                card = create_card(
                    content=ft.Container(
                        bgcolor=row_bgcolor,
                        border_radius=8,
                        padding=get_padding_only(left=8, top=8, right=16, bottom=8),
                        content=ft.Row(
                            alignment=get_main_alignment("spaceBetween"),
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
                                    controls=right_action_controls,
                                    spacing=6,
                                ),
                            ],
                        ),
                    ),
                )

            new_cards.append(card)

        games_list_view.controls = new_cards
        show_games_list()
        page.update()

    # ------------------------------------------
    # LAYOUT BUILD (APPROACH 3: 3-COLUMN SPLIT)
    # ------------------------------------------
    left_panel = create_card(
        content=ft.Container(
            # No bottom padding: the button area below handles its own spacing to line up with the right panel
            padding=get_padding_only(left=15, top=8, right=15, bottom=0) or 15,
            width=380,
            expand=True,
            content=ft.Column(
                expand=True,
                controls=[
                    api_key_header_row,
                    api_key_row,
                    ft.Divider(),
                    add_player_header_row,
                    add_player_row,
                    ft.Divider(),
                    player_list_view,
                    # Divider + button area mirroring the right panel's bottom, measured from the window content edge:
                    # divider line at 4 card margin + 60 + 2 = 60 pagination bar + 4 spacing + 2, and the button
                    # centred at 4 card margin + 26 (60 minus 8 top padding, halved) = the bar's 30px centre line
                    ft.Column(
                        spacing=0,
                        controls=[
                            ft.Divider(height=4),
                            ft.Container(
                                height=60,
                                padding=get_padding_only(top=8),
                                alignment=get_center_alignment(),
                                content=submit_btn,
                            ),
                        ],
                    ),
                ],
            ),
        )
    )

    # Top Row: "Filters" Container (Search Title & Search Tags side-by-side)
    filters_container = create_card(
        content=ft.Container(
            padding=get_padding_symmetric(horizontal=10, vertical=8),
            content=ft.Column(
                spacing=4,
                controls=[
                    ft.Text("Filters", size=13, weight=get_font_weight_bold(), color=get_color("cyan200")),
                    ft.Row(
                        spacing=12,
                        controls=[
                            search_filter,
                            custom_tags_filter,
                        ],
                    ),
                ],
            ),
        )
    )

    # Lower Column 1: "Common Tags" Container (Single compact column of 4 entries with tight vertical padding)
    common_tags_container = create_card(
        content=ft.Container(
            padding=get_padding_symmetric(horizontal=10, vertical=6),
            width=140,
            content=ft.Column(
                spacing=0,
                tight=True,
                controls=[
                    ft.Container(
                        padding=get_padding_only(bottom=2),
                        content=ft.Text(
                            "Common Tags", size=13, weight=get_font_weight_bold(), color=get_color("cyan200")
                        ),
                    ),
                    ft.Container(content=coop_only_cb, height=26),
                    ft.Container(content=multiplayer_only_cb, height=26),
                    ft.Container(content=online_only_cb, height=26),
                    ft.Container(content=vr_only_cb, height=26),
                    ft.Container(content=macos_only_cb, height=26),
                    ft.Container(content=linux_only_cb, height=26),
                ],
            ),
        )
    )

    # Lower Column 2: "Parameters" Container (3 compact rows of sliders with extra horizontal room)
    parameters_container = create_card(
        expand=True,
        content=ft.Container(
            # Minimal horizontal padding so the linked owned/wishlist background nearly spans the card width;
            # each section below applies the horizontal padding itself, keeping all labels aligned.
            padding=get_padding_symmetric(horizontal=1, vertical=4),
            content=ft.Column(
                spacing=4,
                tight=True,
                controls=[
                    ft.Container(
                        padding=get_padding_symmetric(horizontal=10, vertical=0),
                        content=ft.Text(
                            "Parameters", size=13, weight=get_font_weight_bold(), color=get_color("cyan200")
                        ),
                    ),
                    owned_wishlist_group,
                    # Remaining sliders; the middle spacer matches the AND/OR chip and its extra gap so columns line up.
                    # 9px + the 1px border keeps the labels aligned with the other sections.
                    ft.Container(
                        bgcolor=get_color_with_opacity(0.08, "#00838F", "#1400838F"),
                        border=get_border_all(1, get_color_with_opacity(0.4, "#00838F", "#6600838F")),
                        padding=get_padding_symmetric(horizontal=9, vertical=3),
                        content=ft.Column(
                            spacing=0,
                            tight=True,
                            controls=[
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        ft.Row([discount_label, discount_slider], spacing=2, expand=1),
                                        ft.Container(width=and_or_chip_width + slider_edge_inset),
                                        ft.Row([max_price_label, max_price_slider], spacing=2, expand=1),
                                    ],
                                ),
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        ft.Row([min_review_label, min_review_slider], spacing=2, expand=1),
                                        ft.Container(width=and_or_chip_width + slider_edge_inset),
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

    # Lower Column 3: "Special Games" Container (Unreleased & Unavailable switches)
    special_games_container = create_card(
        content=ft.Container(
            padding=get_padding_symmetric(horizontal=10, vertical=6),
            width=155,
            content=ft.Column(
                spacing=6,
                tight=True,
                controls=[
                    ft.Text("Special Games", size=13, weight=get_font_weight_bold(), color=get_color("cyan200")),
                    show_unreleased_switch,
                    show_unavailable_switch,
                ],
            ),
        )
    )

    # Tighter gap between the text filters card and the cards below it: 1px bottom margin instead of the
    # default 4px, and no column spacing (gap goes from 4 + 2 + 4 = 10px down to 1 + 0 + 4 = 5px)
    filters_card_margin = get_margin_only(left=4, top=4, right=4, bottom=1)
    if filters_card_margin is not None:
        try:
            filters_container.margin = filters_card_margin
        except Exception:
            pass

    top_filters_area = ft.Column(
        spacing=0,
        visible=True,
        controls=[
            filters_container,
            ft.Row(
                spacing=6,
                vertical_alignment=get_cross_alignment("start"),
                controls=[
                    common_tags_container,
                    parameters_container,
                    special_games_container,
                ],
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

    # Add controls to page first so all controls are mounted before initial state updates
    page.add(
        ft.Row(
            expand=True,
            controls=[left_panel, right_panel],
            spacing=15,
        )
    )

    # Initial Render of Player List & Counters
    render_player_list()
    filter_and_render_games(recompute_filters=True)
