import base64
import json
import zlib

import pytest

from src.core.squad_code import (
    SQUAD_CODE_PREFIX,
    decode_squad_code,
    MAX_PLAYERS,
    encode_squad_code,
    merge_players,
    sanitize_player_entries,
)

SID_A = "76561197960287930"
SID_B = "76561198000000001"
SID_C = "76561198000000002"


def _player(sid, name="Player", enabled=True):
    return {"steam_id": sid, "display_name": name, "enabled": enabled}


def _code_from_bytes(raw: bytes) -> str:
    return SQUAD_CODE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _code_from_json_text(text: str) -> str:
    return _code_from_bytes(zlib.compress(text.encode("utf-8"), 9))


# ---------- encode / decode ----------

def test_encoded_string_starts_with_prefix():
    assert encode_squad_code([_player(SID_A)]).startswith("SSF1:")


def test_round_trip_returns_sanitized_players():
    players = [
        _player(SID_A, "Alice", True),
        _player(SID_B, "Bob", False),
        _player(SID_C, "Ünïcödé 名前", True),
    ]
    decoded = decode_squad_code(encode_squad_code(players))
    assert decoded == sanitize_player_entries(players)
    assert decoded == players


def test_decode_tolerates_inner_whitespace_and_newlines():
    code = encode_squad_code([_player(SID_A), _player(SID_B)])
    body = code[len(SQUAD_CODE_PREFIX):]
    broken = SQUAD_CODE_PREFIX + "\n".join(body[i:i + 5] for i in range(0, len(body), 5))
    broken = broken.replace("\n", " \r\n\t", 2)
    assert decode_squad_code(broken) == [_player(SID_A), _player(SID_B)]


@pytest.mark.parametrize("text", ["", "   \n "])
def test_decode_empty_input_raises(text):
    with pytest.raises(ValueError, match="Nothing to import"):
        decode_squad_code(text)


def test_decode_rejects_input_over_one_million_chars():
    with pytest.raises(ValueError, match="too long"):
        decode_squad_code("x" * 1_000_001)


def test_decode_zlib_bomb_raises_value_error():
    # Valid JSON that inflates to ~3 MB; the code itself is only a few KB.
    bomb_json = "[" + " " * 3_000_000 + json.dumps({"i": SID_A}) + "]"
    code = _code_from_json_text(bomb_json)
    assert len(code) < 20_000
    with pytest.raises(ValueError):
        decode_squad_code(code)


_INVALID = "doesn't look like a valid squad code"


@pytest.mark.parametrize(
    "text, message",
    [
        ("hello world, this is not a code", _INVALID),
        ("SSF1:!!!not*base64###", _INVALID),
        ("SSF1:A", _INVALID),  # length 1 mod 4 -> invalid base64 padding
        ("SSF1:====", _INVALID),
        ("SSF1:" + "\x00" * 8, _INVALID),
        (_code_from_bytes(b"definitely not zlib data"), _INVALID),
        (_code_from_json_text("{not json"), _INVALID),
        ("[", _INVALID),
        (_code_from_bytes(zlib.compress(b"\xff\xfe\xfd")), _INVALID),  # not utf-8
        ("{}", "No valid players"),
    ],
    ids=[
        "random-text", "bad-base64", "bad-base64-length", "only-padding", "nul-bytes",
        "bad-zlib", "bad-json", "truncated-raw-json", "bad-utf8", "empty-dict",
    ],
)
def test_decode_garbage_raises_value_error_with_message(text, message):
    # Every failure surfaces as ValueError (never a raw binascii/zlib/json error).
    with pytest.raises(ValueError, match=message):
        decode_squad_code(text)


def test_decode_list_without_valid_players_raises():
    with pytest.raises(ValueError, match="No valid players"):
        decode_squad_code(_code_from_json_text('[{"i": "123"}, 5, "x"]'))


def test_decode_without_prefix_parses_raw_json_list():
    text = json.dumps([_player(SID_A, "Alice"), {"i": SID_B, "n": "Bob", "e": 0}])
    assert decode_squad_code(text) == [_player(SID_A, "Alice"), _player(SID_B, "Bob", False)]


def test_decode_without_prefix_config_dict_returns_only_players():
    key = "0123456789abcdefABCDEF0123456789"
    text = json.dumps({"api_key": key, "players": [_player(SID_A)]})
    assert decode_squad_code(text) == [_player(SID_A)]


# ---------- sanitize_player_entries ----------


@pytest.mark.parametrize(
    "sid",
    ["", "123", "7656119796028793", "765611979602879300", "7656119796028793a", None],
)
def test_sanitize_drops_invalid_ids(sid):
    assert sanitize_player_entries([_player(sid)]) == []


def test_sanitize_removes_duplicates_first_wins():
    raw = [_player(SID_A, "First", True), _player(SID_A, "Second", False), _player(SID_B)]
    assert sanitize_player_entries(raw) == [_player(SID_A, "First", True), _player(SID_B)]


@pytest.mark.parametrize("name", ["", "   ", None])
def test_sanitize_empty_name_falls_back_to_id(name):
    assert sanitize_player_entries([_player(SID_A, name)])[0]["display_name"] == SID_A


def test_sanitize_caps_at_500_players():
    raw = [_player(str(76561198000000000 + i)) for i in range(600)]
    result = sanitize_player_entries(raw)
    assert len(result) == 500
    assert result[-1]["steam_id"] == str(76561198000000000 + 499)


def test_sanitize_accepts_compact_keys():
    raw = [{"i": SID_A, "n": "Alice", "e": 0}]
    assert sanitize_player_entries(raw) == [_player(SID_A, "Alice", False)]


def test_sanitize_enabled_defaults_true_and_is_coerced_with_bool():
    raw = [
        {"steam_id": SID_A},
        {"steam_id": SID_B, "enabled": 0},
        {"steam_id": SID_C, "enabled": "false"},  # truthy string -> True
    ]
    assert [p["enabled"] for p in sanitize_player_entries(raw)] == [True, False, True]


# ---------- merge_players ----------

def _p(i, name=None, enabled=True):
    return {"steam_id": f"7656119{i:010d}", "display_name": name or f"P{i}", "enabled": enabled}


def test_merge_adds_new_players_after_existing():
    merged, added, skipped = merge_players([_p(1)], [_p(2), _p(3)])
    assert [p["steam_id"] for p in merged] == [_p(1)["steam_id"], _p(2)["steam_id"], _p(3)["steam_id"]]
    assert (added, skipped) == (2, 0)


def test_merge_existing_player_wins_on_duplicate():
    merged, added, skipped = merge_players([_p(1, "Mine", enabled=False)], [_p(1, "Theirs", enabled=True)])
    assert merged == [_p(1, "Mine", enabled=False)]
    assert (added, skipped) == (0, 0)


def test_merge_applies_cap_and_counts_skipped():
    existing = [_p(i) for i in range(MAX_PLAYERS - 2)]
    imported = [_p(i) for i in range(MAX_PLAYERS - 3, MAX_PLAYERS + 3)]  # 1 duplicate + 5 new
    merged, added, skipped = merge_players(existing, imported)
    assert len(merged) == MAX_PLAYERS
    assert (added, skipped) == (2, 3)
    assert merged[-1]["steam_id"] == _p(MAX_PLAYERS - 1)["steam_id"]


