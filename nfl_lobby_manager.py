"""
nfl_lobby_manager.py — NFL Multiplayer Lobby State
===================================================
Mirror of lobby_manager.py but backed by nfl_game + nfl_data.
Keeps a separate in-memory store so NFL and MLB lobbies never collide.

Public API:
    create_lobby(host_name)                    → lobby_id, player_id
    join_lobby(lobby_id, name)                 → player_id  (or LobbyError)
    start_game(lobby_id, player_id)            → prompts    (host only)
    submit_score(lobby_id, player_id, result)  → updated scoreboard
    mark_ready_for_rematch(lobby_id, player_id)→ ready_count dict
    reset_lobby(lobby_id)                      → None
    get_lobby(lobby_id)                        → lobby dict (deep copy)
    get_player(lobby_id, player_id)            → player dict
    lobby_exists(lobby_id)                     → bool
    is_host(lobby_id, player_id)               → bool
    build_scoreboard(lobby_id)                 → scoreboard dict
    cleanup_stale_lobbies()                    → count removed
"""

import uuid
import time
import logging
from copy import deepcopy

import nfl_game as game_logic
from nfl_data import STAT_CONFIG, generate_prompts, random_stat_key

logger = logging.getLogger(__name__)


MAX_PLAYERS  = 10
MAX_AGE_SECS = 60 * 60 * 3
LOBBY_ID_LEN = 8


class LobbyError(Exception):
    """Raised for invalid lobby operations."""
    pass


_lobbies: dict[str, dict] = {}


def _make_lobby_id() -> str:
    chars = "23456789abcdefghjkmnpqrstuvwxyz"
    raw   = uuid.uuid4().hex
    return "".join(chars[int(c, 16) % len(chars)] for c in raw[:LOBBY_ID_LEN])


def _make_player_id() -> str:
    return uuid.uuid4().hex


def _player_entry(name: str, is_host: bool = False) -> dict:
    return {
        "name":              name.strip()[:24],
        "is_host":           is_host,
        "status":            "waiting",
        "score":             None,
        "efficiency":        None,
        "grade":             None,
        "picks":             [],
        "ready_for_rematch": False,
        "joined_at":         time.time(),
    }


