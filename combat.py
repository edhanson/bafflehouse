# combat.py
#
# Combat resolution for Bafflehouse.
#
# Architecture
# ────────────
# CombatSession    — tracks the state of one combat encounter
# WEAPON_STATS     — damage/stamina costs keyed by entity id
# ARMOUR_STATS     — damage reduction/stamina penalty keyed by entity id
# resolve_exchange — turn-by-turn resolution: (player_action, npc_action) → narrative
# start_combat     — initialise a session and return the opening message
# player_action    — process one player combat command, return (narrative, session)
#
# Combat flow (managed by engine.py)
# ───────────────────────────────────
# 1. Player enters the golem's room → engine calls start_combat()
# 2. Each subsequent player command is intercepted by engine if in_combat
# 3. engine calls player_action(session, command, world) each turn
# 4. Session ends when player_hp ≤ 0 (death), golem_hp ≤ 0 (victory), or flee
#
# Stamina
# ───────
# Stamina is a secondary resource separate from HP.  It drains with every
# offensive action and recovers slightly with defensive ones.  When stamina
# falls below 20 the player's damage output is reduced; hitting 0 means the
# player can only dodge, block, flee, or taunt until they recover.

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from npc_qlearning import CombatState, QLearner, NPC_ACTIONS, PLAYER_ACTIONS, REWARDS


# ── Weapon stats ─────────────────────────────────────────────────────────────
# damage_range: (min, max) base damage
# stamina_cost: stamina spent on a normal attack
# heavy_bonus:  extra damage added by heavy attack
# heavy_cost:   additional stamina for heavy attack (added to stamina_cost)
# two_handed:   True → cannot equip shield simultaneously

WEAPON_STATS: Dict[str, dict] = {
    "bare_hands": {
        "name":         "your fists",
        "damage_range": (3, 5),
        "stamina_cost": 3,
        "heavy_bonus":  (3, 5),
        "heavy_cost":   10,
        "two_handed":   False,
    },
    "hunting_knife": {
        "name":         "the hunting knife",
        "damage_range": (6, 9),
        "stamina_cost": 6,
        "heavy_bonus":  (5, 8),
        "heavy_cost":   12,
        "two_handed":   False,
    },
    "broadsword": {
        "name":         "the broadsword",
        "damage_range": (10, 15),
        "stamina_cost": 12,
        "heavy_bonus":  (8, 12),
        "heavy_cost":   15,
        "two_handed":   True,
    },
    "iron_mace": {
        "name":         "the iron mace",
        "damage_range": (14, 20),
        "stamina_cost": 18,
        "heavy_bonus":  (10, 14),
        "heavy_cost":   18,
        "two_handed":   True,
    },
}


# ── Armour stats ──────────────────────────────────────────────────────────────
# damage_reduction: fraction of incoming damage absorbed (0.0–1.0)
# stamina_penalty:  extra stamina cost added to every action

ARMOUR_STATS: Dict[str, dict] = {
    "chain_coif": {
        "name":             "the chain coif",
        "damage_reduction": 0.25,
        "stamina_penalty":  3,
    },
    "kite_shield": {
        "name":             "the kite shield",
        "damage_reduction": 0.60,   # only applies when blocking
        "stamina_penalty":  0,       # no passive cost — only when worn
        "block_cost":       4,       # stamina cost to declare a block
    },
}

# Golem base stats
GOLEM_MAX_HP       = 120
GOLEM_MAX_STAMINA  = 999   # golem doesn't tire

# Player base stats
PLAYER_MAX_HP      = 100
PLAYER_MAX_STAMINA = 100

# Stamina thresholds
STAMINA_LOW        = 20    # below this, damage output reduced
STAMINA_EXHAUSTED  = 0     # only defensive/flee actions available

# Golem base damage ranges per action
GOLEM_DAMAGE: Dict[str, Tuple[int, int]] = {
    "strike":       (12, 18),
    "heavy_strike": (22, 30),
    "defensive":    (0,   0),   # brace — no attack
    "feint":        ( 8, 12),   # bypasses dodge/block, lower damage
    "pursue":       (10, 15),   # closing attack after flee attempt
    "special":      (15, 15),   # acid splash: flat damage, ignores armour
}

# How much the golem's damage reduces when Jasper distracts it
JASPER_DISTRACT_REDUCTION = 0.5   # action downgraded: heavy→strike, strike→miss
JASPER_HIT_PROB            = 0.4   # chance golem targets Jasper instead of player


