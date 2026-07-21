"""
draft_routes.py — Fantasy Draft Order Randomizer: Routes + SocketIO
===================================================================
Register with your app using:

    from draft_routes import draft_bp, register_draft_socketio_events
    app.register_blueprint(draft_bp)
    register_draft_socketio_events(socketio)

HTTP Routes:
    GET  /draft/                    -> setup page (choose count + names)
    POST /draft/create              -> create a race (JSON), returns run_id
    GET  /draft/race/<run_id>       -> host view (has the Run button)
    GET  /draft/watch/<run_id>      -> spectator view (watch / replay)
    GET  /draft/results/<run_id>    -> final draft order (shareable)

SocketIO Events (client -> server):
    draft_join    { run_id }            -> join the race room, get current state
    draft_start   { run_id }            -> host starts the race
    draft_ended   { run_id }            -> host reports the animation finished

SocketIO Events (server -> client):
    draft_state    { ...public_run, server_time }  -> full state on join
    draft_started  { started_at_ms, server_time }  -> race is live, animate now
    draft_finished { }                             -> race marked finished
"""

import time
import logging
from flask import (
    Blueprint, session, redirect, url_for,
    render_template, request, jsonify, abort,
)
from flask_socketio import join_room, emit

import draft_manager as dm

logger = logging.getLogger(__name__)

draft_bp = Blueprint(
    "draft",
    __name__,
    url_prefix="/draft",
    template_folder="templates/draft",
)

OWNED_KEY = "draft_owned"    # list of run_ids this browser session created

_socketio = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_owner(run_id: str) -> bool:
    return run_id in session.get(OWNED_KEY, [])


def _mark_owner(run_id: str) -> None:
    owned = session.get(OWNED_KEY, [])
    if run_id not in owned:
        owned.append(run_id)
        session[OWNED_KEY] = owned[-50:]   # cap growth
        session.modified = True


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@draft_bp.route("/")
def setup():
    """Landing page: pick player count (2-32) and enter names."""
    return render_template(
        "draft/setup.html",
        min_players=dm.MIN_PLAYERS,
        max_players=dm.MAX_PLAYERS,
    )


@draft_bp.route("/create", methods=["POST"])
def create():
    """Create a race from submitted names. Returns { run_id }."""
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    if not isinstance(names, list) or len(names) < dm.MIN_PLAYERS:
        return jsonify({"error": f"Enter at least {dm.MIN_PLAYERS} names."}), 400
    if len(names) > dm.MAX_PLAYERS:
        return jsonify({"error": f"At most {dm.MAX_PLAYERS} players."}), 400

    dm.cleanup_stale_runs()
    run_id = dm.create_run(names)
    _mark_owner(run_id)
    return jsonify({"run_id": run_id})


@draft_bp.route("/race/<run_id>")
def race(run_id):
    """Host view — includes the Run button. Non-owners are sent to watch."""
    run = dm.get_run(run_id)
    if not run:
        return render_template("draft/gone.html"), 404
    if not _is_owner(run_id):
        return redirect(url_for("draft.watch", run_id=run_id))
    return render_template(
        "draft/race.html",
        run=dm.public_run(run),
        is_host=True,
        server_time=int(time.time() * 1000),
    )


@draft_bp.route("/watch/<run_id>")
def watch(run_id):
    """Spectator view — watch live, or replay once finished."""
    run = dm.get_run(run_id)
    if not run:
        return render_template("draft/gone.html"), 404
    return render_template(
        "draft/race.html",
        run=dm.public_run(run),
        is_host=False,
        server_time=int(time.time() * 1000),
    )


@draft_bp.route("/results/<run_id>")
def results(run_id):
    """Final draft order — shareable, static (no animation)."""
    run = dm.get_run(run_id)
    if not run:
        return render_template("draft/gone.html"), 404
    return render_template(
        "draft/results.html",
        run_id=run_id,
        order=dm.draft_order(run),
        finished=dm.is_finished(run),
    )


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

