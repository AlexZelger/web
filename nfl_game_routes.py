"""
nfl_game_routes.py — NFL Draft Game: Flask Routes
==================================================
Register with your app using:

    from nfl_game_routes import nfl_game_bp
    app.register_blueprint(nfl_game_bp)

Endpoints mirror the MLB version but live under /nfl:
    GET  /nfl/                   → game lobby page
    GET  /nfl/new                → start a new game, redirect to /nfl/play
    GET  /nfl/play               → main game UI page
    GET  /nfl/results            → results page
    POST /nfl/api/search         → autocomplete player search
    POST /nfl/api/years          → valid years for a player+slot
    POST /nfl/api/pick           → submit a single pick
    POST /nfl/api/score          → score the completed game
    GET  /nfl/api/state          → current game state
    GET  /nfl/api/player-image   → ESPN headshot URL for a player
    GET  /nfl/api/team-logo      → ESPN CDN team logo URL
"""

import logging
from flask import (
    Blueprint, session, redirect, url_for,
    render_template, request, jsonify
)

import nfl_game as game_logic
from nfl_data import get_team_logo_url, get_top_stats

logger = logging.getLogger(__name__)


nfl_game_bp = Blueprint(
    "nfl_game",
    __name__,
    url_prefix="/nfl",
    template_folder="templates/nfl",
)

# NFL uses its own session key so a player can have independent MLB and NFL
# games in flight at the same time.
SESSION_KEY        = "nfl_game"
RESULT_SESSION_KEY = "nfl_result"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_state() -> dict | None:
    return session.get(SESSION_KEY)


def _save_state(state: dict) -> None:
    session[SESSION_KEY] = state
    session.modified = True


def _require_state():
    state = _get_state()
    if not state:
        return None, (jsonify({"error": "No active game. Start a new game first."}), 400)
    return state, None


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@nfl_game_bp.route("/")
def lobby():
    return render_template("nfl/lobby.html")


@nfl_game_bp.route("/new")
def new_game():
    try:
        state = game_logic.new_game()
        _save_state(state)
        logger.info("New NFL game started: stat=%s", state["stat_key"])
        return redirect(url_for("nfl_game.play"))
    except FileNotFoundError as e:
        logger.error("NFL player index missing: %s", e)
        return render_template("nfl/error.html", message=(
            "The NFL player index hasn't been built yet. "
            "Run nfl_build_index.py on the server."
        )), 500
    except Exception as e:
        logger.exception("Failed to start new NFL game")
        return render_template("nfl/error.html", message=str(e)), 500


@nfl_game_bp.route("/play")
def play():
    state = _get_state()
    if not state:
        return redirect(url_for("nfl_game.lobby"))
    return render_template("nfl/play.html", state=state)


@nfl_game_bp.route("/results")
def results():
    state = _get_state()
    if not state:
        return redirect(url_for("nfl_game.lobby"))
    result = session.get(RESULT_SESSION_KEY)
    if not result:
        return redirect(url_for("nfl_game.play"))

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

    return render_template("nfl/results.html", state=state, result=result)


# ---------------------------------------------------------------------------
# JSON API routes
# ---------------------------------------------------------------------------

@nfl_game_bp.route("/api/state")
def api_state():
    state, err = _require_state()
    if err:
        return err

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


@nfl_game_bp.route("/api/search", methods=["POST"])
def api_search():
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
        logger.exception("NFL search error")
        return jsonify({"error": str(e)}), 500


@nfl_game_bp.route("/api/years", methods=["POST"])
def api_years():
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
        logger.exception("NFL years lookup error")
        return jsonify({"error": str(e)}), 500


@nfl_game_bp.route("/api/pick", methods=["POST"])
def api_pick():
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
        logger.exception("NFL pick submission error")
        return jsonify({"error": str(e)}), 500


@nfl_game_bp.route("/api/score", methods=["POST"])
def api_score():
    state, err = _require_state()
    if err:
        return err

    if not game_logic.all_slots_filled(state):
        return jsonify({"error": "Not all slots are filled yet."}), 400

    if state.get("submitted"):
        result = session.get(RESULT_SESSION_KEY)
        if result:
            return jsonify(result)

    try:
        result = game_logic.score_game(state)

        state["submitted"] = True
        _save_state(state)
        session[RESULT_SESSION_KEY] = result
        session.modified = True

        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("NFL scoring error")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Image endpoints
# ---------------------------------------------------------------------------

@nfl_game_bp.route("/api/player-image")
def api_player_image():
    """
    Return the headshot URL for an NFL player.
    We pre-baked a `headshot_url` field into the player index at build time
    (sourced from nfl_data_py rosters, which is ESPN's CDN). No runtime
    network calls needed.
    """
    from nfl_data import _load_index   # re-use singleton

    player_name = request.args.get("name", "").strip()
    if not player_name:
        return jsonify({"url": None})

    try:
        index = _load_index()
    except FileNotFoundError:
        return jsonify({"url": None})

    entry = index.get(player_name)
    if not entry:
        return jsonify({"url": None})

    return jsonify({"url": entry.get("headshot_url")})


@nfl_game_bp.route("/api/team-logo")
def api_team_logo():
    team = request.args.get("team", "").strip().upper()
    url  = get_team_logo_url(team) if team else None
    return jsonify({"url": url})
