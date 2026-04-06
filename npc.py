# npc.py
#
# NPC data model, wandering logic, and per-turn behaviour tick for Bafflehouse.
#
# Architecture
# ────────────
# NPCDefinition   — static data describing a creature (name, home rooms,
#                   event table, disposition->behaviour mappings).
# NPC             — runtime instance: current location, last-known player
#                   location, any per-session state.
# npc_tick()      — called by engine.process_input after every player action.
#                   Moves NPCs, updates trust from proximity, generates
#                   atmospheric narrative lines, and returns them to the engine
#                   to append to the turn output.
#
# All ML state (trust, disposition) lives in NPCMemory (npc_bayesian.py).
# This file only contains behaviour logic — what the NPC does given its
# current disposition and situation.

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from npc_bayesian import BayesianReputation, NPCMemory


# ── Atmospheric message pools ────────────────────────────────────────────
# Keyed by (npc_id, disposition, situation).  Each entry is a list of
# strings; one is chosen at random each time that situation fires.
# The engine appends these to the turn output after the player's action.

JASPER_MESSAGES: Dict[Tuple[str, str], List[str]] = {
    # Player enters Jasper's room — various dispositions
    ("enters_room", "cautious"): [
        "A grey cat bolts from the corner and slips through the doorway.",
        "Something small and fast disappears around the edge of the door.",
        "A cat that was sitting in the shadows stands, stares at you for one "
        "cold moment, then walks deliberately out of the room.",
    ],
    ("enters_room", "wary"): [
        "A grey cat regards you from across the room, ears slightly back.",
        "A cat is sitting against the far wall. It watches you without moving.",
        "The cat lifts its head as you enter, then looks deliberately away.",
    ],
    ("enters_room", "neutral"): [
        "A grey cat is here. It glances at you, then resumes washing its paw.",
        "The cat acknowledges your arrival with a slow blink, then looks away.",
        "A cat sits near the wall. It doesn't flee, but doesn't approach either.",
    ],
    ("enters_room", "friendly"): [
        "The cat looks up as you enter and makes a small sound.",
        "A grey cat trots toward you, tail raised like a question mark.",
        "The cat stands and stretches elaborately as you come in.",
    ],
    ("enters_room", "devoted"): [
        "The cat is here. It gets up the moment it sees you.",
        "The cat pads toward you immediately, weaving between your feet.",
        "The cat makes a small sound and comes to meet you at the door.",
    ],

    # Cat has just followed the player into a new room (devoted only).
    # These describe arrival, not a cat already present.
    ("follows", "devoted"): [
        "The cat pads in after you a moment later.",
        "The cat slips through the door behind you.",
        "A soft sound of paws on stone — the cat has followed you in.",
        "The cat rounds the doorway and comes to your side.",
        "The cat trots in and settles near you.",
    ],

    # Cat is already in the same room — ambient presence messages
    ("ambient", "cautious"): [],   # too scared to produce ambient lines
    ("ambient", "wary"): [
        "The cat shifts position slightly, keeping its eyes on you.",
        "The cat's tail flicks once.",
    ],
    ("ambient", "neutral"): [
        "The cat yawns enormously.",
        "The cat sits very still, apparently staring at the wall.",
        "The cat grooms itself with focused intensity.",
    ],
    ("ambient", "friendly"): [
        "The cat bumps its head against your leg.",
        "The cat winds between your feet.",
        "A quiet rumble — the cat is purring.",
    ],
    ("ambient", "devoted"): [
        "The cat stays close to your heels.",
        "The cat watches the corridor ahead of you.",
        "The cat's ears swivel, tracking sounds further down the hall.",
    ],

    # Reactions to specific player actions
    ("fed", "neutral"): [
        "The cat eats with undisguised interest, then sits back and regards you.",
        "The cat demolishes the food quickly and looks up for more.",
    ],
    ("fed", "friendly"): [
        "The cat eats, then rubs its face against your hand.",
        "The cat purrs loudly while eating.",
    ],
    ("fed", "devoted"): [
        "The cat eats quickly, then immediately returns to your side.",
    ],
    ("catnip", "neutral"): [
        "The cat sniffs the catnip, rolls onto its back, and forgets you exist.",
        "Something in the cat's dignified composure dissolves. It rolls wildly.",
    ],
    ("catnip", "friendly"): [
        "The cat snatches the catnip and kicks it across the floor, purring.",
    ],
    ("petted", "friendly"): [
        "The cat leans into your hand.",
        "A deep purr. The cat closes its eyes.",
        "The cat turns its head so you scratch behind its ear.",
    ],
    ("petted", "devoted"): [
        "The cat presses its whole weight against your hand.",
        "The cat closes its eyes and purrs.",
    ],
    ("pet_rejected", "cautious"): [
        "The cat flattens its ears and backs away.",
        "The cat retreats to a safer distance.",
    ],
    ("pet_rejected", "wary"): [
        "The cat leans away from your outstretched hand.",
        "The cat tolerates your proximity but won't let you touch it.",
    ],
    ("pet_rejected", "neutral"): [
        "The cat allows the briefest contact, then steps aside.",
    ],
    ("offered_item", "cautious"): [
        "The cat stares at the offered item from a distance, then leaves.",
    ],
    ("offered_item", "wary"): [
        "The cat approaches a few steps, sniffs the air, then retreats.",
    ],
    ("offered_item", "neutral"): [
        "The cat sniffs your offering with careful attention.",
    ],
    ("called", "cautious"): [
        "No response.",
        "The cat, if it heard you, gives no sign.",
    ],
    ("called", "wary"): [
        "The cat glances in your direction.",
        "An ear rotates toward you. That is all.",
    ],
    ("called", "neutral"): [
        "The cat looks at you briefly, then looks away.",
    ],
    ("called", "friendly"): [
        "The cat meows.",
        "The cat trots toward you.",
    ],
    ("called", "devoted"): [
        "The cat comes immediately.",
        "The cat makes a small chirping sound and comes to your side.",
    ],

    # ── Feeding: cautious and wary dispositions ───────────────────────
    # The cat is still wary but hunger (or curiosity) wins briefly.
    ("fed", "cautious"): [
        "The cat darts forward, snatches the food, and retreats to a safe distance.",
        "The cat takes the food quickly, watching you the entire time.",
    ],
    ("fed", "wary"): [
        "The cat approaches carefully, eats without looking away from you, then retreats.",
        "The cat eats with one eye on you throughout.",
        "The cat finishes quickly and steps back.",
    ],

    # ── Catnip: all reachable dispositions ───────────────────────────
    ("catnip", "cautious"): [
        "The cat sniffs the catnip suspiciously, then — against all dignity — rolls.",
        "Something overrides the cat's caution. It buries its face in the catnip.",
    ],
    ("catnip", "wary"): [
        "The cat's wariness collapses entirely. It rolls, kicks, and forgets you exist.",
        "Catnip, it turns out, is a more powerful force than suspicion.",
    ],
    ("catnip", "devoted"): [
        "The cat seizes the catnip and rolls ecstatically, purring the entire time.",
        "Even at this point, the catnip briefly makes it forget you exist.",
    ],

    # ── Petting: wary and neutral don't allow it but react differently ──
    ("petted", "cautious"): [
        "The cat flinches and backs away sharply.",
    ],
    ("petted", "wary"): [
        "The cat tolerates a single brief touch, then moves away.",
        "The cat allows the contact for a moment, then leans away.",
    ],
    ("petted", "neutral"): [
        "The cat allows it, though it doesn't seem particularly moved.",
        "The cat remains still for the petting, expression unreadable.",
    ],

    # ── Pet rejection at higher dispositions (shouldn't normally fire) ─
    ("pet_rejected", "friendly"): [],   # friendly always accepts petting
    ("pet_rejected", "devoted"): [],    # devoted always accepts petting

    # ── Offering at higher dispositions ──────────────────────────────
    ("offered_item", "friendly"): [
        "The cat sniffs your offering with interest.",
        "The cat examines it, then bumps your hand.",
    ],
    ("offered_item", "devoted"): [
        "The cat sniffs the offering and presses against your leg.",
        "The cat examines it carefully, then looks up at you.",
    ],
}


