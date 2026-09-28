"""
imposter_manager.py — "Imposter" Word Social Deduction: State + Logic
=====================================================================
A minimal, proven social-deduction design (in the family of *The
Chameleon* / *Spyfall*): every player is dealt the same secret word from
a shown category — except one randomly chosen **imposter**, who is only
told the category, not the word. Players take turns giving a single
one-word clue about the word; the imposter must bluff a fitting clue
from the category alone. The group then votes on who the imposter is. If
the imposter is caught they get one chance to guess the secret word and
steal the win.

Design goals (matching the rest of this project's multiplayer games):
  * Minimal per-player state — a name, a score, a connection flag.
  * All authority lives on the server; clients render whatever state
    they're handed. The secret word and imposter identity never appear
    in the public state until the reveal.

State lives in a module-level dict, like the other *_manager modules —
it survives as long as the server process does. Swap _games for a
SQLite-backed store later if persistence is needed.

Public API:
    create_game(host_name)                 -> game_id, player_id
    join_game(game_id, name)               -> player_id
    get_game(game_id)                       -> game dict (safe copy) or None
    public_state(game)                      -> client-safe dict (no secrets)
    role_for(game, player_id)               -> {role, category, word|None}
    start_round(game_id, player_id)         -> None  (host only)
    submit_clue(game_id, player_id, clue)   -> None
    cast_vote(game_id, player_id, suspect)  -> None
    submit_guess(game_id, player_id, guess) -> None  (imposter, if caught)
    next_round(game_id, player_id)          -> None  (host only; back to lobby)
    cleanup_stale_games()                   -> count removed
"""

import uuid
import time
import random
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_PLAYERS   = 3               # imposter + at least two crew for real deduction
MAX_PLAYERS   = 12
GAME_ID_LEN   = 5
MAX_AGE_SECS  = 60 * 60 * 3     # games expire after 3 hours of inactivity
NAME_MAX      = 20

# Scoring
CREW_CATCH_PTS   = 2   # each crew member, when the imposter is caught and fails to guess
IMPOSTER_ESCAPE  = 3   # imposter, when not voted out
IMPOSTER_STEAL   = 2   # imposter, when caught but guesses the word correctly

# ---------------------------------------------------------------------------
# Word bank — shown category, hidden word. Curated for one-word cluing.
# ---------------------------------------------------------------------------

WORD_BANK: dict[str, list[str]] = {
    "Animals":      ["Elephant", "Penguin", "Octopus", "Kangaroo", "Cheetah",
                     "Dolphin", "Hedgehog", "Falcon", "Rhino", "Otter", "Koala", "Walrus"],
    "Foods":        ["Pizza", "Sushi", "Pancake", "Burrito", "Lasagna",
                     "Popcorn", "Waffle", "Dumpling", "Bagel", "Taco", "Meatball", "Nachos"],
    "Sports":       ["Basketball", "Tennis", "Boxing", "Surfing", "Archery",
                     "Bowling", "Hockey", "Golf", "Rowing", "Fencing", "Skiing", "Cricket"],
    "Movies":       ["Titanic", "Jaws", "Frozen", "Gladiator", "Avatar",
                     "Rocky", "Inception", "Shrek", "Casablanca", "Alien", "Up", "Grease"],
    "Jobs":         ["Firefighter", "Astronaut", "Plumber", "Barber", "Chef",
                     "Lawyer", "Farmer", "Pilot", "Dentist", "Lifeguard", "Janitor", "Nurse"],
    "Places":       ["Beach", "Airport", "Casino", "Library", "Volcano",
                     "Museum", "Desert", "Stadium", "Hospital", "Prison", "Aquarium", "Bakery"],
    "Household":    ["Toaster", "Umbrella", "Pillow", "Vacuum", "Mirror",
                     "Candle", "Blender", "Ladder", "Broom", "Kettle", "Doormat", "Clock"],
    "Nature":       ["Volcano", "Glacier", "Rainbow", "Waterfall", "Tornado",
                     "Canyon", "Swamp", "Meadow", "Iceberg", "Geyser", "Reef", "Cavern"],
    "Music":        ["Guitar", "Drums", "Violin", "Trumpet", "Piano",
                     "Harmonica", "Banjo", "Saxophone", "Flute", "Accordion", "Cello", "Tuba"],
    "Space":        ["Comet", "Galaxy", "Asteroid", "Nebula", "Rocket",
                     "Satellite", "Meteor", "Eclipse", "Telescope", "Astronaut", "Crater", "Orbit"],
    "Transport":    ["Submarine", "Helicopter", "Bicycle", "Tractor", "Ferry",
                     "Skateboard", "Ambulance", "Rickshaw", "Sailboat", "Gondola", "Scooter", "Blimp"],
    "Holidays":     ["Halloween", "Christmas", "Thanksgiving", "Easter", "Birthday",
                     "Wedding", "Carnival", "Fireworks", "Parade", "Picnic", "Costume", "Sleigh"],
}

