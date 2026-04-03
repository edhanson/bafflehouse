# troll.py
#
# The bridge troll — a riddle-asking NPC that blocks passage eastward.
#
# Architecture
# ────────────
# TrollRiddle      — a single riddle: text, canonical answer, accepted forms
# TrollState       — persistent state: which riddles seen/solved, weight table
# TrollMemory      — JSON persistence to troll_memory.json
# troll_tick       — called by the engine each turn when player is at bridge
# handle_answer    — called when player types an answer
#
# Progression
# ───────────
# The troll asks riddles drawn from a weighted bank.  The player must
# answer RIDDLES_TO_PASS correctly (across any number of visits) to open
# the bridge permanently.  Wrong answers cost nothing except having to try
# again.  The troll gloats on failure and is grudgingly impressed on success.
#
# Weighting
# ─────────
# Each riddle starts with weight 1.0.  After the player answers correctly
# the weight drops to 0.0 (never asked again).  After a wrong answer the
# weight increases slightly (troll enjoys repeating ones you struggle with).
# Unseen riddles are preferred over seen ones by a small bonus.

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── How many correct answers to open the bridge ───────────────────────────
RIDDLES_TO_PASS = 3


# ── Riddle bank ───────────────────────────────────────────────────────────

@dataclass
class TrollRiddle:
    """One riddle in the bank."""
    rid:      str            # unique id, e.g. "r01"
    text:     str            # the riddle text as the troll speaks it
    answer:   str            # canonical answer (for display/gloating)
    accepted: List[str]      # normalised strings that count as correct


def _norm(s: str) -> str:
    """Lowercase, strip punctuation, collapse spaces."""
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


RIDDLE_BANK: List[TrollRiddle] = [
    TrollRiddle(
        rid="r01",
        text=(
            "I have cities, but no houses live there. "
            "I have mountains, but no trees grow them. "
            "I have water, but no fish swim in it. "
            "I have roads, but no one travels them. "
            "What am I?"
        ),
        answer="a map",
        accepted=["map", "a map", "maps"],
    ),
    TrollRiddle(
        rid="r02",
        text=(
            "The more you take, the more you leave behind. "
            "What am I?"
        ),
        answer="footsteps",
        accepted=["footsteps", "footstep", "steps", "a footstep",
                  "your footsteps", "tracks"],
    ),
    TrollRiddle(
        rid="r03",
        text=(
            "I speak without a mouth and hear without ears. "
            "I have no body, but I come alive with the wind. "
            "What am I?"
        ),
        answer="an echo",
        accepted=["echo", "an echo", "echoes"],
    ),
    TrollRiddle(
        rid="r04",
        text=(
            "I am always in front of you but cannot be seen. "
            "What am I?"
        ),
        answer="the future",
        accepted=["future", "the future", "tomorrow", "what lies ahead"],
    ),
    TrollRiddle(
        rid="r05",
        text=(
            "I have hands but cannot clap. "
            "What am I?"
        ),
        answer="a clock",
        accepted=["clock", "a clock", "watch", "a watch", "timepiece"],
    ),
    TrollRiddle(
        rid="r06",
        text=(
            "The one who makes me has no need of me. "
            "The one who buys me does not want me. "
            "The one who uses me does not know it. "
            "What am I?"
        ),
        answer="a coffin",
        accepted=["coffin", "a coffin", "casket", "a casket",
                  "grave", "a grave", "burial box"],
    ),
    TrollRiddle(
        rid="r07",
        text=(
            "I can be cracked, I can be made. "
            "I can be told, I can be played. "
            "What am I?"
        ),
        answer="a joke",
        accepted=["joke", "a joke", "jokes", "riddle", "a riddle",
                  "jest", "a jest"],
    ),
]


# ── Persistent state ──────────────────────────────────────────────────────

