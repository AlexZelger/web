"""
build_index.py — Build the master player index from cached stats
================================================================
Run this after warm_cache.py has finished. Reads every cached JSON
file and builds a single player_index.json that maps each player to
their full career history (teams, years, and stat values).

This index powers instant autocomplete search during gameplay.

Usage:
    python build_index.py
"""

import json
import logging
from pathlib import Path
from collections import defaultdict

import pandas as pd

CACHE_DIR  = Path(__file__).parent / "stat_cache"
INDEX_PATH = Path(__file__).parent / "player_index.json"

YEAR_RANGE = (1990, 2025)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Division mappings
# Used by the game to generate division-based prompts and validate picks.
# ---------------------------------------------------------------------------

DIVISIONS = {
    "AL East":    ["NYY", "BOS", "TBR", "TOR", "BAL"],
    "AL Central": ["CHW", "CLE", "DET", "KCR", "MIN"],
    "AL West":    ["HOU", "LAA", "OAK", "SEA", "TEX"],
    "NL East":    ["ATL", "MIA", "NYM", "PHI", "WSN"],
    "NL Central": ["CHC", "CIN", "MIL", "PIT", "STL"],
    "NL West":    ["ARI", "COL", "LAD", "SDP", "SFG"],
}

# Reverse map: team → division
TEAM_TO_DIVISION = {
    team: div
    for div, teams in DIVISIONS.items()
    for team in teams
}


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def build_index() -> dict:
    """
    Reads all cached batting and pitching JSONs and builds a player index.

    Structure of player_index.json:
    {
        "Bryce Harper": {
            "type": "batter",          // "batter" or "pitcher"
            "seasons": [
                {
                    "year": 2015,
                    "team": "WSN",
                    "division": "NL East",
                    "HR": 42,
                    "RBI": 99,
                    "AVG": 0.330,
                    "WAR": 9.9
                },
                ...
            ]
        },
        ...
    }
    """
    # player_name → { type, seasons: [ {year, team, division, stats...} ] }
    index = defaultdict(lambda: {"type": None, "seasons": []})

    years = list(range(*YEAR_RANGE))
    years.append(YEAR_RANGE[1])  # inclusive end

    batting_files  = 0
    pitching_files = 0
    missing        = []

    for year in years:

        # --- Batting ---
        bat_path = CACHE_DIR / f"batting_{year}.json"
        if bat_path.exists():
            df = pd.read_json(bat_path, orient="records")
            batting_files += 1
            for _, row in df.iterrows():
                name = row["Name"]
                team = row.get("Team", "")
                index[name]["type"] = "batter"
                index[name]["seasons"].append({
                    "year":     int(row["year"]) if "year" in row else year,
                    "team":     team,
                    "division": TEAM_TO_DIVISION.get(team, "Unknown"),
                    "HR":       int(row["HR"])    if pd.notna(row.get("HR"))  else None,
                    "RBI":      int(row["RBI"])   if pd.notna(row.get("RBI")) else None,
                    "AVG":      float(row["AVG"]) if pd.notna(row.get("AVG")) else None,
                    "WAR":      float(row["WAR"]) if pd.notna(row.get("WAR")) else None,
                })
        else:
            missing.append(f"batting_{year}.json")

        # --- Pitching ---
        pit_path = CACHE_DIR / f"pitching_{year}.json"
        if pit_path.exists():
            df = pd.read_json(pit_path, orient="records")
            pitching_files += 1
            for _, row in df.iterrows():
                name = row["Name"]
                team = row.get("Team", "")
                # Don't overwrite type if this player also has batting entries
                if index[name]["type"] is None:
                    index[name]["type"] = "pitcher"
                index[name]["seasons"].append({
                    "year":     int(row["year"]) if "year" in row else year,
                    "team":     team,
                    "division": TEAM_TO_DIVISION.get(team, "Unknown"),
                    "ERA":      float(row["ERA"]) if pd.notna(row.get("ERA")) else None,
                    "WAR":      float(row["WAR"]) if pd.notna(row.get("WAR")) else None,
                })
        else:
            missing.append(f"pitching_{year}.json")

    # Convert defaultdict to plain dict for JSON serialisation
    plain = dict(index)

    # Sort each player's seasons chronologically
    for name in plain:
        plain[name]["seasons"].sort(key=lambda s: s["year"])

    return plain, batting_files, pitching_files, missing


def save_index(index: dict) -> None:
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Building player index from cache…")

    if not CACHE_DIR.exists():
        print("ERROR: stat_cache/ directory not found.")
        print("Run  python warm_cache.py  first.")
        raise SystemExit(1)

    index, batting_files, pitching_files, missing = build_index()

    save_index(index)

    print("\n" + "=" * 50)
    print("  Player index built!")
    print(f"  Batting files read:  {batting_files}")
    print(f"  Pitching files read: {pitching_files}")
    print(f"  Unique players:      {len(index)}")
    if missing:
        print(f"\n  Missing cache files ({len(missing)}) — run warm_cache.py --missing-only:")
        for f in missing[:10]:
            print(f"    {f}")
        if len(missing) > 10:
            print(f"    … and {len(missing) - 10} more")
    print(f"\n  Index saved → {INDEX_PATH}")
    print("=" * 50)
    print("\nNext step: run  python data.py  to verify search works.")