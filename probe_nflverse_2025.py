"""
probe_nflverse_2025.py — HEAD-check candidate nflverse URLs for 2025.

Run:  python probe_nflverse_2025.py

Prints one line per URL with the HTTP status code. 200 = file exists,
404 = not found. Use the 200 URLs to update _PLAYER_STATS_URL_PATTERNS
and _ROSTER_URL_PATTERNS in nfl_build_index.py.
"""
import urllib.request
import urllib.error

YEAR = 2025

CANDIDATES = [
    # player_stats release, modern names
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_reg_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_week_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_post_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_season_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/stats_player_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{y}.parquet",

    # Split-tag variants (nflverse sometimes splits release tags)
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player_reg/stats_player_reg_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player_week/stats_player_week_{y}.parquet",

    # Offense-only variants
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats_off/stats_player_off_reg_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats_off/stats_player_off_week_{y}.parquet",

    # PFR-sourced
    "https://github.com/nflverse/nflverse-data/releases/download/pfr_advstats/advstats_season_pass_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/pfr_advstats/advstats_season_rush_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/pfr_advstats/advstats_season_rec_{y}.parquet",

    # ESPN-sourced
    "https://github.com/nflverse/nflverse-data/releases/download/espn_player_stats/espn_player_stats_{y}.parquet",

    # --- Roster candidates ---
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/roster/roster_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/roster_weekly/roster_weekly_{y}.parquet",
    "https://github.com/nflverse/nflverse-data/releases/download/rosters/rosters_{y}.parquet",
]


def head(url: str) -> str:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return str(r.status)
    except urllib.error.HTTPError as e:
        return str(e.code)
    except Exception as e:
        return f"ERR ({type(e).__name__})"


print(f"Probing nflverse URLs for year={YEAR}\n")
hits = []
for tmpl in CANDIDATES:
    url = tmpl.format(y=YEAR)
    status = head(url)
    marker = "  ✓" if status == "200" else "   "
    print(f"{marker} {status}  {url}")
    if status == "200":
        hits.append(url)

print("\n" + "=" * 60)
if hits:
    print(f"FOUND {len(hits)} live URL(s) for {YEAR}:")
    for u in hits:
        print(f"  {u}")
else:
    print(f"No live URLs for {YEAR} among the candidates above.")
    print("The 2025 data may not yet be published to nflverse-data releases.")
