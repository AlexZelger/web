"""
game.py — Baseball Draft Game: Game Logic
==========================================
Manages game state, validates picks, and scores completed rounds.
Called by Flask routes — never touches the database or HTTP directly.

Typical flow:
    1. state = new_game()                         # start a round
    2. store state in Flask session
    3. players = search_players(query, slot_index, state)   # autocomplete
    4. years  = get_years_for_pick(player, slot_index, state)
    5. state  = submit_pick(state, slot_index, player, year) # one at a time
    6. result = score_game(state)                 # after all 5 slots filled
"""

import logging
from copy import deepcopy

from data import (
    STAT_CONFIG,
    generate_prompts,
    random_stat_key,
    search_players as _search_players,
    get_valid_years,
    get_stat,
    get_best_stat,
    get_top_stats,
)

logger = logging.getLogger(__name__)

NUM_SLOTS = 5


# ---------------------------------------------------------------------------
# Game state schema
# ---------------------------------------------------------------------------
#
# A game state is a plain dict (JSON-serialisable so it can live in the
# Flask session without any extra work).
#
# {
#   "stat_key":   "HR",
#   "stat_label": "Home Runs",
#   "prompts": [
#     {
#       "index":    0,
#       "type":     "team",           # "team" or "division"
#       "team":     "PHI",            # None if division prompt
#       "division": None,             # None if team prompt
#       "year_min": 2010,             # None if no range
#       "year_max": 2020,             # None if no range
#       "label":    "A player who played for the PHI between 2010–2020"
#     },
#     ...
#   ],
#   "picks": [
#     {
#       "slot":       0,
#       "player":     "Bryce Harper",
#       "year":       2017,
#       "stat_value": 35,             # filled in by submit_pick
#       "locked":     True            # True once submitted
#     },
#     None,   # unfilled slots are None
#     ...
#   ],
#   "submitted": False   # True once score_game() is called
# }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def new_game() -> dict:
    """
    Create a fresh game state with a random stat and 5 generated prompts.
    Returns the state dict — store this in the Flask session.
    """
    stat_key = random_stat_key()
    prompts  = generate_prompts(stat_key, n=NUM_SLOTS)

    # Tag each prompt with its slot index
    for i, p in enumerate(prompts):
        p["index"] = i

    state = {
        "stat_key":   stat_key,
        "stat_label": STAT_CONFIG[stat_key]["label"],
        "prompts":    prompts,
        "picks":      [None] * NUM_SLOTS,
        "submitted":  False,
    }

    logger.info("New game: stat=%s, prompts=%s",
                stat_key, [p["label"] for p in prompts])
    return state


def search_players(query: str, slot_index: int, state: dict, limit: int = 10) -> list[dict]:
    """
    Autocomplete search constrained to the rules of a specific slot.
    Returns a list of matching player dicts for the frontend dropdown.

    Each result: { name, type, matching_seasons, teams }
    """
    if not query or len(query.strip()) < 2:
        return []

    prompt = _get_prompt(state, slot_index)
    return _search_players(
        query,
        team=prompt.get("team"),
        division=prompt.get("division"),
        year_min=prompt.get("year_min"),
        year_max=prompt.get("year_max"),
        stat_key=state["stat_key"],
        limit=limit,
        min_teams=prompt.get("min_teams"),
        league=prompt.get("league"),
        rival_team=prompt.get("rival_team"),
        min_stat_key=prompt.get("min_stat_key"),
        min_stat_val=prompt.get("min_stat_val"),
    )


def get_years_for_pick(player_name: str, slot_index: int, state: dict) -> list[int]:
    """
    Return the valid years a user can select for a player in a given slot.
    Called after the user selects a player from the autocomplete.

    Returns a sorted list of ints, e.g. [2012, 2013, 2014, 2015, 2016].
    Returns [] if the player doesn't match the slot constraints.
    """
    prompt = _get_prompt(state, slot_index)
    return get_valid_years(
        player_name,
        team=prompt.get("team"),
        division=prompt.get("division"),
        year_min=prompt.get("year_min"),
        year_max=prompt.get("year_max"),
        stat_key=state["stat_key"],
        min_stat_key=prompt.get("min_stat_key"),
        min_stat_val=prompt.get("min_stat_val"),
    )


