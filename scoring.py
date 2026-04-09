# scoring.py
#
# Point-based scoring system for Bafflehouse.
#
# Architecture
# ────────────
# MILESTONES      — dict mapping milestone id to (points, description)
# ScoringTracker  — records which milestones have been hit (no double-counting)
#                   and exposes the current score
# score_summary() — returns a formatted end-of-game score report
#
# Milestone ids are stable strings used throughout engine.py.  The tracker
# is a module-level singleton reset on each new game.
#
# Maximum score: 195 points.

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# ── Milestone definitions ─────────────────────────────────────────────────
# (points, short description shown in final summary)

MILESTONES: Dict[str, Tuple[int, str]] = {
    # ── Puzzle completion ─────────────────────────────────────────────────
    "lamp_lit":            ( 5,  "Lit the oil lamp"),
    "kitchen_reached":     (10,  "Pulled the lever and reached the kitchen"),
    "display_case_opened": (15,  "Opened the display case"),
    "basin_activated":     (20,  "Activated the stone basin"),
    "troll_solved":        (25,  "Solved the troll's riddles"),
    "golem_defeated":      (30,  "Defeated the slime golem"),

    # ── Exploration ───────────────────────────────────────────────────────
    "manor_entered":       ( 5,  "Entered the manor"),
    "secret_study_found":  (10,  "Discovered the secret study"),

    # ── Relationship ──────────────────────────────────────────────────────
    "jasper_neutral":      ( 5,  "Earned Jasper's cautious trust"),
    "jasper_devoted":      (15,  "Became devoted companions with Jasper"),
    "jasper_fed":          ( 5,  "Fed Jasper"),

    # ── End game ─────────────────────────────────────────────────────────
    "game_won":            (50,  "Found the way home"),
}

MAXIMUM_SCORE = sum(pts for pts, _ in MILESTONES.values())


# ── Tracker ───────────────────────────────────────────────────────────────

class ScoringTracker:
    """
    Records which milestones have been achieved.

    Each milestone can only be awarded once per game.  The tracker is
    intentionally simple — no persistence needed since scores are only
    meaningful within a single session.
    """

    def __init__(self) -> None:
        self._achieved: Dict[str, int] = {}   # milestone_id -> points awarded

    def award(self, milestone_id: str) -> Optional[str]:
        """
        Award a milestone if it hasn't been awarded yet.

        Returns a notification string if newly awarded, or None if the
        milestone was already achieved (or doesn't exist).
        """
        if milestone_id in self._achieved:
            return None
        if milestone_id not in MILESTONES:
            return None

        points, desc = MILESTONES[milestone_id]
        self._achieved[milestone_id] = points
        return f"[+{points}] {desc}"

    def score(self) -> int:
        """Return the current total score."""
        return sum(self._achieved.values())

    def achieved(self) -> List[str]:
        """Return list of achieved milestone ids."""
        return list(self._achieved.keys())

    def reset(self) -> None:
        """Reset all milestones — used on game restart."""
        self._achieved.clear()


# ── Module-level singleton ────────────────────────────────────────────────

TRACKER = ScoringTracker()


# ── Summary ───────────────────────────────────────────────────────────────

def score_summary(turns: int, outcome: str = "quit") -> str:
    """
    Return a formatted end-of-game score report.

    outcome: "won", "died", or "quit"
    """
    score = TRACKER.score()
    pct   = int(100 * score / MAXIMUM_SCORE) if MAXIMUM_SCORE else 0

    outcome_line = {
        "won":  "You found the way home.",
        "died": "You did not survive.",
        "quit": "You left the Bafflehouse.",
    }.get(outcome, "Game over.")

    lines = [
        "",
        "─" * 40,
        f"  {outcome_line}",
        f"  Score:  {score} / {MAXIMUM_SCORE}  ({pct}%)",
        f"  Turns:  {turns}",
        "─" * 40,
    ]

    if TRACKER.achieved():
        lines.append("  Milestones reached:")
        for mid in TRACKER.achieved():
            pts, desc = MILESTONES[mid]
            lines.append(f"    {pts:>3}  {desc}")
        lines.append("─" * 40)

    return "\n".join(lines)