"""
multiplayer_routes.py — Multiplayer Lobby: Routes + SocketIO Events
====================================================================
Register with your app using:

    from multiplayer_routes import mp_bp, register_socketio_events
    app.register_blueprint(mp_bp)
    register_socketio_events(socketio)   # pass your SocketIO instance

HTTP Routes:
    GET  /mp/                      → multiplayer landing (create or join)
    POST /mp/create                → create a lobby, redirect to waiting room
    GET  /mp/join/{lobby_id}       → join page (enter display name)
    POST /mp/join/{lobby_id}       → submit name, redirect to waiting room
    GET  /mp/wait/{lobby_id}       → waiting room page
    GET  /mp/play/{lobby_id}       → game page (reuses play.html with mp context)
    GET  /mp/results/{lobby_id}    → live scoreboard page

SocketIO Events (client → server):
    join_lobby_room    { lobby_id, player_id }  → join the socket room
    start_game         { lobby_id, player_id }  → host starts game
    player_finished    { lobby_id, player_id, result } → submit score

SocketIO Events (server → client):
    lobby_updated      { players, status, ... } → player joined/left
    game_started       { stat_key, stat_label, prompts } → game begins
    scoreboard_updated { players, all_done, ... } → score change
"""

import logging
from flask import (
    Blueprint, session, redirect, url_for,
    render_template, request, jsonify
)
from flask_socketio import join_room, emit

import lobby_manager as lm
from lobby_manager import LobbyError

logger = logging.getLogger(__name__)

mp_bp = Blueprint(
    "mp",
    __name__,
    url_prefix="/mp",
    template_folder="templates/game",
)

MP_SESSION_KEY = "mp_player"   # { lobby_id, player_id, player_name }


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

@mp_bp.route("/")
def index():
    """Multiplayer landing page — create a lobby or paste a join link."""
    return render_template("mp_lobby.html")


@mp_bp.route("/create", methods=["POST"])
def create():
    """Create a new lobby and redirect the host to the waiting room."""
    name = request.form.get("name", "").strip()
    if not name:
        return render_template("mp_lobby.html", error="Please enter a display name.")

    try:
        lobby_id, player_id = lm.create_lobby(name)
        _set_mp_session(lobby_id, player_id, name)
        return redirect(url_for("mp.wait", lobby_id=lobby_id))
    except Exception as e:
        logger.exception("Failed to create lobby")
        return render_template("mp_lobby.html", error=str(e))


@mp_bp.route("/join/<lobby_id>", methods=["GET", "POST"])
def join(lobby_id: str):
    """Join an existing lobby by entering a display name."""
    if not lm.lobby_exists(lobby_id):
        return render_template("mp_lobby.html",
                               error="Lobby not found. The link may have expired.")

    if request.method == "GET":
        try:
            lobby = lm.get_lobby(lobby_id)
        except LobbyError as e:
            return render_template("mp_lobby.html", error=str(e))
        return render_template("mp_join.html", lobby=lobby, lobby_id=lobby_id)

    # POST — submit name
    name = request.form.get("name", "").strip()
    if not name:
        return render_template("mp_join.html", lobby_id=lobby_id,
                               error="Please enter a display name.")
    try:
        player_id = lm.join_lobby(lobby_id, name)
        _set_mp_session(lobby_id, player_id, name)
        return redirect(url_for("mp.wait", lobby_id=lobby_id))
    except LobbyError as e:
        return render_template("mp_join.html", lobby_id=lobby_id, error=str(e))


@mp_bp.route("/wait/<lobby_id>")
def wait(lobby_id: str):
    """Waiting room — shows who's joined, host can start game."""
    mp = _get_mp_session()
    if not mp or mp["lobby_id"] != lobby_id:
        return redirect(url_for("mp.join", lobby_id=lobby_id))

    try:
        lobby = lm.get_lobby(lobby_id)
    except LobbyError:
        return redirect(url_for("mp.index"))

    # If game already started, send to play page
    if lobby["status"] == "playing":
        return redirect(url_for("mp.play", lobby_id=lobby_id))

    return render_template("mp_wait.html",
                           lobby=lobby,
                           player_id=mp["player_id"],
                           is_host=lm.is_host(lobby_id, mp["player_id"]))


@mp_bp.route("/play/<lobby_id>")
def play(lobby_id: str):
    """
    Multiplayer game page. Reuses the single-player play.html but
    passes extra context so JS knows to report scores back to the lobby.
    Also seeds the Flask session with the lobby's game state so all
    existing /game/api/* endpoints (search, years, pick, score) work
    without modification.
    """
    mp = _get_mp_session()
    if not mp or mp["lobby_id"] != lobby_id:
        return redirect(url_for("mp.join", lobby_id=lobby_id))

    try:
        lobby = lm.get_lobby(lobby_id)
    except LobbyError:
        return redirect(url_for("mp.index"))

    if lobby["status"] == "waiting":
        return redirect(url_for("mp.wait", lobby_id=lobby_id))

    if lobby["status"] == "finished":
        return redirect(url_for("mp.results", lobby_id=lobby_id))

    # ── Seed the Flask session with this lobby's game state ──────────
    # This makes all existing /game/api/* endpoints work correctly for
    # this player — search, years, pick, and score all read from
    # session["baseball_game"], so we write a fresh valid state there.
    from game import NUM_SLOTS
    session_game_state = {
        "stat_key":   lobby["stat_key"],
        "stat_label": lobby["stat_label"],
        "prompts":    lobby["prompts"],
        "picks":      [None] * NUM_SLOTS,
        "submitted":  False,           # always False — fresh game for this player
    }
    session["baseball_game"] = session_game_state
    session.pop("baseball_result", None)   # clear any previous solo result
    session.modified = True

    # Template context for mp_play.html
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

    return render_template("mp_play.html", state=state)


