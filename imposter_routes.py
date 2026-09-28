"""
imposter_routes.py — "Imposter" Word Game: Routes + SocketIO
============================================================
Register with your app using:

    from imposter_routes import imposter_bp, register_imposter_socketio_events
    app.register_blueprint(imposter_bp)
    register_imposter_socketio_events(socketio)

HTTP Routes:
    GET  /imposter/                 -> landing (create a game or join by code)
    POST /imposter/create           -> create a game (JSON), returns game_id + player_id
    POST /imposter/g/<gid>/join     -> join a game (JSON), returns player_id
    GET  /imposter/g/<gid>          -> the game room (single page; all phases)

Player identity is stored per-game in the Flask session so a refresh keeps
you in your seat. The secret word / imposter identity are never sent to the
browser except to the player they belong to (private per-socket emits).

SocketIO events (client -> server), all prefixed `imp_`:
    imp_join      { game_id, player_id }          -> join room, get state + role
    imp_want_role { game_id, player_id }          -> (re)fetch your own private role
    imp_start     { game_id, player_id }          -> host starts a round
    imp_clue      { game_id, player_id, clue }    -> submit your one-word clue
    imp_reveal    { game_id, player_id }          -> host reveals clues / opens voting early
    imp_vote      { game_id, player_id, suspect } -> vote for the imposter
    imp_abstain   { game_id, player_id }          -> abstain from this vote
    imp_guess     { game_id, player_id, guess }   -> caught imposter guesses the word
    imp_next      { game_id, player_id }          -> host returns to lobby (keep scores)

SocketIO events (server -> client):
    imp_state  { ...public_state }   -> full public snapshot (broadcast on change)
    imp_role   { role, category, word|null }  -> private role (per player)
    imp_error  { message }           -> a rejected action (to the acting socket)
"""

import logging
from flask import (
    Blueprint, session, redirect, url_for, render_template, request, jsonify,
)
from flask_socketio import join_room, emit

import imposter_manager as im

logger = logging.getLogger(__name__)

imposter_bp = Blueprint(
    "imposter",
    __name__,
    url_prefix="/imposter",
    template_folder="templates/imposter",
)

SESSION_KEY = "imposter_players"     # { game_id: player_id } for this browser
_socketio = None

# sid -> (game_id, player_id), so a disconnect can find who dropped.
_sid_index: dict[str, tuple[str, str]] = {}


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _remember(game_id: str, player_id: str) -> None:
    seats = session.get(SESSION_KEY, {})
    seats[game_id] = player_id
    session[SESSION_KEY] = seats
    session.modified = True


def _my_player_id(game_id: str) -> str | None:
    return session.get(SESSION_KEY, {}).get(game_id)


# ---------------------------------------------------------------------------
# Page + JSON routes
# ---------------------------------------------------------------------------

@imposter_bp.route("/")
def landing():
    return render_template("imposter/landing.html",
                           min_players=im.MIN_PLAYERS,
                           max_players=im.MAX_PLAYERS)


@imposter_bp.route("/create", methods=["POST"])
def create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Enter your name first."}), 400
    game_id, player_id = im.create_game(name)
    _remember(game_id, player_id)
    return jsonify({"game_id": game_id, "player_id": player_id})


@imposter_bp.route("/g/<gid>/join", methods=["POST"])
def join(gid):
    if not im.game_exists(gid):
        return jsonify({"error": "Game not found. Check the code."}), 404
    # Already seated in this game (e.g. the host) — just hand back the id.
    existing = _my_player_id(gid)
    if existing and im.player_in_game(gid, existing):
        return jsonify({"game_id": gid, "player_id": existing})
    data = request.get_json(silent=True) or {}
    try:
        player_id = im.join_game(gid, data.get("name") or "")
    except im.ImposterError as e:
        return jsonify({"error": str(e)}), 400
    _remember(gid, player_id)
    return jsonify({"game_id": gid, "player_id": player_id})


