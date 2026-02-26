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
    "SB":  {"label": "Stolen Bases",    "type": "batting",  "ascending": False},
    "3B":  {"label": "Triples",         "type": "batting",  "ascending": False},
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

# ---------------------------------------------------------------------------
# Prompt type definitions
# ---------------------------------------------------------------------------
#
# Each prompt type is a string key. _build_prompt() switches on these.
# Weights control how often each type is chosen — rarer/harder types are
# given lower weight so the game stays approachable.
#
PROMPT_TYPE_WEIGHTS = {
    # Original types
    "team_with_range":      4,
    "team_only":            3,
    "division_with_range":  3,
    "division_only":        2,
    # New types
    "multi_team":           2,   # played for N+ teams in career
    "al_only":              2,   # career exclusively in AL
    "nl_only":              2,   # career exclusively in NL
    "min_stat_team":        3,   # hit X+ [stat] in a season for [team]
    "min_stat_division":    2,   # hit X+ [stat] in a season for any [division] team
    "rival_pair":           2,   # played for both teams in a rivalry
}

# Weighted list for random.choices()
_PROMPT_TYPES  = list(PROMPT_TYPE_WEIGHTS.keys())
_PROMPT_WEIGHTS = list(PROMPT_TYPE_WEIGHTS.values())

# Classic rivalries — pairs of teams where playing for both is interesting
RIVAL_PAIRS = [
    ("NYY", "BOS"),   # Yankees / Red Sox
    ("NYY", "NYM"),   # Subway Series
    ("LAD", "SFG"),   # Dodgers / Giants
    ("CHC", "STL"),   # Cubs / Cardinals
    ("CHW", "CHC"),   # Chicago crosstown
    ("LAD", "LAA"),   # LA crosstown
    ("OAK", "SFG"),   # Bay Bridge
    ("NYM", "PHI"),   # NL East rivals
    ("BOS", "TOR"),   # AL East
    ("HOU", "TEX"),   # AL West rivals
]

# Minimum stat thresholds for min_stat prompts
# Tuned so that a reasonable pool of players qualifies (not too rare)
MIN_STAT_THRESHOLDS = {
    "HR":  [20, 25, 30, 35, 40],
    "RBI": [80, 90, 100, 110],
    "SB":  [20, 30, 40, 50],
    "3B":  [5, 8, 10, 12],
    "AVG": [0.290, 0.300, 0.310, 0.320],
    "WAR": [3.0, 4.0, 5.0, 6.0],
    # ERA: ascending — "ERA below X" thresholds
    "ERA": [3.50, 3.25, 3.00, 2.75],
}

YEAR_RANGE = (1990, 2025)


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
                    year_max: int | None,
                    min_stat_key: str | None = None,
                    min_stat_val: float | None = None) -> bool:
    """Return True if a season dict passes all active filters."""
    if year_min and season["year"] < year_min:
        return False
    if year_max and season["year"] > year_max:
        return False
    if team and season["team"] != team:
        return False
    if division and season.get("division") != division:
        return False
    # Minimum stat threshold — season must have the stat at or beyond the threshold
    if min_stat_key is not None and min_stat_val is not None:
        v = season.get(min_stat_key)
        if v is None:
            return False
        stat_cfg = STAT_CONFIG.get(min_stat_key, {})
        if stat_cfg.get("ascending"):
            # ERA-style: lower is better, so "min" means "at most"
            if v > min_stat_val:
                return False
        else:
            if v < min_stat_val:
                return False
    return True


