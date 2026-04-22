"""
nfl_multiplayer_routes.py — NFL Multiplayer Lobby: Routes + SocketIO
====================================================================
Mirror of multiplayer_routes.py for NFL. Register with your app:

    from nfl_multiplayer_routes import nfl_mp_bp, register_nfl_socketio_events
    app.register_blueprint(nfl_mp_bp)
    register_nfl_socketio_events(socketio)

Lives at /nfl-mp and uses a dedicated session key + SocketIO events
(nfl_join_lobby_room, nfl_start_game, etc.) so it never collides with
the MLB multiplayer routes.

HTTP Routes:
    GET  /nfl-mp/                      → multiplayer landing
    POST /nfl-mp/create                → create a lobby
    GET  /nfl-mp/join/{lobby_id}       → join page
    POST /nfl-mp/join/{lobby_id}       → submit name
    GET  /nfl-mp/wait/{lobby_id}       → waiting room
    GET  /nfl-mp/play/{lobby_id}       → game page
    GET  /nfl-mp/results/{lobby_id}    → scoreboard

SocketIO Events (client → server):
    nfl_join_lobby_room      { lobby_id, player_id }
    nfl_start_game           { lobby_id, player_id }
    nfl_player_finished      { lobby_id, player_id, result }
    nfl_request_scoreboard   { lobby_id }
    nfl_play_again           { lobby_id, player_id }
    nfl_chat_message         { lobby_id, player_id, text }

SocketIO Events (server → client):
    nfl_lobby_updated, nfl_game_started, nfl_scoreboard_updated,
    nfl_game_over, nfl_rematch_ready_update, nfl_lobby_reset,
    nfl_chat_message, nfl_error
"""

import logging
from flask import (
    Blueprint, session, redirect, url_for,
    render_template, request
)
from flask_socketio import join_room, emit

import nfl_lobby_manager as lm
from nfl_lobby_manager import LobbyError
from nfl_data import get_top_stats

logger = logging.getLogger(__name__)

nfl_mp_bp = Blueprint(
    "nfl_mp",
    __name__,
    url_prefix="/nfl-mp",
    template_folder="templates/nfl",
)

MP_SESSION_KEY = "nfl_mp_player"
# NFL game API reads from this session key (see nfl_game_routes.SESSION_KEY)
NFL_GAME_SESSION_KEY    = "nfl_game"
NFL_RESULT_SESSION_KEY  = "nfl_result"

_socketio = None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _set_mp_session(lobby_id: str, player_id: str, name: str) -> None:
    session[MP_SESSION_KEY] = {
        "lobby_id":  lobby_id,
        "player_id": player_id,
        "name":      name,
    }
    session.modified = True


def _get_mp_session() -> dict | None:
    return session.get(MP_SESSION_KEY)


# ---------------------------------------------------------------------------
# HTTP Routes
# ---------------------------------------------------------------------------

@nfl_mp_bp.route("/")
def index():
    return render_template("nfl/mp_lobby.html")


@nfl_mp_bp.route("/create", methods=["POST"])
def create():
    name = request.form.get("name", "").strip()
    if not name:
        return render_template("nfl/mp_lobby.html", error="Please enter a display name.")

    try:
        lobby_id, player_id = lm.create_lobby(name)
        _set_mp_session(lobby_id, player_id, name)
        return redirect(url_for("nfl_mp.wait", lobby_id=lobby_id))
    except Exception as e:
        logger.exception("Failed to create NFL lobby")
        return render_template("nfl/mp_lobby.html", error=str(e))


@nfl_mp_bp.route("/join/<lobby_id>", methods=["GET", "POST"])
def join(lobby_id: str):
    if not lm.lobby_exists(lobby_id):
        return render_template("nfl/mp_lobby.html",
                               error="Lobby not found. The link may have expired.")

    if request.method == "GET":
        try:
            lobby = lm.get_lobby(lobby_id)
        except LobbyError as e:
            return render_template("nfl/mp_lobby.html", error=str(e))
        if lobby["status"] != "waiting":
            return render_template("nfl/mp_join.html", lobby=lobby,
                                   lobby_id=lobby_id, game_started=True)
        return render_template("nfl/mp_join.html", lobby=lobby, lobby_id=lobby_id)

    name = request.form.get("name", "").strip()
    if not name:
        return render_template("nfl/mp_join.html", lobby_id=lobby_id,
                               error="Please enter a display name.")
    try:
        lobby = lm.get_lobby(lobby_id)
        if lobby["status"] != "waiting":
            return render_template("nfl/mp_join.html", lobby=lobby,
                                   lobby_id=lobby_id, game_started=True)
        player_id = lm.join_lobby(lobby_id, name)
        _set_mp_session(lobby_id, player_id, name)

        if _socketio:
            lobby = lm.get_lobby(lobby_id)
            _socketio.emit("nfl_lobby_updated", _lobby_summary(lobby), to=lobby_id)

        return redirect(url_for("nfl_mp.wait", lobby_id=lobby_id))
    except LobbyError as e:
        return render_template("nfl/mp_join.html", lobby_id=lobby_id, error=str(e))


