from typing import Dict, List, Optional, Set

from src.core.pricing import has_unreleased_keyword


# ==========================================
# DATA MODELS
# ==========================================
class Player:
    def __init__(self, steam_id: str, display_name: str = "", avatar_url: str = ""):
        self.steam_id = steam_id
        self.display_name = display_name or steam_id
        self.avatar_url = avatar_url
        self.owned_games: Dict[int, str] = {}     # appid -> game_name
        self.wishlist_games: Dict[int, str] = {}  # appid -> game_name
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
        self.is_unavailable: bool = False
        self.header_image_url: str = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
        self.price_str: str = ""
        self.final_price: Optional[float] = None  # Numeric price in dollars AFTER applied discounts
        self.discount_pct: int = 0
        self.review_percentage: Optional[int] = None  # 0..100 positive user review percentage
        self.review_desc: str = ""
        self.price_fetched: bool = False
        self.store_info_fetched: bool = False
        self.details_fetched: bool = False  # Tracks single-game appdetails fallback completion

    @property
    def is_effectively_unreleased(self) -> bool:
        """
        Authoritative check for whether this game is unreleased.
        True if Steam flagged it (`is_unreleased`) or if release_date / price_str contains an
        unreleased keyword ("Coming soon", "TBA", ...). Dates and years in the text are not parsed.
        """
        return self.is_unreleased or has_unreleased_keyword(self.release_date) or has_unreleased_keyword(self.price_str)

    @property
    def is_effectively_unavailable(self) -> bool:
        """
        Authoritative check for whether this game is unavailable/delisted.
        Ensures unreleased games are never misclassified as unavailable.
        """
        if self.is_effectively_unreleased:
            return False
        return bool(self.is_unavailable)

    @property
    def is_on_sale(self) -> bool:
        """A game with any discount is on sale."""
        return self.discount_pct > 0

    @property
    def is_free(self) -> bool:
        """Returns True if the game is released and Free to Play ($0.00)."""
        if self.is_effectively_unreleased:
            return False
        return (self.final_price is not None and self.final_price == 0.0) or ("free" in self.price_str.lower())

    @property
    def is_coop(self) -> bool:
        """Checks dynamically resolved categories & tags for Co-op support."""
        all_labels = self.categories | self.genres
        return any("co-op" in c.lower() or "coop" in c.lower() for c in all_labels)

    @property
    def is_multiplayer(self) -> bool:
        """Checks dynamically resolved categories & tags for Multiplayer / PvP support."""
        all_labels = self.categories | self.genres
        return any(
            "multiplayer" in c.lower() or "multi-player" in c.lower() or "pvp" in c.lower()
            for c in all_labels
        )

    @property
    def is_online(self) -> bool:
        """Checks categories & tags for online play (Online Co-op / Online PvP / MMO), excluding local-only co-op."""
        all_labels = self.categories | self.genres
        return any(
            "online" in c.lower() or "mmo" in c.lower() or "massively multiplayer" in c.lower()
            for c in all_labels
        )

    @property
    def is_vr(self) -> bool:
        """Checks dynamically resolved categories & tags for VR support."""
        all_labels = self.categories | self.genres
        return any(
            c.lower() == "vr"
            or "vr " in c.lower()
            or " vr" in c.lower()
            or "virtual reality" in c.lower()
            for c in all_labels
        )
