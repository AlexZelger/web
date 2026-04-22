"""
nfl_game.py — NFL Draft Game: Game Logic
=========================================
Mirrors game.py but for NFL. Manages game state, validates picks, and
scores completed rounds. Never touches HTTP — called by nfl_game_routes
and nfl_multiplayer_routes.

Typical flow:
    1. state = new_game()
    2. store state in Flask session
    3. players = search_players(query, slot_index, state)
    4. years   = get_years_for_pick(player, slot_index, state)
    5. state   = submit_pick(state, slot_index, player, year)
    6. result  = score_game(state)
"""

import logging
from copy import deepcopy

from nfl_data import (
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


def new_game() -> dict:
    """Create a fresh game state with a random stat and 5 generated prompts."""
    stat_key = random_stat_key()
    prompts  = generate_prompts(stat_key, n=NUM_SLOTS)

    for i, p in enumerate(prompts):
        p["index"] = i

    state = {
        "stat_key":   stat_key,
        "stat_label": STAT_CONFIG[stat_key]["label"],
        "prompts":    prompts,
        "picks":      [None] * NUM_SLOTS,
        "submitted":  False,
    }

    logger.info("New NFL game: stat=%s, prompts=%s",
                stat_key, [p["label"] for p in prompts])
    return state


def search_players(query: str, slot_index: int, state: dict, limit: int = 10) -> list[dict]:
    """Autocomplete search constrained to the rules of a specific slot."""
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
    """Return the valid years a user can select for a player in a given slot."""
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
    """Record a player + year pick for a slot. Looks up and stores the stat value."""
    _assert_game_active(state)

    if slot_index < 0 or slot_index >= NUM_SLOTS:
        raise ValueError(f"Invalid slot index: {slot_index}")

    if state["picks"][slot_index] is not None and state["picks"][slot_index].get("locked"):
        raise ValueError(f"Slot {slot_index} is already locked.")

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

    logger.info("NFL pick slot %d: %s %d → %s=%s",
                slot_index, player_name, year, state["stat_key"], stat_value)

    return state, pick


def all_slots_filled(state: dict) -> bool:
    """Return True if all 5 slots have a locked pick."""
    return all(p is not None and p.get("locked") for p in state["picks"])


def score_game(state: dict) -> dict:
    """Score the completed game. Call after all 5 slots are filled."""
    if not all_slots_filled(state):
        raise ValueError("Cannot score — not all slots are filled.")

    stat_key     = state["stat_key"]
    ascending    = STAT_CONFIG[stat_key]["ascending"]
    slot_results = []
    total_score  = 0
    max_possible = 0

    for i, pick in enumerate(state["picks"]):
        prompt = state["prompts"][i]

        stat_value = pick["stat_value"] if pick["stat_value"] is not None else 0

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

        if ascending:
            # Lower is better (PASS_INT). Zero interceptions is legal & ideal,
            # so guard division-by-zero when player's stat was 0.
            slot_score  = stat_value
            slot_max    = best_value
            if stat_value:
                efficiency = round(best_value / stat_value, 4)
            else:
                # Zero interceptions is a perfect result — cap at 1.0.
                efficiency = 1.0
            points_left = round(stat_value - best_value, 4)
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

    # For ascending stats (PASS_INT): total_score = average across slots
    # (lower avg is better).  For all others: total_score = raw sum.
    if ascending and n_slots > 0:
        total_score  = round(total_score / n_slots, 4)
        max_possible = round(max_possible / n_slots, 4)

    overall_efficiency = (
        round(max_possible / total_score, 4)
        if ascending and total_score else
        round(total_score / max_possible, 4) if max_possible else 0
    )

    result = {
        "stat_key":      stat_key,
        "stat_label":    STAT_CONFIG[stat_key]["label"],
        "ascending":     ascending,
        "total_score":   round(total_score, 4),
        "max_possible":  round(max_possible, 4),
        "efficiency":    min(overall_efficiency, 1.0),
        "grade":         _grade(min(overall_efficiency, 1.0)),
        "slots":         slot_results,
    }

    logger.info("NFL game scored: %s/%s (%.0f%%) grade=%s",
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