# ── Jasper combat contribution ─────────────────────────────────────────────
# Probabilities must sum to 1.0
JASPER_COMBAT_OUTCOMES = {
    "distract": 0.25,   # golem action downgraded (reduced from 0.40)
    "attack":   0.25,   # 1–2 HP direct damage
    "hiss":     0.20,   # 50% chance golem wastes action on Jasper
    "cower":    0.30,   # no contribution (increased from 0.15)
}

JASPER_HISS_MESSAGES = [
    "Jasper arches his back, fur standing on end, and lets out a sound like "
    "a miniature thunderclap. The golem pauses, distracted.",
    "Jasper puffs to twice his size and advances on the golem, hissing "
    "with an intensity that seems implausible from something so small.",
    "Jasper's tail goes rigid and he emits a sustained yowl of pure fury. "
    "The golem turns toward him, momentarily uncertain.",
]
JASPER_ATTACK_MESSAGES = [
    "Jasper launches himself at the golem and rakes at its surface with "
    "both forepaws before rebounding off.",
    "Jasper bites down hard on what passes for the golem's ankle and twists. "
    "It makes no sound but shudders.",
    "Jasper streaks low and fast, clawing across the golem's base before "
    "it can react.",
]
JASPER_DODGE_MESSAGES = [
    "The golem swipes at Jasper — but he's already somewhere else.",
    "The golem's limb passes through the space where Jasper was a moment ago.",
    "Jasper ducks under the blow with uncanny timing.",
]
JASPER_HIT_MESSAGES = [
    "The golem's limb catches Jasper a glancing blow. He yowls and retreats "
    "a few steps, shaken.",
    "The golem connects. Jasper tumbles sideways and takes a moment to "
    "right himself, eyes wide.",
]
JASPER_COWER_MESSAGES = [
    "Jasper retreats to the corner and watches from a safe distance.",
    "Jasper has decided this is not his fight, at least for this round.",
]


# ── Combat session ─────────────────────────────────────────────────────────

@dataclass
class CombatSession:
    """
    State of a single combat encounter.

    Tracks HP, stamina, round number, the active weapon and armour,
    and whether the golem telegraphed a heavy strike last turn
    (giving the player a warning to dodge/block).
    """
    player_hp:          int   = PLAYER_MAX_HP
    player_max_hp:      int   = PLAYER_MAX_HP
    player_stamina:     int   = PLAYER_MAX_STAMINA
    player_max_stamina: int   = PLAYER_MAX_STAMINA
    golem_hp:           int   = GOLEM_MAX_HP
    golem_max_hp:       int   = GOLEM_MAX_HP
    round_num:          int   = 1
    weapon_id:          str   = "bare_hands"   # active weapon entity id
    wearing_coif:       bool  = False
    wearing_shield:     bool  = False
    last_player_action: str   = "attack"
    heavy_strike_warning: bool = False   # golem telegraphed heavy strike
    golem_defeated:     bool  = False
    jasper_present:     bool  = False    # True when devoted cat is in room
    jasper_rattled:     bool  = False    # True after golem hits Jasper
    wearing_amulet:     bool  = False    # True when jeweled amulet is worn
    acid_cooldown:      int   = 0        # turns until acid can fire again
    acid_total:         int   = 0        # total acid attacks this session
    ACID_COOLDOWN_MIN:  int   = 3        # minimum turns between acid attacks
    ACID_MAX_SESSION:   int   = 4        # max acid attacks per session

    def stamina_low(self) -> bool:
        return self.player_stamina < STAMINA_LOW

    def stamina_exhausted(self) -> bool:
        return self.player_stamina <= STAMINA_EXHAUSTED

    def spend_stamina(self, amount: int) -> int:
        """Spend stamina, flooring at 0. Returns actual amount spent."""
        spent = min(self.player_stamina, amount)
        self.player_stamina = max(0, self.player_stamina - amount)
        return spent

    def recover_stamina(self, amount: int) -> None:
        self.player_stamina = min(
            self.player_max_stamina,
            self.player_stamina + amount
        )

    def armour_penalty(self) -> int:
        """Total extra stamina cost from worn armour."""
        return ARMOUR_STATS["chain_coif"]["stamina_penalty"] if self.wearing_coif else 0

    def coif_reduction(self) -> float:
        return ARMOUR_STATS["chain_coif"]["damage_reduction"] if self.wearing_coif else 0.0

    def _action_cost(self, base_cost: int) -> int:
        """Total stamina cost including armour penalty."""
        return base_cost + self.armour_penalty()

    def can_attack(self) -> bool:
        """True if the player has enough stamina for a normal attack."""
        weapon = WEAPON_STATS.get(self.weapon_id, WEAPON_STATS["bare_hands"])
        return self.player_stamina >= self._action_cost(weapon["stamina_cost"])

    def can_heavy_attack(self) -> bool:
        """True if the player has enough stamina for a heavy attack."""
        weapon = WEAPON_STATS.get(self.weapon_id, WEAPON_STATS["bare_hands"])
        cost = weapon["stamina_cost"] + weapon["heavy_cost"]
        return self.player_stamina >= self._action_cost(cost)

    def update_jasper(self, present: bool) -> None:
        """Update Jasper's presence — checked each round, not just at start."""
        if present and not self.jasper_present:
            self.jasper_rattled = False   # fresh arrival, reset rattled
        self.jasper_present = present

    def to_combat_state(self) -> CombatState:
        return CombatState(
            player_hp          = self.player_hp,
            player_max_hp      = self.player_max_hp,
            npc_hp             = self.golem_hp,
            npc_max_hp         = self.golem_max_hp,
            player_last_action = self.last_player_action,
            round_num          = self.round_num,
        )