def get_message(
    npc_id: str,
    situation: str,
    disposition: str,
    display_name: Optional[str] = None,
) -> Optional[str]:
    """
    Return a random atmospheric message for the given situation and disposition,
    or None if no messages are defined for that combination.

    If display_name is provided and differs from the default ("the cat"),
    occurrences of "The cat" and "the cat" in the message are replaced so
    that Jasper's name appears once the player has learned it.
    """
    pool = JASPER_MESSAGES.get((situation, disposition), [])
    if not pool:
        return None
    msg = random.choice(pool)
    if display_name and display_name.lower() != "a grey cat":
        msg = msg.replace("The cat", display_name.capitalize())
        msg = msg.replace("the cat", display_name.lower())
    return msg


# ── NPC definition ────────────────────────────────────────────────────────

@dataclass
class NPCDefinition:
    """
    Static data describing a creature.  One instance per creature type.
    Shared across all runtime NPC instances of that type.
    """
    npc_id:        str
    name:          str                    # display name, e.g. "a grey cat"
    proper_name:   str                    # once known, e.g. "Jasper"
    home_rooms:    Set[str]               # rooms the NPC may wander into
    start_room:    str                    # where it begins each session
    wander_chance: float = 0.4            # probability of moving each turn

    # Disposition -> set of interactions the NPC will accept
    # Interactions not listed for a disposition are rejected
    accepts_at: Dict[str, Set[str]] = field(default_factory=dict)

    # Custom event table (confirm_delta, disconfirm_delta) per named event.
    # Merged with DEFAULT_EVENTS at construction time.
    event_overrides: Dict[str, Tuple[float, float]] = field(
        default_factory=dict
    )

    # Whether the NPC follows the player when devoted
    follows_when_devoted: bool = True

    # Whether the NPC fights alongside the player when devoted
    fights_when_devoted: bool = True


