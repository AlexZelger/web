"""
nfl_data.py — NFL Draft Game: Data Layer
=========================================
Mirror of data.py but for NFL. All lookups are served from the pre-built
nfl_player_index.json (see nfl_build_index.py). No network calls happen
during gameplay.

Public API (used by nfl_game.py):
    search_players(query, team=None, division=None, year_min=None, year_max=None, ...)
    get_valid_years(player_name, team=None, division=None, ...)
    get_stat(player_name, year, stat_key)
    get_best_stat(player_name, stat_key, team=None, division=None, ...)
    get_top_stats(stat_key, n=5, team=None, ...)
    generate_prompts(stat_key, n=5)
    random_stat_key()
    get_team_logo_url(team_abbr)
"""

import json
import random
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_PATH = Path(__file__).parent / "nfl_player_index.json"

# Stats the game can quiz on.
#
# type maps to the player's position bucket:
#   passing   — QBs (and occasional trick-play passers)
#   rushing   — RBs + QBs (both rack up rushing yards; we include QB rushing)
#   receiving — WR / TE / RB receiving
#
# "ascending: True" means lower is better (interceptions).
# For passer rating, higher is better, so ascending=False.
STAT_CONFIG = {
    "PASS_YDS":     {"label": "Passing Yards",   "type": "passing",   "ascending": False},
    "PASS_TD":      {"label": "Passing TDs",     "type": "passing",   "ascending": False},
    "PASS_INT":     {"label": "Interceptions",   "type": "passing",   "ascending": True},
    "PASS_RATING":  {"label": "Passer Rating",   "type": "passing",   "ascending": False},
    "RUSH_YDS":     {"label": "Rushing Yards",   "type": "rushing",   "ascending": False},
    "RUSH_TD":      {"label": "Rushing TDs",     "type": "rushing",   "ascending": False},
    "REC":          {"label": "Receptions",      "type": "receiving", "ascending": False},
    "REC_YDS":      {"label": "Receiving Yards", "type": "receiving", "ascending": False},
    "REC_TD":       {"label": "Receiving TDs",   "type": "receiving", "ascending": False},
}

# NFL divisions (current alignment). We normalize historical abbreviations
# (OAK→LV, STL→LA, SD→LAC) at index-build time so any season from 2000–2025
# maps cleanly into one of these 32 teams.
DIVISIONS = {
    "AFC East":    ["BUF", "MIA", "NE",  "NYJ"],
    "AFC North":   ["BAL", "CIN", "CLE", "PIT"],
    "AFC South":   ["HOU", "IND", "JAX", "TEN"],
    "AFC West":    ["DEN", "KC",  "LV",  "LAC"],
    "NFC East":    ["DAL", "NYG", "PHI", "WAS"],
    "NFC North":   ["CHI", "DET", "GB",  "MIN"],
    "NFC South":   ["ATL", "CAR", "NO",  "TB"],
    "NFC West":    ["ARI", "LA",  "SF",  "SEA"],
}

TEAM_TO_DIVISION = {
    team: div
    for div, teams in DIVISIONS.items()
    for team in teams
}

ALL_TEAMS = [t for teams in DIVISIONS.values() for t in teams]

# ESPN CDN hosts NFL team logos at a predictable path.
# Abbreviation lowercased; a handful of ESPN-specific quirks covered in the map.
_ESPN_LOGO_ABBR = {
    # nflverse abbr → ESPN abbr
    "LA":  "lar",
    "LV":  "lv",
    "JAX": "jax",
    "WAS": "wsh",
    "NE":  "ne",
    "NO":  "no",
    "SF":  "sf",
    "TB":  "tb",
    "GB":  "gb",
    "KC":  "kc",
    "LAC": "lac",
}


def get_team_logo_url(team_abbr: str) -> str | None:
    """Return the ESPN CDN logo URL for an NFL team abbreviation, or None."""
    if not team_abbr:
        return None
    if team_abbr not in TEAM_TO_DIVISION:
        return None
    slug = _ESPN_LOGO_ABBR.get(team_abbr, team_abbr.lower())
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{slug}.png"


# ---------------------------------------------------------------------------
# Prompt generation config
# ---------------------------------------------------------------------------

