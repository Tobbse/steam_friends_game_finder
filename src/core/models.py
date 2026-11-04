from typing import Dict, List, Optional, Set

from src.core.pricing import has_unreleased_keyword


class Player:
    def __init__(self, steam_id: str, display_name: str = ""):
        self.steam_id = steam_id
        self.display_name = display_name or steam_id
        self.owned_games: Dict[int, str] = {}  # appid -> name
        self.wishlist_games: Dict[int, str] = {}  # appid -> name
        self.is_private: bool = False
        self.fetch_error: Optional[str] = None
        self.library_fetched: bool = False


class GameItem:
    def __init__(self, appid: int, name: str):
        self.appid = appid
        self.name = name
        self.owners: List[Player] = []
        self.wishlisters: List[Player] = []
        self.categories: Set[str] = set()
        self.genres: Set[str] = set()
        self.mac_supported: Optional[bool] = None
        self.windows_supported: Optional[bool] = None
        self.linux_supported: Optional[bool] = None
        self.short_description: str = ""
        self.developers: List[str] = []
        self.release_date: str = ""
        self.is_unreleased: bool = False
        self.is_unavailable: bool = False  # delisted from the store
        self.header_image_url: str = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
        self.price_str: str = ""
        self.final_price: Optional[float] = None  # dollars, after discount
        self.discount_pct: int = 0
        self.review_percentage: Optional[int] = None  # % positive
        self.review_desc: str = ""
        self.price_fetched: bool = False
        self.store_info_fetched: bool = False
        self.details_fetched: bool = False

    def _has_label(self, *needles: str) -> bool:
        return any(n in label.lower() for label in self.categories | self.genres for n in needles)

    @property
    def is_effectively_unreleased(self) -> bool:
        """Steam's flag, or a "Coming soon" / "TBA" style release date or price text."""
        return self.is_unreleased or has_unreleased_keyword(self.release_date) or has_unreleased_keyword(self.price_str)

    @property
    def is_effectively_unavailable(self) -> bool:
        """Unreleased games are never counted as unavailable."""
        return self.is_unavailable and not self.is_effectively_unreleased

    @property
    def is_on_sale(self) -> bool:
        return self.discount_pct > 0

    @property
    def is_coop(self) -> bool:
        return self._has_label("co-op", "coop")

    @property
    def is_multiplayer(self) -> bool:
        return self._has_label("multiplayer", "multi-player", "pvp")

    @property
    def is_online(self) -> bool:
        return self._has_label("online", "mmo", "massively multiplayer")

    @property
    def is_vr(self) -> bool:
        return any(
            label.lower() == "vr" or "vr " in label.lower() or " vr" in label.lower() or "virtual reality" in label.lower()
            for label in self.categories | self.genres
        )