def submit_pick(state: dict, slot_index: int, player_name: str, year: int) -> tuple[dict, dict]:
    """
    Record a player + year pick for a slot. Looks up and stores the stat value.

    Returns:
        (updated_state, pick_result)

    pick_result: {
        "slot":       0,
        "player":     "Bryce Harper",
        "year":       2017,
        "stat_key":   "HR",
        "stat_label": "Home Runs",
        "stat_value": 35,
        "valid":      True    # False if player/year combo is invalid for this slot
    }

    Raises ValueError if the slot is already locked or game is submitted.
    """
    _assert_game_active(state)

    if slot_index < 0 or slot_index >= NUM_SLOTS:
        raise ValueError(f"Invalid slot index: {slot_index}")

    if state["picks"][slot_index] is not None and state["picks"][slot_index].get("locked"):
        raise ValueError(f"Slot {slot_index} is already locked.")

    # Validate that this player+year is legal for the slot
    valid_years = get_years_for_pick(player_name, slot_index, state)
    if year not in valid_years:
        return state, {
            "slot":       slot_index,
            "player":     player_name,
            "year":       year,
            "stat_key":   state["stat_key"],
            "stat_label": state["stat_label"],
            "stat_value": None,
            "valid":      False,
            "error":      f"{player_name} does not qualify for this slot in {year}.",
        }

    stat_value = get_stat(player_name, year, state["stat_key"])

    pick = {
        "slot":       slot_index,
        "player":     player_name,
        "year":       year,
        "stat_key":   state["stat_key"],
        "stat_label": state["stat_label"],
        "stat_value": stat_value,
        "locked":     True,
        "valid":      True,
    }

    state = deepcopy(state)
    state["picks"][slot_index] = pick

    logger.info("Pick slot %d: %s %d → %s=%s",
                slot_index, player_name, year, state["stat_key"], stat_value)

    return state, pick


def all_slots_filled(state: dict) -> bool:
    """Return True if all 5 slots have a locked pick."""
    return all(p is not None and p.get("locked") for p in state["picks"])