PROMPT_TYPE_WEIGHTS = {
    "team_with_range":      4,
    "team_only":            3,
    "division_with_range":  3,
    "division_only":        2,
    "multi_team":           2,   # played for N+ teams in career
    "afc_only":             2,   # career exclusively in AFC
    "nfc_only":             2,   # career exclusively in NFC
    "min_stat_team":        3,   # hit X+ [stat] in a season for [team]
    "min_stat_division":    2,   # hit X+ [stat] in a season in [division]
    "rival_pair":           2,   # played for both teams in a rivalry
}

_PROMPT_TYPES  = list(PROMPT_TYPE_WEIGHTS.keys())
_PROMPT_WEIGHTS = list(PROMPT_TYPE_WEIGHTS.values())

# Classic NFL rivalries — pairs of teams where playing for both stands out.
RIVAL_PAIRS = [
    ("NE",  "NYJ"),   # AFC East
    ("NE",  "BUF"),
    ("BAL", "PIT"),   # AFC North
    ("CIN", "CLE"),
    ("GB",  "CHI"),   # NFC North
    ("MIN", "GB"),
    ("DAL", "PHI"),   # NFC East
    ("DAL", "NYG"),
    ("WAS", "DAL"),
    ("SF",  "SEA"),   # NFC West
    ("LA",  "SF"),
    ("KC",  "DEN"),   # AFC West
    ("LV",  "KC"),
    ("TB",  "NO"),    # NFC South
    ("IND", "TEN"),   # AFC South
]

# Minimum stat thresholds for min_stat prompts.
# Values tuned so enough players qualify without giving away the answer.
MIN_STAT_THRESHOLDS = {
    "PASS_YDS":    [3500, 4000, 4500, 5000],
    "PASS_TD":     [25, 30, 35, 40],
    "PASS_INT":    [5, 8, 10, 12],         # ascending: "at most"
    "PASS_RATING": [95.0, 100.0, 105.0],
    "RUSH_YDS":    [1000, 1200, 1500, 1800],
    "RUSH_TD":     [8, 10, 12, 15],
    "REC":         [80, 90, 100, 110],
    "REC_YDS":     [1000, 1200, 1400, 1600],
    "REC_TD":      [8, 10, 12, 15],
}

YEAR_RANGE = (2000, 2025)


# ---------------------------------------------------------------------------
# Index loader (singleton)
# ---------------------------------------------------------------------------

_index: dict | None = None


def _load_index() -> dict:
    global _index
    if _index is None:
        if not INDEX_PATH.exists():
            raise FileNotFoundError(
                f"nfl_player_index.json not found at {INDEX_PATH}.\n"
                "Run  python nfl_build_index.py  first."
            )
        with open(INDEX_PATH) as f:
            _index = json.load(f)
        logger.info("NFL player index loaded: %d players", len(_index))
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
    if min_stat_key is not None and min_stat_val is not None:
        v = season.get(min_stat_key)
        if v is None:
            return False
        stat_cfg = STAT_CONFIG.get(min_stat_key, {})
        if stat_cfg.get("ascending"):
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


# Map a stat "type" to the player position buckets that are eligible.
#
# Passing stats — only pure passers (QB).
# Rushing stats — QBs count (scrambling rushing yards/TDs) plus RB/WR.
# Receiving stats — WR, TE, RB.
STAT_TYPE_TO_POSITIONS = {
    "passing":   {"QB"},
    "rushing":   {"QB", "RB", "FB"},
    "receiving": {"WR", "TE", "RB", "FB"},
}