def _matching_seasons(player_name: str,
                      team: str | None = None,
                      division: str | None = None,
                      year_min: int | None = None,
                      year_max: int | None = None,
                      min_stat_key: str | None = None,
                      min_stat_val: float | None = None) -> list[dict]:
    """Return all seasons for a player that pass the given filters."""
    index = _load_index()
    entry = index.get(player_name)
    if not entry:
        return []
    return [
        s for s in entry["seasons"]
        if _season_matches(s, team, division, year_min, year_max,
                           min_stat_key, min_stat_val)
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
                   limit: int = 10,
                   # New constraint fields
                   min_teams: int | None = None,           # career must span N+ teams
                   league: str | None = None,              # "AL" or "NL" (exclusive)
                   rival_team: str | None = None,          # must ALSO have played for this team
                   min_stat_key: str | None = None,        # stat key for threshold filter
                   min_stat_val: float | None = None,      # threshold value
                   ) -> list[dict]:
    """
    Autocomplete search. Returns players whose name contains `query`
    and who have at least one qualifying season matching all filters.

    New constraint parameters:
        min_teams    — player must have played for at least this many distinct teams
        league       — "AL" or "NL": player's entire career must be in that league only
        rival_team   — player must have also played for this additional team at any point
        min_stat_key/val — player must have had at least one season meeting this threshold
                           (combined with team/division filters if those are set)
    """
    index = _load_index()
    query_lower = query.strip().lower()
    results = []

    stat_type = STAT_CONFIG[stat_key]["type"] if stat_key else None

    STAT_TYPE_TO_PLAYER_TYPE = {
        "batting":  "batter",
        "pitching": "pitcher",
    }

    for name, entry in index.items():
        if query_lower not in name.lower():
            continue

        # Filter by stat type
        if stat_type and stat_type != "both":
            expected_player_type = STAT_TYPE_TO_PLAYER_TYPE.get(stat_type)
            if entry["type"] != expected_player_type:
                continue

        # ── Career-level filters (use pre-computed summary fields) ──────

        # Multi-team: career must span N+ distinct teams
        if min_teams is not None:
            if entry.get("career_team_count", 0) < min_teams:
                continue

        # League exclusivity: all career teams must be in the specified league
        if league is not None:
            career_leagues = entry.get("career_leagues", [])
            # Must have played in the league AND never played in the other
            if league not in career_leagues:
                continue
            other = "NL" if league == "AL" else "AL"
            if other in career_leagues:
                continue

        # Rival pair: must have played for rival_team at some point in career
        if rival_team is not None:
            if rival_team not in entry.get("career_teams", []):
                continue

        # ── Season-level filters ─────────────────────────────────────────

        # matching seasons respects team/division/year range + min stat threshold
        matching = _matching_seasons(name, team, division, year_min, year_max,
                                     min_stat_key, min_stat_val)

        # Filter seasons that have a value for the scoring stat
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

    results.sort(key=lambda r: r["matching_seasons"], reverse=True)
    return results[:limit]


def get_valid_years(player_name: str,
                    team: str | None = None,
                    division: str | None = None,
                    year_min: int | None = None,
                    year_max: int | None = None,
                    stat_key: str | None = None,
                    min_stat_key: str | None = None,
                    min_stat_val: float | None = None) -> list[int]:
    """
    Return the list of years a player qualifies for, given the slot constraints.
    Used to populate the year dropdown after a player is selected.

    Example:
        get_valid_years("Bryce Harper", team="WSN", year_min=2012, year_max=2018)
        → [2012, 2013, 2014, 2015, 2016, 2017, 2018]
    """
    seasons = _matching_seasons(player_name, team, division, year_min, year_max,
                                min_stat_key, min_stat_val)
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

# Prompt types that are valid per stat category
# "both" stats (WAR) only get the simple positional types — career-level
# filters are unreliable across the mixed batter/pitcher index.
_VALID_PROMPT_TYPES: dict[str, list[str]] = {
    "batting":  list(PROMPT_TYPE_WEIGHTS.keys()),   # all types
    "pitching": ["team_with_range", "team_only",
                 "division_with_range", "division_only"],
    "both":     ["team_with_range", "team_only",
                 "division_with_range", "division_only"],
}

# Simple types that are virtually guaranteed to have enough eligible players.
# Used as guaranteed fallbacks when exotic types keep failing.
_SIMPLE_PROMPT_TYPES   = ["team_with_range", "team_only",
                           "division_with_range", "division_only"]
_SIMPLE_PROMPT_WEIGHTS = [4, 3, 3, 2]


def generate_prompts(stat_key: str, n: int = 5) -> list[dict]:
    """
    Generate n random slot prompts for a game round.

    Strategy:
      - Phase 1: try up to 60 attempts using the full weighted pool
        (filtered to types valid for this stat).
      - Phase 2: if still short, fill remaining slots using only the four
        simple team/division types which always have plenty of players.

    This guarantees we always return n prompts while still surfacing
    exotic constraint types whenever they successfully generate.
    """
    MIN_PLAYERS = 5

    stat_type      = STAT_CONFIG[stat_key]["type"]   # "batting" | "pitching" | "both"
    valid_types    = _VALID_PROMPT_TYPES[stat_type]
    valid_weights  = [PROMPT_TYPE_WEIGHTS[t] for t in valid_types]

    prompts  = []

    # ── Phase 1: attempt exotic + simple types ───────────────────────────
    for _ in range(60):
        if len(prompts) >= n:
            break

        prompt_type = random.choices(valid_types, weights=valid_weights, k=1)[0]

        try:
            prompt = _build_prompt(prompt_type, stat_key)
        except ValueError:
            continue

        eligible = _count_eligible(prompt, stat_key, MIN_PLAYERS)
        if eligible >= MIN_PLAYERS:
            prompts.append(prompt)

    # ── Phase 2: guaranteed fallback using simple types only ─────────────
    fallback_attempts = 0
    while len(prompts) < n and fallback_attempts < 100:
        fallback_attempts += 1
        prompt_type = random.choices(_SIMPLE_PROMPT_TYPES,
                                     weights=_SIMPLE_PROMPT_WEIGHTS, k=1)[0]
        try:
            prompt = _build_prompt(prompt_type, stat_key)
        except ValueError:
            continue

        eligible = _count_eligible(prompt, stat_key, MIN_PLAYERS)
        if eligible >= MIN_PLAYERS:
            prompts.append(prompt)

    if len(prompts) < n:
        logger.warning("Could only generate %d valid prompts (wanted %d)",
                       len(prompts), n)

    return prompts


