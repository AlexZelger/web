"""
data.py — Baseball Draft Game: Data Layer (v2)
===============================================
All lookups are served from the pre-built player_index.json.
No network calls happen during gameplay — everything is instant.

Public API (used by game.py):
    search_players(query, team=None, division=None, year_min=None, year_max=None)
    get_valid_years(player_name, team=None, division=None, year_min=None, year_max=None)
    get_stat(player_name, year, stat_key)
    get_best_stat(player_name, stat_key, team=None, division=None, year_min=None, year_max=None)
    generate_prompts(stat_key, n=5)
    random_stat_key()
"""

import json
import random
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_PATH = Path(__file__).parent / "player_index.json"

STAT_CONFIG = {
    "HR":  {"label": "Home Runs",       "type": "batting",  "ascending": False},
    "RBI": {"label": "RBI",             "type": "batting",  "ascending": False},
    "AVG": {"label": "Batting Average", "type": "batting",  "ascending": False},
    "WAR": {"label": "WAR",             "type": "both",     "ascending": False},
    "ERA": {"label": "ERA",             "type": "pitching", "ascending": True},
}

DIVISIONS = {
    "AL East":    ["NYY", "BOS", "TBR", "TOR", "BAL"],
    "AL Central": ["CHW", "CLE", "DET", "KCR", "MIN"],
    "AL West":    ["HOU", "LAA", "OAK", "SEA", "TEX"],
    "NL East":    ["ATL", "MIA", "NYM", "PHI", "WSN"],
    "NL Central": ["CHC", "CIN", "MIL", "PIT", "STL"],
    "NL West":    ["ARI", "COL", "LAD", "SDP", "SFG"],
}

TEAM_TO_DIVISION = {
    team: div
    for div, teams in DIVISIONS.items()
    for team in teams
}

ALL_TEAMS = [t for teams in DIVISIONS.values() for t in teams]

# MLB team IDs used by mlbstatic.com CDN for team logos
# Logo URL: https://www.mlbstatic.com/team-logos/{TEAM_ID}.svg
TEAM_LOGO_IDS = {
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CHW": 145, "CIN": 113, "CLE": 114, "COL": 115, "DET": 116,
    "HOU": 117, "KCR": 118, "LAA": 108, "LAD": 119, "MIA": 146,
    "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "OAK": 133,
    "PHI": 143, "PIT": 134, "SDP": 135, "SEA": 136, "SFG": 137,
    "STL": 138, "TBR": 139, "TEX": 140, "TOR": 141, "WSN": 120,
}

def get_team_logo_url(team_abbr: str) -> str | None:
    """Return the MLB CDN logo URL for a team abbreviation, or None if unknown."""
    team_id = TEAM_LOGO_IDS.get(team_abbr)
    if not team_id:
        return None
    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"

# Prompt types the game can generate
PROMPT_TYPES = ["team_with_range", "team_only", "division_with_range", "division_only"]

YEAR_RANGE = (1990, 2023)


# ---------------------------------------------------------------------------
# Index loader (singleton — loaded once at import time)
# ---------------------------------------------------------------------------

_index: dict | None = None


def _load_index() -> dict:
    global _index
    if _index is None:
        if not INDEX_PATH.exists():
            raise FileNotFoundError(
                f"player_index.json not found at {INDEX_PATH}.\n"
                "Run  python warm_cache.py  then  python build_index.py  first."
            )
        with open(INDEX_PATH) as f:
            _index = json.load(f)
        logger.info("Player index loaded: %d players", len(_index))
    return _index


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _season_matches(season: dict,
                    team: str | None,
                    division: str | None,
                    year_min: int | None,
                    year_max: int | None) -> bool:
    """Return True if a season dict passes all active filters."""
    if year_min and season["year"] < year_min:
        return False
    if year_max and season["year"] > year_max:
        return False
    if team and season["team"] != team:
        return False
    if division and season.get("division") != division:
        return False
    return True


