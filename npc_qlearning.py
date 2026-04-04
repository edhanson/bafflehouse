# npc_qlearning.py
#
# Tabular Q-learning for combat NPCs.
#
# The NPC learns which combat actions produce good outcomes against this
# specific player across sessions.  The Q-table is small (a few KB),
# requires no pretraining, and updates from actual gameplay.
#
# Key design choices:
#   - epsilon (exploration rate) never goes to zero — the NPC always has
#     a small chance of doing something unexpected.
#   - Learning rate is deliberately modest so the NPC doesn't become a
#     perfect counter in a single session.
#   - The NPC's health is part of the state, so it fights differently
#     when injured vs. at full strength.

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── State encoding ──────────────────────────────────────────────────────────
# State is a tuple of small integers representing the combat situation.
#
# (player_health_tier, npc_health_tier, player_last_action, round_number_tier)
#
#   health tiers:  0=critical(0-25%), 1=hurt(25-50%), 2=okay(50-75%), 3=full(75-100%)
#   action index:  integer index into the PLAYER_ACTIONS list
#   round tier:    0=early(1-3), 1=mid(4-8), 2=late(9+)

PLAYER_ACTIONS = [
    "attack",        # standard strike with current weapon
    "heavy_attack",  # powerful strike, high stamina cost
    "dodge",         # evasion attempt
    "block",         # shield-based damage reduction
    "use_item",      # use a held item in combat
    "flee",          # attempt to escape
    "taunt",         # provoke the NPC, stamina-positive
]

NPC_ACTIONS = [
    "strike",        # standard attack
    "heavy_strike",  # powerful but telegraphed
    "defensive",     # brace, reduced damage taken this round
    "feint",         # bypasses dodge/block but lower damage
    "pursue",        # close distance if player tries to flee
    "special",       # acid splash: bypasses physical defence
]

REWARDS = {
    "hit_landed":     1.0,
    "hit_received":  -1.0,
    "player_dodged": -0.5,
    "player_blocked":-0.4,
    "feint_success":  1.2,    # catches dodge/block
    "combat_win":     5.0,
    "combat_loss":   -5.0,
    "player_fled":    2.0,    # NPC "wins" if it drives player away
    "round_survived": 0.2,
}


def encode_health(hp: int, max_hp: int) -> int:
    ratio = hp / max(max_hp, 1)
    if ratio > 0.75: return 3
    if ratio > 0.50: return 2
    if ratio > 0.25: return 1
    return 0


def encode_round(round_num: int) -> int:
    if round_num <= 3:  return 0
    if round_num <= 8:  return 1
    return 2


@dataclass
class CombatState:
    player_hp:          int
    player_max_hp:      int
    npc_hp:             int
    npc_max_hp:         int
    player_last_action: str
    round_num:          int

    def to_key(self) -> tuple:
        return (
            encode_health(self.player_hp, self.player_max_hp),
            encode_health(self.npc_hp,    self.npc_max_hp),
            PLAYER_ACTIONS.index(self.player_last_action)
                if self.player_last_action in PLAYER_ACTIONS else 0,
            encode_round(self.round_num),
        )


@dataclass
class QLearner:
    """
    Tabular Q-learner for one NPC.

    Parameters
    ----------
    alpha          : learning rate — how quickly new experience updates beliefs
    gamma          : discount factor — how much future rewards are valued
    epsilon        : exploration rate — probability of a random action
    epsilon_decay  : shrinks epsilon each session (not each round)
    epsilon_min    : floor for epsilon — always some unpredictability
    """
    npc_id:        str
    alpha:         float = 0.15
    gamma:         float = 0.85
    epsilon:       float = 0.30
    epsilon_decay: float = 0.95
    epsilon_min:   float = 0.10

    _q: Dict[tuple, List[float]] = field(default_factory=dict)
    _sessions: int = 0

    def _q_row(self, state_key: tuple) -> List[float]:
        if state_key not in self._q:
            self._q[state_key] = [0.0] * len(NPC_ACTIONS)
        return self._q[state_key]

    def choose_action(self, state: CombatState) -> str:
        if random.random() < self.epsilon:
            return random.choice(NPC_ACTIONS)
        key = state.to_key()
        q_values = self._q_row(key)
        best_idx = q_values.index(max(q_values))
        return NPC_ACTIONS[best_idx]

    def update(
        self,
        state:      CombatState,
        action:     str,
        reward:     float,
        next_state: Optional[CombatState],
    ) -> None:
        """Q(s,a) ← Q(s,a) + α × [r + γ × max_a' Q(s',a') − Q(s,a)]"""
        key     = state.to_key()
        act_idx = NPC_ACTIONS.index(action)
        current = self._q_row(key)[act_idx]

        if next_state is not None:
            next_key = next_state.to_key()
            next_max = max(self._q_row(next_key))
            target   = reward + self.gamma * next_max
        else:
            target = reward

        self._q[key][act_idx] = current + self.alpha * (target - current)

    def end_session(self) -> None:
        self._sessions += 1
        self.epsilon = max(self.epsilon_min,
                           self.epsilon * self.epsilon_decay)

    def to_dict(self) -> dict:
        return {
            "npc_id":        self.npc_id,
            "alpha":         self.alpha,
            "gamma":         self.gamma,
            "epsilon":       self.epsilon,
            "epsilon_decay": self.epsilon_decay,
            "epsilon_min":   self.epsilon_min,
            "sessions":      self._sessions,
            "q_table":       {str(k): v for k, v in self._q.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QLearner":
        obj = cls(
            npc_id        = data["npc_id"],
            alpha         = data.get("alpha",         0.15),
            gamma         = data.get("gamma",         0.85),
            epsilon       = data.get("epsilon",       0.30),
            epsilon_decay = data.get("epsilon_decay", 0.95),
            epsilon_min   = data.get("epsilon_min",   0.10),
        )
        obj._sessions = data.get("sessions", 0)
        obj._q = {
            tuple(int(x) for x in k.strip("()").split(",") if x.strip()): v
            for k, v in data.get("q_table", {}).items()
        }
        return obj


class CombatMemory:
    """Persistent store for Q-learners, one per combat NPC."""

    def __init__(self, save_path: str = "./combat_memory.json") -> None:
        self.save_path = Path(save_path)
        self._learners: Dict[str, QLearner] = {}
        self._load()

    def _load(self) -> None:
        if self.save_path.exists():
            try:
                raw = json.loads(self.save_path.read_text())
                for npc_id, data in raw.items():
                    self._learners[npc_id] = QLearner.from_dict(data)
            except Exception:
                pass

    def save(self) -> None:
        raw = {nid: l.to_dict() for nid, l in self._learners.items()}
        self.save_path.write_text(json.dumps(raw, indent=2))

    def learner(self, npc_id: str) -> QLearner:
        if npc_id not in self._learners:
            self._learners[npc_id] = QLearner(npc_id=npc_id)
        return self._learners[npc_id]