"""Squad codes: a player list as a short, copy-pasteable string. Never contains the API key."""
import base64
import json
import re
import zlib
from typing import List, Set, Tuple

SQUAD_CODE_PREFIX = "SSF1:"
MAX_PLAYERS = 500
MAX_INPUT_CHARS = 1_000_000


def sanitize_player_entries(raw_players) -> List[dict]:
    """Valid, de-duplicated players. Accepts both the long and the compact (i/n/e) keys."""
    cleaned: List[dict] = []
    seen: Set[str] = set()
    if not isinstance(raw_players, list):
        return cleaned
    for item in raw_players:
        if len(cleaned) >= MAX_PLAYERS:
            break
        if not isinstance(item, dict):
            continue
        sid = str(item.get("steam_id", item.get("i", "")) or "").strip()
        if not re.fullmatch(r"[0-9]{17}", sid) or sid in seen:
            continue
        name = str(item.get("display_name", item.get("n", "")) or "").strip()[:64] or sid
        enabled_raw = item.get("enabled", item.get("e", True))
        cleaned.append({"steam_id": sid, "display_name": name, "enabled": bool(enabled_raw)})
        seen.add(sid)
    return cleaned


def merge_players(existing, imported) -> Tuple[List[dict], int, int]:
    """Returns (merged, added, skipped). Existing players win on duplicates; `skipped` were cut by MAX_PLAYERS."""
    existing_clean = sanitize_player_entries(existing)
    existing_ids = {p["steam_id"] for p in existing_clean}
    new_entries = [p for p in sanitize_player_entries(imported) if p["steam_id"] not in existing_ids]
    merged = sanitize_player_entries(existing_clean + new_entries)
    added = len(merged) - len(existing_clean)
    return merged, added, len(new_entries) - added


def encode_squad_code(players: List[dict]) -> str:
    compact = [
        {"i": p["steam_id"], "n": p.get("display_name", ""), "e": 1 if p.get("enabled", True) else 0}
        for p in sanitize_player_entries(players)
    ]
    raw = json.dumps(compact, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return SQUAD_CODE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def decode_squad_code(text: str) -> List[dict]:
    """Decodes a squad code or a config file's JSON into a player list. Raises ValueError."""
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("The pasted text is too long to be a squad code or config JSON.")
    text = text.strip()
    if not text:
        raise ValueError("Nothing to import.")
    try:
        if text.upper().startswith(SQUAD_CODE_PREFIX):
            payload = re.sub(r"\s+", "", text[len(SQUAD_CODE_PREFIX):])
            payload += "=" * (-len(payload) % 4)
            decompressor = zlib.decompressobj()
            raw = decompressor.decompress(base64.urlsafe_b64decode(payload), 2_000_000)
            data = json.loads(raw.decode("utf-8"))
        else:
            data = json.loads(text)
    except Exception as err:
        raise ValueError("This doesn't look like a valid squad code or config JSON.") from err

    if isinstance(data, dict):
        data = data.get("players", [])
    if not isinstance(data, list):
        raise ValueError("No player list found in the pasted text.")
    players = sanitize_player_entries(data)
    if not players:
        raise ValueError("No valid players (17-digit SteamID64s) found in the pasted text.")
    return players
