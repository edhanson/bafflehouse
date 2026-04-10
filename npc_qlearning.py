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
    "flee",          # attempt to escape
    "taunt",         # provoke the NPC, stamina-positive
    "equip",         # player changed weapon or armour mid-combat
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
    wearing_coif:       bool = False
    wearing_shield:     bool = False

    def to_key(self) -> tuple:
        return (
            encode_health(self.player_hp, self.player_max_hp),
            encode_health(self.npc_hp,    self.npc_max_hp),
            PLAYER_ACTIONS.index(self.player_last_action)
                if self.player_last_action in PLAYER_ACTIONS else 0,
            encode_round(self.round_num),
            int(self.wearing_coif),
            int(self.wearing_shield),
        )


@dataclass
class QLearner:
    """
    Tabular Q-learner for one NPC.

    Action selection uses a minimum-floor blended probability rather than
    pure epsilon-greedy.  Every action is guaranteed at least min_action_prob
    probability regardless of Q-table state, preventing the all-zeros
    initialisation from collapsing exploitation to always-pick-first-action.

    The remaining probability mass (1 - n_actions * min_action_prob) is
    distributed proportional to softmax-normalised Q-values so the learner
    can still develop strong preferences over sessions.

    Parameters
    ----------
    alpha            : learning rate
    gamma            : discount factor
    epsilon          : retained for serialisation compatibility; no longer
                       used in action selection (floor replaces it)
    epsilon_decay    : retained for compatibility
    epsilon_min      : retained for compatibility
    min_action_prob  : minimum probability for each action (default 0.05)
    softmax_temp     : temperature for Q-value softmax (default 1.0)
                       higher = more uniform; lower = more greedy
    """
    npc_id:           str
    alpha:            float = 0.15
    gamma:            float = 0.85
    epsilon:          float = 0.30      # kept for serialisation compatibility
    epsilon_decay:    float = 0.95      # kept for serialisation compatibility
    epsilon_min:      float = 0.10      # kept for serialisation compatibility
    min_action_prob:  float = 0.05      # floor probability per action
    softmax_temp:     float = 1.0       # Q-value softmax temperature

    _q: Dict[tuple, List[float]] = field(default_factory=dict)
    _sessions: int = 0

    def _q_row(self, state_key: tuple) -> List[float]:
        if state_key not in self._q:
            self._q[state_key] = [0.0] * len(NPC_ACTIONS)
        return self._q[state_key]

    def _softmax(self, values: List[float]) -> List[float]:
        """
        Compute softmax probabilities with temperature scaling.

        Subtracts the max before exponentiation for numerical stability —
        avoids overflow when Q-values are large positives.
        """
        import math
        scaled = [v / self.softmax_temp for v in values]
        max_v  = max(scaled)
        exps   = [math.exp(v - max_v) for v in scaled]
        total  = sum(exps)
        return [e / total for e in exps]

    def choose_action(
        self,
        state: CombatState,
        forbidden: Optional[List[str]] = None,
    ) -> str:
        """
        Select an action using minimum-floor blended probabilities.

        Every action gets at least min_action_prob probability.  The
        remaining mass is distributed by softmax-normalised Q-values.
        Actions in `forbidden` are excluded (their mass redistributed).

        This replaces epsilon-greedy: every action fires occasionally from
        session one, while the learner develops preferences over time.
        """
        forbidden = forbidden or []
        key       = state.to_key()
        q_values  = self._q_row(key)

        n      = len(NPC_ACTIONS)
        floor  = self.min_action_prob
        q_probs = self._softmax(q_values)

        # Blended probability: floor + Q-weighted remainder
        remaining = 1.0 - n * floor
        probs = [floor + remaining * qp for qp, _ in zip(q_probs, NPC_ACTIONS)]

        # Zero out forbidden actions and renormalise
        for i, action in enumerate(NPC_ACTIONS):
            if action in forbidden:
                probs[i] = 0.0
        total = sum(probs)
        if total <= 0.0:
            # All actions forbidden — fall back to first non-forbidden
            for i, action in enumerate(NPC_ACTIONS):
                if action not in forbidden:
                    return action
            return NPC_ACTIONS[0]
        probs = [p / total for p in probs]

        # Weighted random choice
        roll = random.random()
        cumulative = 0.0
        for action, prob in zip(NPC_ACTIONS, probs):
            cumulative += prob
            if roll < cumulative:
                return action
        return NPC_ACTIONS[-1]   # floating-point safety fallback

    def update(
        self,
        state:      CombatState,
        action:     str,
        reward:     float,
        next_state: Optional[CombatState],
    ) -> None:
        """Q(s,a) ← Q(s,a) + α × [r + γ × max_a' Q(s',a') − Q(s,a)]"""
        import math
        # Guard: never allow inf or NaN into the table — clamp reward to
        # a large finite value so a sentinel leaking through can't corrupt.
        if not math.isfinite(reward):
            reward = max(-100.0, min(100.0, 0.0 if math.isnan(reward) else
                         (100.0 if reward > 0 else -100.0)))
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
        # epsilon decay retained for compatibility even though epsilon
        # is no longer used in action selection
        self.epsilon = max(self.epsilon_min,
                           self.epsilon * self.epsilon_decay)

    def to_dict(self) -> dict:
        return {
            "npc_id":          self.npc_id,
            "alpha":           self.alpha,
            "gamma":           self.gamma,
            "epsilon":         self.epsilon,
            "epsilon_decay":   self.epsilon_decay,
            "epsilon_min":     self.epsilon_min,
            "min_action_prob": self.min_action_prob,
            "softmax_temp":    self.softmax_temp,
            "sessions":        self._sessions,
            "q_table":         {str(k): v for k, v in self._q.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QLearner":
        obj = cls(
            npc_id          = data["npc_id"],
            alpha           = data.get("alpha",           0.15),
            gamma           = data.get("gamma",           0.85),
            epsilon         = data.get("epsilon",         0.30),
            epsilon_decay   = data.get("epsilon_decay",   0.95),
            epsilon_min     = data.get("epsilon_min",     0.10),
            min_action_prob = data.get("min_action_prob", 0.05),
            softmax_temp    = data.get("softmax_temp",    1.0),
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