# ── Runtime NPC instance ──────────────────────────────────────────────────

@dataclass
class NPC:
    """
    Runtime state for one NPC instance.

    defn           — the NPCDefinition for this creature type
    location       — current room ID
    last_room      — room the NPC was in last turn (for rapid-entry detection)
    revealed_name  — True once the player has learned the NPC's proper name
    session_moves  — number of turns elapsed this session (for diagnostics)
    """
    defn:           NPCDefinition
    location:       str
    last_room:      str       = ""
    revealed_name:  bool      = False
    session_moves:  int       = 0
    just_fled:      bool      = False  # set True when forcibly moved away;
                                       # prevents tick from wandering back

    @property
    def npc_id(self) -> str:
        return self.defn.npc_id

    @property
    def display_name(self) -> str:
        return self.defn.proper_name if self.revealed_name else self.defn.name


# ── Wandering logic ───────────────────────────────────────────────────────

def _choose_wander_destination(
    npc:   NPC,
    world: object,             # World — typed as object to avoid circular import
    away_from: Optional[str] = None,
) -> Optional[str]:
    """
    Choose a room for the NPC to wander into.

    If away_from is set (a room ID), prefer exits that lead away from it —
    used when the NPC is fleeing.  Returns None if no valid destination exists.
    """
    current_room = world.rooms.get(npc.location)
    if not current_room:
        return None

    # Candidate destinations: adjacent rooms within home territory
    candidates = [
        rid for rid in current_room.exits.values()
        if rid in npc.defn.home_rooms
    ]
    if not candidates:
        return None

    if away_from and len(candidates) > 1:
        # Prefer rooms that are not the player's room
        fleeing = [r for r in candidates if r != away_from]
        if fleeing:
            return random.choice(fleeing)

    return random.choice(candidates)


def _move_npc(npc: NPC, destination: str, world: object) -> None:
    """Move an NPC entity to a new room, updating both model and world."""
    # Remove from current room
    current = world.rooms.get(npc.location)
    if current and npc.npc_id in current.entities:
        current.entities.remove(npc.npc_id)

    # Add to new room
    npc.last_room = npc.location
    npc.location  = destination
    # Sync the world entity location so do_look finds Jasper in the right room
    if npc.npc_id in world.entities:
        world.entities[npc.npc_id].location = destination
    dest = world.rooms.get(destination)
    if dest and npc.npc_id not in dest.entities:
        dest.entities.append(npc.npc_id)