CATEGORY_KEYS = list(WORD_BANK.keys())

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_games: dict[str, dict] = {}


class ImposterError(Exception):
    """Raised for invalid game operations (surfaced to the client)."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_game_id() -> str:
    """Short, URL-safe, unambiguous game code (no 0/O, 1/l/I)."""
    chars = "23456789abcdefghjkmnpqrstuvwxyz"
    raw = uuid.uuid4().hex
    gid = "".join(chars[int(c, 16) % len(chars)] for c in raw[:GAME_ID_LEN])
    while gid in _games:
        raw = uuid.uuid4().hex
        gid = "".join(chars[int(c, 16) % len(chars)] for c in raw[:GAME_ID_LEN])
    return gid


def _make_player_id() -> str:
    return uuid.uuid4().hex


def _clean_name(name: str) -> str:
    return (str(name).strip() if name is not None else "")[:NAME_MAX]


def _player_entry(name: str, is_host: bool = False) -> dict:
    return {
        "name":      name,
        "is_host":   is_host,
        "score":     0,
        "connected": True,
        "joined_at": time.time(),
    }


def _touch(game: dict) -> None:
    game["updated_at"] = time.time()


def _blank_round() -> dict:
    return {
        "number":         0,
        "category":       None,
        "word":           None,    # secret — never in public_state
        "imposter_id":    None,    # secret — never in public_state until reveal
        "reveal_order":   [],      # player_ids, randomized order clues are shown in
        "hint_round":     1,       # bumps each time a no-majority vote sends it back to clues
        "clues":          {},      # player_id -> clue word (submitted simultaneously)
        "votes":          {},      # voter_id -> suspect_id
        "abstains":       [],      # voter_ids who chose to abstain
        "imposter_guess": None,    # str, once the caught imposter guesses
        "result":         None,    # computed dict at reveal
    }


# ---------------------------------------------------------------------------
# Public API — lobby
# ---------------------------------------------------------------------------

def create_game(host_name: str) -> tuple[str, str]:
    """Create a new game in the lobby state. Returns (game_id, host_player_id)."""
    cleanup_stale_games()

    name = _clean_name(host_name) or "Host"
    game_id   = _make_game_id()
    player_id = _make_player_id()

    _games[game_id] = {
        "game_id":    game_id,
        "host_id":    player_id,
        "status":     "lobby",     # lobby | clues | voting | guessing | reveal
        "created_at": time.time(),
        "updated_at": time.time(),
        "players":    {player_id: _player_entry(name, is_host=True)},
        "order":      [player_id],  # stable seating order (join order)
        "round":      _blank_round(),
    }
    logger.info("Imposter game created: %s by %s", game_id, name)
    return game_id, player_id


def join_game(game_id: str, name: str) -> str:
    """Add a player to a lobby. Returns the new player_id."""
    game = _get_raw(game_id)

    if game["status"] != "lobby":
        raise ImposterError("This game is already in progress.")
    if len(game["players"]) >= MAX_PLAYERS:
        raise ImposterError(f"Game is full ({MAX_PLAYERS} players max).")

    clean = _clean_name(name)
    if not clean:
        raise ImposterError("Please enter a name.")
    taken = {p["name"].lower() for p in game["players"].values()}
    if clean.lower() in taken:
        raise ImposterError(f'The name "{clean}" is already taken in this game.')

    player_id = _make_player_id()
    game["players"][player_id] = _player_entry(clean)
    game["order"].append(player_id)
    _touch(game)
    logger.info("Player %s joined imposter game %s", clean, game_id)
    return player_id


def get_game(game_id: str) -> dict | None:
    g = _games.get(game_id)
    return deepcopy(g) if g else None


def game_exists(game_id: str) -> bool:
    return game_id in _games


def is_host(game_id: str, player_id: str) -> bool:
    g = _games.get(game_id)
    return bool(g and g["host_id"] == player_id)


def player_in_game(game_id: str, player_id: str) -> bool:
    g = _games.get(game_id)
    return bool(g and player_id in g["players"])


def set_connected(game_id: str, player_id: str, connected: bool) -> None:
    g = _games.get(game_id)
    if g and player_id in g["players"]:
        g["players"][player_id]["connected"] = connected
        _touch(g)


# ---------------------------------------------------------------------------
# Public API — round flow
# ---------------------------------------------------------------------------

def start_round(game_id: str, player_id: str) -> None:
    """
    Host starts a round: pick a category + word, assign one imposter, and
    randomize the cluing order. Moves status to 'clues'.
    """
    game = _get_raw(game_id)
    if game["host_id"] != player_id:
        raise ImposterError("Only the host can start the round.")
    if game["status"] not in ("lobby", "reveal"):
        raise ImposterError("A round is already in progress.")
    if len(game["players"]) < MIN_PLAYERS:
        raise ImposterError(f"Need at least {MIN_PLAYERS} players to start.")

    pids = list(game["order"])
    rng = random.Random(time.time_ns())

    category  = rng.choice(CATEGORY_KEYS)
    word      = rng.choice(WORD_BANK[category])
    imposter  = rng.choice(pids)
    # Clues are revealed together in a shuffled order, so the reveal order
    # doesn't hint at who sits where.
    reveal_order = pids[:]
    rng.shuffle(reveal_order)

    prev_number = game["round"]["number"]
    rnd = _blank_round()
    rnd.update({
        "number":       prev_number + 1,
        "category":     category,
        "word":         word,
        "imposter_id":  imposter,
        "reveal_order": reveal_order,
    })
    game["round"]  = rnd
    game["status"] = "clues"
    _touch(game)
    logger.info("Imposter game %s round %d started (category=%s)",
                game_id, rnd["number"], category)


def submit_clue(game_id: str, player_id: str, clue: str) -> None:
    """
    Record a player's single one-word clue. Clues are submitted privately and
    all revealed together, so nobody sees anyone else's word until everyone
    (still connected) has locked one in — that's what flips the round to voting.
    """
    game = _get_raw(game_id)
    if game["status"] != "clues":
        raise ImposterError("It's not time to give clues.")
    if player_id not in game["players"]:
        raise ImposterError("You're not in this game.")
    rnd = game["round"]

    if player_id in rnd["clues"]:
        raise ImposterError("You've already given your clue.")

    word = _normalize_clue(clue)
    if not word:
        raise ImposterError("Enter a one-word clue.")
    if " " in word:
        raise ImposterError("Clues must be a single word.")
    # A clue that simply is the secret word gives the imposter a free ride.
    if rnd["word"] and word.lower() == rnd["word"].lower():
        raise ImposterError("You can't use the secret word itself as your clue.")

    rnd["clues"][player_id] = word
    _touch(game)
    if _all_connected_clued(game):
        game["status"] = "voting"   # everyone's in — reveal clues, open voting


def force_reveal(game_id: str, player_id: str) -> None:
    """
    Host safety valve: reveal clues and open voting now, even if someone is
    slow or has dropped. Players who never submitted show a blank clue.
    """
    game = _get_raw(game_id)
    if game["host_id"] != player_id:
        raise ImposterError("Only the host can reveal early.")
    if game["status"] != "clues":
        raise ImposterError("There are no clues to reveal right now.")
    if not game["round"]["clues"]:
        raise ImposterError("Wait for at least one clue first.")
    game["status"] = "voting"
    _touch(game)


def _all_connected_clued(game: dict) -> bool:
    connected = [pid for pid, p in game["players"].items() if p["connected"]]
    if not connected:
        return False
    return all(pid in game["round"]["clues"] for pid in connected)


def cast_vote(game_id: str, player_id: str, suspect_id: str) -> None:
    """Record a vote for who the imposter is. Resolving is triggered once
    everyone present has either voted or abstained."""
    game = _get_raw(game_id)
    if game["status"] != "voting":
        raise ImposterError("It's not time to vote.")
    if player_id not in game["players"]:
        raise ImposterError("You're not in this game.")
    rnd = game["round"]

    if suspect_id not in game["players"]:
        raise ImposterError("That player isn't in the game.")
    if suspect_id == player_id:
        raise ImposterError("You can't vote for yourself.")

    rnd["votes"][player_id] = suspect_id
    if player_id in rnd["abstains"]:
        rnd["abstains"].remove(player_id)     # switching from abstain to a vote
    _touch(game)

    if _all_connected_acted(game):
        _resolve_votes(game)


def abstain_vote(game_id: str, player_id: str) -> None:
    """Record that a player abstains. Abstentions count toward 'everyone has
    acted' but never toward any suspect's total — so they make a majority
    harder to reach and can push the round back to another set of hints."""
    game = _get_raw(game_id)
    if game["status"] != "voting":
        raise ImposterError("It's not time to vote.")
    if player_id not in game["players"]:
        raise ImposterError("You're not in this game.")
    rnd = game["round"]

    rnd["votes"].pop(player_id, None)         # switching from a vote to abstain
    if player_id not in rnd["abstains"]:
        rnd["abstains"].append(player_id)
    _touch(game)

    if _all_connected_acted(game):
        _resolve_votes(game)


def recheck_after_disconnect(game_id: str, player_id: str) -> bool:
    """
    Called when a player disconnects. Marks them offline and, if the game was
    only waiting on them (to clue or to vote), advances the round. Returns True
    if the disconnect advanced game state (so the caller should re-broadcast).
    """
    game = _games.get(game_id)
    if not game or player_id not in game["players"]:
        return False
    game["players"][player_id]["connected"] = False
    _touch(game)
    if game["status"] == "clues" and game["round"]["clues"] and _all_connected_clued(game):
        game["status"] = "voting"
        return True
    if game["status"] == "voting" and _all_connected_acted(game):
        _resolve_votes(game)
        return True
    return False


def _all_connected_acted(game: dict) -> bool:
    """True once every present player has either voted or abstained."""
    connected = [pid for pid, p in game["players"].items() if p["connected"]]
    if not connected:
        return False
    rnd = game["round"]
    return all(pid in rnd["votes"] or pid in rnd["abstains"] for pid in connected)


def submit_guess(game_id: str, player_id: str, guess: str) -> None:
    """The caught imposter's single guess at the secret word."""
    game = _get_raw(game_id)
    if game["status"] != "guessing":
        raise ImposterError("There's no guess to make right now.")
    rnd = game["round"]
    if player_id != rnd["imposter_id"]:
        raise ImposterError("Only the imposter can guess the word.")

    rnd["imposter_guess"] = (str(guess).strip() if guess else "")[:NAME_MAX]
    _finalize(game)
    _touch(game)


