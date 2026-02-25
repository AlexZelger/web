"""
warm_cache.py — One-time cache warmer for the Baseball Draft Game
==================================================================
Run this once after deploying to pre-fetch all batting and pitching
stats from FanGraphs (via pybaseball) for every year in YEAR_RANGE.

Usage:
    python warm_cache.py

    # Only fetch years you're missing (safe to re-run anytime):
    python warm_cache.py --missing-only

    # Fetch a specific year range:
    python warm_cache.py --start 2000 --end 2010

Expected runtime: ~15-30 minutes for the full 1990-2025 range.
After this, all lookups are instant from local JSON files.
"""

import argparse
import logging
import time
from pathlib import Path

import pandas as pd
from pybaseball import batting_stats, pitching_stats
from pybaseball import cache as pybaseball_cache

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

YEAR_RANGE   = (1990, 2025)
MIN_PA       = 200   # minimum plate appearances to qualify (batters)
MIN_IP       = 40    # minimum innings pitched to qualify (pitchers)
CACHE_DIR    = Path(__file__).parent / "stat_cache"
SLEEP_BETWEEN_REQUESTS = 2  # seconds — be polite to FanGraphs

# Enable pybaseball's own cache too (secondary safety net)
pybaseball_cache.enable()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cache_path(year: int, stat_type: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{stat_type}_{year}.json"


def already_cached(year: int, stat_type: str) -> bool:
    return cache_path(year, stat_type).exists()


def save(df: pd.DataFrame, year: int, stat_type: str) -> None:
    df.to_json(cache_path(year, stat_type), orient="records", indent=2)


# ---------------------------------------------------------------------------
# Fetch functions
# ---------------------------------------------------------------------------

BATTING_COLS  = ["Name", "Team", "G", "PA", "HR", "RBI", "AVG", "WAR"]
PITCHING_COLS = ["Name", "Team", "G", "GS", "IP", "ERA", "SO", "WAR", "W", "L"]


def fetch_batting_year(year: int) -> pd.DataFrame:
    df = batting_stats(year, year, qual=MIN_PA)
    cols = [c for c in BATTING_COLS if c in df.columns]
    df = df[cols].copy()
    df["HR"]  = pd.to_numeric(df["HR"],  errors="coerce").fillna(0).astype(int)
    df["RBI"] = pd.to_numeric(df["RBI"], errors="coerce").fillna(0).astype(int)
    df["AVG"] = pd.to_numeric(df["AVG"], errors="coerce").round(3)
    df["WAR"] = pd.to_numeric(df["WAR"], errors="coerce").round(1)
    df["year"] = year
    return df


def fetch_pitching_year(year: int) -> pd.DataFrame:
    df = pitching_stats(year, year, qual=MIN_IP)
    cols = [c for c in PITCHING_COLS if c in df.columns]
    df = df[cols].copy()
    df["ERA"] = pd.to_numeric(df["ERA"], errors="coerce").round(2)
    df["WAR"] = pd.to_numeric(df["WAR"], errors="coerce").round(1)
    df["IP"]  = pd.to_numeric(df["IP"],  errors="coerce").round(1)
    df["year"] = year
    return df


# ---------------------------------------------------------------------------
# Main warm loop
# ---------------------------------------------------------------------------

def warm(start: int, end: int, missing_only: bool) -> None:
    years = list(range(start, end + 1))
    total = len(years) * 2  # batting + pitching per year
    done  = 0
    skipped = 0
    failed = []

    logger.info("Starting cache warm: %d–%d (%d years, %d fetches)",
                start, end, len(years), total)

    for year in years:
        for stat_type, fetch_fn in [("batting", fetch_batting_year),
                                     ("pitching", fetch_pitching_year)]:

            if missing_only and already_cached(year, stat_type):
                skipped += 1
                done += 1
                logger.debug("Skip (cached): %s %d", stat_type, year)
                continue

            try:
                logger.info("[%d/%d] Fetching %s %d…", done + 1, total, stat_type, year)
                df = fetch_fn(year)
                save(df, year, stat_type)
                logger.info("  ✓  %d rows saved → %s_%d.json", len(df), stat_type, year)
                done += 1

                # Be polite — don't hammer FanGraphs
                time.sleep(SLEEP_BETWEEN_REQUESTS)

            except Exception as e:
                logger.error("  ✗  Failed %s %d: %s", stat_type, year, e)
                failed.append((stat_type, year))
                done += 1

    # Summary
    print("\n" + "=" * 50)
    print(f"  Cache warm complete!")
    print(f"  Years:    {start}–{end}")
    print(f"  Fetched:  {done - skipped - len(failed)}")
    print(f"  Skipped:  {skipped} (already cached)")
    print(f"  Failed:   {len(failed)}")
    if failed:
        print("\n  Failed fetches (re-run with --missing-only to retry):")
        for stat_type, year in failed:
            print(f"    {stat_type} {year}")
    print("=" * 50)
    print("\nNext step: run  python build_index.py")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-fetch baseball stats into local cache.")
    parser.add_argument("--start",        type=int, default=YEAR_RANGE[0], help="Start year")
    parser.add_argument("--end",          type=int, default=YEAR_RANGE[1], help="End year")
    parser.add_argument("--missing-only", action="store_true",
                        help="Skip years that are already cached (safe to re-run)")
    args = parser.parse_args()

    warm(args.start, args.end, args.missing_only)