# ── Per-turn NPC tick ─────────────────────────────────────────────────────

def npc_tick(
    world:      object,          # World
    npc:        NPC,
    memory:     NPCMemory,
    player_moved: bool = False,  # True if the player just moved rooms
) -> List[str]:
    """
    Advance one NPC by one game turn.  Called by engine.process_input after
    every player action.

    Returns a list of narrative strings to append to the turn output.
    May be empty.

    Behaviour summary:
      - If the player is in Jasper's room:
          * Record "player_present" trust event
          * Disposition cautious -> flee to an adjacent room
          * Otherwise produce an ambient or enters_room message
      - If the player just moved into Jasper's room -> enters_room message
      - If devoted and player moved -> follow the player
      - Otherwise -> random wander with probability wander_chance
    """
    npc.session_moves += 1
    messages: List[str] = []

    player_room  = world.player.location
    npc_room     = npc.location
    same_room    = (player_room == npc_room)
    disposition  = memory.disposition(npc.npc_id)

    # ── Devoted: follow the player, with occasional independent wandering ──
    # Devoted NPCs follow the player to any room, ignoring home_rooms.
    # A small wander_chance_devoted chance applies each turn so the cat
    # occasionally drifts into a neighbouring room on its own — this fires
    # both when the player moves AND when they take in-room actions.
    WANDER_CHANCE_DEVOTED = 0.05

    if disposition == "devoted" and npc.defn.follows_when_devoted:
        # Name reveal: fires exactly once the first time the cat becomes devoted
        if not npc.revealed_name and same_room:
            npc.revealed_name = True
            messages.append(
                f"Something shifts in the way {npc.defn.name} looks at you. "
                f"You feel a genuine bond with the cat. "
                f"You decide to name it {npc.defn.proper_name}."
            )
        # Occasional independent wander even when devoted
        npc.just_fled = False  # clear flag at start of each tick
        if random.random() < WANDER_CHANCE_DEVOTED:
            # Pick any adjacent room — no home_rooms restriction when devoted
            current_room = world.rooms.get(npc.location)
            was_with_player = (npc.location == player_room)
            if current_room:
                candidates = list(current_room.exits.values())
                if candidates:
                    dest = random.choice(candidates)
                    _move_npc(npc, dest, world)
                    npc_room  = dest
                    same_room = (dest == player_room)
                    # If cat just left the player's room, say so
                    if was_with_player and not same_room:
                        messages.append(
                            f"{npc.display_name.capitalize()} slips away into the next room."
                        )
        elif not same_room:
            # Follow the player
            _move_npc(npc, player_room, world)
            npc_room  = player_room
            same_room = True
            # Produce a "follows" message — distinct from enters_room
            # (which describes finding the cat already present).
            msg = get_message(npc.npc_id, "follows", "devoted", display_name=npc.display_name)
            if msg:
                messages.append(msg)
            return messages

    # ── Player is in the same room as the NPC ────────────────────────────
    if same_room:
        # Record passive presence
        memory.record(npc.npc_id, "player_present")
        disposition = memory.disposition(npc.npc_id)   # may have changed

        if disposition == "cautious":
            # Flee probabilistically — the closer trust is to the wary
            # threshold, the less likely the cat is to bolt.  This gives
            # the player a window to build trust through repeated presence
            # without the cat being completely unreachable.
            trust = memory.trust(npc.npc_id)
            cautious_floor = 0.0
            wary_threshold = 0.35
            # flee_chance: 1.0 at trust=0, 0.3 at trust just below wary
            flee_chance = 1.0 - 0.7 * (trust / wary_threshold)
            flee_chance = max(0.3, min(1.0, flee_chance))

            if random.random() < flee_chance:
                dest = _choose_wander_destination(npc, world, away_from=player_room)
                if dest:
                    _move_npc(npc, dest, world)
                    msg = get_message(npc.npc_id, "enters_room", "cautious", display_name=npc.display_name)
                    if msg:
                        messages.append(msg)
                    if npc.last_room == player_room:
                        memory.record(npc.npc_id, "player_startled")
            else:
                # Stayed — produce a wary-style message to show hesitation
                msg = get_message(npc.npc_id, "enters_room", "wary", display_name=npc.display_name)
                if msg and player_moved:
                    messages.append(msg)
            return messages

        # Not cautious — occasional wander even when with the player.
        # Disposition controls how likely the cat is to drift away:
        # wary cats are still uncomfortable and move on more often;
        # friendly cats are content to stay.  Devoted cats never
        # reach this branch — they are handled above.
        WANDER_CHANCE_IN_ROOM = {
            "wary":    0.20,
            "neutral": 0.12,
            "friendly":0.06,
        }
        wander_roll = WANDER_CHANCE_IN_ROOM.get(disposition, 0.0)
        if wander_roll > 0.0 and random.random() < wander_roll:
            dest = _choose_wander_destination(npc, world, away_from=None)
            if dest:
                _move_npc(npc, dest, world)
                # Produce a departure message so the player sees it leave
                wander_msgs = {
                    "wary":    "The cat moves away and slips into the next room.",
                    "neutral": "The cat gets up and wanders off.",
                    "friendly":"The cat trots off to investigate something.",
                }
                messages.append(wander_msgs.get(disposition, "The cat moves away."))
            return messages

        # Produce an enters_room or ambient message if the cat stayed
        if player_moved:
            # Player just arrived — enters_room message
            msg = get_message(npc.npc_id, "enters_room", disposition, display_name=npc.display_name)
            if msg:
                messages.append(msg)
        else:
            # Player was already here — occasional ambient message
            if random.random() < 0.25:
                msg = get_message(npc.npc_id, "ambient", disposition, display_name=npc.display_name)
                if msg:
                    messages.append(msg)

        return messages

    # ── NPC is in a different room — random wander ────────────────────────
    # Skip if the NPC was forcibly moved this turn (e.g. kicked) to
    # prevent the tick from immediately wandering it back.
    if npc.just_fled:
        npc.just_fled = False
        return messages

    if random.random() < npc.defn.wander_chance:
        dest = _choose_wander_destination(npc, world)
        if dest:
            was_with_player = (npc.location == player_room)
            _move_npc(npc, dest, world)
            # If cat just left the player's room, produce a departure message
            if was_with_player:
                disp = memory.disposition(npc.npc_id)
                dn = npc.display_name.capitalize()
                wander_msgs = {
                    "wary":    f"{dn} moves away and slips into the next room.",
                    "neutral": f"{dn} gets up and wanders off.",
                    "friendly":f"{dn} trots off to investigate something.",
                    "devoted": f"{dn} slips away into the next room.",
                }
                msg = wander_msgs.get(disp, f"{dn} moves away.")
                messages.append(msg)

    return messages