def next_round(game_id: str, player_id: str) -> None:
    """Host returns a finished round to the lobby so scores carry over."""
    game = _get_raw(game_id)
    if game["host_id"] != player_id:
        raise ImposterError("Only the host can continue.")
    if game["status"] != "reveal":
        raise ImposterError("The round isn't finished yet.")
    game["status"] = "lobby"
    # Keep round["number"] so the next start increments it; clear the rest.
    n = game["round"]["number"]
    game["round"] = _blank_round()
    game["round"]["number"] = n
    _touch(game)


# ---------------------------------------------------------------------------
# Vote resolution & scoring
# ---------------------------------------------------------------------------

def _resolve_votes(game: dict) -> None:
    """
    Tally the votes. Someone is convicted only with a **majority** — strictly
    more than half of the players present (abstentions count toward the head
    count but not toward any suspect, so they raise the bar). If nobody reaches
    a majority, the round goes back for another set of hints. If the convicted
    player is the imposter they get one guess at the word ('guessing');
    convicting an innocent instead hands the imposter the win.
    """
    rnd = game["round"]
    present = sum(1 for p in game["players"].values() if p["connected"])

    tally: dict[str, int] = {}
    for suspect in rnd["votes"].values():
        tally[suspect] = tally.get(suspect, 0) + 1

    top_suspect = None
    if tally:
        top_votes = max(tally.values())
        leaders = [pid for pid, c in tally.items() if c == top_votes]
        # A strict majority can only ever be held by one player.
        if len(leaders) == 1 and top_votes * 2 > present:
            top_suspect = leaders[0]

    # No majority → another round of hints with the same word and imposter.
    if top_suspect is None:
        _start_hint_round(game)
        return

    caught = (top_suspect == rnd["imposter_id"])
    rnd["result"] = {
        "tally":       tally,
        "top_suspect": top_suspect,
        "caught":      caught,
    }

    # A caught imposter gets one chance to win outright by naming the word —
    # but only if they're still here. If they've dropped, finalize now (the
    # missing guess counts as wrong, so the crew wins cleanly).
    imposter_here = game["players"].get(rnd["imposter_id"], {}).get("connected", False)
    if caught and imposter_here:
        game["status"] = "guessing"
    else:
        _finalize(game)