def _position_matches_stat(entry: dict, stat_key: str | None) -> bool:
    if not stat_key:
        return True
    stat_type = STAT_CONFIG[stat_key]["type"]
    allowed = STAT_TYPE_TO_POSITIONS.get(stat_type)
    if not allowed:
        return True
    positions = set(entry.get("positions") or [])
    if not positions:
        # No position info — allow through (best-effort); the stat-value
        # filter below will cull players who never posted that stat anyway.
        return True
    return bool(positions & allowed)


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
                   min_teams: int | None = None,
                   league: str | None = None,              # "AFC" or "NFC"
                   rival_team: str | None = None,
                   min_stat_key: str | None = None,
                   min_stat_val: float | None = None,
                   ) -> list[dict]:
    """
    Autocomplete search. Returns players whose name contains `query` and who
    have at least one qualifying season matching all filters.
    """
    index = _load_index()
    query_lower = query.strip().lower()
    results = []

    for name, entry in index.items():
        if query_lower not in name.lower():
            continue

        if not _position_matches_stat(entry, stat_key):
            continue

        # Career-level filters
        if min_teams is not None:
            if entry.get("career_team_count", 0) < min_teams:
                continue

        if league is not None:
            career_leagues = entry.get("career_leagues", [])
            if league not in career_leagues:
                continue
            other = "NFC" if league == "AFC" else "AFC"
            if other in career_leagues:
                continue

        if rival_team is not None:
            if rival_team not in entry.get("career_teams", []):
                continue

        # Season-level filters
        matching = _matching_seasons(name, team, division, year_min, year_max,
                                     min_stat_key, min_stat_val)

        if stat_key:
            matching = [s for s in matching if s.get(stat_key) is not None]

        if not matching:
            continue

        unique_teams = list({s["team"] for s in matching})
        results.append({
            "name":             name,
            "type":             entry.get("primary_position", "player"),
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
    """Return the list of years a player qualifies for given slot constraints."""
    seasons = _matching_seasons(player_name, team, division, year_min, year_max,
                                min_stat_key, min_stat_val)
    if stat_key:
        seasons = [s for s in seasons if s.get(stat_key) is not None]
    return sorted({s["year"] for s in seasons})


def get_stat(player_name: str, year: int, stat_key: str) -> float | int | None:
    """Look up a single stat value for a player in a specific year."""
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
    """Find the player's best season for a given stat within slot constraints."""
    seasons = _matching_seasons(player_name, team, division, year_min, year_max)
    seasons = [s for s in seasons if s.get(stat_key) is not None]
    if not seasons:
        return None

    ascending = STAT_CONFIG[stat_key]["ascending"]
    best = min(seasons, key=lambda s: s[stat_key]) if ascending \
           else max(seasons, key=lambda s: s[stat_key])

    return {"year": best["year"], "value": best[stat_key]}


def get_top_stats(stat_key: str,
                  n: int = 5,
                  team: str | None = None,
                  division: str | None = None,
                  year_min: int | None = None,
                  year_max: int | None = None,
                  min_stat_key: str | None = None,
                  min_stat_val: float | None = None,
                  min_teams: int | None = None,
                  league: str | None = None,
                  rival_team: str | None = None) -> list[dict]:
    """Return the top N player-seasons for a stat matching a prompt's constraints."""
    index     = _load_index()
    ascending = STAT_CONFIG[stat_key]["ascending"]

    candidates = []

    for name, entry in index.items():
        if not _position_matches_stat(entry, stat_key):
            continue

        if min_teams is not None and entry.get("career_team_count", 0) < min_teams:
            continue
        if league is not None:
            career_leagues = entry.get("career_leagues", [])
            if league not in career_leagues:
                continue
            other = "NFC" if league == "AFC" else "AFC"
            if other in career_leagues:
                continue
        if rival_team is not None and rival_team not in entry.get("career_teams", []):
            continue

        seasons = _matching_seasons(name, team, division, year_min, year_max,
                                    min_stat_key, min_stat_val)
        for s in seasons:
            v = s.get(stat_key)
            if v is None:
                continue
            candidates.append((v, name, s["year"]))

    candidates.sort(key=lambda c: c[0], reverse=not ascending)

    return [
        {"player": name, "year": year, "value": value}
        for value, name, year in candidates[:n]
    ]


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

# Which prompt types are valid per stat category.
# All NFL stats map to a single position bucket so every prompt type is valid.
_VALID_PROMPT_TYPES: dict[str, list[str]] = {
    "passing":   list(PROMPT_TYPE_WEIGHTS.keys()),
    "rushing":   list(PROMPT_TYPE_WEIGHTS.keys()),
    "receiving": list(PROMPT_TYPE_WEIGHTS.keys()),
}

_SIMPLE_PROMPT_TYPES   = ["team_with_range", "team_only",
                          "division_with_range", "division_only"]
_SIMPLE_PROMPT_WEIGHTS = [4, 3, 3, 2]


def generate_prompts(stat_key: str, n: int = 5) -> list[dict]:
    """Generate n random slot prompts for a game round."""
    MIN_PLAYERS = 5

    stat_type     = STAT_CONFIG[stat_key]["type"]
    valid_types   = _VALID_PROMPT_TYPES[stat_type]
    valid_weights = [PROMPT_TYPE_WEIGHTS[t] for t in valid_types]

    prompts = []

    # Phase 1: exotic + simple
    for _ in range(60):
        if len(prompts) >= n:
            break

        prompt_type = random.choices(valid_types, weights=valid_weights, k=1)[0]
        try:
            prompt = _build_prompt(prompt_type, stat_key)
        except ValueError:
            continue

        if _count_eligible(prompt, stat_key, MIN_PLAYERS) >= MIN_PLAYERS:
            prompts.append(prompt)

    # Phase 2: simple fallbacks
    fallback_attempts = 0
    while len(prompts) < n and fallback_attempts < 100:
        fallback_attempts += 1
        prompt_type = random.choices(_SIMPLE_PROMPT_TYPES,
                                     weights=_SIMPLE_PROMPT_WEIGHTS, k=1)[0]
        try:
            prompt = _build_prompt(prompt_type, stat_key)
        except ValueError:
            continue

        if _count_eligible(prompt, stat_key, MIN_PLAYERS) >= MIN_PLAYERS:
            prompts.append(prompt)

    if len(prompts) < n:
        logger.warning("Could only generate %d valid NFL prompts (wanted %d)",
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
    """Build a single prompt dict for the given type."""
    base = {
        "team": None, "division": None,
        "year_min": None, "year_max": None,
        "min_teams": None, "league": None, "rival_team": None,
        "min_stat_key": None, "min_stat_val": None,
    }

    stat_label = STAT_CONFIG[stat_key]["label"]

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

    if prompt_type == "multi_team":
        n_teams = random.choice([3, 4, 5])
        label   = f"A player who played for at least {n_teams} different teams in their career"
        return {**base, "type": "multi_team", "min_teams": n_teams, "label": label}

    if prompt_type == "afc_only":
        label = "A player who spent their entire career in the AFC"
        return {**base, "type": "league", "league": "AFC", "label": label}

    if prompt_type == "nfc_only":
        label = "A player who spent their entire career in the NFC"
        return {**base, "type": "league", "league": "NFC", "label": label}

    if prompt_type == "rival_pair":
        team_a, team_b = random.choice(RIVAL_PAIRS)
        label = f"A player who played for both the {team_a} and the {team_b}"
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
            label = (f"A player with at most {threshold} {stat_label} "
                     f"in a season with the {team}")
        else:
            label = (f"A player with at least {_fmt_threshold(threshold)} {stat_label} "
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
            label = (f"A player with at most {threshold} {stat_label} "
                     f"in a season in the {division}")
        else:
            label = (f"A player with at least {_fmt_threshold(threshold)} {stat_label} "
                     f"in a season in the {division}")
        return {**base, "type": "min_stat_division",
                "division": division,
                "min_stat_key": stat_key, "min_stat_val": threshold,
                "label": label}

    raise ValueError(f"Unknown prompt type: {prompt_type}")


def _fmt_threshold(v) -> str:
    """Format a threshold — drop .0 on whole-number floats, otherwise str()."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _random_year_range() -> tuple[int, int]:
    """Pick a random year window of 5–12 years within YEAR_RANGE."""
    window = random.randint(5, 12)
    start  = random.randint(YEAR_RANGE[0], YEAR_RANGE[1] - window)
    return start, start + window


def random_stat_key() -> str:
    return random.choice(list(STAT_CONFIG.keys()))


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    stat = random_stat_key()
    print(f"\nStat for this round: {STAT_CONFIG[stat]['label']} ({stat})")

    try:
        results = search_players("", stat_key=stat, limit=5)
        print(f"\n--- Top 5 eligible players for '{stat}' (empty query) ---")
        for r in results:
            print(f"  {r['name']:28s}  {r['matching_seasons']} seasons  {r['teams']}")
    except FileNotFoundError as e:
        print(f"\n[index not built yet — run nfl_build_index.py]")
        print(f"  ({e})")

    print("\n--- Generating 5 prompts (no index needed for labels only) ---")
    try:
        prompts = generate_prompts(stat, n=5)
        for i, p in enumerate(prompts, 1):
            print(f"  Slot {i}: {p['label']}")
    except FileNotFoundError:
        print("  (Skipped — needs index.)")
