import flet as ft

from src.platform.desktop import DesktopPlatform
from src.ui.app import build_app


async def main(page: ft.Page):
    await build_app(page, DesktopPlatform(page))


if __name__ == "__main__":
    ft.run(main)