def create_lobby(host_name: str) -> tuple[str, str]:
    cleanup_stale_lobbies()

    lobby_id  = _make_lobby_id()
    player_id = _make_player_id()

    _lobbies[lobby_id] = {
        "lobby_id":   lobby_id,
        "host_id":    player_id,
        "status":     "waiting",
        "stat_key":   None,
        "stat_label": None,
        "prompts":    [],
        "players":    {player_id: _player_entry(host_name, is_host=True)},
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    logger.info("NFL lobby created: %s by %s", lobby_id, host_name)
    return lobby_id, player_id


def join_lobby(lobby_id: str, name: str) -> str:
    lobby = _get_lobby_raw(lobby_id)

    if lobby["status"] != "waiting":
        raise LobbyError("This game has already started.")

    if len(lobby["players"]) >= MAX_PLAYERS:
        raise LobbyError(f"Lobby is full ({MAX_PLAYERS} players max).")

    existing_names = {p["name"].lower() for p in lobby["players"].values()}
    clean_name = name.strip()[:24]
    if clean_name.lower() in existing_names:
        raise LobbyError(f'The name "{clean_name}" is already taken in this lobby.')

    player_id = _make_player_id()
    lobby["players"][player_id] = _player_entry(clean_name)
    lobby["updated_at"] = time.time()

    logger.info("Player %s joined NFL lobby %s", clean_name, lobby_id)
    return player_id


def start_game(lobby_id: str, player_id: str) -> list[dict]:
    lobby = _get_lobby_raw(lobby_id)

    if lobby["host_id"] != player_id:
        raise LobbyError("Only the host can start the game.")

    if lobby["status"] != "waiting":
        raise LobbyError("Game has already started.")

    if len(lobby["players"]) < 1:
        raise LobbyError("Need at least 1 player to start.")

    stat_key = random_stat_key()
    prompts  = generate_prompts(stat_key, n=5)
    for i, p in enumerate(prompts):
        p["index"] = i

    lobby["stat_key"]   = stat_key
    lobby["stat_label"] = STAT_CONFIG[stat_key]["label"]
    lobby["prompts"]    = prompts
    lobby["status"]     = "playing"
    lobby["updated_at"] = time.time()

    for p in lobby["players"].values():
        p["status"] = "playing"

    logger.info("NFL lobby %s started: stat=%s", lobby_id, stat_key)
    return prompts


def mark_ready_for_rematch(lobby_id: str, player_id: str) -> dict:
    lobby  = _get_lobby_raw(lobby_id)
    player = _get_player_raw(lobby, player_id)

    if lobby["status"] != "finished":
        raise LobbyError("Cannot ready up — game is not finished yet.")

    player["ready_for_rematch"] = True
    lobby["updated_at"] = time.time()

    ready_count = sum(1 for p in lobby["players"].values() if p["ready_for_rematch"])
    total       = len(lobby["players"])
    all_ready   = ready_count == total

    logger.info("Player %s ready for rematch in NFL lobby %s (%d/%d)",
                player_id, lobby_id, ready_count, total)
    return {"ready_count": ready_count, "total_players": total, "all_ready": all_ready}


def reset_lobby(lobby_id: str) -> None:
    lobby = _get_lobby_raw(lobby_id)

    if lobby["status"] != "finished":
        raise LobbyError("Can only reset a finished lobby.")

    lobby["status"]     = "waiting"
    lobby["stat_key"]   = None
    lobby["stat_label"] = None
    lobby["prompts"]    = []
    lobby["updated_at"] = time.time()

    for p in lobby["players"].values():
        p["status"]            = "waiting"
        p["score"]             = None
        p["efficiency"]        = None
        p["grade"]             = None
        p["picks"]             = []
        p["ready_for_rematch"] = False

    logger.info("NFL lobby %s reset for rematch", lobby_id)


def submit_score(lobby_id: str, player_id: str, result: dict) -> dict:
    lobby  = _get_lobby_raw(lobby_id)
    player = _get_player_raw(lobby, player_id)

    player["status"]     = "finished"
    player["score"]      = result["total_score"]
    player["efficiency"] = result["efficiency"]
    player["grade"]      = result["grade"]
    player["picks"]      = result.get("slots", [])
    lobby["updated_at"]  = time.time()

    all_done = all(p["status"] == "finished" for p in lobby["players"].values())
    if all_done:
        lobby["status"] = "finished"
        logger.info("NFL lobby %s finished — all players done", lobby_id)

    return build_scoreboard(lobby_id)


def get_lobby(lobby_id: str) -> dict:
    return deepcopy(_get_lobby_raw(lobby_id))


def get_player(lobby_id: str, player_id: str) -> dict:
    lobby = _get_lobby_raw(lobby_id)
    return deepcopy(_get_player_raw(lobby, player_id))


def lobby_exists(lobby_id: str) -> bool:
    return lobby_id in _lobbies


def is_host(lobby_id: str, player_id: str) -> bool:
    try:
        return _get_lobby_raw(lobby_id)["host_id"] == player_id
    except LobbyError:
        return False


def build_scoreboard(lobby_id: str) -> dict:
    lobby   = _get_lobby_raw(lobby_id)
    players = lobby["players"]

    rows = []
    for pid, p in players.items():
        rows.append({
            "player_id":  pid,
            "name":       p["name"],
            "is_host":    p["is_host"],
            "status":     p["status"],
            "score":      p["score"],
            "efficiency": p["efficiency"],
            "grade":      p["grade"],
            "picks":      p["picks"],
        })

    stat_key  = lobby.get("stat_key")
    ascending = STAT_CONFIG[stat_key]["ascending"] if stat_key and stat_key in STAT_CONFIG else False

    finished   = sorted([r for r in rows if r["status"] == "finished"],
                        key=lambda r: r["score"] or 0, reverse=not ascending)
    unfinished = sorted([r for r in rows if r["status"] != "finished"],
                        key=lambda r: r["name"])

    ranked = []
    for i, r in enumerate(finished, 1):
        r["rank"] = i
        ranked.append(r)
    for r in unfinished:
        r["rank"] = None
        ranked.append(r)

    return {
        "lobby_id":      lobby_id,
        "status":        lobby["status"],
        "stat_key":      lobby["stat_key"],
        "stat_label":    lobby["stat_label"],
        "players":       ranked,
        "all_done":      lobby["status"] == "finished",
        "total_players": len(players),
        "done_count":    sum(1 for p in players.values() if p["status"] == "finished"),
    }


def cleanup_stale_lobbies() -> int:
    now   = time.time()
    stale = [lid for lid, l in _lobbies.items()
             if now - l.get("updated_at", 0) > MAX_AGE_SECS]
    for lid in stale:
        del _lobbies[lid]
    if stale:
        logger.info("Cleaned up %d stale NFL lobbies", len(stale))
    return len(stale)


def _get_lobby_raw(lobby_id: str) -> dict:
    lobby = _lobbies.get(lobby_id)
    if not lobby:
        raise LobbyError("Lobby not found. The link may have expired.")
    return lobby


def _get_player_raw(lobby: dict, player_id: str) -> dict:
    player = lobby["players"].get(player_id)
    if not player:
        raise LobbyError("Player not found in this lobby.")
    return player
