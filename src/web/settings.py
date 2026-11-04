"""Web server settings, read once from environment variables at import time.

Ported from the ``web`` branch. ``STEAM_API_KEY`` (a server-owned key) is dropped: each user
enters their own key, which stays in their browser storage.
"""
import os

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8181


_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("0", "false", "no", "off")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    if raw.lower() in _TRUE_VALUES:
        return True
    if raw.lower() in _FALSE_VALUES:
        return False
    print(f"Warning: Ignoring unrecognised {name}={raw!r}, using {'1' if default else '0'}")
    return default


def _env_port(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        print(f"Warning: Ignoring invalid {name}={raw!r}, using {default}")
        return default
    if not 0 < port < 65536:
        print(f"Warning: Ignoring out-of-range {name}={raw!r}, using {default}")
        return default
    return port


HOST = (os.environ.get("HOST") or "").strip() or DEFAULT_HOST
# Render/Railway/Fly inject PORT automatically
PORT = _env_port("PORT", DEFAULT_PORT)

# Optional Discord Application ID. Normally the handshake script reads it from the
# <client_id>.discordsays.com hostname. The server inserts only its digits into the script.
DISCORD_CLIENT_ID = (os.environ.get("DISCORD_CLIENT_ID") or "").strip()

# Serve Steam thumbnails through this server (same-origin). Needed inside Discord (its sandbox blocks
# third-party image hosts) and avoids browser CORS issues for Flutter web. Set to "0"/"false"/"no"/"off"
# to disable.
PROXY_STEAM_IMAGES = _env_bool("PROXY_STEAM_IMAGES", True)