@nfl_mp_bp.route("/wait/<lobby_id>")
def wait(lobby_id: str):
    mp = _get_mp_session()
    if not mp or mp["lobby_id"] != lobby_id:
        return redirect(url_for("nfl_mp.join", lobby_id=lobby_id))

    try:
        lobby = lm.get_lobby(lobby_id)
    except LobbyError:
        return redirect(url_for("nfl_mp.index"))

    if lobby["status"] == "playing":
        return redirect(url_for("nfl_mp.play", lobby_id=lobby_id))

    return render_template("nfl/mp_wait.html",
                           lobby=lobby,
                           player_id=mp["player_id"],
                           player_name=mp["name"],
                           is_host=lm.is_host(lobby_id, mp["player_id"]))


@nfl_mp_bp.route("/play/<lobby_id>")
def play(lobby_id: str):
    """
    Multiplayer NFL game page. Seeds the NFL game session state so the
    existing /nfl/api/* endpoints serve this lobby's prompts.
    """
    mp = _get_mp_session()
    if not mp or mp["lobby_id"] != lobby_id:
        return redirect(url_for("nfl_mp.join", lobby_id=lobby_id))

    try:
        lobby = lm.get_lobby(lobby_id)
    except LobbyError:
        return redirect(url_for("nfl_mp.index"))

    if lobby["status"] == "waiting":
        return redirect(url_for("nfl_mp.wait", lobby_id=lobby_id))

    if lobby["status"] == "finished":
        return redirect(url_for("nfl_mp.results", lobby_id=lobby_id))

    # Seed the NFL game session for this player
    from nfl_game import NUM_SLOTS
    session[NFL_GAME_SESSION_KEY] = {
        "stat_key":   lobby["stat_key"],
        "stat_label": lobby["stat_label"],
        "prompts":    lobby["prompts"],
        "picks":      [None] * NUM_SLOTS,
        "submitted":  False,
    }
    session.pop(NFL_RESULT_SESSION_KEY, None)
    session.modified = True

    state = {
        "stat_key":   lobby["stat_key"],
        "stat_label": lobby["stat_label"],
        "prompts":    lobby["prompts"],
        "picks":      [None] * NUM_SLOTS,
        "submitted":  False,
        "mp": {
            "lobby_id":    lobby_id,
            "player_id":   mp["player_id"],
            "player_name": mp["name"],
        }
    }

    return render_template("nfl/mp_play.html", state=state)


@nfl_mp_bp.route("/results/<lobby_id>")
def results(lobby_id: str):
    mp = _get_mp_session()
    if not mp or mp["lobby_id"] != lobby_id:
        return redirect(url_for("nfl_mp.join", lobby_id=lobby_id))

    try:
        scoreboard = lm.build_scoreboard(lobby_id)
        lobby      = lm.get_lobby(lobby_id)
    except LobbyError:
        return redirect(url_for("nfl_mp.index"))

    stat_key = lobby.get("stat_key")
    prompts  = lobby.get("prompts", [])
    top_picks_by_slot = []
    if stat_key and prompts:
        for prompt in prompts:
            top_picks_by_slot.append(get_top_stats(
                stat_key, n=5,
                team=prompt.get("team"),
                division=prompt.get("division"),
                year_min=prompt.get("year_min"),
                year_max=prompt.get("year_max"),
                min_stat_key=prompt.get("min_stat_key"),
                min_stat_val=prompt.get("min_stat_val"),
                min_teams=prompt.get("min_teams"),
                league=prompt.get("league"),
                rival_team=prompt.get("rival_team"),
            ))

    return render_template("nfl/mp_results.html",
                           scoreboard=scoreboard,
                           lobby=lobby,
                           player_id=mp["player_id"],
                           top_picks_by_slot=top_picks_by_slot)


