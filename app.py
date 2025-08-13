from flask import Flask, render_template, jsonify, request
import random, time
from datetime import datetime, timezone

app = Flask(__name__)

g_ftps2 = 32.174

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
def index():
    return render_template("index.html")

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

# For local dev only (use a real WSGI/ASGI server for production)
if __name__ == "__main__":
    app.run(debug=True)