# ── Interaction handlers ──────────────────────────────────────────────────
# These are called by engine action handlers when the player targets an NPC.

def handle_pet_npc(
    world: object, npc: NPC, memory: NPCMemory
) -> Tuple[str, bool]:
    """
    Player attempts to pet the NPC.  Only succeeds at friendly+ disposition.
    """
    disposition = memory.disposition(npc.npc_id)

    if world.player.location != npc.location:
        return f"{npc.display_name.capitalize()} isn't here.", False

    if disposition in ("cautious", "wary"):
        # Reaching toward a still-wary cat is alarming — small trust penalty.
        memory.record(npc.npc_id, "player_startled")
        msg = get_message(npc.npc_id, "pet_rejected", disposition, display_name=npc.display_name)
        return msg or f"{npc.display_name.capitalize()} backs away.", False

    memory.record(npc.npc_id, "player_petted")
    msg = get_message(npc.npc_id, "petted", disposition, display_name=npc.display_name)
    return msg or f"You pet {npc.display_name}.", True


def handle_feed_npc(
    world: object, npc: NPC, memory: NPCMemory, food_eid: str
) -> Tuple[str, bool]:
    """
    Player feeds the NPC a specific food item.  Consumes the item from
    inventory and fires the appropriate trust event.
    """
    if world.player.location != npc.location:
        return f"{npc.display_name.capitalize()} isn't here.", False

    if food_eid not in world.player.inventory:
        food_ent = world.entities.get(food_eid)
        name = food_ent.name if food_ent else "that"
        return f"You aren't carrying {name}.", False

    food_ent = world.entities[food_eid]
    disposition = memory.disposition(npc.npc_id)

    # Only food-tagged or catnip-tagged items are accepted.
    # Anything else is refused without consuming the item.
    is_catnip = "catnip" in food_ent.tags
    is_food   = "food"   in food_ent.tags
    if not is_catnip and not is_food:
        return (
            f"{npc.display_name.capitalize()} sniffs {food_ent.name} "
            "and turns away. It isn't interested in that."
        ), False

    # Determine event type from food tags
    if is_catnip:
        event = "player_gave_catnip"
        situation = "catnip"
    else:
        event = "player_gave_food"
        situation = "fed"

    # Consume the food
    if food_eid in world.player.inventory: world.player.inventory.remove(food_eid)
    food_ent.location = "consumed"

    memory.record(npc.npc_id, event)
    new_disposition = memory.disposition(npc.npc_id)

    msg = get_message(npc.npc_id, situation, new_disposition, display_name=npc.display_name)
    return msg or f"You feed {npc.display_name} {food_ent.name}.", True