def register_draft_socketio_events(socketio):
    global _socketio
    _socketio = socketio

    @socketio.on("draft_join")
    def on_join(data):
        data = data or {}
        run_id = data.get("run_id")
        viewer_id = data.get("viewer_id")
        run = dm.get_run(run_id) if run_id else None
        if not run:
            emit("draft_error", {"message": "Race not found."})
            return
        join_room(run_id)
        state = dm.public_run(run)
        state["server_time"] = int(time.time() * 1000)
        # Social state
        state["claims"] = dm.claims_public(run)
        my_lane = dm.claim_of(run, viewer_id)
        state["your_claim"] = my_lane
        preds = dm.predictions_public(run)
        state["your_prediction"] = preds.get(my_lane) if my_lane is not None else None
        state["pred_count"] = len(preds)
        if dm.is_finished(run):
            state["predictions"] = preds
            state["winner_lane"] = dm.winner_lane(run)
        emit("draft_state", state)

    @socketio.on("draft_start")
    def on_start(data):
        run_id = (data or {}).get("run_id")
        run = dm.get_run(run_id) if run_id else None
        if not run:
            emit("draft_error", {"message": "Race not found."})
            return
        try:
            started_at = dm.start_run(run_id)
        except dm.DraftError as e:
            emit("draft_error", {"message": str(e)})
            return
        socketio.emit(
            "draft_started",
            {"started_at_ms": started_at, "server_time": int(time.time() * 1000)},
            to=run_id,
        )

    @socketio.on("draft_ended")
    def on_ended(data):
        run_id = (data or {}).get("run_id")
        run = dm.get_run(run_id) if run_id else None
        if not run:
            return
        dm.finish_run(run_id)
        run = dm.get_run(run_id)
        socketio.emit(
            "draft_finished",
            {"predictions": dm.predictions_public(run), "winner_lane": dm.winner_lane(run)},
            to=run_id,
        )

    @socketio.on("draft_claim")
    def on_claim(data):
        data = data or {}
        run_id, viewer_id, lane = data.get("run_id"), data.get("viewer_id"), data.get("lane")
        if not run_id or not viewer_id or not isinstance(lane, int):
            emit("draft_error", {"message": "Bad claim."})
            return
        try:
            dm.claim_spot(run_id, viewer_id, lane)
        except dm.DraftError as e:
            emit("draft_claim_rejected", {"message": str(e)})
            return
        run = dm.get_run(run_id)
        socketio.emit("draft_claims", {"claims": dm.claims_public(run)}, to=run_id)
        emit("draft_your_claim", {"lane": lane})

    @socketio.on("draft_predict")
    def on_predict(data):
        data = data or {}
        run_id, viewer_id, lane = data.get("run_id"), data.get("viewer_id"), data.get("lane")
        if not run_id or not viewer_id or not isinstance(lane, int):
            emit("draft_error", {"message": "Bad prediction."})
            return
        try:
            dm.predict(run_id, viewer_id, lane)
        except dm.DraftError as e:
            emit("draft_error", {"message": str(e)})
            return
        run = dm.get_run(run_id)
        socketio.emit(
            "draft_pred_count",
            {"count": len(dm.predictions_public(run)), "total": len(dm.claims_public(run))},
            to=run_id,
        )
        emit("draft_your_prediction", {"lane": lane})

    @socketio.on("draft_react")
    def on_react(data):
        data = data or {}
        run_id, viewer_id, emoji = data.get("run_id"), data.get("viewer_id"), data.get("emoji")
        run = dm.get_run(run_id) if run_id else None
        if not run:
            return
        lane = dm.claim_of(run, viewer_id)
        if lane is None:
            emit("draft_error", {"message": "Claim your spot to react."})
            return
        if not isinstance(emoji, str) or not (1 <= len(emoji) <= 8):
            return
        socketio.emit(
            "draft_reaction",
            {"lane": lane, "emoji": emoji, "name": run["runners"][lane]["name"]},
            to=run_id,
        )