def score_game(state: dict) -> dict:
    """
    Score the completed game. Call this after all 5 slots are filled.

    Returns a result dict:
    {
        "stat_key":    "HR",
        "stat_label":  "Home Runs",
        "total_score": 187,
        "slots": [
            {
                "slot":        0,
                "label":       "A player who played for the PHI between 2010–2020",
                "player":      "Bryce Harper",
                "year":        2017,
                "stat_value":  35,
                "best_value":  42,     # best possible pick for this slot
                "best_year":   2015,
                "best_player": None,   # not revealed (keeps replay interesting)
                "points_left": 7,      # how many points were left on the table
                "efficiency":  0.83    # stat_value / best_value (0–1)
            },
            ...
        ],
        "max_possible": 225,   # sum of best_value across all slots
        "efficiency":   0.83   # total_score / max_possible
    }
    """
    if not all_slots_filled(state):
        raise ValueError("Cannot score — not all slots are filled.")

    stat_key    = state["stat_key"]
    ascending   = STAT_CONFIG[stat_key]["ascending"]
    slot_results = []
    total_score  = 0
    max_possible = 0

    for i, pick in enumerate(state["picks"]):
        prompt = state["prompts"][i]

        stat_value = pick["stat_value"] if pick["stat_value"] is not None else 0

        # Find the best achievable value for this slot
        best = get_best_stat(
            pick["player"],
            stat_key,
            team=prompt.get("team"),
            division=prompt.get("division"),
            year_min=prompt.get("year_min"),
            year_max=prompt.get("year_max"),
        )
        best_value = best["value"] if best else stat_value
        best_year  = best["year"]  if best else pick["year"]

        # For ascending stats (ERA), lower is better.
        # Score = raw ERA value. Total score = average ERA across slots.
        # Efficiency = best_possible / your_value (1.0 if you matched best).
        if ascending:
            slot_score  = stat_value                    # raw ERA (e.g. 2.48)
            slot_max    = best_value                    # best ERA achievable for this slot
            efficiency  = round(best_value / stat_value, 4) if stat_value else 1.0
            points_left = round(stat_value - best_value, 4)   # how much lower you could have gone
        else:
            slot_score  = stat_value
            slot_max    = best_value
            efficiency  = round(stat_value / best_value, 4) if best_value else 1.0
            points_left = best_value - stat_value

        total_score  += slot_score
        max_possible += slot_max

        slot_results.append({
            "slot":        i,
            "label":       prompt["label"],
            "player":      pick["player"],
            "year":        pick["year"],
            "stat_value":  stat_value,
            "stat_label":  STAT_CONFIG[stat_key]["label"],
            "best_value":  best_value,
            "best_year":   best_year,
            "points_left": points_left,
            "efficiency":  efficiency,
        })

    n_slots = len(slot_results)

    # For ascending stats (ERA): total_score = average ERA (lower is better).
    # For all others: total_score = sum of raw stat values.
    if ascending and n_slots > 0:
        total_score  = round(total_score / n_slots, 4)
        max_possible = round(max_possible / n_slots, 4)   # average of best ERAs

    overall_efficiency = round(max_possible / total_score, 4) \
        if ascending and total_score else \
        round(total_score / max_possible, 4) if max_possible else 0

    result = {
        "stat_key":      stat_key,
        "stat_label":    STAT_CONFIG[stat_key]["label"],
        "ascending":     ascending,                        # frontend uses to flip bar/label
        "total_score":   round(total_score, 4),
        "max_possible":  round(max_possible, 4),           # best avg ERA achievable
        "efficiency":    min(overall_efficiency, 1.0),     # cap at 1.0
        "grade":         _grade(min(overall_efficiency, 1.0)),
        "slots":         slot_results,
    }

    logger.info("Game scored: %s/%s (%.0f%%) grade=%s",
                result["total_score"], result["max_possible"],
                overall_efficiency * 100, result["grade"])

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_prompt(state: dict, slot_index: int) -> dict:
    try:
        return state["prompts"][slot_index]
    except IndexError:
        raise ValueError(f"No prompt for slot {slot_index}")


def _assert_game_active(state: dict) -> None:
    if state.get("submitted"):
        raise ValueError("This game has already been submitted.")


def _grade(efficiency: float) -> str:
    """Convert efficiency ratio (0–1) to a letter grade."""
    if efficiency >= 0.95: return "S"
    if efficiency >= 0.85: return "A"
    if efficiency >= 0.75: return "B"
    if efficiency >= 0.60: return "C"
    if efficiency >= 0.45: return "D"
    return "F"


# ---------------------------------------------------------------------------
# Smoke test  (python game.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    print("\n=== Starting new game ===")
    state = new_game()
    print(f"Stat: {state['stat_label']} ({state['stat_key']})")
    print(f"\n5 Slot Prompts:")
    for p in state["prompts"]:
        print(f"  Slot {p['index']}: {p['label']}")

    print("\n=== Simulating autocomplete for slot 0 ===")
    results = search_players("a", 0, state, limit=5)
    if results:
        print(f"  Top results for 'a':")
        for r in results:
            print(f"    {r['name']:25s} ({r['matching_seasons']} seasons)")

        # Pick the first result
        chosen_player = results[0]["name"]
        print(f"\n=== Getting valid years for '{chosen_player}' in slot 0 ===")
        years = get_years_for_pick(chosen_player, 0, state)
        print(f"  Valid years: {years}")

        if years:
            chosen_year = years[0]
            print(f"\n=== Submitting pick: {chosen_player} {chosen_year} ===")
            state, pick_result = submit_pick(state, 0, chosen_player, chosen_year)
            print(f"  Result: {pick_result}")
    else:
        print("  No results found for slot 0 — try re-running (random prompts vary)")

    print("\n=== Checking all_slots_filled ===")
    print(f"  All filled: {all_slots_filled(state)}")