# ── Narrative pools ───────────────────────────────────────────────────────

_OPENING = [
    (
        "The golem turns toward you. For a moment it is still — then it lunges, "
        "and the air fills with the smell of sulphur and hot metal.\n"
        "Combat has begun."
    ),
    (
        "The thing notices you with something that isn't exactly sight. "
        "It reorients, shoulders spreading, and begins to move.\n"
        "There is no avoiding this."
    ),
]

_PLAYER_ATTACKS = {
    "bare_hands": [
        "You swing with your fist. It connects with something that yields "
        "unpleasantly, like wet clay.",
        "You strike at the golem with your bare hands. It isn't ideal.",
    ],
    "hunting_knife": [
        "You drive the knife into the golem's mass. Dark ichor clings to the blade.",
        "The knife finds purchase. The golem makes no sound but recoils slightly.",
    ],
    "broadsword": [
        "The broadsword bites deep. A chunk of the golem separates briefly "
        "before reabsorbing.",
        "You bring the blade across in a sweeping arc. It cuts through.",
    ],
    "iron_mace": [
        "The mace connects with a wet, heavy thud. The golem staggers.",
        "You bring the mace down hard. Whatever it hits, it hits solidly.",
    ],
}

_PLAYER_HEAVY_ATTACKS = {
    "bare_hands":    ["You throw everything into a desperate lunge."],
    "hunting_knife": ["You drive the knife in with both hands and twist."],
    "broadsword":    ["You wind up and deliver a two-handed blow with full force."],
    "iron_mace":     ["You raise the mace overhead and bring it down with everything you have."],
}

_GOLEM_STRIKES = [
    "The golem's limb sweeps across — a formless, heavy blow.",
    "It slams a mass of itself toward you with surprising speed.",
    "The golem flows forward and impacts you with its full weight.",
]
_GOLEM_HEAVY_WARNING = [
    "The golem draws back, coiling like a spring. Something is coming.",
    "It pulls its mass inward — telegraphing. You have a moment.",
]
_GOLEM_HEAVY_STRIKES = [
    "It releases everything at once. The impact is massive.",
    "The golem explodes forward. There is no stopping this one.",
]
_GOLEM_DEFENSIVE = [
    "The golem pulls its mass inward, becoming dense and compact. "
    "Your attack finds little purchase.",
    "It hardens — your blow transmits almost nothing.",
]
_GOLEM_FEINT = [
    "The golem makes a false motion — you adjust, and that's what it wanted. "
    "The real strike comes from the other side.",
    "It feints left. By the time you realise, the impact is already happening.",
]
_GOLEM_SPECIAL = [
    "The golem opens — briefly — and a spray of something caustic erupts outward. "
    "It burns where it touches you.",
    "Acid. You don't see it coming until you feel it.",
]
_GOLEM_PURSUE = [
    "The golem flows after you and catches you on the way out.",
    "You turn to run — it's already there.",
]