@imposter_bp.route("/g/<gid>")
def room(gid):
    game = im.get_game(gid)
    if not game:
        return render_template("imposter/gone.html"), 404
    player_id = _my_player_id(gid)
    # If this browser isn't seated yet, the page shows a name/join overlay
    # and calls /join before opening the socket.
    if player_id and not im.player_in_game(gid, player_id):
        player_id = None
    return render_template(
        "imposter/room.html",
        game_id=gid,
        player_id=player_id or "",
        min_players=im.MIN_PLAYERS,
        max_players=im.MAX_PLAYERS,
    )


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

def register_imposter_socketio_events(socketio):
    global _socketio
    _socketio = socketio

    def _broadcast(game_id: str) -> None:
        game = im.get_game(game_id)
        if game:
            socketio.emit("imp_state", im.public_state(game), to=game_id)

    def _send_role_to_caller(game_id: str, player_id: str) -> None:
        """
        Reply to the requesting socket with its own private role. Emitting
        without an explicit `to=` targets exactly the socket that asked, so
        a player's secret word can only ever reach that player — no per-player
        socket-id bookkeeping to get wrong.
        """
        game = im.get_game(game_id)
        if not game:
            return
        role = im.role_for(game, player_id)
        # A crew member always gets a word; only the imposter's word is null.
        if role is not None:
            emit("imp_role", role)

    @socketio.on("imp_join")
    def on_join(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        game = im.get_game(game_id) if game_id else None
        if not game or not player_id or not im.player_in_game(game_id, player_id):
            emit("imp_error", {"message": "Could not join — game or seat not found."})
            return
        join_room(game_id)
        _sid_index[request.sid] = (game_id, player_id)
        im.set_connected(game_id, player_id, True)
        # Hand this socket the current public state and (if a round is live) its
        # own role. The client also pulls its role via imp_want_role, so a
        # reconnect or a race at round start always recovers the right word.
        emit("imp_state", im.public_state(im.get_game(game_id)))
        _send_role_to_caller(game_id, player_id)
        _broadcast(game_id)   # let others see the (re)connection

    @socketio.on("imp_want_role")
    def on_want_role(data):
        """Client-driven role fetch — the authoritative way a player learns
        whether they're crew (with the word) or the imposter."""
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        if game_id and player_id and im.player_in_game(game_id, player_id):
            _send_role_to_caller(game_id, player_id)

    @socketio.on("imp_start")
    def on_start(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.start_round(game_id, player_id)
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)   # clients pull their fresh role on seeing the new round

    @socketio.on("imp_clue")
    def on_clue(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.submit_clue(game_id, player_id, data.get("clue", ""))
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)

    @socketio.on("imp_reveal")
    def on_reveal(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.force_reveal(game_id, player_id)
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)

    @socketio.on("imp_vote")
    def on_vote(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.cast_vote(game_id, player_id, data.get("suspect"))
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)

    @socketio.on("imp_abstain")
    def on_abstain(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.abstain_vote(game_id, player_id)
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)

    @socketio.on("imp_guess")
    def on_guess(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.submit_guess(game_id, player_id, data.get("guess", ""))
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)

    @socketio.on("imp_next")
    def on_next(data):
        data = data or {}
        game_id, player_id = data.get("game_id"), data.get("player_id")
        try:
            im.next_round(game_id, player_id)
        except im.ImposterError as e:
            emit("imp_error", {"message": str(e)})
            return
        _broadcast(game_id)

    @socketio.on("disconnect")
    def on_disconnect():
        entry = _sid_index.pop(request.sid, None)
        if not entry:
            return
        game_id, player_id = entry
        # Mark them offline; may advance the round if it was only waiting on them.
        im.recheck_after_disconnect(game_id, player_id)
        game = im.get_game(game_id)
        if game:
            socketio.emit("imp_state", im.public_state(game), to=game_id)
