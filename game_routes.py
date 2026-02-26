"""
routes.py — Baseball Draft Game: Flask Routes
==============================================
Register with your app using:

    from game_routes import game_bp
    app.register_blueprint(game_bp)

Endpoints:
    GET  /game/                  → game lobby page
    GET  /game/new               → start a new game, redirect to /game/play
    GET  /game/play              → main game UI page
    GET  /game/results           → results page
    POST /game/api/search        → autocomplete player search (JSON)
    POST /game/api/years         → get valid years for a player+slot (JSON)
    POST /game/api/pick          → submit a single pick (JSON)
    POST /game/api/score         → score the completed game (JSON)
    GET  /game/api/state         → return current game state (JSON)
"""

import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from flask import (
    Blueprint, session, redirect, url_for,
    render_template, request, jsonify
)
import game as game_logic
from data import get_team_logo_url, get_top_stats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MLBAM player ID cache (name → id), persisted to disk
# ---------------------------------------------------------------------------
MLBAM_CACHE_PATH = Path(__file__).parent / "mlbam_id_cache.json"

def _load_mlbam_cache() -> dict:
    if MLBAM_CACHE_PATH.exists():
        with open(MLBAM_CACHE_PATH) as f:
            return json.load(f)
    return {}

def _save_mlbam_cache(cache: dict) -> None:
    with open(MLBAM_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

def _lookup_mlbam_id(player_name: str) -> int | None:
    """
    Query the free MLB Stats API to resolve a player name to an MLBAM ID.
    No API key required. Returns None on failure.
    """
    try:
        encoded = urllib.parse.quote(player_name)
        url = f"https://statsapi.mlb.com/api/v1/people/search?names={encoded}&sportIds=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        people = data.get("people", [])
        if people:
            return people[0]["id"]
    except Exception as e:
        logger.warning("MLBAM ID lookup failed for %s: %s", player_name, e)
    return None


game_bp = Blueprint(
    "game",
    __name__,
    url_prefix="/game",
    template_folder="templates/game",   # templates/game/*.html
)

SESSION_KEY = "baseball_game"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_state() -> dict | None:
    return session.get(SESSION_KEY)


def _save_state(state: dict) -> None:
    session[SESSION_KEY] = state
    session.modified = True


def _require_state():
    """Return state or raise a JSON error if no active game."""
    state = _get_state()
    if not state:
        return None, (jsonify({"error": "No active game. Start a new game first."}), 400)
    return state, None


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@game_bp.route("/")
def lobby():
    """Game landing / info page."""
    return render_template("lobby.html")


@game_bp.route("/new")
def new_game():
    """Start a fresh game and redirect to the play page."""
    try:
        state = game_logic.new_game()
        _save_state(state)
        logger.info("New game started: stat=%s", state["stat_key"])
        return redirect(url_for("game.play"))
    except FileNotFoundError as e:
        # Index hasn't been built yet
        logger.error("Player index missing: %s", e)
        return render_template("error.html", message=(
            "The player index hasn't been built yet. "
            "Run warm_cache.py then build_index.py on the server."
        )), 500
    except Exception as e:
        logger.exception("Failed to start new game")
        return render_template("error.html", message=str(e)), 500


@game_bp.route("/play")
def play():
    """Main game UI — requires an active game in session."""
    state = _get_state()
    if not state:
        return redirect(url_for("game.lobby"))
    return render_template("play.html", state=state)


@game_bp.route("/results")
def results():
    """Results page — requires a scored game in session."""
    state = _get_state()
    if not state:
        return redirect(url_for("game.lobby"))
    result = session.get("baseball_result")
    if not result:
        return redirect(url_for("game.play"))

    # Compute top picks fresh here — never stored in session to avoid
    # exceeding Flask's 4KB cookie limit.
    import copy
    result = copy.deepcopy(result)
    prompts = state.get("prompts", [])
    for i, slot in enumerate(result.get("slots", [])):
        prompt = prompts[i] if i < len(prompts) else {}
        slot["top_picks"] = get_top_stats(
            result["stat_key"], n=5,
            team=prompt.get("team"),
            division=prompt.get("division"),
            year_min=prompt.get("year_min"),
            year_max=prompt.get("year_max"),
            min_stat_key=prompt.get("min_stat_key"),
            min_stat_val=prompt.get("min_stat_val"),
            min_teams=prompt.get("min_teams"),
            league=prompt.get("league"),
            rival_team=prompt.get("rival_team"),
        )

    return render_template("results.html", state=state, result=result)


# ---------------------------------------------------------------------------
# JSON API routes
# ---------------------------------------------------------------------------

@game_bp.route("/api/state")
def api_state():
    """
    GET /game/api/state
    Return the current game state (minus stat reveals for unfilled slots).
    """
    state, err = _require_state()
    if err:
        return err

    # Strip stat_value from unfilled/unlocked picks before sending to client
    safe_picks = []
    for pick in state["picks"]:
        if pick and pick.get("locked"):
            safe_picks.append(pick)
        else:
            safe_picks.append(None)

    return jsonify({
        "stat_key":      state["stat_key"],
        "stat_label":    state["stat_label"],
        "prompts":       state["prompts"],
        "picks":         safe_picks,
        "all_filled":    game_logic.all_slots_filled(state),
        "submitted":     state.get("submitted", False),
    })


@game_bp.route("/api/search", methods=["POST"])
def api_search():
    """
    POST /game/api/search
    Body: { "query": "harper", "slot": 0 }
    Returns: { "results": [ { name, type, matching_seasons, teams }, ... ] }

    Called on every keystroke in the player search box.
    """
    state, err = _require_state()
    if err:
        return err

    data  = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    slot  = data.get("slot")

    if slot is None or not isinstance(slot, int):
        return jsonify({"error": "Missing or invalid 'slot' field."}), 400

    if len(query) < 2:
        return jsonify({"results": []})

    try:
        results = game_logic.search_players(query, slot, state, limit=10)
        return jsonify({"results": results})
    except Exception as e:
        logger.exception("Search error")
        return jsonify({"error": str(e)}), 500


@game_bp.route("/api/years", methods=["POST"])
def api_years():
    """
    POST /game/api/years
    Body: { "player": "Bryce Harper", "slot": 0 }
    Returns: { "years": [2012, 2013, 2014, 2015, 2016, 2017, 2018] }

    Called after the user selects a player from autocomplete.
    """
    state, err = _require_state()
    if err:
        return err

    data   = request.get_json(silent=True) or {}
    player = data.get("player", "").strip()
    slot   = data.get("slot")

    if not player:
        return jsonify({"error": "Missing 'player' field."}), 400
    if slot is None or not isinstance(slot, int):
        return jsonify({"error": "Missing or invalid 'slot' field."}), 400

    try:
        years = game_logic.get_years_for_pick(player, slot, state)
        if not years:
            return jsonify({
                "years": [],
                "warning": f"{player} has no qualifying seasons for this slot."
            })
        return jsonify({"years": years})
    except Exception as e:
        logger.exception("Years lookup error")
        return jsonify({"error": str(e)}), 500


@game_bp.route("/api/pick", methods=["POST"])
def api_pick():
    """
    POST /game/api/pick
    Body: { "slot": 0, "player": "Bryce Harper", "year": 2015 }
    Returns: {
        "pick": { slot, player, year, stat_key, stat_label, stat_value, valid },
        "all_filled": bool
    }

    Called when the user confirms a pick for a slot.
    Immediately reveals the stat value for that pick.
    """
    state, err = _require_state()
    if err:
        return err

    data   = request.get_json(silent=True) or {}
    slot   = data.get("slot")
    player = data.get("player", "").strip()
    year   = data.get("year")

    if slot is None or not isinstance(slot, int):
        return jsonify({"error": "Missing or invalid 'slot' field."}), 400
    if not player:
        return jsonify({"error": "Missing 'player' field."}), 400
    if not year or not isinstance(year, int):
        return jsonify({"error": "Missing or invalid 'year' field."}), 400

    try:
        new_state, pick_result = game_logic.submit_pick(state, slot, player, year)
        _save_state(new_state)
        return jsonify({
            "pick":       pick_result,
            "all_filled": game_logic.all_slots_filled(new_state),
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Pick submission error")
        return jsonify({"error": str(e)}), 500


@game_bp.route("/api/score", methods=["POST"])
def api_score():
    """
    POST /game/api/score
    Body: {} (no body needed — scores the game in session)
    Returns: full result dict from game_logic.score_game()

    Called when the user clicks Submit after filling all 5 slots.
    Also saves the result to session and marks game as submitted.
    """
    state, err = _require_state()
    if err:
        return err

    if not game_logic.all_slots_filled(state):
        return jsonify({"error": "Not all slots are filled yet."}), 400

    if state.get("submitted"):
        # Already scored — just return the cached result
        result = session.get("baseball_result")
        if result:
            return jsonify(result)

    try:
        result = game_logic.score_game(state)

        # Mark game as submitted and persist result
        state["submitted"] = True
        _save_state(state)
        session["baseball_result"] = result
        session.modified = True

        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Scoring error")
        return jsonify({"error": str(e)}), 500


@game_bp.route("/api/player-image")
def api_player_image():
    """
    GET /game/api/player-image?name=Bryce+Harper
    Returns the MLB headshot URL for a player, resolved via MLBAM ID.
    IDs are cached locally in mlbam_id_cache.json so each name is only
    looked up once ever.

    Response: { "url": "https://img.mlbstatic.com/..." }
              { "url": null }  — if player not found (frontend shows silhouette)
    """
    player_name = request.args.get("name", "").strip()
    if not player_name:
        return jsonify({"url": None})

    cache = _load_mlbam_cache()

    # Check cache first
    if player_name in cache:
        mlbam_id = cache[player_name]
    else:
        mlbam_id = _lookup_mlbam_id(player_name)
        cache[player_name] = mlbam_id  # cache even if None (avoid re-querying)
        _save_mlbam_cache(cache)

    if not mlbam_id:
        return jsonify({"url": None})

    # d_people:generic:headshot:67:current.png = fallback silhouette if no photo
    url = (
        f"https://img.mlbstatic.com/mlb-photos/image/upload/"
        f"d_people:generic:headshot:67:current.png/"
        f"w_213,q_auto:best/v1/people/{mlbam_id}/headshot/67/current"
    )
    return jsonify({"url": url})


@game_bp.route("/api/team-logo")
def api_team_logo():
    """
    GET /game/api/team-logo?team=PHI
    Returns the MLB CDN SVG logo URL for a team abbreviation.
    Pure data — no network call needed, just a lookup.

    Response: { "url": "https://www.mlbstatic.com/team-logos/143.svg" }
              { "url": null }  — if abbreviation unknown
    """
    team = request.args.get("team", "").strip().upper()
    url  = get_team_logo_url(team) if team else None
    return jsonify({"url": url})