_DODGE_SUCCESS = [
    "You throw yourself aside. The blow passes through where you were.",
    "You duck and roll. It misses — barely.",
    "You sidestep cleanly.",
]
_DODGE_FAIL_FEINT = [
    "You dodge — but the feint caught you leaning the wrong way.",
    "You moved. It was the wrong direction.",
]
_BLOCK_SUCCESS = [
    "You take the blow on the shield. The impact rattles up your arm but holds.",
    "The shield catches it. Your arm rings but you're intact.",
]
_BLOCK_FAIL_FEINT = [
    "You raise the shield — the feint goes around it.",
    "The shield was in the right place. The strike wasn't.",
]
_BLOCK_ACID = [
    "You raise the shield but acid splashes around it.",
    "The shield stops some of it. Not all.",
]
_TAUNT_MESSAGES = [
    "You shout something deliberately provoking. The golem's posture shifts.",
    "You gesture dismissively. Somewhere in its mass, something responds to that.",
]
_FLEE_MESSAGES = [
    "You back toward the exit and bolt.",
    "You disengage and run.",
]
_FLEE_CAUGHT = [
    "The golem cuts off your escape. You can't get past it.",
    "It anticipated the retreat. You're not getting out that way this round.",
]
_EXHAUSTED = [
    "You're too spent for that. You need to recover.",
    "Your arms won't respond properly. Something less demanding.",
]

_GOLEM_DEATH = (
    "The golem shudders. Something that might be a sound escapes it — not a "
    "voice, just a release of pressure — and then it collapses. The form that "
    "was vaguely humanoid flattens into a spreading pool of dark, iridescent "
    "fluid. The smell of sulphur lingers.\n\n"
    "In the centre of the remains, something glitters."
)
_PLAYER_DEATH = (
    "The last impact sends you to the floor. The golem stands over you, "
    "its mass settling. The ceiling above looks very far away.\n\n"
    "You are dead.\n\n"
    "[ Press Enter to quit, or type RESTART to begin again. ]"
)
_FLEE_SUCCESS = (
    "You make it out. The golem does not immediately follow — "
    "but you can hear it behind you, reorienting."
)


def _pick(pool: list) -> str:
    return random.choice(pool)


def _weapon_stats(weapon_id: str) -> dict:
    return WEAPON_STATS.get(weapon_id, WEAPON_STATS["bare_hands"])


def _roll(rng: Tuple[int, int]) -> int:
    return random.randint(rng[0], rng[1])


# ── Jasper contribution ───────────────────────────────────────────────────

def resolve_jasper(session: CombatSession) -> Tuple[str, str]:
    """
    Determine Jasper's contribution this round.

    Returns (jasper_narrative, effect) where effect is one of:
        "distract"  — golem action downgraded
        "attack"    — golem takes 1-2 HP damage
        "hiss"      — 50% chance golem wastes its action
        "cower"     — no effect
        "absent"    — Jasper not present
    """
    if not session.jasper_present:
        return "", "absent"

    # Rattled Jasper (just been hit) cowers with 80% probability
    if session.jasper_rattled:
        if random.random() < 0.80:
            return _pick(JASPER_COWER_MESSAGES), "cower"
        # 20% chance: adrenaline keeps him fighting despite being shaken

    roll = random.random()
    cumulative = 0.0
    for outcome, prob in JASPER_COMBAT_OUTCOMES.items():
        cumulative += prob
        if roll < cumulative:
            if outcome == "distract":
                return _pick(JASPER_ATTACK_MESSAGES), "distract"
            elif outcome == "attack":
                return _pick(JASPER_ATTACK_MESSAGES), "attack"
            elif outcome == "hiss":
                return _pick(JASPER_HISS_MESSAGES), "hiss"
            else:
                return _pick(JASPER_COWER_MESSAGES), "cower"
    return _pick(JASPER_COWER_MESSAGES), "cower"


def apply_jasper_to_golem_action(
    golem_action: str,
    jasper_effect: str,
    session: CombatSession,
) -> Tuple[str, str]:
    """
    Modify the golem's action based on Jasper's effect.

    Returns (possibly modified golem_action, extra_narrative).
    """
    extra = ""
    if jasper_effect == "distract":
        # Downgrade: heavy_strike → strike, strike → miss, others unchanged
        if golem_action == "heavy_strike":
            golem_action = "strike"
            extra = " Jasper's assault draws the golem's attention just enough."
        elif golem_action == "strike":
            golem_action = "_partial"  # sentinel: golem hits at half damage
            extra = " Jasper's interference costs the golem its aim."
    elif jasper_effect == "hiss":
        # 50% chance golem targets Jasper instead
        if random.random() < 0.50:
            golem_action = "_swipe_jasper"
    elif jasper_effect == "attack":
        # Small HP damage applied separately — no action change
        pass
    return golem_action, extra