def _count_eligible(prompt: dict, stat_key: str, limit: int) -> int:
    """Return the number of eligible players for a prompt, up to limit+1."""
    results = search_players(
        "", limit=limit + 1, stat_key=stat_key,
        team=prompt.get("team"),
        division=prompt.get("division"),
        year_min=prompt.get("year_min"),
        year_max=prompt.get("year_max"),
        min_teams=prompt.get("min_teams"),
        league=prompt.get("league"),
        rival_team=prompt.get("rival_team"),
        min_stat_key=prompt.get("min_stat_key"),
        min_stat_val=prompt.get("min_stat_val"),
    )
    return len(results)


def _build_prompt(prompt_type: str, stat_key: str) -> dict:
    """
    Build a single prompt dict for the given type.
    Raises ValueError if a valid prompt cannot be constructed
    (e.g. no thresholds available for this stat).

    Every prompt dict always contains these keys so callers never
    need to guard for missing fields:
        type, team, division, year_min, year_max,
        min_teams, league, rival_team,
        min_stat_key, min_stat_val, label
    """
    # Base template — all fields default to None
    base = {
        "team": None, "division": None,
        "year_min": None, "year_max": None,
        "min_teams": None, "league": None, "rival_team": None,
        "min_stat_key": None, "min_stat_val": None,
    }

    stat_label = STAT_CONFIG[stat_key]["label"]

    # ── Original prompt types ────────────────────────────────────────────

    if prompt_type in ("team_with_range", "team_only"):
        team = random.choice(ALL_TEAMS)
        if prompt_type == "team_with_range":
            year_min, year_max = _random_year_range()
            label = f"A player who played for the {team} between {year_min}–{year_max}"
        else:
            year_min = year_max = None
            label = f"A player who played for the {team} at any point"
        return {**base, "type": "team", "team": team,
                "year_min": year_min, "year_max": year_max, "label": label}

    if prompt_type in ("division_with_range", "division_only"):
        division = random.choice(list(DIVISIONS.keys()))
        if prompt_type == "division_with_range":
            year_min, year_max = _random_year_range()
            label = f"A player who played in the {division} between {year_min}–{year_max}"
        else:
            year_min = year_max = None
            label = f"A player who played in the {division} at any point"
        return {**base, "type": "division", "division": division,
                "year_min": year_min, "year_max": year_max, "label": label}

    # ── New prompt types ─────────────────────────────────────────────────

    if prompt_type == "multi_team":
        n_teams = random.choice([3, 4, 5])
        label   = f"A player who played for at least {n_teams} different teams in their career"
        return {**base, "type": "multi_team", "min_teams": n_teams, "label": label}

    if prompt_type == "al_only":
        label = "A player who spent their entire career in the American League"
        return {**base, "type": "league", "league": "AL", "label": label}

    if prompt_type == "nl_only":
        label = "A player who spent their entire career in the National League"
        return {**base, "type": "league", "league": "NL", "label": label}

    if prompt_type == "rival_pair":
        team_a, team_b = random.choice(RIVAL_PAIRS)
        label = f"A player who played for both the {team_a} and the {team_b}"
        # team_a is the primary filter; rival_team ensures they also played for team_b
        return {**base, "type": "rival_pair",
                "team": team_a, "rival_team": team_b, "label": label}

    if prompt_type == "min_stat_team":
        thresholds = MIN_STAT_THRESHOLDS.get(stat_key)
        if not thresholds:
            raise ValueError(f"No thresholds for stat {stat_key}")
        threshold = random.choice(thresholds)
        team      = random.choice(ALL_TEAMS)
        ascending = STAT_CONFIG[stat_key]["ascending"]
        if ascending:
            label = (f"A player with an ERA below {threshold:.2f} "
                     f"in a season with the {team}")
        else:
            label = (f"A player with at least {threshold} {stat_label} "
                     f"in a season with the {team}")
        return {**base, "type": "min_stat_team",
                "team": team,
                "min_stat_key": stat_key, "min_stat_val": threshold,
                "label": label}

    if prompt_type == "min_stat_division":
        thresholds = MIN_STAT_THRESHOLDS.get(stat_key)
        if not thresholds:
            raise ValueError(f"No thresholds for stat {stat_key}")
        threshold = random.choice(thresholds)
        division  = random.choice(list(DIVISIONS.keys()))
        ascending = STAT_CONFIG[stat_key]["ascending"]
        if ascending:
            label = (f"A player with an ERA below {threshold:.2f} "
                     f"in a season in the {division}")
        else:
            label = (f"A player with at least {threshold} {stat_label} "
                     f"in a season in the {division}")
        return {**base, "type": "min_stat_division",
                "division": division,
                "min_stat_key": stat_key, "min_stat_val": threshold,
                "label": label}

    raise ValueError(f"Unknown prompt type: {prompt_type}")


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