def _start_hint_round(game: dict) -> None:
    """No majority: clear clues/votes and run another clue round. Same secret
    word and imposter; a fresh, reshuffled reveal order."""
    rnd = game["round"]
    rnd["hint_round"] += 1
    rnd["clues"] = {}
    rnd["votes"] = {}
    rnd["abstains"] = []
    rng = random.Random(time.time_ns())
    rng.shuffle(rnd["reveal_order"])
    game["status"] = "clues"
    logger.info("Imposter game %s round %d: no majority, hint round %d",
                game["game_id"], rnd["number"], rnd["hint_round"])


def _finalize(game: dict) -> None:
    """Apply scoring and move to the reveal. Assumes result.caught is set."""
    rnd    = game["round"]
    result = rnd["result"]
    caught = result["caught"]
    imposter = rnd["imposter_id"]

    deltas = {pid: 0 for pid in game["players"]}
    guessed_right = False

    if not caught:
        # The group reached a majority on an innocent player — the imposter
        # blended in well enough to pin the blame elsewhere and wins.
        deltas[imposter] = IMPOSTER_ESCAPE
        outcome = "imposter_escaped"
    else:
        guess = (rnd.get("imposter_guess") or "").strip().lower()
        guessed_right = bool(guess) and guess == rnd["word"].strip().lower()
        if guessed_right:
            # Caught, but stole the win by naming the word.
            deltas[imposter] = IMPOSTER_STEAL
            outcome = "imposter_stole"
        else:
            # Crew caught the imposter cleanly.
            for pid in game["players"]:
                if pid != imposter:
                    deltas[pid] = CREW_CATCH_PTS
            outcome = "crew_won"

    for pid, d in deltas.items():
        if pid in game["players"]:
            game["players"][pid]["score"] += d

    result.update({
        "outcome":       outcome,
        "guessed_right": guessed_right,
        "deltas":        deltas,
    })
    game["status"] = "reveal"