def _matching_seasons(player_name: str,
                      team: str | None = None,
                      division: str | None = None,
                      year_min: int | None = None,
                      year_max: int | None = None) -> list[dict]:
    """Return all seasons for a player that pass the given filters."""
    index = _load_index()
    entry = index.get(player_name)
    if not entry:
        return []
    return [
        s for s in entry["seasons"]
        if _season_matches(s, team, division, year_min, year_max)
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_players(query: str,
                   team: str | None = None,
                   division: str | None = None,
                   year_min: int | None = None,
                   year_max: int | None = None,
                   stat_key: str | None = None,
                   limit: int = 10) -> list[dict]:
    """
    Autocomplete search. Returns players whose name contains `query`
    and who have at least one qualifying season matching the filters.

    Each result dict:
        name, type, matching_seasons (count), teams (list of unique teams)

    Example:
        search_players("harper", team="WSN", year_min=2012, year_max=2018)
    """
    index = _load_index()
    query_lower = query.strip().lower()
    results = []

    stat_type = STAT_CONFIG[stat_key]["type"] if stat_key else None

    # The index stores "batter" / "pitcher" — map config types to match
    STAT_TYPE_TO_PLAYER_TYPE = {
        "batting":  "batter",
        "pitching": "pitcher",
    }

    for name, entry in index.items():
        if query_lower not in name.lower():
            continue

        # Filter by stat type (batting vs pitching), skip filter for "both" (WAR)
        if stat_type and stat_type != "both":
            expected_player_type = STAT_TYPE_TO_PLAYER_TYPE.get(stat_type)
            if entry["type"] != expected_player_type:
                continue

        # Check if any seasons match the prompt constraints
        matching = _matching_seasons(name, team, division, year_min, year_max)

        # Also filter seasons that have a value for the requested stat
        if stat_key:
            matching = [s for s in matching if s.get(stat_key) is not None]

        if not matching:
            continue

        unique_teams = list({s["team"] for s in matching})
        results.append({
            "name":             name,
            "type":             entry["type"],
            "matching_seasons": len(matching),
            "teams":            sorted(unique_teams),
        })

    # Sort by most matching seasons (most relevant players first)
    results.sort(key=lambda r: r["matching_seasons"], reverse=True)
    return results[:limit]


def get_valid_years(player_name: str,
                    team: str | None = None,
                    division: str | None = None,
                    year_min: int | None = None,
                    year_max: int | None = None,
                    stat_key: str | None = None) -> list[int]:
    """
    Return the list of years a player qualifies for, given the slot constraints.
    Used to populate the year dropdown after a player is selected.

    Example:
        get_valid_years("Bryce Harper", team="WSN", year_min=2012, year_max=2018)
        → [2012, 2013, 2014, 2015, 2016, 2017, 2018]
    """
    seasons = _matching_seasons(player_name, team, division, year_min, year_max)
    if stat_key:
        seasons = [s for s in seasons if s.get(stat_key) is not None]
    return sorted({s["year"] for s in seasons})


def get_stat(player_name: str, year: int, stat_key: str) -> float | int | None:
    """
    Look up a single stat value for a player in a specific year.
    Returns None if the player/year/stat combination doesn't exist.

    Example:
        get_stat("Bryce Harper", 2015, "HR")  → 42
    """
    index = _load_index()
    entry = index.get(player_name)
    if not entry:
        return None
    for season in entry["seasons"]:
        if season["year"] == year:
            return season.get(stat_key)
    return None


def get_best_stat(player_name: str,
                  stat_key: str,
                  team: str | None = None,
                  division: str | None = None,
                  year_min: int | None = None,
                  year_max: int | None = None) -> dict | None:
    """
    Find the player's best season for a given stat within the slot constraints.
    Returns { year, value } or None.

    Used on the results screen to show "best possible pick" for each slot.

    Example:
        get_best_stat("Bryce Harper", "HR", team="WSN")
        → { "year": 2015, "value": 42 }
    """
    seasons = _matching_seasons(player_name, team, division, year_min, year_max)
    seasons = [s for s in seasons if s.get(stat_key) is not None]
    if not seasons:
        return None

    ascending = STAT_CONFIG[stat_key]["ascending"]
    best = min(seasons, key=lambda s: s[stat_key]) if ascending \
           else max(seasons, key=lambda s: s[stat_key])

    return {"year": best["year"], "value": best[stat_key]}


# ---------------------------------------------------------------------------
# Prompt generator
# ---------------------------------------------------------------------------

def generate_prompts(stat_key: str, n: int = 5) -> list[dict]:
    """
    Generate n random slot prompts for a game round.

    Each prompt is one of:
        { type: "team",     team: "PHI",     year_min: 2010, year_max: 2020, label: "..." }
        { type: "team",     team: "NYY",     year_min: None, year_max: None, label: "..." }
        { type: "division", division: "AL East", year_min: 2005, year_max: 2015, label: "..." }
        { type: "division", division: "NL West", year_min: None, year_max: None, label: "..." }

    Only prompts that have at least MIN_PLAYERS eligible players are kept,
    so every slot is always answerable.
    """
    MIN_PLAYERS = 5
    prompts = []
    attempts = 0
    max_attempts = 100

    while len(prompts) < n and attempts < max_attempts:
        attempts += 1
        prompt_type = random.choice(PROMPT_TYPES)
        prompt = _build_prompt(prompt_type, stat_key)

        # Verify that enough players exist for this prompt
        eligible = search_players("", limit=MIN_PLAYERS + 1, stat_key=stat_key,
                                  team=prompt.get("team"),
                                  division=prompt.get("division"),
                                  year_min=prompt.get("year_min"),
                                  year_max=prompt.get("year_max"))
        if len(eligible) >= MIN_PLAYERS:
            prompts.append(prompt)

    if len(prompts) < n:
        logger.warning("Could only generate %d valid prompts (wanted %d)", len(prompts), n)

    return prompts


def _build_prompt(prompt_type: str, stat_key: str) -> dict:
    """Build a single raw prompt dict."""
    if prompt_type in ("team_with_range", "team_only"):
        team = random.choice(ALL_TEAMS)
        if prompt_type == "team_with_range":
            year_min, year_max = _random_year_range()
            label = f"A player who played for the {team} between {year_min}–{year_max}"
        else:
            year_min = year_max = None
            label = f"A player who played for the {team} at any point"
        return {"type": "team", "team": team, "division": None,
                "year_min": year_min, "year_max": year_max, "label": label}

    else:  # division_with_range or division_only
        division = random.choice(list(DIVISIONS.keys()))
        if prompt_type == "division_with_range":
            year_min, year_max = _random_year_range()
            label = f"A player who played in the {division} between {year_min}–{year_max}"
        else:
            year_min = year_max = None
            label = f"A player who played in the {division} at any point"
        return {"type": "division", "team": None, "division": division,
                "year_min": year_min, "year_max": year_max, "label": label}


def _random_year_range() -> tuple[int, int]:
    """Pick a random year window of 5–15 years within YEAR_RANGE."""
    window = random.randint(5, 15)
    start  = random.randint(YEAR_RANGE[0], YEAR_RANGE[1] - window)
    return start, start + window


def random_stat_key() -> str:
    return random.choice(list(STAT_CONFIG.keys()))


# ---------------------------------------------------------------------------
# Smoke test  (python data.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    stat = random_stat_key()
    print(f"\nStat for this round: {STAT_CONFIG[stat]['label']} ({stat})")

    print("\n--- Testing search_players ---")
    results = search_players("harper", stat_key=stat)
    for r in results:
        print(f"  {r['name']:25s}  {r['type']:8s}  {r['matching_seasons']} seasons  {r['teams']}")

    print("\n--- Testing get_valid_years ---")
    years = get_valid_years("Bryce Harper", team="WSN", stat_key=stat)
    print(f"  Bryce Harper on WSN: {years}")

    print("\n--- Testing get_stat ---")
    val = get_stat("Bryce Harper", 2015, "HR")
    print(f"  Bryce Harper HR 2015: {val}")

    print("\n--- Testing get_best_stat ---")
    best = get_best_stat("Bryce Harper", "HR", team="WSN")
    print(f"  Bryce Harper best HR on WSN: {best}")

    print("\n--- Generating 5 prompts ---")
    prompts = generate_prompts(stat, n=5)
    for i, p in enumerate(prompts, 1):
        print(f"  Slot {i}: {p['label']}")