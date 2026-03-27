# npc_bayesian.py
#
# Bayesian reputation model for friendly NPCs in Bafflehouse.
#
# Each NPC maintains a Beta(confirmations, disconfirmations) distribution
# over a trust value in [0, 1].  The expected trust is:
#
#     confirmations / (confirmations + disconfirmations)
#
# Parameters:
#   confirmations    — accumulated evidence of trustworthy / kind behaviour
#   disconfirmations — accumulated evidence of threatening / hostile behaviour
#
# Both start at values set per-NPC rather than a fixed prior, so different
# creatures can begin at different default dispositions.
#
# Reference: Josang & Ismail (2002), "The Beta Reputation System",
# Proceedings of the 15th Bled Electronic Commerce Conference.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple


# ── Disposition tiers ─────────────────────────────────────────────────────
# Maps a disposition name to the minimum trust value required to hold it.
# Tiers are checked in order from highest to lowest; the first one whose
# threshold the current trust meets is the active disposition.
DISPOSITION_TIERS = [
    ("devoted",   0.85),
    ("friendly",  0.70),
    ("neutral",   0.55),
    ("wary",      0.35),
    ("cautious",  0.00),   # floor — always matches if nothing else does
]


def trust_to_disposition(trust: float) -> str:
    """Return the disposition name for a given trust value."""
    for name, threshold in DISPOSITION_TIERS:
        if trust >= threshold:
            return name
    return "cautious"


# ── Trust event table ─────────────────────────────────────────────────────
# Each entry maps an event string to (confirm_delta, disconfirm_delta).
# Values are intentionally small so trust shifts gradually across sessions.
#
# Per-NPC event tables can extend or override these defaults.
DEFAULT_EVENTS: Dict[str, Tuple[float, float]] = {
    # Presence and proximity
    "player_present":        (0.3,  0.0),   # player in same room (per move)

    # Offerings and gifts
    "player_offered_item":   (0.5,  0.0),
    "player_gave_food":      (2.0,  0.0),
    "player_gave_catnip":    (1.5,  0.0),

    # Physical interaction
    "player_petted":         (1.0,  0.0),

    # Hostile actions
    "player_struck":         (0.0,  6.0),
    "player_startled":       (0.0,  0.5),   # rapid re-entry after fleeing

    # Neutral context
    "combat_nearby":         (0.0,  0.3),   # frightening but not directed
}


@dataclass
class BayesianReputation:
    """
    Beta distribution parameters representing one NPC's opinion of the player.

    confirmations    — weight of positive evidence (increments alpha)
    disconfirmations — weight of negative evidence (increments beta)
    interactions     — total events recorded (diagnostic)
    events           — per-NPC event table (confirm_delta, disconfirm_delta)
    """
    confirmations:    float = 4.0
    disconfirmations: float = 8.0
    interactions:     int   = 0
    events: Dict[str, Tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_EVENTS)
    )

    @property
    def trust(self) -> float:
        """
        Expected value of Beta(confirmations, disconfirmations).
        Always in (0, 1) since both parameters are kept > 0.
        """
        return self.confirmations / (self.confirmations + self.disconfirmations)

    @property
    def disposition(self) -> str:
        """Named disposition tier for the current trust level."""
        return trust_to_disposition(self.trust)

    @property
    def precision(self) -> float:
        """
        Concentration of the distribution: confirmations + disconfirmations.

        Low precision = NPC has little evidence and can be swayed easily.
        High precision = NPC has a well-formed, stable opinion.
        The starting prior contributes 12.0 (4 + 8), so any value above
        that represents genuine observed interactions.
        """
        return self.confirmations + self.disconfirmations

    @property
    def uncertainty(self) -> float:
        """
        Normalized variance of the distribution, anchored to the starting prior.

        Returns 1.0 at the prior (maximum uncertainty for this model),
        decreasing monotonically toward 0.0 as evidence accumulates.
        """
        c = self.confirmations
        d = self.disconfirmations
        n = c + d
        current_var = (c * d) / (n * n * (n + 1))

        # Variance of the starting prior Beta(4, 8)
        # = (4 * 8) / (12^2 * 13) = 32 / 1872 ≈ 0.01709
        prior_var = (4.0 * 8.0) / (144.0 * 13.0)

        return min(1.0, current_var / prior_var)

    def update(self, event: str) -> bool:
        """
        Apply a named event.  Returns True if the event was recognised.
        Unknown events are silently ignored so new events can be added
        to content without touching this class.
        """
        if event not in self.events:
            return False
        c_delta, d_delta = self.events[event]
        self.confirmations    += c_delta
        self.disconfirmations += d_delta
        self.interactions     += 1
        return True

    def update_raw(self, confirm_delta: float, disconfirm_delta: float) -> None:
        """Apply arbitrary deltas — for one-off scripted moments."""
        self.confirmations    = max(0.01, self.confirmations    + confirm_delta)
        self.disconfirmations = max(0.01, self.disconfirmations + disconfirm_delta)
        self.interactions     += 1

    def to_dict(self) -> dict:
        return {
            "confirmations":    self.confirmations,
            "disconfirmations": self.disconfirmations,
            "interactions":     self.interactions,
            # events table is not persisted — it is set at NPC construction
            # time from the NPC definition, not from save data.
        }

    @classmethod
    def from_dict(cls, data: dict, events: Optional[Dict] = None) -> "BayesianReputation":
        return cls(
            confirmations    = data.get("confirmations",    4.0),
            disconfirmations = data.get("disconfirmations", 8.0),
            interactions     = data.get("interactions",     0),
            events           = events if events is not None else dict(DEFAULT_EVENTS),
        )


class NPCMemory:
    """
    Persistent store for Bayesian reputation objects, one per NPC.

    Saved as a JSON file so trust survives between sessions.
    """

    def __init__(self, save_path: str = "./npc_memory.json") -> None:
        self.save_path = Path(save_path)
        self._store: Dict[str, BayesianReputation] = {}
        self._event_tables: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.save_path.exists():
            return
        try:
            raw = json.loads(self.save_path.read_text())
            for npc_id, data in raw.items():
                events = self._event_tables.get(npc_id, dict(DEFAULT_EVENTS))
                self._store[npc_id] = BayesianReputation.from_dict(data, events)
        except Exception:
            pass  # corrupt save — start fresh

    def save(self) -> None:
        """Persist all NPC reputations to disk."""
        raw = {nid: rep.to_dict() for nid, rep in self._store.items()}
        self.save_path.write_text(json.dumps(raw, indent=2))

    def register_events(self, npc_id: str, events: Dict[str, Tuple[float, float]]) -> None:
        """
        Register a custom event table for an NPC before its reputation is
        first accessed.  Must be called before reputation() for the events
        to be used when loading from disk.
        """
        self._event_tables[npc_id] = events
        if npc_id in self._store:
            self._store[npc_id].events = events

    def reputation(self, npc_id: str) -> BayesianReputation:
        """Return (creating if absent) the reputation for an NPC."""
        if npc_id not in self._store:
            events = self._event_tables.get(npc_id, dict(DEFAULT_EVENTS))
            self._store[npc_id] = BayesianReputation(events=events)
        return self._store[npc_id]

    def record(self, npc_id: str, event: str) -> BayesianReputation:
        """Record an event and return the updated reputation."""
        rep = self.reputation(npc_id)
        rep.update(event)
        return rep

    def disposition(self, npc_id: str) -> str:
        """Convenience: return the named disposition for an NPC."""
        return self.reputation(npc_id).disposition

    def trust(self, npc_id: str) -> float:
        """Convenience: return the raw trust value for an NPC."""
        return self.reputation(npc_id).trust