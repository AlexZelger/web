"""
nfl_build_index.py — Build the master NFL player index
=======================================================
Pulls season-level offensive stats from nflverse via the nfl_data_py
package, aggregates them into a single nfl_player_index.json that powers
instant autocomplete search during gameplay.

This is the NFL equivalent of build_index.py + warm_cache.py combined
— nfl_data_py returns aggregated data directly, so we don't need a
separate per-year caching step.

Usage:
    pip install nfl_data_py pandas
    python nfl_build_index.py

Output: nfl_player_index.json  (~5–15 MB depending on year range)

Index schema:
{
    "Tom Brady": {
        "primary_position": "QB",
        "positions":        ["QB"],
        "headshot_url":     "https://a.espncdn.com/...",
        "seasons": [
            {
                "year":          2007,
                "team":          "NE",
                "division":      "AFC East",
                "PASS_YDS":      4806,
                "PASS_TD":       50,
                "PASS_INT":      8,
                "PASS_RATING":   117.2,
                "RUSH_YDS":      98,
                "RUSH_TD":       2,
                "REC":           null,
                "REC_YDS":       null,
                "REC_TD":        null
            },
            ...
        ],
        "career_teams":      ["NE", "TB"],
        "career_team_count": 2,
        "career_leagues":    ["AFC", "NFC"],
        "career_year_min":   2000,
        "career_year_max":   2022
    },
    ...
}
"""

import json
import logging
from pathlib import Path
from collections import defaultdict
from urllib.error import HTTPError

# pandas is only needed at build time — don't import it at game-server startup.
import pandas as pd

from nfl_data import DIVISIONS, TEAM_TO_DIVISION, YEAR_RANGE

INDEX_PATH = Path(__file__).parent / "nfl_player_index.json"

# ---------------------------------------------------------------------------
# nflverse-data URLs (direct fetch — bypass nfl_data_py which pins stale URLs)
# The `player_stats` release publishes weekly and aggregated regular-season
# parquet files per year; we try the modern name first, then the legacy one.
# ---------------------------------------------------------------------------

_PLAYER_STATS_URL_PATTERNS = [
    # --- player_stats release (2000–2024 usually live here) -----------------
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_reg_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_week_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_season_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{year}.parquet",

    # --- stats_player split release (nflverse moved current-season here) ---
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player_reg/stats_player_reg_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player_week/stats_player_week_{year}.parquet",

    # --- PFR-sourced fallback (has offense_player cols) --------------------
    "https://github.com/nflverse/nflverse-data/releases/download/pfr_advstats/advstats_season_pass_{year}.parquet",
]