@dataclass
class TrollState:
    """
    Persistent state for the troll NPC.

    correct_count — total correct answers given this player
    solved        — set of riddle ids answered correctly (never repeated)
    seen          — set of riddle ids the player has been asked
    weights       — current sampling weight per riddle id
    bridge_open   — True once the player has earned passage
    current_rid   — riddle id currently being asked (None if not active)
    """
    correct_count: int        = 0
    solved:        List[str]  = field(default_factory=list)
    seen:          List[str]  = field(default_factory=list)
    weights:       Dict[str, float] = field(default_factory=dict)
    bridge_open:   bool       = False
    current_rid:   Optional[str] = None

    def __post_init__(self):
        # Initialise weights for any riddle not yet tracked
        for r in RIDDLE_BANK:
            if r.rid not in self.weights:
                self.weights[r.rid] = 1.0

    def effective_weight(self, rid: str) -> float:
        """Weight used for sampling — zero for solved riddles."""
        if rid in self.solved:
            return 0.0
        # Slight bonus for unseen riddles
        base = self.weights.get(rid, 1.0)
        if rid not in self.seen:
            base += 0.5
        return max(0.0, base)

    def pick_riddle(self) -> Optional[TrollRiddle]:
        """
        Sample a riddle from the bank using current weights.
        Returns None if all riddles have been solved.
        """
        candidates = [r for r in RIDDLE_BANK
                      if self.effective_weight(r.rid) > 0.0]
        if not candidates:
            return None
        weights = [self.effective_weight(r.rid) for r in candidates]
        return random.choices(candidates, weights=weights, k=1)[0]

    def record_correct(self, rid: str) -> None:
        self.correct_count += 1
        if rid not in self.solved:
            self.solved.append(rid)
        self.weights[rid] = 0.0
        if self.correct_count >= RIDDLES_TO_PASS:
            self.bridge_open = True
        self.current_rid = None

    def record_wrong(self, rid: str) -> None:
        # Increase weight slightly — troll enjoys repeating your failures
        self.weights[rid] = self.weights.get(rid, 1.0) + 0.4

    def to_dict(self) -> dict:
        return {
            "correct_count": self.correct_count,
            "solved":         self.solved,
            "seen":           self.seen,
            "weights":        self.weights,
            "bridge_open":    self.bridge_open,
            "current_rid":    self.current_rid,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrollState":
        state = cls(
            correct_count = data.get("correct_count", 0),
            solved        = data.get("solved",         []),
            seen          = data.get("seen",            []),
            weights       = data.get("weights",         {}),
            bridge_open   = data.get("bridge_open",     False),
            current_rid   = data.get("current_rid",     None),
        )
        return state


class TrollMemory:
    """Persists TrollState to disk."""

    def __init__(self, save_path: str = "./troll_memory.json") -> None:
        self.save_path = Path(save_path)
        self._state: Optional[TrollState] = None
        self._load()

    def _load(self) -> None:
        if not self.save_path.exists():
            return
        try:
            data = json.loads(self.save_path.read_text())
            self._state = TrollState.from_dict(data)
        except Exception:
            self._state = None

    def state(self) -> TrollState:
        if self._state is None:
            self._state = TrollState()
        return self._state

    def save(self) -> None:
        self.save_path.write_text(
            json.dumps(self.state().to_dict(), indent=2)
        )

    def reset(self) -> None:
        """Wipe state — for testing."""
        self._state = TrollState()
        if self.save_path.exists():
            self.save_path.unlink()


# ── Message pools ─────────────────────────────────────────────────────────

# Greeting when player first arrives at the bridge
TROLL_GREETINGS = [
    (
        "A large shape detaches itself from the shadow under the bridge and "
        "plants itself in your path. It is broad, grey-skinned, and unimpressed.\n"
        "\"HALT,\" it says, with the air of someone who has said this many times "
        "and enjoyed it every time. \"No one crosses my bridge without answering "
        "a riddle. Them's the rules.\""
    ),
    (
        "Something heavy shifts beneath the bridge. A moment later, an enormous "
        "grey figure hauls itself up onto the road and regards you with small, "
        "bright eyes.\n"
        "\"Riddle time,\" it announces. \"Answer right, you pass. Answer wrong, "
        "you don't. Simple as stones.\""
    ),
]

# When the troll asks a riddle
TROLL_ASK_PREFIX = [
    "\"Right then. Here's your riddle:\"",
    "\"Listen carefully. You only get one chance at this one:\"",
    "\"Pay attention. This one separates the clever from the rest:\"",
    "\"Try this:\"",
    "\"Here we go then:\"",
]

# When player answers correctly
TROLL_CORRECT = [
    (
        "The troll's eyes narrow. It turns the answer over, looking for a flaw, "
        "and finds none.\n"
        "\"Hm,\" it says finally. \"Correct.\""
    ),
    (
        "A pause. The troll clearly was not expecting that.\n"
        "\"...Right,\" it says, in the tone of someone recalculating. \"That's right.\""
    ),
    (
        "\"Correct,\" the troll says, sounding mildly annoyed. "
        "\"Don't look so pleased with yourself.\""
    ),
]

# When player answers wrongly
TROLL_WRONG = [
    (
        "\"Wrong!\" the troll booms, and its face splits into a wide, "
        "unpleasant grin. \"HA. Wrong, wrong, wrong. Try again.\""
    ),
    (
        "The troll shakes its head slowly, savouring the moment.\n"
        "\"No,\" it says. \"That is not right. Not even slightly right. "
        "Would you like to think about it?\""
    ),
    (
        "\"Ohhh, so close,\" the troll says, in a way that makes clear "
        "it was not close at all. \"Try again.\""
    ),
    (
        "The troll makes a long, disappointed sound.\n"
        "\"Wrong. And you seemed so confident, too. Try again.\""
    ),
]

# Progress updates (after correct answer, before bridge is open)
TROLL_PROGRESS = {
    1: "\"One down.\" The troll holds up a thick finger. \"Two more to go.\"",
    2: "\"Two.\" Another finger. \"One more. Don't get comfortable.\"",
}

# When bridge opens
TROLL_BRIDGE_OPEN = (
    "The troll stands aside with an air of theatrical reluctance.\n"
    "\"Fine,\" it says. \"You've earned it. Pass.\"\n"
    "The way east is open."
)

# When player returns and bridge is already open
TROLL_ALREADY_OPEN = (
    "The troll watches you cross without comment. "
    "It has the look of someone waiting for a rematch."
)

# When troll has run out of riddles (all solved)
TROLL_NO_RIDDLES = (
    "The troll stares at you with something approaching respect.\n"
    "\"You've answered them all,\" it says, sounding put out. "
    "\"Every last one. Pass, then. Pass.\""
)

# When player tries to just walk past
TROLL_BLOCKS = [
    "\"Oh no you don't,\" the troll says, stepping squarely into your path.",
    "The troll shifts its weight and you find yourself unable to proceed. "
    "It hasn't moved much — it simply takes up a great deal of space.",
    "\"Riddle first,\" the troll says pleasantly. \"Then bridge.\""
]

# Ambient messages when player lingers at the bridge
TROLL_AMBIENT = [
    "The troll waits. It is very good at waiting.",
    "The troll picks something from between its teeth and examines it.",
    "The troll drums its fingers on the bridge parapet.",
    "\"Well?\" says the troll.",
]

# When player examines the troll
TROLL_EXAMINE = {
    "not_started": (
        "It is large. Larger than seems reasonable, really. Its skin is the "
        "same grey as the bridge stones, which probably isn't a coincidence. "
        "It watches you with small, clever eyes that don't miss much."
    ),
    "in_progress": (
        "It is large and clearly enjoying this. It has the patient look of "
        "something that has been doing this for a very long time and intends "
        "to do it for a great deal longer."
    ),
    "bridge_open": (
        "The troll leans against the parapet and watches you with an "
        "expression that might be grudging respect. Or indigestion. "
        "It is hard to tell with trolls."
    ),
}


# ── Core logic ────────────────────────────────────────────────────────────

def check_answer(riddle: TrollRiddle, player_input: str) -> bool:
    """Return True if player_input matches any accepted answer."""
    normalised = _norm(player_input)
    # Strip common preamble phrases before checking
    for prefix in ["the answer is", "i think its", "i think it is",
                   "its", "it is", "my answer is", "the answer to this riddle is"]:
        if normalised.startswith(prefix):
            normalised = normalised[len(prefix):].strip()
            break
    return normalised in [_norm(a) for a in riddle.accepted]


def get_riddle_by_id(rid: str) -> Optional[TrollRiddle]:
    for r in RIDDLE_BANK:
        if r.rid == rid:
            return r
    return None


def troll_encounter(
    state: TrollState,
    player_moved: bool,
) -> List[str]:
    """
    Called each turn the player is at the bridge.

    Returns a list of narrative strings to display.
    player_moved: True if the player just arrived this turn.
    """
    messages: List[str] = []

    if state.bridge_open:
        if player_moved:
            messages.append(TROLL_ALREADY_OPEN)
        return messages

    if player_moved:
        # Greet the player on arrival
        messages.append(random.choice(TROLL_GREETINGS))

    # If no current riddle, pick one and ask it
    if state.current_rid is None:
        riddle = state.pick_riddle()
        if riddle is None:
            # All riddles exhausted — open bridge anyway
            state.bridge_open = True
            messages.append(TROLL_NO_RIDDLES)
            return messages
        state.current_rid = riddle.rid
        if riddle.rid not in state.seen:
            state.seen.append(riddle.rid)
        prefix = random.choice(TROLL_ASK_PREFIX)
        messages.append(f"{prefix}\n\n{riddle.text}")
    elif not player_moved:
        # Player is lingering — occasional ambient message
        if random.random() < 0.3:
            messages.append(random.choice(TROLL_AMBIENT))

    return messages


def handle_troll_answer(
    state: TrollState,
    player_input: str,
) -> Tuple[str, bool]:
    """
    Process a player's answer attempt.

    Returns (response_text, answered_correctly).
    """
    if state.bridge_open:
        return "The bridge is already open. The troll gestures you through.", False

    if state.current_rid is None:
        return "The troll isn't asking anything right now.", False

    riddle = get_riddle_by_id(state.current_rid)
    if riddle is None:
        return "The troll looks confused.", False

    if check_answer(riddle, player_input):
        state.record_correct(riddle.rid)
        response = random.choice(TROLL_CORRECT)

        if state.bridge_open:
            response += "\n\n" + TROLL_BRIDGE_OPEN
        elif state.correct_count in TROLL_PROGRESS:
            response += "\n\n" + TROLL_PROGRESS[state.correct_count]
            # Ask next riddle immediately
            next_riddle = state.pick_riddle()
            if next_riddle:
                state.current_rid = next_riddle.rid
                if next_riddle.rid not in state.seen:
                    state.seen.append(next_riddle.rid)
                prefix = random.choice(TROLL_ASK_PREFIX)
                response += f"\n\n{prefix}\n\n{next_riddle.text}"

        return response, True
    else:
        state.record_wrong(riddle.rid)
        return random.choice(TROLL_WRONG), False