@mp_bp.route("/results/<lobby_id>")
def results(lobby_id: str):
    """Live scoreboard page."""
    mp = _get_mp_session()
    if not mp or mp["lobby_id"] != lobby_id:
        return redirect(url_for("mp.join", lobby_id=lobby_id))

    try:
        scoreboard = lm.build_scoreboard(lobby_id)
        lobby      = lm.get_lobby(lobby_id)
    except LobbyError:
        return redirect(url_for("mp.index"))

    return render_template("mp_results.html",
                           scoreboard=scoreboard,
                           lobby=lobby,
                           player_id=mp["player_id"])


# ---------------------------------------------------------------------------
# SocketIO event handlers
# ---------------------------------------------------------------------------

def register_socketio_events(socketio):
    """
    Call this after creating your SocketIO instance:
        register_socketio_events(socketio)
    """

    @socketio.on("join_lobby_room")
    def on_join(data):
        """
        Client connects and joins the SocketIO room for their lobby.
        Emits the current lobby state back to the joining client.
        """
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")

        if not lobby_id or not lm.lobby_exists(lobby_id):
            emit("error", {"message": "Lobby not found."})
            return

        join_room(lobby_id)
        logger.debug("Socket joined room %s (player %s)", lobby_id, player_id)

        try:
            lobby = lm.get_lobby(lobby_id)
            # Send current player list to the newly connected client
            emit("lobby_updated", _lobby_summary(lobby))
        except LobbyError as e:
            emit("error", {"message": str(e)})


    @socketio.on("start_game")
    def on_start(data):
        """
        Host starts the game. Broadcasts prompts to all players in the room.
        """
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")

        try:
            prompts = lm.start_game(lobby_id, player_id)
            lobby   = lm.get_lobby(lobby_id)

            # Broadcast game start to everyone in the room (including sender)
            socketio.emit("game_started", {
                "stat_key":   lobby["stat_key"],
                "stat_label": lobby["stat_label"],
                "prompts":    prompts,
                "lobby_id":   lobby_id,
            }, to=lobby_id)

            logger.info("Game started in lobby %s", lobby_id)

        except LobbyError as e:
            emit("error", {"message": str(e)})


    @socketio.on("player_finished")
    def on_finished(data):
        """
        A player has submitted their picks and been scored.
        Broadcasts updated scoreboard to all players in the room.
        """
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")
        result    = data.get("result")   # the full score_game() result dict

        if not all([lobby_id, player_id, result]):
            emit("error", {"message": "Missing data in player_finished event."})
            return

        try:
            scoreboard = lm.submit_score(lobby_id, player_id, result)

            # Broadcast to everyone in the lobby room
            socketio.emit("scoreboard_updated", scoreboard, to=lobby_id)

            # If all done, also emit a game_over event
            if scoreboard["all_done"]:
                socketio.emit("game_over", scoreboard, to=lobby_id)

            logger.info("Player %s finished in lobby %s (score=%s)",
                        player_id, lobby_id, result.get("total_score"))

        except LobbyError as e:
            emit("error", {"message": str(e)})


    @socketio.on("request_scoreboard")
    def on_request_scoreboard(data):
        """Client requests the current scoreboard (e.g. on page load/reconnect)."""
        lobby_id = data.get("lobby_id")
        try:
            scoreboard = lm.build_scoreboard(lobby_id)
            emit("scoreboard_updated", scoreboard)
        except LobbyError as e:
            emit("error", {"message": str(e)})


    @socketio.on("play_again")
    def on_play_again(data):
        """
        A player clicks 'Play Again' on the results page.
        Marks them as ready and broadcasts the updated ready count.
        If everyone is ready, resets the lobby and redirects all to wait screen.
        """
        lobby_id  = data.get("lobby_id")
        player_id = data.get("player_id")

        try:
            status = lm.mark_ready_for_rematch(lobby_id, player_id)

            # Broadcast current ready count to everyone in the room
            socketio.emit("rematch_ready_update", {
                "ready_count":   status["ready_count"],
                "total_players": status["total_players"],
                "all_ready":     status["all_ready"],
            }, to=lobby_id)

            # If everyone clicked Play Again, reset and redirect
            if status["all_ready"]:
                lm.reset_lobby(lobby_id)
                lobby = lm.get_lobby(lobby_id)
                socketio.emit("lobby_reset", _lobby_summary(lobby), to=lobby_id)
                logger.info("Lobby %s reset — all players ready for rematch", lobby_id)

        except LobbyError as e:
            emit("error", {"message": str(e)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lobby_summary(lobby: dict) -> dict:
    """Trim lobby dict to what the waiting room needs."""
    return {
        "lobby_id": lobby["lobby_id"],
        "status":   lobby["status"],
        "players": [
            {"name": p["name"], "is_host": p["is_host"], "status": p["status"]}
            for p in lobby["players"].values()
        ],
    }