def handle_offer_npc(
    world: object, npc: NPC, memory: NPCMemory, item_eid: str
) -> Tuple[str, bool]:
    """
    Player holds an item out toward the NPC.  Builds a small amount of trust
    at any disposition.  Does not consume the item.
    """
    if world.player.location != npc.location:
        return f"{npc.display_name.capitalize()} isn't here.", False

    if item_eid not in world.player.inventory:
        item_ent = world.entities.get(item_eid)
        name = item_ent.name if item_ent else "that"
        return f"You aren't carrying {name}.", False

    item_ent = world.entities[item_eid]
    memory.record(npc.npc_id, "player_offered_item")
    disposition = memory.disposition(npc.npc_id)

    msg = get_message(npc.npc_id, "offered_item", disposition, display_name=npc.display_name)
    return (
        msg or f"You hold out {item_ent.name}. {npc.display_name.capitalize()} notices."
    ), True


def handle_call_npc(
    world: object, npc: NPC, memory: NPCMemory
) -> Tuple[str, bool]:
    """
    Player calls or speaks to the NPC.  No trust change — purely narrative.
    """
    disposition = memory.disposition(npc.npc_id)
    msg = get_message(npc.npc_id, "called", disposition, display_name=npc.display_name)
    return msg or "Nothing happens.", True


# ── NPC registry ──────────────────────────────────────────────────────────
# All NPC definitions for the game, keyed by npc_id.
# Add new NPCs here as they are introduced.

from npc_bayesian import DEFAULT_EVENTS

JASPER_EVENTS = {
    **DEFAULT_EVENTS,
    # Jasper-specific overrides — he's more sensitive to being startled
    # than the generic model suggests
    "player_startled": (0.0, 0.5),   # Jasper-specific — re-entry after flee
}

JASPER = NPCDefinition(
    npc_id       = "jasper",
    name         = "a grey cat",
    proper_name  = "Jasper",
    home_rooms   = {"hall_1", "hall_2", "hall_3", "library",
                    "upstairs_landing", "bedroom_east", "bedroom_west"},
    start_room   = "hall_2",
    wander_chance= 0.4,
    accepts_at   = {
        "neutral":  {"feed", "offer"},
        "friendly": {"feed", "offer", "pet"},
        "devoted":  {"feed", "offer", "pet"},
    },
    event_overrides      = JASPER_EVENTS,
    follows_when_devoted = True,
    fights_when_devoted  = True,
)

NPC_REGISTRY: Dict[str, NPCDefinition] = {
    "jasper": JASPER,
}