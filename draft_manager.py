"""
draft_manager.py — Fantasy Draft Order Randomizer: State + Simulation
=====================================================================
Manages in-memory "race" state for the fantasy-football draft-order
randomizer. Each race is a deterministic, seeded simulation: given the
same run, every viewer (the host, live spectators, and anyone opening a
replay link later) reconstructs the *identical* animation and the same
final draft order.

Determinism trick
-----------------
Instead of shipping a physics engine and hoping Python and JS RNGs agree,
the server computes a handful of numeric parameters per runner and the
client evaluates a closed-form progress curve from them:

    p(tau) = tau + a1*sin(pi*tau) + a2*sin(2*pi*tau) + a3*sin(3*pi*tau)

where tau = elapsed / finish_time, clamped to [0, 1]. Because sin(k*pi*tau)
is exactly 0 at tau=0 and tau=1, every runner starts at p=0 and finishes
at p=1 regardless of the wobble terms — so the finish order is fully
determined by finish_time, while the a-terms create mid-race lead changes.
Amplitude bounds are chosen so p'(tau) > 0 everywhere (no backward motion).

Public API:
    create_run(names)          -> run_id
    get_run(run_id)            -> run dict (safe copy) or None
    public_run(run)            -> client-safe dict
    start_run(run_id)          -> started_at_ms (marks status=running)
    finish_run(run_id)         -> None (marks status=finished)
    is_finished(run)           -> bool (respects the duration fallback)
    cleanup_stale_runs()       -> removes runs older than MAX_AGE
"""

import uuid
import time
import math
import random
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_PLAYERS = 2
MAX_PLAYERS = 32
RUN_ID_LEN  = 6
MAX_AGE_SECS = 60 * 60 * 6          # races expire after 6 hours

BASE_DURATION = 9.0                 # nominal seconds for an average runner
TAIL_SECS     = 1.2                 # extra time so the last runner clearly crosses

# 32 visually distinct lane colors.
COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff",
    "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1",
    "#000075", "#a9a9a9", "#e6beff", "#00b894", "#ff7675", "#0984e3",
    "#fdcb6e", "#6c5ce7", "#e17055", "#00cec9", "#d63031", "#74b9ff",
    "#55efc4", "#fd79a8",
]

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_runs: dict[str, dict] = {}


class DraftError(Exception):
    """Raised for invalid draft operations."""
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_run_id() -> str:
    """Short, URL-safe, unambiguous race code (no 0/O, 1/l/I)."""
    chars = "23456789abcdefghjkmnpqrstuvwxyz"
    raw = uuid.uuid4().hex
    rid = "".join(chars[int(c, 16) % len(chars)] for c in raw[:RUN_ID_LEN])
    # Avoid the astronomically unlikely collision.
    while rid in _runs:
        raw = uuid.uuid4().hex
        rid = "".join(chars[int(c, 16) % len(chars)] for c in raw[:RUN_ID_LEN])
    return rid