_ROSTER_URL_PATTERNS = [
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{year}.parquet",
    # Split-release variants
    "https://github.com/nflverse/nflverse-data/releases/download/roster/roster_{year}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/roster_weekly/roster_weekly_{year}.parquet",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Team abbreviation normalization
# Historical team moves + nflverse vs. display abbreviation quirks.
# ---------------------------------------------------------------------------

TEAM_ALIASES = {
    # Relocations
    "OAK": "LV",    # Oakland Raiders → Las Vegas (2020)
    "STL": "LA",    # St. Louis Rams  → Los Angeles (2016); nflverse uses "LA"
    "LAR": "LA",    # some feeds use LAR
    "SD":  "LAC",   # San Diego Chargers → LA Chargers (2017)
    "SDG": "LAC",
    # Inconsistent feeds
    "JAC": "JAX",
    "WSH": "WAS",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}


def normalize_team(abbr: str | None) -> str | None:
    if not abbr or pd.isna(abbr):
        return None
    abbr = str(abbr).strip().upper()
    abbr = TEAM_ALIASES.get(abbr, abbr)
    if abbr not in TEAM_TO_DIVISION:
        return None
    return abbr


def division_for(team: str | None) -> str:
    if not team:
        return "Unknown"
    return TEAM_TO_DIVISION.get(team, "Unknown")


def league_for(division: str) -> str | None:
    if division.startswith("AFC"):
        return "AFC"
    if division.startswith("NFC"):
        return "NFC"
    return None


# ---------------------------------------------------------------------------
# Passer rating (NFL formula)
# ---------------------------------------------------------------------------

def passer_rating(att, cmp_, yds, td, int_) -> float | None:
    """NFL passer rating; each of the four components capped at [0, 2.375]."""
    if not att or att <= 0:
        return None
    a = ((cmp_ / att) - 0.3) * 5
    b = ((yds / att) - 3) * 0.25
    c = (td / att) * 20
    d = 2.375 - ((int_ / att) * 25)
    a = max(0.0, min(a, 2.375))
    b = max(0.0, min(b, 2.375))
    c = max(0.0, min(c, 2.375))
    d = max(0.0, min(d, 2.375))
    return round(((a + b + c + d) / 6) * 100, 1)


# ---------------------------------------------------------------------------
# Position bucketing
# ---------------------------------------------------------------------------

POSITION_BUCKETS = {
    "QB":  "QB",
    "RB":  "RB",
    "FB":  "FB",
    "WR":  "WR",
    "TE":  "TE",
    "HB":  "RB",
}


def bucket_position(pos: str | None) -> str | None:
    if not pos or pd.isna(pos):
        return None
    return POSITION_BUCKETS.get(str(pos).upper())


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def _fetch_parquet(url_patterns: list[str], year: int, label: str) -> pd.DataFrame | None:
    """
    Try each URL pattern in order for a given year.
    Returns the first parquet DataFrame that loads successfully, or None
    if every pattern 404s. Logs which one worked for diagnostic clarity.
    """
    last_err = None
    for pattern in url_patterns:
        url = pattern.format(year=year)
        try:
            df = pd.read_parquet(url, engine="auto")
            return df
        except (HTTPError, FileNotFoundError, ValueError, OSError) as e:
            last_err = e
            # pandas wraps HTTP 404s variously; just try the next pattern.
            continue
    logger.warning("  ✗ %s %d: no URL pattern matched (last error: %s)",
                   label, year, last_err)
    return None


def _aggregate_weekly_to_seasonal(weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse a weekly stats frame (one row per player-week) into seasonal
    totals (one row per player-season). Assumes REG season filter upstream
    has *not* been applied — we filter out postseason here.
    """
    # Only keep regular-season rows if season_type is present.
    if "season_type" in weekly.columns:
        weekly = weekly[weekly["season_type"].isin(["REG", "Regular Season"])]

    sum_cols = [
        "completions", "attempts", "passing_yards", "passing_tds",
        "interceptions", "carries", "rushing_yards", "rushing_tds",
        "receptions", "targets", "receiving_yards", "receiving_tds",
    ]
    existing = [c for c in sum_cols if c in weekly.columns]

    group_keys = ["player_id", "season"]
    agg = weekly.groupby(group_keys, dropna=False)[existing].sum(min_count=1).reset_index()

    # Preserve latest team for the season — the weekly format has `recent_team`
    # which is updated week-over-week, so the row with the max week wins.
    if "recent_team" in weekly.columns and "week" in weekly.columns:
        latest = (
            weekly.sort_values("week")
            .groupby(group_keys, dropna=False)["recent_team"]
            .last()
            .reset_index()
            .rename(columns={"recent_team": "team"})
        )
        agg = agg.merge(latest, on=group_keys, how="left")

    return agg


def _load_seasonal_stats(years: list[int]) -> pd.DataFrame:
    """Fetch + concat regular-season stats across all years."""
    frames: list[pd.DataFrame] = []
    aggregated_from_weekly = 0

    for y in years:
        df = _fetch_parquet(_PLAYER_STATS_URL_PATTERNS, y, "stats")
        if df is None or df.empty:
            continue

        # If the frame has `week`, it's weekly-granular → aggregate.
        if "week" in df.columns:
            df = _aggregate_weekly_to_seasonal(df)
            aggregated_from_weekly += 1
        else:
            # Already seasonal (stats_player_reg_*). Filter to REG if flagged.
            if "season_type" in df.columns:
                df = df[df["season_type"].isin(["REG", "Regular Season"])]

        # Ensure `season` column exists.
        if "season" not in df.columns:
            df["season"] = y

        frames.append(df)
        logger.info("  ✓ %d: %d player-season rows", y, len(df))

    if not frames:
        raise RuntimeError(
            "Could not fetch any seasonal stats from nflverse. "
            "Check your internet connection, or the nflverse release URLs "
            "may have changed again — update _PLAYER_STATS_URL_PATTERNS "
            "in nfl_build_index.py."
        )

    if aggregated_from_weekly:
        logger.info("  (aggregated weekly→seasonal for %d years)",
                    aggregated_from_weekly)

    return pd.concat(frames, ignore_index=True)


def _load_rosters(years: list[int]) -> pd.DataFrame:
    """Fetch + concat roster data across all years."""
    frames: list[pd.DataFrame] = []

    for y in years:
        df = _fetch_parquet(_ROSTER_URL_PATTERNS, y, "roster")
        if df is None or df.empty:
            continue

        # Weekly rosters → take the last row per (player, season) by week.
        if "week" in df.columns:
            if "season" not in df.columns:
                df["season"] = y
            key = ["player_id", "season"] if "player_id" in df.columns else ["gsis_id", "season"]
            df = df.sort_values("week").groupby(key, dropna=False).tail(1)

        if "season" not in df.columns:
            df["season"] = y

        frames.append(df)

    if not frames:
        raise RuntimeError(
            "Could not fetch any roster data from nflverse. "
            "Update _ROSTER_URL_PATTERNS in nfl_build_index.py."
        )

    return pd.concat(frames, ignore_index=True)


def build_index() -> dict:
    """
    Pulls seasonal data + rosters for YEAR_RANGE, aggregates per-player-season,
    and returns the full index dict.
    """
    start, end = YEAR_RANGE
    years = list(range(start, end + 1))

    logger.info("Fetching seasonal stats for %d–%d (%d seasons)…",
                start, end, len(years))
    seasonal = _load_seasonal_stats(years)
    logger.info("  → %d player-season rows total", len(seasonal))

    # Rosters give us player_name, position, team, headshot_url.
    logger.info("Fetching rosters for team-per-season + metadata…")
    rosters = _load_rosters(years)
    logger.info("  → %d roster rows total", len(rosters))

    # Normalize column names across the various nflverse formats so the
    # downstream code (which predates the direct-fetch rewrite) keeps working.
    _normalize_columns_inplace(seasonal)
    _normalize_columns_inplace(rosters)

    # Detect roster-coverage gaps. When nflverse publishes a year's stats
    # before its rosters file (common for the current season), we synthesize
    # roster rows from the stats parquet so those players still get names,
    # positions, and headshots.
    roster_years = set(rosters["season"].dropna().astype(int).unique()) if "season" in rosters.columns else set()
    stats_years  = set(seasonal["season"].dropna().astype(int).unique())
    missing_roster_years = sorted(stats_years - roster_years)
    if missing_roster_years:
        logger.info("No roster file for year(s) %s — synthesizing from stats parquet",
                    missing_roster_years)
        synth = _synthesize_rosters_from_stats(seasonal, missing_roster_years)
        if not synth.empty:
            rosters = pd.concat([rosters, synth], ignore_index=True)
            logger.info("  + %d synthesized roster rows", len(synth))

    # ── Build a (player_id, season) → team mapping ────────────────────────
    # Each player can appear with multiple teams in a single season (traded
    # mid-year). We pick the team where they played the most games.
    roster_cols = {"player_id", "season", "team", "position",
                   "player_name", "headshot_url", "games"}
    missing = roster_cols - set(rosters.columns)
    if missing:
        # Fallback: older nfl_data_py versions expose slightly different cols.
        # Silently coerce — we handle missing columns with .get() below.
        logger.warning("roster df missing columns: %s", missing)

    # ── Per-player roster metadata (latest name + position + headshot) ────
    player_meta: dict[str, dict] = {}
    # (player_id, season) → primary team (most games)
    season_team: dict[tuple, str] = {}
    # player_id → set[bucketed position]
    player_positions: dict[str, set] = defaultdict(set)

    for _, row in rosters.iterrows():
        pid = row.get("player_id")
        if not pid or pd.isna(pid):
            continue
        pid = str(pid)

        season = int(row["season"]) if pd.notna(row.get("season")) else None
        team   = normalize_team(row.get("team"))
        pos    = bucket_position(row.get("position"))
        name   = (row.get("player_display_name") or row.get("player_name")
                  or row.get("full_name"))
        headshot = row.get("headshot_url")

        if pos:
            player_positions[pid].add(pos)

        if name and pid not in player_meta:
            player_meta[pid] = {
                "name":         str(name).strip(),
                "position":     pos,
                "headshot_url": str(headshot) if headshot and pd.notna(headshot) else None,
            }
        else:
            # Keep latest headshot (rosters iterate chronologically by year)
            if headshot and pd.notna(headshot):
                player_meta.setdefault(pid, {})["headshot_url"] = str(headshot)
            # If the initial roster row lacked a name but a later one has it
            if name and not player_meta.get(pid, {}).get("name"):
                player_meta.setdefault(pid, {})["name"] = str(name).strip()

        # Track the primary team for each (player, season).
        # import_seasonal_rosters returns one row per player per season
        # (already aggregated). If multiple show up, keep the one with
        # more games.
        if season and team:
            key  = (pid, season)
            prev = season_team.get(key)
            games = row.get("games", 0) or 0
            if prev is None:
                season_team[key] = team
                season_team[(pid, season, "_games")] = games
            else:
                prev_games = season_team.get((pid, season, "_games"), 0)
                if games > prev_games:
                    season_team[key] = team
                    season_team[(pid, season, "_games")] = games

    logger.info("  → %d unique players with roster entries", len(player_meta))

    # ── Aggregate seasonal stats keyed by player_id ───────────────────────
    # player_id → { position, seasons: [ {year, team, stats...} ] }
    index: dict[str, dict] = defaultdict(lambda: {
        "primary_position": None,
        "positions":        [],
        "headshot_url":     None,
        "seasons":          [],
    })

    for _, row in seasonal.iterrows():
        pid = row.get("player_id")
        if not pid or pd.isna(pid):
            continue
        pid = str(pid)

        year = int(row["season"]) if pd.notna(row.get("season")) else None
        if year is None:
            continue

        team = season_team.get((pid, year))
        if not team:
            # Fallback: try the team column on the seasonal row itself.
            team = normalize_team(row.get("team"))
        if not team:
            # Still no team — skip this row (likely a retired-year row).
            continue

        # Stats. seasonal fields from nfl_data_py:
        #   passing_yards, passing_tds, interceptions, attempts, completions
        #   rushing_yards, rushing_tds
        #   receptions, receiving_yards, receiving_tds
        att   = float(row.get("attempts")       or 0)
        cmp_  = float(row.get("completions")    or 0)
        pyds  = float(row.get("passing_yards")  or 0)
        ptd   = float(row.get("passing_tds")    or 0)
        pint  = float(row.get("interceptions")  or 0)

        rating = passer_rating(att, cmp_, pyds, ptd, pint) if att >= 14 else None
        # 14 attempts is the NFL's threshold for qualifying (224 per year);
        # use 14 (1/16th) so a reasonable cutoff still keeps backups out.

        season_dict = {
            "year":        year,
            "team":        team,
            "division":    division_for(team),
            # Passing
            "PASS_YDS":    int(pyds) if att else None,
            "PASS_TD":     int(ptd)  if att else None,
            "PASS_INT":    int(pint) if att else None,
            "PASS_RATING": rating,
            # Rushing (everyone can have rushing)
            "RUSH_YDS":    _safe_int(row.get("rushing_yards")),
            "RUSH_TD":     _safe_int(row.get("rushing_tds")),
            # Receiving
            "REC":         _safe_int(row.get("receptions")),
            "REC_YDS":     _safe_int(row.get("receiving_yards")),
            "REC_TD":      _safe_int(row.get("receiving_tds")),
        }

        index[pid]["seasons"].append(season_dict)

    logger.info("  → %d unique player ids with seasonal stats", len(index))

    # ── Merge roster metadata into index, keying by display name ───────────
    # Multiple player_ids can share the same name (rare but it happens —
    # e.g. two different Adrian Petersons). We disambiguate by appending
    # a suffix only when there's an actual conflict.
    by_name: dict[str, dict] = {}
    name_counts: dict[str, int] = defaultdict(int)

    for pid, data in index.items():
        meta = player_meta.get(pid, {})
        name = meta.get("name") or f"Unknown ({pid})"
        positions = sorted(player_positions.get(pid, set()))

        # Skip completely empty entries (no stats, no position)
        if not data["seasons"] and not positions:
            continue

        entry = {
            "primary_position": meta.get("position") or (positions[0] if positions else None),
            "positions":        positions,
            "headshot_url":     meta.get("headshot_url"),
            "seasons":          sorted(data["seasons"], key=lambda s: s["year"]),
        }

        # Collision handling: if two different players share a display name,
        # annotate the later one with their active year range so search-by-
        # name can still find both.
        if name in by_name:
            name_counts[name] += 1
            suffix_year = entry["seasons"][0]["year"] if entry["seasons"] else "??"
            unique_name = f"{name} ({suffix_year})"
            # Also re-label the original if we haven't yet.
            if name_counts[name] == 1:
                first = by_name.pop(name)
                first_year = first["seasons"][0]["year"] if first["seasons"] else "??"
                by_name[f"{first['_display_name']} ({first_year})"] = first
            by_name[unique_name] = entry
            entry["_display_name"] = name
        else:
            entry["_display_name"] = name
            by_name[name] = entry

    # Strip internal _display_name field before final output.
    for entry in by_name.values():
        entry.pop("_display_name", None)

    # ── Career summary fields ─────────────────────────────────────────────
    for name, entry in by_name.items():
        seasons = entry["seasons"]
        teams   = sorted({s["team"] for s in seasons if s.get("team")})
        leagues = set()
        for s in seasons:
            lg = league_for(s.get("division", ""))
            if lg:
                leagues.add(lg)

        entry["career_teams"]      = teams
        entry["career_team_count"] = len(teams)
        entry["career_leagues"]    = sorted(leagues)
        entry["career_year_min"]   = seasons[0]["year"]  if seasons else None
        entry["career_year_max"]   = seasons[-1]["year"] if seasons else None

    return by_name


def _safe_int(v):
    if v is None or pd.isna(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _synthesize_rosters_from_stats(seasonal: pd.DataFrame,
                                   years: list[int]) -> pd.DataFrame:
    """
    Build a minimal roster frame from the seasonal-stats parquet for years
    where the canonical roster file is missing (e.g. current season, before
    nflverse ships the roster release).

    Keeps one row per (player_id, season) with name, team, position,
    and headshot_url — everything downstream code needs.
    """
    if seasonal.empty:
        return pd.DataFrame()

    wanted_cols = [c for c in (
        "player_id", "season", "team", "position",
        "player_name", "headshot_url",
    ) if c in seasonal.columns]

    if "season" not in seasonal.columns or "player_id" not in seasonal.columns:
        return pd.DataFrame()

    df = seasonal[seasonal["season"].isin(years)][wanted_cols].copy()
    df = df.dropna(subset=["player_id"])
    # Keep one row per (player, season) — stats parquet is already deduped
    # at that granularity, but be defensive.
    df = df.drop_duplicates(subset=["player_id", "season"])
    return df


def _normalize_columns_inplace(df: pd.DataFrame) -> None:
    """
    Map the various nflverse column names to the canonical ones used by
    downstream code. Only adds missing canonical columns — never overwrites.
    """
    # Player identifier: stats files use `player_id`, roster files use `gsis_id`.
    if "player_id" not in df.columns and "gsis_id" in df.columns:
        df["player_id"] = df["gsis_id"]

    # Display name: prefer player_display_name, fall back to full_name / player_name.
    if "player_name" not in df.columns:
        for alt in ("player_display_name", "full_name", "display_name"):
            if alt in df.columns:
                df["player_name"] = df[alt]
                break

    # Team: stats files have `recent_team`, roster files have `team`.
    if "team" not in df.columns and "recent_team" in df.columns:
        df["team"] = df["recent_team"]


def save_index(index: dict) -> None:
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Building NFL player index…")
    index = build_index()
    save_index(index)

    print("\n" + "=" * 50)
    print("  NFL player index built!")
    print(f"  Unique players:      {len(index)}")
    print(f"  Index saved →        {INDEX_PATH}")
    print("=" * 50)
    print("\nNext step: run  python nfl_data.py  to verify search works.")
