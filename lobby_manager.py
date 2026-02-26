"""
lobby_manager.py — Multiplayer Lobby State
==========================================
Manages in-memory lobby state for multiplayer games.
Lobbies are stored in a module-level dict — they survive as long as
the server process is running. If you need lobbies to survive restarts,
swap _lobbies for a SQLite-backed store later.

Public API:
    create_lobby(host_name)         → lobby_id, player_id
    join_lobby(lobby_id, name)      → player_id  (or raises LobbyError)
    start_game(lobby_id, player_id) → prompts    (host only)
    submit_score(lobby_id, player_id, result) → updated scoreboard
    get_lobby(lobby_id)             → lobby dict (safe copy)
    get_player(lobby_id, player_id) → player dict
    cleanup_stale_lobbies()         → removes lobbies older than MAX_AGE
"""

import uuid
import time
import logging
from copy import deepcopy

import game as game_logic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_PLAYERS    = 10
MAX_AGE_SECS   = 60 * 60 * 3   # lobbies expire after 3 hours of inactivity
LOBBY_ID_LEN   = 8              # characters in the shareable lobby code

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LobbyError(Exception):
    """Raised for invalid lobby operations."""
    pass

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_lobbies: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lobby_id() -> str:
    """Generate a short, URL-safe lobby ID."""
    # Use only unambiguous chars (no 0/O, 1/l/I)
    chars = "23456789abcdefghjkmnpqrstuvwxyz"
    raw   = uuid.uuid4().hex
    return "".join(chars[int(c, 16) % len(chars)] for c in raw[:LOBBY_ID_LEN])


def _make_player_id() -> str:
    return uuid.uuid4().hex


def _player_entry(name: str, is_host: bool = False) -> dict:
    return {
        "name":       name.strip()[:24],   # cap display name length
        "is_host":    is_host,
        "status":     "waiting",           # waiting | playing | finished
        "score":      None,
        "efficiency": None,
        "grade":      None,
        "picks":      [],
        "joined_at":  time.time(),
    }

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_lobby(host_name: str) -> tuple[str, str]:
    """
    Create a new lobby. Returns (lobby_id, host_player_id).
    """
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

    logger.info("Lobby created: %s by %s", lobby_id, host_name)
    return lobby_id, player_id


def join_lobby(lobby_id: str, name: str) -> str:
    """
    Add a player to an existing lobby. Returns the new player_id.
    Raises LobbyError if lobby doesn't exist, is full, or already started.
    """
    lobby = _get_lobby_raw(lobby_id)

    if lobby["status"] != "waiting":
        raise LobbyError("This game has already started.")

    if len(lobby["players"]) >= MAX_PLAYERS:
        raise LobbyError(f"Lobby is full ({MAX_PLAYERS} players max).")

    # Prevent duplicate display names
    existing_names = {p["name"].lower() for p in lobby["players"].values()}
    clean_name     = name.strip()[:24]
    if clean_name.lower() in existing_names:
        raise LobbyError(f'The name "{clean_name}" is already taken in this lobby.')

    player_id = _make_player_id()
    lobby["players"][player_id] = _player_entry(clean_name)
    lobby["updated_at"] = time.time()

    logger.info("Player %s joined lobby %s", clean_name, lobby_id)
    return player_id


def start_game(lobby_id: str, player_id: str) -> list[dict]:
    """
    Start the game. Only the host can call this.
    Generates shared prompts and moves lobby to 'playing'.
    Returns the list of prompts.
    Raises LobbyError if caller is not host or lobby not in waiting state.
    """
    lobby = _get_lobby_raw(lobby_id)

    if lobby["host_id"] != player_id:
        raise LobbyError("Only the host can start the game.")

    if lobby["status"] != "waiting":
        raise LobbyError("Game has already started.")

    if len(lobby["players"]) < 1:
        raise LobbyError("Need at least 1 player to start.")

    # Generate shared game config — same stat + prompts for everyone
    stat_key = game_logic.STAT_CONFIG_KEYS[
        __import__('random').randint(0, len(game_logic.STAT_CONFIG_KEYS) - 1)
    ] if hasattr(game_logic, 'STAT_CONFIG_KEYS') else __import__('data').random_stat_key()

    from data import generate_prompts
    prompts = generate_prompts(stat_key, n=5)
    for i, p in enumerate(prompts):
        p["index"] = i

    lobby["stat_key"]   = stat_key
    lobby["stat_label"] = __import__('data').STAT_CONFIG[stat_key]["label"]
    lobby["prompts"]    = prompts
    lobby["status"]     = "playing"
    lobby["updated_at"] = time.time()

    # Move all players to 'playing'
    for p in lobby["players"].values():
        p["status"] = "playing"

    logger.info("Lobby %s started: stat=%s", lobby_id, stat_key)
    return prompts


def submit_score(lobby_id: str, player_id: str, result: dict) -> dict:
    """
    Record a player's final score. Returns the updated scoreboard.
    """
    lobby  = _get_lobby_raw(lobby_id)
    player = _get_player_raw(lobby, player_id)

    player["status"]     = "finished"
    player["score"]      = result["total_score"]
    player["efficiency"] = result["efficiency"]
    player["grade"]      = result["grade"]
    player["picks"]      = result.get("slots", [])
    lobby["updated_at"]  = time.time()

    # Check if all players are done
    all_done = all(p["status"] == "finished" for p in lobby["players"].values())
    if all_done:
        lobby["status"] = "finished"
        logger.info("Lobby %s finished — all players done", lobby_id)

    return build_scoreboard(lobby_id)


def get_lobby(lobby_id: str) -> dict:
    """Return a safe deep copy of the lobby state."""
    return deepcopy(_get_lobby_raw(lobby_id))


def get_player(lobby_id: str, player_id: str) -> dict:
    """Return a safe copy of a single player's state."""
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
    """
    Build the current scoreboard for a lobby.
    Returns a dict safe to JSON-serialise and broadcast.
    """
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
        })

    # Sort: finished players by score desc, then unfinished alphabetically
    finished   = sorted([r for r in rows if r["status"] == "finished"],
                        key=lambda r: r["score"] or 0, reverse=True)
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
        "lobby_id":    lobby_id,
        "status":      lobby["status"],
        "stat_label":  lobby["stat_label"],
        "players":     ranked,
        "all_done":    lobby["status"] == "finished",
        "total_players": len(players),
        "done_count":  sum(1 for p in players.values() if p["status"] == "finished"),
    }


def cleanup_stale_lobbies() -> int:
    """Remove lobbies older than MAX_AGE_SECS. Returns count removed."""
    now     = time.time()
    stale   = [lid for lid, l in _lobbies.items()
                if now - l.get("updated_at", 0) > MAX_AGE_SECS]
    for lid in stale:
        del _lobbies[lid]
    if stale:
        logger.info("Cleaned up %d stale lobbies", len(stale))
    return len(stale)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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