# ---------------------------------------------------------------------------
# SocketIO event handlers — NFL-namespaced
# ---------------------------------------------------------------------------

def register_nfl_socketio_events(socketio):
    global _socketio
    _socketio = socketio

    @socketio.on("nfl_join_lobby_room")
    def on_join(data):
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")

        if not lobby_id or not lm.lobby_exists(lobby_id):
            emit("nfl_error", {"message": "Lobby not found."})
            return

        join_room(lobby_id)
        logger.debug("NFL socket joined room %s (player %s)", lobby_id, player_id)

        try:
            lobby = lm.get_lobby(lobby_id)
            socketio.emit("nfl_lobby_updated", _lobby_summary(lobby), to=lobby_id)
        except LobbyError as e:
            emit("nfl_error", {"message": str(e)})

    @socketio.on("nfl_start_game")
    def on_start(data):
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")

        try:
            prompts = lm.start_game(lobby_id, player_id)
            lobby   = lm.get_lobby(lobby_id)

            socketio.emit("nfl_game_started", {
                "stat_key":   lobby["stat_key"],
                "stat_label": lobby["stat_label"],
                "prompts":    prompts,
                "lobby_id":   lobby_id,
            }, to=lobby_id)

            logger.info("NFL game started in lobby %s", lobby_id)

        except LobbyError as e:
            emit("nfl_error", {"message": str(e)})

    @socketio.on("nfl_player_finished")
    def on_finished(data):
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")
        result    = data.get("result")

        if not all([lobby_id, player_id, result]):
            emit("nfl_error", {"message": "Missing data in nfl_player_finished event."})
            return

        try:
            scoreboard = lm.submit_score(lobby_id, player_id, result)

            socketio.emit("nfl_scoreboard_updated", scoreboard, to=lobby_id)

            if scoreboard["all_done"]:
                socketio.emit("nfl_game_over", scoreboard, to=lobby_id)

            logger.info("NFL player %s finished in lobby %s (score=%s)",
                        player_id, lobby_id, result.get("total_score"))

        except LobbyError as e:
            emit("nfl_error", {"message": str(e)})

    @socketio.on("nfl_request_scoreboard")
    def on_request_scoreboard(data):
        lobby_id = data.get("lobby_id")
        try:
            scoreboard = lm.build_scoreboard(lobby_id)
            emit("nfl_scoreboard_updated", scoreboard)
        except LobbyError as e:
            emit("nfl_error", {"message": str(e)})

    @socketio.on("nfl_play_again")
    def on_play_again(data):
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")

        try:
            status = lm.mark_ready_for_rematch(lobby_id, player_id)

            socketio.emit("nfl_rematch_ready_update", {
                "ready_count":   status["ready_count"],
                "total_players": status["total_players"],
                "all_ready":     status["all_ready"],
            }, to=lobby_id)

            if status["all_ready"]:
                lm.reset_lobby(lobby_id)
                lobby = lm.get_lobby(lobby_id)
                socketio.emit("nfl_lobby_reset", _lobby_summary(lobby), to=lobby_id)
                logger.info("NFL lobby %s reset — all players ready for rematch", lobby_id)

        except LobbyError as e:
            emit("nfl_error", {"message": str(e)})

    @socketio.on("nfl_chat_message")
    def on_chat_message(data):
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")
        text      = (data.get("text") or "").strip()

        if not text or not lobby_id or not player_id:
            emit("nfl_chat_error", {"message": "Missing lobby_id, player_id, or text."})
            return

        text = text[:200]

        try:
            lobby  = lm.get_lobby(lobby_id)
            player = lobby["players"].get(player_id)
            name   = player["name"] if player else "Unknown"

            socketio.emit("nfl_chat_message", {
                "player_id": player_id,
                "name":      name,
                "text":      text,
            }, to=lobby_id)

        except LobbyError as e:
            emit("nfl_chat_error", {"message": f"Lobby error: {e}"})
        except Exception as e:
            logger.exception("nfl_chat_message unexpected error: %s", e)
            emit("nfl_chat_error", {"message": f"Server error: {e}"})


def _lobby_summary(lobby: dict) -> dict:
    return {
        "lobby_id": lobby["lobby_id"],
        "status":   lobby["status"],
        "players": [
            {"name": p["name"], "is_host": p["is_host"], "status": p["status"]}
            for p in lobby["players"].values()
        ],
    }