def _normalize_clue(clue: str) -> str:
    return (str(clue).strip() if clue else "")[:NAME_MAX]


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _player_rows(game: dict) -> list[dict]:
    rows = []
    for pid in game["order"]:
        p = game["players"].get(pid)
        if not p:
            continue
        rows.append({
            "player_id": pid,
            "name":      p["name"],
            "is_host":   p["is_host"],
            "score":     p["score"],
            "connected": p["connected"],
        })
    return rows


def public_state(game: dict) -> dict:
    """
    Client-safe snapshot broadcast to everyone in the room. Contains NO
    secret information (word / imposter identity) until the reveal, where
    it's disclosed to all deliberately.
    """
    rnd = game["round"]
    status = game["status"]

    # Simultaneous reveal: while clues are being collected, expose only WHO has
    # locked in (so players see progress) — never the words. Once voting opens,
    # all clues are revealed together in the shuffled reveal order.
    clues_revealed = status in ("voting", "guessing", "reveal")
    clues_public = []
    if clues_revealed:
        clues_public = [
            {"player_id": pid, "name": game["players"][pid]["name"], "clue": rnd["clues"][pid]}
            for pid in rnd["reveal_order"] if pid in rnd["clues"] and pid in game["players"]
        ]

    state = {
        "game_id":      game["game_id"],
        "host_id":      game["host_id"],
        "status":       status,
        "round_number": rnd["number"],
        "min_players":  MIN_PLAYERS,
        "max_players":  MAX_PLAYERS,
        "players":      _player_rows(game),
        "category":     rnd["category"] if status != "lobby" else None,
        "hint_round":   rnd["hint_round"] if status != "lobby" else 1,
        "submitted":    sorted(rnd["clues"].keys()) if status == "clues" else [],
        "clues":        clues_public,
        "voted":        sorted(rnd["votes"].keys()),      # who cast a real vote (not their pick)
        "abstained":    sorted(rnd["abstains"]),          # who abstained
    }

    if status == "reveal":
        res = rnd["result"] or {}
        state["reveal"] = {
            "imposter_id":    rnd["imposter_id"],
            "word":           rnd["word"],
            "category":       rnd["category"],
            "votes":          rnd["votes"],            # voter_id -> suspect_id
            "tally":          res.get("tally", {}),
            "top_suspect":    res.get("top_suspect"),
            "caught":         res.get("caught", False),
            "imposter_guess": rnd.get("imposter_guess"),
            "guessed_right":  res.get("guessed_right", False),
            "outcome":        res.get("outcome"),
            "deltas":         res.get("deltas", {}),
        }
    return state


def role_for(game: dict, player_id: str) -> dict | None:
    """
    Private, per-player role info sent only to that player. The imposter
    learns the category but not the word; crew learn both. None outside of
    an active round.
    """
    rnd = game["round"]
    if game["status"] not in ("clues", "voting", "guessing", "reveal"):
        return None
    if player_id not in game["players"]:
        return None
    is_imp = (player_id == rnd["imposter_id"])
    return {
        "round":    rnd["number"],   # lets the client tell a fresh role from a stale one
        "role":     "imposter" if is_imp else "crew",
        "category": rnd["category"],
        "word":     None if is_imp else rnd["word"],
    }


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def cleanup_stale_games() -> int:
    now = time.time()
    stale = [gid for gid, g in _games.items()
             if now - g.get("updated_at", 0) > MAX_AGE_SECS]
    for gid in stale:
        _games.pop(gid, None)
    if stale:
        logger.info("Cleaned up %d stale imposter games", len(stale))
    return len(stale)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _get_raw(game_id: str) -> dict:
    g = _games.get(game_id)
    if not g:
        raise ImposterError("Game not found. The link may have expired.")
    return g