# ── Core resolution ───────────────────────────────────────────────────────

def resolve_exchange(
    session:      CombatSession,
    player_action: str,
    golem_action:  str,
    learner:       QLearner,
) -> Tuple[str, int, int, float]:
    """
    Resolve one round of combat.

    Returns (narrative, player_hp_delta, golem_hp_delta, reward).
    Deltas are negative for damage, positive for healing (not used currently).
    """
    lines: List[str] = []
    player_dmg = 0   # damage taken by player
    golem_dmg  = 0   # damage taken by golem
    reward     = REWARDS["round_survived"]
    weapon     = _weapon_stats(session.weapon_id)
    coif_red   = session.coif_reduction()

    # ── Jasper contribution ───────────────────────────────────────────────
    jasper_narrative, jasper_effect = resolve_jasper(session)
    if jasper_narrative:
        lines.append(jasper_narrative)

    # Apply Jasper's effect to golem action
    golem_action, jasper_extra = apply_jasper_to_golem_action(
        golem_action, jasper_effect, session
    )
    if jasper_extra:
        lines.append(jasper_extra)

    # Jasper direct attack damage
    if jasper_effect == "attack":
        jasper_dmg = random.randint(1, 2)
        golem_dmg += jasper_dmg

    # ── Stamina check ──────────────────────────────────────────────────────
    armour_penalty = session.armour_penalty()

    # ── Player action resolution ──────────────────────────────────────────
    if player_action == "attack":
        cost = weapon["stamina_cost"] + armour_penalty
        session.spend_stamina(cost)
        base_dmg = _roll(weapon["damage_range"])
        if session.stamina_low():
            base_dmg = max(1, int(base_dmg * 0.6))
        # Golem defensive halves the damage
        if golem_action == "defensive":
            base_dmg = max(1, base_dmg // 2)
            lines.append(_pick(_GOLEM_DEFENSIVE))
        else:
            lines.append(_pick(_PLAYER_ATTACKS.get(session.weapon_id,
                               _PLAYER_ATTACKS["bare_hands"])))
        # Amulet bonus: +1 damage on every successful hit
        if session.wearing_amulet:
            base_dmg += session.wearing_amulet  # True == 1
            lines.append(
                "The amulet glows faintly as a mysterious power courses "
                "through your veins."
            )
        golem_dmg += base_dmg
        reward += REWARDS["hit_landed"]

    elif player_action == "heavy_attack":
        cost = weapon["stamina_cost"] + weapon["heavy_cost"] + armour_penalty
        session.spend_stamina(cost)
        base_dmg = _roll(weapon["damage_range"])
        bonus    = _roll(weapon["heavy_bonus"])
        if golem_action == "defensive":
            total = max(1, (base_dmg + bonus) // 3)
            lines.append(_pick(_GOLEM_DEFENSIVE))
        else:
            total = base_dmg + bonus
            lines.append(_pick(_PLAYER_HEAVY_ATTACKS.get(
                session.weapon_id, ["You attack with full force."]
            )))
        if session.stamina_low():
            total = max(1, int(total * 0.6))
        # Amulet bonus: +1 damage on every successful hit
        if session.wearing_amulet and golem_action != "defensive":
            total += 1
            lines.append(
                "The amulet glows faintly as a mysterious power courses "
                "through your veins."
            )
        golem_dmg += total
        reward += REWARDS["hit_landed"] * 1.5

    elif player_action == "dodge":
        session.recover_stamina(8 - armour_penalty)
        # Dodge succeeds against strike/heavy_strike/pursue, fails against feint.
        # Each golem action gets explicit narrative so there's always a message.
        if golem_action in ("strike", "heavy_strike", "pursue", "_miss"):
            lines.append(_pick(_DODGE_SUCCESS))
            reward += abs(REWARDS["player_dodged"])
            golem_action = "_dodged"
        elif golem_action == "feint":
            lines.append(_pick(_DODGE_FAIL_FEINT))
            reward += REWARDS["player_dodged"]
        elif golem_action == "defensive":
            # Golem is bracing — nothing to dodge, stamina still recovers
            lines.append(
                "The golem pulls inward, bracing. Your dodge was unnecessary "
                "but costs nothing."
            )
        elif golem_action == "special":
            # Dodging reduces acid splash damage by half — you get clear but
            # not completely
            lines.append(
                "You throw yourself aside — the acid spray catches you at "
                "the edge of its arc."
            )
            # Acid damage applied below at half value; mark with sentinel
            golem_action = "_acid_partial"

    elif player_action == "block":
        if not session.wearing_shield:
            lines.append("You have no shield to block with.")
            session.spend_stamina(2)  # wasted action
        else:
            block_cost = ARMOUR_STATS["kite_shield"]["block_cost"] + armour_penalty
            session.spend_stamina(block_cost)
            if golem_action in ("strike", "heavy_strike", "pursue"):
                lines.append(_pick(_BLOCK_SUCCESS))
                golem_action = "_blocked"  # partial damage only
                reward += abs(REWARDS["player_blocked"])
            elif golem_action == "feint":
                lines.append(_pick(_BLOCK_FAIL_FEINT))
                reward += REWARDS["player_blocked"]
                # Feint still hits
            elif golem_action == "special":
                lines.append(_pick(_BLOCK_ACID))
                # Acid splash gets partial reduction

    elif player_action == "taunt":
        session.recover_stamina(5)
        lines.append(_pick(_TAUNT_MESSAGES))
        # Taunt has no immediate damage effect — Q-learner will learn
        # to respond with heavy_strike, which the engine handles next round

    elif player_action == "flee":
        # Pursue action catches fleeing players
        if golem_action == "pursue":
            lines.append(_pick(_FLEE_CAUGHT))
            golem_action = "pursue"  # damage applied below
        else:
            lines.append(_pick(_FLEE_MESSAGES))
            # Return special sentinel reward value to signal flee success
            return "\n".join(lines), 0, 0, float("inf")

    elif player_action == "use_item":
        # Handled by caller (engine) before this function — placeholder
        lines.append("You use an item.")

    # ── Golem action resolution ───────────────────────────────────────────
    if golem_action == "_miss":
        lines.append("The golem's attack finds nothing.")

    elif golem_action == "_partial":
        # Distracted strike — half damage
        dmg_range = GOLEM_DAMAGE["strike"]
        raw_dmg   = _roll(dmg_range) // 2
        final_dmg = max(1, int(raw_dmg * (1.0 - coif_red)))
        lines.append(_pick(_GOLEM_STRIKES))
        lines.append(f"You take {final_dmg} damage (glancing blow).")
        player_dmg += final_dmg
        reward += REWARDS["hit_received"] * 0.5

    elif golem_action == "_dodged":
        pass   # already narrated

    elif golem_action == "_blocked":
        # Partial damage through shield
        base = _roll(GOLEM_DAMAGE.get("strike", (12, 18)))
        blocked = int(base * ARMOUR_STATS["kite_shield"]["damage_reduction"])
        partial = base - blocked
        partial = int(partial * (1.0 - coif_red))
        if partial > 0:
            lines.append(f"The blow transmits through the shield. You take {partial} damage.")
            player_dmg += partial
            reward += REWARDS["hit_received"] * 0.4
        else:
            lines.append("The shield absorbs it completely.")

    elif golem_action == "_swipe_jasper":
        # Golem goes after Jasper
        if random.random() < 0.70:   # 70% Jasper dodge chance
            lines.append(_pick(JASPER_DODGE_MESSAGES))
        else:
            lines.append(_pick(JASPER_HIT_MESSAGES))
            session.jasper_rattled = True
            # Jasper disposition reduction handled by engine

    elif golem_action in ("strike", "heavy_strike", "pursue"):
        dmg_range = GOLEM_DAMAGE[golem_action]
        raw_dmg   = _roll(dmg_range)
        final_dmg = int(raw_dmg * (1.0 - coif_red))
        if golem_action == "heavy_strike":
            lines.append(_pick(_GOLEM_HEAVY_STRIKES))
        elif golem_action == "pursue":
            lines.append(_pick(_GOLEM_PURSUE))
        else:
            lines.append(_pick(_GOLEM_STRIKES))
        lines.append(f"You take {final_dmg} damage.")
        player_dmg += final_dmg
        reward += REWARDS["hit_received"]

    elif golem_action == "defensive":
        # The golem braces — narratively handled in the player attack branches
        # above for "attack" and "heavy_attack".  For dodge, block, taunt,
        # flee, etc. the brace is silent: the player isn't swinging.
        pass

    elif golem_action == "feint":
        dmg_range = GOLEM_DAMAGE["feint"]
        raw_dmg   = _roll(dmg_range)
        final_dmg = int(raw_dmg * (1.0 - coif_red))
        lines.append(_pick(_GOLEM_FEINT))
        lines.append(f"You take {final_dmg} damage.")
        player_dmg += final_dmg
        reward += REWARDS["hit_received"]

    elif golem_action in ("special", "_acid_partial"):
        # Acid: self-damage applies regardless of whether player dodged.
        # Cooldown and total are only counted once (on the original "special").
        if golem_action == "special":
            session.acid_cooldown  = session.ACID_COOLDOWN_MIN
            session.acid_total    += 1
            golem_dmg             += 5   # self-damage
        raw_dmg = _roll(GOLEM_DAMAGE["special"])
        # Damage reduction by situation
        if golem_action == "_acid_partial":
            # Player dodged — caught at the edge of the arc
            final_dmg = max(1, raw_dmg // 2)
        elif player_action == "block" and session.wearing_shield:
            final_dmg = int(raw_dmg * 0.5)
            lines.append(_pick(_BLOCK_ACID))
        else:
            final_dmg = raw_dmg
            lines.append(_pick(_GOLEM_SPECIAL))
        lines.append(f"You take {final_dmg} damage from the acid.")
        player_dmg += final_dmg
        reward += REWARDS["hit_received"] * 1.2

    return "\n".join(lines), -player_dmg, -golem_dmg, reward


# ── Public interface ──────────────────────────────────────────────────────

def start_combat(
    session: CombatSession,
    weapon_id: str = "bare_hands",
    wearing_coif: bool = False,
    wearing_shield: bool = False,
    jasper_present: bool = False,
    wearing_amulet: bool = False,
) -> str:
    """Initialise combat state and return the opening narrative."""
    session.weapon_id      = weapon_id
    session.wearing_coif   = wearing_coif
    session.wearing_shield = wearing_shield
    session.jasper_present = jasper_present
    session.wearing_amulet = wearing_amulet
    return _pick(_OPENING) + "\n\n" + _combat_prompt(session)


def _combat_prompt(session: CombatSession) -> str:
    """Return the action prompt shown to the player each round."""
    hp  = session.player_hp
    st  = session.player_stamina
    ghp = session.golem_hp

    shield_str = " / block" if session.wearing_shield else ""
    can_atk   = session.can_attack()
    can_heavy = session.can_heavy_attack()

    # Build the action list based on what stamina allows
    action_parts = []
    if can_atk:
        action_parts.append("attack")
    if can_heavy:
        action_parts.append("heavy attack")
    action_parts.append("dodge" + shield_str)
    action_parts.append("flee")
    action_parts.append("taunt")
    if can_atk:
        action_parts.append("use item")
    actions = " / ".join(action_parts)

    if not can_atk:
        note = " [exhausted — offensive actions unavailable]"
    elif not can_heavy:
        note = " [low stamina — heavy attack unavailable]"
    else:
        note = ""

    warning = ""
    if session.heavy_strike_warning:
        warning = "\n[!] The golem is coiling for a powerful strike."

    jasper_str = ""
    if session.jasper_present and not session.jasper_rattled:
        jasper_str = "\n[Jasper] Jasper is fighting alongside you."
    elif session.jasper_present and session.jasper_rattled:
        jasper_str = "\n[Jasper] Jasper has been struck and retreated to the edge of the room."

    return (
        f"HP: {hp}/100  Stamina: {st}/100  Golem HP: {ghp}/120"
        f"{jasper_str}{warning}\n"
        f"[{actions}]{note}"
    )


def combat_status(session: CombatSession) -> str:
    """Return a short status line for mid-combat display."""
    return _combat_prompt(session)


def process_player_combat_action(
    session:       CombatSession,
    player_input:  str,
    learner:       QLearner,
) -> Tuple[str, str]:
    """
    Process one player turn in combat.

    Returns (full_narrative, outcome) where outcome is one of:
        "continue"  — combat ongoing
        "player_dead" — player HP reached 0
        "golem_dead"  — golem HP reached 0
        "fled"        — player successfully fled
        "invalid"     — unrecognised action (no turn consumed)
    """
    normalised = player_input.lower().strip()

    # Map input to canonical action
    action_map = {
        "attack":        "attack",
        "a":             "attack",
        "heavy attack":  "heavy_attack",
        "heavy":         "heavy_attack",
        "ha":            "heavy_attack",
        "dodge":         "dodge",
        "d":             "dodge",
        "block":         "block",
        "b":             "block",
        "flee":          "flee",
        "run":           "flee",
        "escape":        "flee",
        "taunt":         "taunt",
        "t":             "taunt",
        "use item":      "use_item",
        "use":           "use_item",
    }

    # Strip target names so "taunt golem", "dodge the creature", etc. work.
    # Also handles "attack golem with X" → "attack".
    # Sorted longest-first so "the slime golem" matches before "golem"
    _target_words = ("the slime golem", "slime golem", "the golem",
                     "the creature", "the monster", "the thing",
                     "golem", "creature", "monster", "thing", "it")
    _attack_with = ("attack the slime golem with", "attack slime golem with",
                    "attack the golem with", "attack golem with",
                    "attack with", "hit with", "strike with")
    for prefix in _attack_with:
        if normalised.startswith(prefix):
            normalised = "attack"
            break
    else:
        # Strip trailing target word from any command
        for target in _target_words:
            if normalised.endswith(" " + target):
                normalised = normalised[: -(len(target) + 1)].strip()
                break
            if normalised.startswith(target + " "):
                normalised = normalised[len(target) + 1:].strip()
                break

    player_action = action_map.get(normalised)
    if player_action is None:
        return f"In combat, your options are limited. {_combat_prompt(session)}", "invalid"

    # Stamina gate — each action requires enough stamina to cover its cost.
    if player_action == "attack" and not session.can_attack():
        return _pick(_EXHAUSTED) + "\n\n" + _combat_prompt(session), "invalid"
    if player_action == "heavy_attack" and not session.can_heavy_attack():
        return _pick(_EXHAUSTED) + "\n\n" + _combat_prompt(session), "invalid"

    # Choose golem action via Q-learner using minimum-floor blended
    # probabilities — every action has at least 5% chance each round.
    # Acid cooldown and session cap are enforced via the forbidden list,
    # so the learner still picks naturally but acid is excluded when
    # it is on cooldown or has been used too many times this session.
    # "pursue" is forbidden unless the player is actually fleeing.
    state = session.to_combat_state()
    forbidden = []
    if player_action != "flee":
        forbidden.append("pursue")
    if session.acid_cooldown > 0 or session.acid_total >= session.ACID_MAX_SESSION:
        forbidden.append("special")
    # Tick down acid cooldown
    if session.acid_cooldown > 0:
        session.acid_cooldown -= 1
    golem_action = learner.choose_action(state, forbidden=forbidden)

    # Telegraph heavy strike one round in advance
    if golem_action == "heavy_strike" and not session.heavy_strike_warning:
        session.heavy_strike_warning = True
        golem_action = "defensive"   # delay the heavy strike by one round
    else:
        session.heavy_strike_warning = False

    # Resolve the exchange
    original_golem_action = golem_action   # preserve for Q-update (sentinels may modify it)
    narrative, player_delta, golem_delta, reward = resolve_exchange(
        session, player_action, golem_action, learner
    )

    # Apply HP changes
    session.player_hp += player_delta
    session.golem_hp  += golem_delta
    session.player_hp  = max(0, session.player_hp)
    session.golem_hp   = max(0, session.golem_hp)
    session.last_player_action = player_action
    session.round_num += 1

    # Q-learner update
    if session.player_hp <= 0 or session.golem_hp <= 0:
        next_state = None
        if session.player_hp <= 0:
            reward += REWARDS["combat_win"]    # from golem's perspective
        else:
            reward += REWARDS["combat_loss"]
    else:
        next_state = session.to_combat_state()

    learner.update(state, original_golem_action, reward, next_state)

    # Determine outcome.
    # Flee success is signalled by reward == inf (sentinel from resolve_exchange).
    if reward == float("inf"):
        learner.update(state, original_golem_action, REWARDS["player_fled"], None)
        learner.end_session()
        return narrative + "\n\n" + _fled_prompt(), "fled"

    if session.player_hp <= 0:
        learner.end_session()
        return narrative + "\n\n" + _PLAYER_DEATH, "player_dead"

    if session.golem_hp <= 0:
        session.golem_defeated = True
        learner.end_session()
        return narrative + "\n\n" + _GOLEM_DEATH, "golem_dead"

    return narrative + "\n\n" + _combat_prompt(session), "continue"


def _fled_prompt() -> str:
    return _FLEE_SUCCESS