def _clean_names(names) -> list[str]:
    cleaned = []
    if names:
        for i, n in enumerate(names[:MAX_PLAYERS]):
            n = (str(n).strip() if n is not None else "")
            cleaned.append((n or f"Player {i + 1}")[:24])
    # Pad up to the minimum if too few valid names were supplied.
    while len(cleaned) < MIN_PLAYERS:
        cleaned.append(f"Player {len(cleaned) + 1}")
    return cleaned[:MAX_PLAYERS]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def create_run(names) -> str:
    """
    Build a deterministic race from a list of names and store it.
    Returns the run_id.
    """
    cleaned = _clean_names(names)
    n = len(cleaned)

    seed = time.time_ns()
    rng = random.Random(seed)

    runners = []
    for i, name in enumerate(cleaned):
        # Faster speed -> shorter finish time. Jitter keeps ties away.
        speed = rng.uniform(0.82, 1.22)
        finish_time = BASE_DURATION / speed + rng.uniform(-0.15, 0.15)

        # Lead-change wobble. Bounds keep p'(tau) > 0 everywhere:
        #   0.08*pi + 0.04*2pi + 0.025*3pi = pi*0.735 < 1
        a1 = rng.uniform(-0.08, 0.08)
        a2 = rng.uniform(-0.04, 0.04)
        a3 = rng.uniform(-0.025, 0.025)

        runners.append({
            "name":        name,
            "lane":        i,
            "number":      rng.randint(1, 99),
            "color":       COLORS[i % len(COLORS)],
            "finish_time": round(finish_time, 4),
            "a1":          round(a1, 5),
            "a2":          round(a2, 5),
            "a3":          round(a3, 5),
            "place":       None,
        })

    # Draft order = finish order (first across the line drafts first).
    order = sorted(range(n), key=lambda idx: runners[idx]["finish_time"])
    for place, idx in enumerate(order, start=1):
        runners[idx]["place"] = place

    duration = max(r["finish_time"] for r in runners) + TAIL_SECS

    run_id = _make_run_id()
    _runs[run_id] = {
        "run_id":         run_id,
        "seed":           seed,
        "created_at":     time.time(),
        "status":         "ready",          # ready | running | finished
        "started_at_ms":  None,
        "duration":       round(duration, 4),
        "runners":        runners,
    }
    logger.info("Created draft run %s with %d runners", run_id, n)
    return run_id


def get_run(run_id: str) -> dict | None:
    run = _runs.get(run_id)
    return deepcopy(run) if run else None


def public_run(run: dict) -> dict:
    """Client-safe view of a run (everything the animation needs)."""
    return {
        "run_id":   run["run_id"],
        "status":   run["status"],
        "duration": run["duration"],
        "started_at_ms": run["started_at_ms"],
        "runners": [
            {
                "name":   r["name"],
                "lane":   r["lane"],
                "number": r["number"],
                "color":  r["color"],
                "finish_time": r["finish_time"],
                "a1": r["a1"], "a2": r["a2"], "a3": r["a3"],
                "place":  r["place"],
            }
            for r in run["runners"]
        ],
    }


def draft_order(run: dict) -> list[dict]:
    """Runners sorted by finishing place (pick 1 first)."""
    return sorted(
        (public_run(run)["runners"]),
        key=lambda r: r["place"],
    )


def start_run(run_id: str) -> int:
    """Mark a race as running and stamp the start time. Returns started_at_ms."""
    run = _runs.get(run_id)
    if not run:
        raise DraftError("Race not found.")
    # Allow (re)starting from ready or finished for replays/rematches.
    run["status"] = "running"
    run["started_at_ms"] = _now_ms()
    return run["started_at_ms"]


def finish_run(run_id: str) -> None:
    run = _runs.get(run_id)
    if run:
        run["status"] = "finished"


def is_finished(run: dict) -> bool:
    """
    True if the race is done. Falls back to the wall clock so a results
    link works even if the host disconnected before emitting 'race_ended'.
    """
    if run["status"] == "finished":
        return True
    if run["status"] == "running" and run["started_at_ms"]:
        elapsed_ms = _now_ms() - run["started_at_ms"]
        return elapsed_ms > (run["duration"] * 1000 + 2000)
    return False


def cleanup_stale_runs() -> int:
    now = time.time()
    stale = [rid for rid, r in _runs.items() if now - r["created_at"] > MAX_AGE_SECS]
    for rid in stale:
        _runs.pop(rid, None)
    return len(stale)


def progress(runner: dict, elapsed: float) -> float:
    """
    Reference implementation of the client-side progress curve (0..1).
    Kept in Python for tests / server-side rendering parity.
    """
    ft = runner["finish_time"]
    if ft <= 0:
        return 1.0
    tau = min(max(elapsed / ft, 0.0), 1.0)
    p = (tau
         + runner["a1"] * math.sin(math.pi * tau)
         + runner["a2"] * math.sin(2 * math.pi * tau)
         + runner["a3"] * math.sin(3 * math.pi * tau))
    return min(max(p, 0.0), 1.0)
