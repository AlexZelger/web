"""
app.py — Updated for multiplayer support
=========================================
Key changes from the single-player version:
  1. Flask app wrapped with Flask-SocketIO
  2. Multiplayer blueprint registered
  3. SocketIO events registered

Install new dependency:
    pip install flask-socketio
"""
from flask import Flask, render_template, jsonify, request
import random, time
from datetime import datetime, timezone
from flask_socketio import SocketIO
import os


import tips

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-fallback")
g_ftps2 = 32.174

# ── SocketIO ──────────────────────────────────────────────────────────────
# async_mode="threading" works on any host without extra dependencies.
# If you later move to gunicorn + eventlet, change to async_mode="eventlet"
# and pip install eventlet.
socketio = SocketIO(app, async_mode="gevent", cors_allowed_origins="*")

# ── Blueprints ────────────────────────────────────────────────────────────
from game_routes import game_bp
app.register_blueprint(game_bp)

from multiplayer_routes import mp_bp, register_socketio_events
app.register_blueprint(mp_bp)
register_socketio_events(socketio)   # wire up the SocketIO event handlers

# NFL Draft Game — single-player blueprint + multiplayer blueprint.
# Uses the same SocketIO instance but with nfl_*-prefixed event names so
# the MLB and NFL multiplayer lobbies can coexist on one server.
from nfl_game_routes import nfl_game_bp
app.register_blueprint(nfl_game_bp)

from nfl_multiplayer_routes import nfl_mp_bp, register_nfl_socketio_events
app.register_blueprint(nfl_mp_bp)
register_nfl_socketio_events(socketio)

# Generate 1 randomized run for 6-12 players
def generate_run(num_players: int = 8, names=None):
    num_players = max(6, min(12, int(num_players)))
    cleaned_names = []
    if names:
        for i, n in enumerate(names[:num_players]):
            n = (str(n).strip() if n is not None else "")
            cleaned_names.append(n if n else f"Player {i + 1}")

    seed = int(time.time_ns())
    rng = random.Random(seed)

    players = []
    for i in range(num_players):
        name = cleaned_names[i] if i < len(cleaned_names) else f"Player {i + 1}"
        d = max(340.0, min(500.0, rng.gauss(410.0, 25.0)))
        d += rng.uniform(-3.0, 3.0)  # tiny extra variance to reduce ties
        angle_deg = rng.uniform(22.0, 35.0)

        # Add player statistics
        players.append({
            "name": name,
            "distance_ft": round(d, 2),
            "angle_deg": round(angle_deg, 2),
        })
    # Sort players by distance
    placements = sorted(players, key=lambda p: p["distance_ft"], reverse=True)
    for idx, p in enumerate(placements, start=1):
        p["place"] = idx

    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "seed": seed,
        "fence_ft": 400,
        "players": players,
        "placements": placements,
    }

@app.get("/")
def home():
    return render_template("home.html")

@app.route("/portfolio")
def portfolio():
    return render_template("portfolio.html")

@app.route("/draftorder")
def draftorder():
    return render_template("draftorder.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/wolves")
def wolves():
    return render_template("wolves.html")


# GET:  /api/simulate?num_players=8&name=Alex&name=Barry
# POST: JSON {"num_players": 8, "names": ["Alex","Barry",...]}
@app.get("/api/simulate")
def api_simulate():
    names = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        num_players = payload.get("num_players", 8)
        names = payload.get("names")
    else:
        num_players = request.args.get("num_players", 8)
        names = request.args.getlist("name") or None

    try:
        num_players = int(num_players)
    except (TypeError, ValueError):
        num_players = 8
    return jsonify(generate_run(num_players=num_players, names=names))

@app.get("/tips")
def tip():
    return render_template("tips.html")

@app.post("/calculate_tips")
def calculate_tips():
    data = request.get_json(silent=True) or {}
    total = float(data["total"])
    tip_percentage = float(data["tip_percentage"])
    num_people = int(data["num_people"])
    print(f"{total}, {tip_percentage}, {(tip_percentage / 100.0)}, {num_people}")

    tip_amount = round(total * (tip_percentage / 100.0), 2)
    total_with_tip = round(total + tip_amount, 2)
    per_person = round(total_with_tip / max(num_people, 1), 2)

    return jsonify({
        "tip_amount": tip_amount,
        "total_with_tip": total_with_tip,
        "per_person": per_person
    })


# For local dev only (use a real WSGI/ASGI server for production)
if __name__ == "__main__":
    # Use socketio.run instead of app.run for WebSocket support
    socketio.run(app, debug=True)
    #app.run(debug=True)

