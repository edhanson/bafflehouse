# engine.py
#
# Game logic layer — action handlers, world-state mutation, and the main
# input-processing loop.
#
# Architecture notes:
#   - Every player command ultimately resolves to a call to exec_action(),
#     which dispatches to a per-verb handler via ACTION_HANDLERS.
#   - Each handler receives the full World and a grounded IR dict, and returns
#     a (message: str, success: bool) tuple.
#   - Handlers that mutate world state (move entities, set props, add exits)
#     do so directly on the World dataclasses — there is no separate mutation
#     layer.
#   - The "dark cellar" mechanic is enforced through visible_entities_for_room(),
#     which overrides World.visible_entities() when the player is in the cellar
#     without a lit lamp.

import random
import re
from typing import Callable, Dict, List, Optional, Tuple

from ir import clarify_ir
from model import World
from npc import NPC, NPC_REGISTRY, npc_tick, handle_pet_npc, handle_feed_npc, handle_offer_npc, handle_call_npc
from troll import (
    TrollMemory, troll_encounter, handle_troll_answer,
    TROLL_EXAMINE, TROLL_BLOCKS,
)
from npc_qlearning import CombatMemory
from combat import (
    CombatSession, start_combat, process_player_combat_action,
    combat_status, WEAPON_STATS,
)
from npc_bayesian import NPCMemory
from parser import (
    DIRECTIONS,
    ParserSystem,
    expand_coordinated_objects,
    ground_intent,
    normalize,
    parse_to_candidates,
    split_compound,
)


# ============================================================
# NPC system — module-level singletons
# ============================================================

# Persistent memory store — loaded from npc_memory.json on first access.
NPC_MEMORY = NPCMemory("./npc_memory.json")
TROLL_MEMORY    = TrollMemory()
COMBAT_MEMORY   = CombatMemory()
_COMBAT_SESSION: Optional[CombatSession] = None

# Register Jasper's custom event table before any reputation is loaded.
from npc import JASPER_EVENTS
NPC_MEMORY.register_events("jasper", JASPER_EVENTS)

# Runtime NPC instances — one per creature currently in the world.
# Keyed by npc_id.  Reset each time build_demo_world() is called
# (in main.py startup) but memory persists across sessions.
_NPC_INSTANCES: dict = {}


def get_npc_instances(world: World) -> dict:
    """
    Return (creating if absent) the runtime NPC instances for the world.
    Each NPC instance is initialised to its start_room from the definition.
    """
    global _NPC_INSTANCES
    if not _NPC_INSTANCES:
        for npc_id, defn in NPC_REGISTRY.items():
            _NPC_INSTANCES[npc_id] = NPC(
                defn     = defn,
                location = defn.start_room,
            )
            # Sync entity location in world model and add to room entity list
            if npc_id in world.entities:
                world.entities[npc_id].location = defn.start_room
            start_room = world.rooms.get(defn.start_room)
            if start_room and npc_id not in start_room.entities:
                start_room.entities.append(npc_id)
    return _NPC_INSTANCES


# ============================================================
# Visibility helpers
# ============================================================

def phrase_in_room_text(world: World, phrase: str) -> bool:
    """
    Return True if every normalised token of *phrase* appears in the
    combined text that the player can currently see: the room description
    (including desc_lit when lit) plus the description of every scenery
    entity in the room.

    Used to distinguish "you don't see that here" (no match) from
    "nothing special about it" (phrase appears in descriptive text but
    has no dedicated entity).  Avoids the need to exhaustively enumerate
    every colour-noun in the game world.
    """
    import re
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower())

    tokens = [t for t in _norm(phrase).split() if t]
    if not tokens:
        return False

    room = world.room()

    # Collect all visible descriptive text
    texts: list = []
    if hasattr(room, "desc_lit") and player_has_lit_lamp(world):
        texts.append(room.desc_lit)
    else:
        texts.append(room.desc)

    # Include scenery entity descriptions — players read these and
    # may refer to nouns they contain.
    for eid in room.entities:
        ent = world.entities.get(eid)
        if ent and "scenery" in ent.tags:
            texts.append(ent.props.get("desc", ""))
            texts.append(ent.name)

    combined = _norm(" ".join(texts))
    return all(t in combined for t in tokens)



def player_has_lit_lamp(world: World) -> bool:
    """
    Return True if the player is carrying the oil lamp and it is currently lit.
    This is used both for the dark-cellar mechanic and as a prerequisite check
    in handle_pull (lever) and handle_examine (dark-end items).
    """
    lamp = world.entities.get("oil_lamp")
    if lamp is None:
        return False
    return (lamp.location == "player") and lamp.props.get("lit", False)


def visible_entities_for_room(world: World) -> List[str]:
    """
    Return the list of entity ids currently visible to the player.

    This wraps World.visible_entities() with an extra layer:
      - In the cellar, entities whose props["requires_light"] is True are only
        included when the player carries a lit lamp.
      - Entities in location "hidden" are never included (they are not in any
        room's entity list, so World.visible_entities() never sees them).

    All other rooms behave exactly as before.
    """
    base = world.visible_entities()

    if world.player.location != "cellar":
        return base

    has_light = player_has_lit_lamp(world)

    # Filter out light-requiring items if the lamp is not lit and carried.
    return [
        eid for eid in base
        if not world.entity(eid).props.get("requires_light", False) or has_light
    ]


# ============================================================
# Output helpers
# ============================================================

def narrate(options: List[str]) -> str:
    """Pick a random response string from a list of alternatives."""
    return random.choice(options)


def format_clarification(world: World, clar: dict) -> str:
    """Format a clarification prompt with numbered entity names."""
    lines = [clar["question"], ""]
    for i, eid in enumerate(clar["options"], start=1):
        lines.append(f"{i}) {world.entity(eid).name}")
    lines.append("")
    lines.append("Please reply with a number or a short name.")
    return "\n".join(lines)


def do_look(world: World, show_npcs: bool = True) -> str:
    """
    Describe the current room.

    Uses visible_entities_for_room() so that dark-cellar items are correctly
    hidden when the lamp is unlit.

    For rooms that have a desc_lit attribute (currently only the cellar),
    the lit description is used when the player has a lit lamp, so the
    room text and visible entity list are always consistent with each other.

    show_npcs: when False, suppresses the NPC presence line. Set to False
    on movement so the NPC tick's enters_room message is the sole
    description of the NPC — avoids doubling up on the same turn.
    """
    room = world.room()

    # Select dynamic description if available.
    if hasattr(room, "desc_lit") and player_has_lit_lamp(world):
        desc = room.desc_lit
    else:
        desc = room.desc

    lines = [room.title, desc]

    visible = visible_entities_for_room(world)

    # Show non-scenery, non-NPC, non-inventory items in the room.
    # NPC presence is conveyed by the NPC tick's atmospheric messages.
    visible_non_scenery = [
        eid for eid in visible
        if "scenery" not in world.entity(eid).tags
        and "npc"     not in world.entity(eid).tags
        and eid not in world.player.inventory
    ]

    # Only list items when there is something portable present.
    # Scenery is described by the room text; an empty list here would
    # print "You see nothing of interest." even in a room full of
    # interesting-but-fixed things, which misleads the player.
    if visible_non_scenery:
        things = ", ".join(world.entity(eid).name for eid in visible_non_scenery)
        lines.append(f"You see {things}.")

    # Describe any NPCs present in the room — only when the player
    # explicitly looks.  On movement the NPC tick fires enters_room
    # messages; showing both would double-describe the NPC.
    npc_eids = list(dict.fromkeys(
        eid for eid in visible if "npc" in world.entity(eid).tags
    ))
    if npc_eids and show_npcs:
        from npc_bayesian import trust_to_disposition
        npc_look_lines = {
            "cautious": [
                "{name} is here, watching you from a distance.",
                "{name} sits very still at the far end of the room.",
                "{name} is pressed against the wall, watching.",
            ],
            "wary": [
                "{name} is here, keeping its distance.",
                "{name} watches you from across the room.",
            ],
            "neutral": [
                "{name} is here.",
                "{name} sits nearby, ignoring you.",
            ],
            "friendly": [
                "{name} is here, tail raised.",
                "{name} is nearby, watching you with bright eyes.",
            ],
            "devoted": [
                "{name} is here at your side.",
                "{name} stays close, ears forward.",
            ],
        }
        import random as _random
        for eid in npc_eids:
            ent  = world.entity(eid)
            disp = NPC_MEMORY.disposition(eid)
            pool = npc_look_lines.get(disp, npc_look_lines["neutral"])
            name = ent.name.capitalize()
            lines.append(_random.choice(pool).format(name=name))

    exits = ", ".join(sorted(room.exits.keys())) if room.exits else "none"
    lines.append(f"Exits: {exits}.")
    return "\n".join(lines)


def do_inventory(world: World) -> str:
    """List carried items, noting which ones are worn or wielded."""
    if not world.player.inventory:
        return "You are empty-handed."

    parts = []
    for eid in world.player.inventory:
        ent = world.entity(eid)
        label = ent.name
        if eid == world.player.wielded_weapon:
            label += " (wielded)"
        if ent.props.get("worn", False):
            label += " (worn)"
        if ent.props.get("lit", False):
            label += " (lit)"
        parts.append(label)

    return f"You are carrying: {', '.join(parts)}."


# ============================================================
# Execution helpers
# ============================================================

def move_entity(world: World, eid: str, dest: str) -> None:
    """
    Move an entity from its current location to dest.

    dest can be:
      - a room id       -> entity goes into room.entities
      - "player"        -> entity goes into player.inventory
      - an entity id    -> entity goes into that entity's .contains list
      - "hidden"        -> entity is removed from everywhere (made invisible)
    """
    ent = world.entity(eid)

    # Remove from current location.
    if ent.location == "player":
        if eid in world.player.inventory:
            world.player.inventory.remove(eid)
    elif ent.location in world.rooms:
        room = world.rooms[ent.location]
        if eid in room.entities:
            room.entities.remove(eid)
    elif ent.location in world.entities:
        container = world.entity(ent.location)
        if eid in container.contains:
            container.contains.remove(eid)
    # If location is "hidden", there is nothing to remove from.

    # Place in new location.
    ent.location = dest
    if dest == "player":
        world.player.inventory.append(eid)
    elif dest in world.rooms:
        world.rooms[dest].entities.append(eid)
    elif dest in world.entities:
        world.entity(dest).contains.append(eid)
    # dest == "hidden": entity is now invisible; no list to add to.


def require_visible(world: World, eid: str) -> Optional[str]:
    """
    Return an error message if eid is not currently visible, else None.

    Uses visible_entities_for_room() so the dark-cellar filter is respected.
    When in the cellar without a lit lamp, entities that require light
    return a darkness-specific message instead of the generic "not here".
    """
    if eid not in visible_entities_for_room(world):
        # Give a more atmospheric message for light-gated entities in the cellar.
        if (
            world.player.location == "cellar"
            and eid in world.entities
            and world.entity(eid).props.get("requires_light", False)
            and not player_has_lit_lamp(world)
        ):
            return "It's too dark to make anything out at that end of the room."
        return "You don't see that here."
    return None


def other_side_of_door(world: World, door_eid: str) -> Optional[str]:
    """
    Given a door entity, return the room on the other side of the door
    from the player's current location.  Returns None if something is wrong.
    """
    if door_eid not in world.entities:
        return None

    door = world.entity(door_eid)
    room_a = door.props.get("room_a")
    room_b = door.props.get("room_b")
    current_room = world.player.location

    if not isinstance(room_a, str) or not isinstance(room_b, str):
        return None
    if room_a not in world.rooms or room_b not in world.rooms:
        return None

    if current_room == room_a:
        return room_b
    if current_room == room_b:
        return room_a

    return None


def traverse_door(world: World, door_eid: str) -> Tuple[str, bool]:
    """
    Attempt to move the player through a door entity.

    Checks: entity exists, visible, is a door, is open, connects current room.
    """
    if door_eid not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, door_eid)
    if err:
        return err, False

    door = world.entity(door_eid)

    if "door" not in door.tags:
        return "That's not something you can enter.", False

    if not door.props.get("open", False):
        return "It's closed.", False

    destination = other_side_of_door(world, door_eid)
    if destination is None:
        return "You can't go that way.", False

    world.player.location = destination
    world.note_ref([door_eid])
    return do_look(world), True


# ============================================================
# Action handlers — original verbs
# ============================================================

def handle_go(world: World, ir: dict) -> Tuple[str, bool]:
    """Move the player in a compass direction or through a named door."""
    iobj = ir.get("iobj")

    if iobj in DIRECTIONS.values():
        direction = iobj
        room = world.room()

        if direction not in room.exits:
            return "You can't go that way.", False

        world.player.location = room.exits[direction]
        # Suppress NPC presence line on movement — the tick's
        # enters_room message handles it for this turn.
        return do_look(world, show_npcs=False), True

    if iobj in world.entities:
        return traverse_door(world, iobj)

    return "You can't quite manage that.", False


def handle_enter(world: World, ir: dict) -> Tuple[str, bool]:
    """Move the player through a named door or portal."""
    obj = ir.get("obj")

    if not obj:
        return "Enter what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    return traverse_door(world, obj)


def handle_examine(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Describe an entity in detail.

    Also enforces the dark-cellar mechanic: light-requiring entities return
    a "too dark" message unless the player has a lit lamp.
    """
    obj = ir.get("obj")

    if not obj:
        return "Examine what?", False
    if obj not in world.entities:
        # obj is the raw ungrounded phrase — check if it appears in the
        # current room's visible text before denying existence outright.
        if phrase_in_room_text(world, str(obj)):
            return "You notice nothing special about it.", True
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)
    world.note_ref([obj])

    # Special reveal: examining the garden hedges uncovers the catnip.
    # The catnip entity starts with props["visible"] = False; examining
    # the hedges sets it True so the entity becomes findable and takeable.
    # For NPC entities: append a disposition-sensitive observation that
    # hints at how to interact without spelling it out explicitly.
    if obj == "troll":
        state = TROLL_MEMORY.state()
        if state.bridge_open:
            key = "bridge_open"
        elif state.correct_count > 0:
            key = "in_progress"
        else:
            key = "not_started"
        return TROLL_EXAMINE.get(key, "You see a troll."), False

    if "npc" in ent.tags:
        disposition = NPC_MEMORY.disposition(obj)
        npc_examine_suffix = {
            "cautious": (
                " It is watching the exits as much as it is watching you."
            ),
            "wary": (
                " It holds its ground but keeps you at a measured distance. "
                "It seems to be waiting to see what you do next."
            ),
            "neutral": (
                " It seems prepared to tolerate your presence, at least for now. "
                "It shows mild interest in what you're carrying."
            ),
            "friendly": (
                " It is watching you with open curiosity, tail moving slowly."
            ),
            "devoted": (
                " It stays close, alert to everything around you both."
            ),
        }
        suffix = npc_examine_suffix.get(disposition, "")
        base = ent.props.get("desc", "You see nothing special.")
        return base + suffix, True

    if obj == "garden_hedges" and "catnip" in world.entities:
        catnip = world.entities["catnip"]
        if not catnip.props.get("visible", False):
            catnip.props["visible"] = True
            # Move it into the room so it appears in entity listings.
            catnip.location = world.player.location
            room = world.rooms.get(world.player.location)
            if room and "catnip" not in room.entities:
                room.entities.append("catnip")

    # Select the appropriate description based on entity state.
    #
    # Priority order:
    #   1. Liquid container that has been emptied (empty=True)
    #   2. Solid container that is open and has no contents
    #   3. Scenery with a state-dependent desc_open (e.g. bricked_wall)
    #   4. Default desc
    _is_empty_liquid = ent.props.get("empty", False) and "desc_empty" in ent.props
    _is_empty_solid  = (
        "container" in ent.tags
        and ent.props.get("open", False)
        and not ent.contains
        and not (ent.props.get("liquid") and not ent.props.get("empty", False))
        and "desc_empty" in ent.props
    )
    if _is_empty_liquid or _is_empty_solid:
        desc = ent.props["desc_empty"]
    elif ("desc_open" in ent.props
          and world.rooms.get(world.player.location, None) is not None
          and world.rooms[world.player.location].exits.get("north") == "cellar"):
        desc = ent.props["desc_open"]
    else:
        desc = ent.props.get("desc", "You see nothing special.")
    lines = [desc]

    if "openable" in ent.tags:
        lines.append("It is open." if ent.props.get("open", False) else "It is closed.")

    if "container" in ent.tags and ent.props.get("open", False):
        # Solid contents: child entities tracked in ent.contains.
        if ent.contains:
            contents = ", ".join(world.entity(cid).name for cid in ent.contains)
            lines.append(f"It contains {contents}.")

        # Liquid contents are stored in props["liquid"], not as child entities.
        # props["empty"] is set True once the liquid has been used or poured out.
        liquid = ent.props.get("liquid")
        if liquid and not ent.props.get("empty", False):
            lines.append(f"It contains {liquid}.")
        elif not ent.contains and (not liquid or ent.props.get("empty", False)):
            # Only say "empty" when there are truly no solid or liquid contents.
            lines.append("It is empty.")

    # Report lit/fuelled state for lamps.
    if "lightable" in ent.tags:
        if ent.props.get("lit", False):
            lines.append("It is burning steadily.")
        elif ent.props.get("fuelled", False):
            lines.append("It is filled with oil but unlit.")
        else:
            lines.append("It is empty of fuel.")

    # Report worn state for wearable items.
    if "wearable" in ent.tags:
        if ent.props.get("worn", False):
            lines.append("You are wearing it.")

    # Report remaining matches for fire sources.
    if "fire_source" in ent.tags:
        n = ent.props.get("matches_remaining", 0)
        if n == 0:
            lines.append("The box is empty. No matches remain.")
        elif n == 1:
            lines.append("One match remains.")
        else:
            lines.append(f"{n} matches remain.")

    return "\n".join(lines), True


def handle_take(world: World, ir: dict) -> Tuple[str, bool]:
    """Pick up a portable entity and add it to inventory."""
    obj = ir.get("obj")

    if not obj:
        return "Take what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "That's not something you can pick up.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "mounted" in ent.tags:
        return (
            f"You'll need to take {ent.name} down from the wall first."
        ), False
    if "portable" not in ent.tags or "scenery" in ent.tags:
        return "You can't take that.", False

    if obj in world.player.inventory or ent.location == "player":
        return "You already have it.", False

    move_entity(world, obj, "player")
    world.note_ref([obj])
    # All responses name the item explicitly to avoid pronoun-number
    # ambiguity ("You take them." vs "You take it." for plural-sounding
    # names like "a box of matches").  "Taken." is intentionally omitted
    # from the pool since it uses no name and cannot be tested reliably.
    return narrate([
        f"You take {ent.name}.",
        f"You pick up {ent.name}.",
        f"You grab {ent.name}.",
    ]), True


def handle_drop(world: World, ir: dict) -> Tuple[str, bool]:
    """Drop a carried entity into the current room."""
    obj = ir.get("obj")

    if not obj:
        return "Drop what?", False
    if obj not in world.entities or obj not in world.player.inventory:
        return "You aren't holding that.", False

    # Prevent dropping a lit lamp (safety / narrative gate).
    ent = world.entity(obj)
    if ent.props.get("lit", False):
        return "You'd rather not put down a lit lamp.", False

    move_entity(world, obj, world.player.location)
    world.note_ref([obj])
    return narrate(["Dropped.", "Done."]), True


def handle_open(world: World, ir: dict) -> Tuple[str, bool]:
    """Open an openable entity (door, container, etc.)."""
    obj = ir.get("obj")
    prep = ir.get("prep")

    if not obj:
        return "Open what?", False

    if prep is not None:
        if prep == "with":
            return "You can't open things with that. Perhaps you mean UNLOCK something WITH it.", False
        return "I don't understand that phrasing.", False

    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)
    if "openable" not in ent.tags:
        return "That's not something you can open.", False
    if ent.props.get("open", False):
        return "It's already open.", False
    if ent.props.get("locked", False):
        return "It seems to be locked.", False

    ent.props["open"] = True
    world.note_ref([obj])

    # Both lockable doors use the same pattern: opening adds the compass
    # exits that allow the player to walk through; closing removes them.
    # This means unlock + open is always required to gain passage, which
    # is consistent between the oak door and the study door.
    if obj == "oak_door":
        world.rooms["foyer"].exits["north"] = "hall_1"
        world.rooms["hall_1"].exits["south"] = "foyer"
        return narrate([
            "The oak door swings open.",
            "The oak door opens with a low groan.",
            "The oak door swings back on its hinges.",
        ]), True

    if obj == "study_door":
        world.rooms["trophy_room"].exits["south"] = "secret_study"
        world.rooms["secret_study"].exits["north"] = "trophy_room"
        return narrate([
            "The heavy door swings inward.",
            "The door opens with a reluctant creak.",
            "The door gives way, swinging open.",
        ]), True

    return narrate(["Opened.", "The thing opens.", "With a modest show of cooperation, it opens."]), True


def handle_close(world: World, ir: dict) -> Tuple[str, bool]:
    """Close an openable entity."""
    obj = ir.get("obj")
    prep = ir.get("prep")

    if not obj:
        return "Close what?", False
    if prep is not None:
        return "I don't understand that phrasing.", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)
    if "openable" not in ent.tags:
        return "That's not something you can close.", False
    if not ent.props.get("open", False):
        return "It's already closed.", False

    ent.props["open"] = False
    world.note_ref([obj])

    # Mirror of handle_open: closing a door removes the compass exits.
    if obj == "oak_door":
        world.rooms["foyer"].exits.pop("north", None)
        world.rooms["hall_1"].exits.pop("south", None)
        return "The oak door swings shut with a heavy thud.", True

    if obj == "study_door":
        world.rooms["trophy_room"].exits.pop("south", None)
        world.rooms["secret_study"].exits.pop("north", None)
        return "The heavy door grinds shut behind you.", True

    return narrate(["Closed.", "The thing closes."]), True


def handle_put(world: World, ir: dict) -> Tuple[str, bool]:
    """Put a carried entity into a container or onto a surface."""
    obj = ir.get("obj")
    prep = ir.get("prep")
    iobj = ir.get("iobj")

    if not obj:
        return "Put what?", False
    if obj not in world.entities or obj not in world.player.inventory:
        return "You aren't holding that.", False
    if not iobj:
        return "Put it where?", False
    if iobj not in world.entities:
        return "Put it where?", False

    target = world.entity(iobj)

    if prep in {"in", "into", "inside"}:
        if "container" not in target.tags:
            return "You can't put things in that.", False
        if "openable" in target.tags and not target.props.get("open", False):
            return "It's closed.", False
        move_entity(world, obj, iobj)
        world.note_ref([obj, iobj])
        return narrate(["Done.", "Okay.", "You put it in."]), True

    if prep in {"on", "onto"}:
        if "support" not in target.tags:
            return "You can't put things on that.", False
        move_entity(world, obj, iobj)
        world.note_ref([obj, iobj])
        return narrate(["Done.", "Placed.", "You put it on."]), True

    return "You can't put it that way.", False


def handle_unlock(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Unlock a lockable entity with a key from inventory.

    Special behaviour:
      - Unlocking the study_door also adds the "north" exit to trophy_room,
        making the secret study accessible by compass direction.
    """
    obj = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Unlock what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    if not iobj:
        return "Unlock it with what?", False
    if iobj not in world.entities:
        return f"You aren't holding any {iobj}.", False
    if iobj not in world.player.inventory:
        return "You aren't holding that.", False

    thing = world.entity(obj)
    key = world.entity(iobj)

    if "lockable" not in thing.tags:
        return "That doesn't have a lock.", False
    if not thing.props.get("locked", False):
        return "It's not locked.", False

    needed = thing.props.get("key_id")
    if needed is not None and key.props.get("key_id") != needed:
        return "That key doesn't seem to fit.", False

    thing.props["locked"] = False
    world.note_ref([obj, iobj])

    # Both doors (oak_door, study_door) follow the same two-step pattern:
    # unlock removes the lock, open adds the compass exit.  Unlocking alone
    # does not grant passage — the player must also open the door.
    return narrate(["Unlocked.", "The lock clicks open."]), True


# ============================================================
# Action handlers — new verbs
# ============================================================

def handle_lock(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Lock a lockable entity with a key from inventory.
    Mirrors handle_unlock — the door must already be closed.
    """
    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Lock what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "That doesn't have a lock.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    if not iobj:
        return "Lock it with what?", False
    if iobj not in world.entities:
        return f"You aren't holding any {iobj}.", False
    if iobj not in world.player.inventory:
        return "You aren't holding that.", False

    thing = world.entity(obj)
    key   = world.entity(iobj)

    if "lockable" not in thing.tags:
        return "That doesn't have a lock.", False
    if thing.props.get("locked", False):
        return "It's already locked.", False
    if thing.props.get("open", False):
        return "You'll need to close it first.", False

    needed = thing.props.get("key_id")
    if needed is not None and key.props.get("key_id") != needed:
        return "That key doesn't seem to fit.", False

    thing.props["locked"] = True
    world.note_ref([obj, iobj])
    return narrate(["Locked.", "The lock clicks shut."]), True


def handle_drink(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Attempt to drink something.

    Currently no drinkable items exist in the world, so this handler
    exists to give sensible responses rather than routing to fill/pour
    or producing nonsense.  Checks for the "drinkable" tag for future
    content; everything else gets a contextual refusal.
    """
    obj = ir.get("obj")

    if not obj:
        return "Drink what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "You can't drink that.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "drinkable" in ent.tags:
        # Future: consume the item and apply its effect.
        return f"You drink {ent.name}.", True

    if "liquid" in ent.tags or ent.props.get("liquid"):
        return f"Drinking {ent.name} would be a terrible idea.", False

    if "living" in ent.tags or "npc" in ent.tags:
        return "That's not something you can drink.", False

    if "portable" not in ent.tags and "container" not in ent.tags:
        return "You can't drink that.", False

    return "That's not something you can drink.", False


def handle_eat(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Attempt to eat something.

    Checks for the "food" tag for items that can actually be consumed.
    NPCs, scenery, and non-food items get contextual refusals.
    """
    obj = ir.get("obj")

    if not obj:
        return "Eat what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "You can't eat that.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "living" in ent.tags or "npc" in ent.tags:
        return f"You can't eat {ent.name}.", False

    if "food" in ent.tags:
        # Future: consume and apply effect.  For now, decline gracefully.
        return (
            f"You consider eating {ent.name}. "
            "Better to save it for a more pressing need."
        ), False

    if "scenery" in ent.tags:
        return "You can't eat that.", False

    return "That doesn't look edible.", False


def handle_read(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Read a 'readable'-tagged entity.

    Returns the entity's "readable_text" prop.  The entity must be visible
    (in the room or in inventory) and have the "readable" tag.
    """
    obj = ir.get("obj")

    if not obj:
        return "Read what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "There's nothing there to read.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "readable" not in ent.tags:
        return "There's nothing on it worth reading.", False

    text = ent.props.get("readable_text", "")
    if not text:
        return "The writing is too faded to make out.", False

    world.note_ref([obj])
    return text, True


def _find_fire_source(world: World) -> Optional[str]:
    """
    Return the eid of a usable fire source in inventory, or None.

    A fire source is any entity tagged "fire_source" with
    props["matches_remaining"] > 0.  Written generically so future
    fire sources work automatically if given the same tag.
    """
    for eid in world.player.inventory:
        ent = world.entity(eid)
        if "fire_source" in ent.tags and ent.props.get("matches_remaining", 0) > 0:
            return eid
    return None


def handle_light(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Light a lightable entity.

    Prerequisites:
      - Entity must be in inventory.
      - Entity must have fuelled: True.
      - Player must carry a fire source (matchbox with matches remaining).

    Using the matchbox decrements matches_remaining by one.  When the
    count reaches zero the box is spent and lighting fails until a new
    fire source is found.
    """
    obj = ir.get("obj")

    if not obj:
        return "Light what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You'd need to be holding it to light it.", False

    ent = world.entity(obj)

    # Special case: lighting/striking the matchbox itself.
    # "light match", "strike match", "light matches" — the player is
    # striking a match as a standalone action rather than to light
    # something else.  Consume one match and describe the small flame.
    if "fire_source" in ent.tags:
        n = ent.props.get("matches_remaining", 0)
        if n == 0:
            return (
                "You open the matchbox — it's empty. "
                "There are no matches left to strike."
            ), False
        ent.props["matches_remaining"] -= 1
        remaining = ent.props["matches_remaining"]
        world.note_ref([obj])
        msg = (
            "You strike a match. A small flame gutters to life, casting "
            "a modest circle of warm light. It won't last long on its own."
        )
        if remaining == 0:
            msg += " That was your last match."
        elif remaining <= 3:
            plural = "es" if remaining != 1 else ""
            msg += f" Only {remaining} match{plural} left."
        return msg, True

    if "lightable" not in ent.tags:
        return "That's not something you can light.", False
    if ent.props.get("lit", False):
        return "It's already lit.", False
    if not ent.props.get("fuelled", False):
        return "It has no fuel. You'll need to fill it with oil first.", False

    # Require a fire source before allowing the light action.
    fire_eid = _find_fire_source(world)
    if fire_eid is None:
        has_spent = any(
            "fire_source" in world.entity(e).tags
            for e in world.player.inventory
        )
        if has_spent:
            return (
                "You open the matchbox — it's empty. "
                "There are no matches left."
            ), False
        return (
            "You need something to light it with. "
            "A fire source of some kind would help."
        ), False

    # Consume one match.
    fire_ent = world.entity(fire_eid)
    fire_ent.props["matches_remaining"] -= 1
    remaining = fire_ent.props["matches_remaining"]

    ent.props["lit"] = True
    world.note_ref([obj, fire_eid])

    msg = (
        "You strike a match. The lamp catches with a warm, steady flame, "
        "pushing the darkness back."
    )
    if remaining == 0:
        msg += " That was your last match."
    elif remaining <= 3:
        plural = "es" if remaining != 1 else ""
        msg += f" Only {remaining} match{plural} left."
    return msg, True


def handle_extinguish(world: World, ir: dict) -> Tuple[str, bool]:
    """Extinguish a lit entity."""
    obj = ir.get("obj")

    if not obj:
        return "Extinguish what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "lightable" not in ent.tags:
        return "That's not something you can extinguish.", False
    if not ent.props.get("lit", False):
        return "It isn't lit.", False

    ent.props["lit"] = False
    world.note_ref([obj])
    return narrate(["The flame dies.", "Darkness rushes back in.", "Extinguished."]), True


def handle_push(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Push a 'pushable'-tagged entity.

    Currently no puzzles require pushing, but the handler is wired in so
    the verb works generically and future content can tag items "pushable".
    """
    obj = ir.get("obj")

    if not obj:
        return "Push what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "That's not something you can push.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "pushable" not in ent.tags:
        return "Pushing that accomplishes nothing.", False

    world.note_ref([obj])
    return "You push it, but nothing seems to happen.", True


def handle_pull(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Pull a 'pullable'-tagged entity.

    Puzzle hooks:
      - Pulling "stone_stag" (or its antler aliases) drops the display_key
        into the trophy room (Puzzle 2 payoff).
      - Pulling "cellar_lever" (requires lit lamp) opens the hall west passage
        (Puzzle 1 payoff).
    """
    obj = ir.get("obj")

    if not obj:
        return "Pull what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            return "That's not something you can pull.", False
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "pullable" not in ent.tags:
        return "Pulling that accomplishes nothing.", False

    # ---- Puzzle 2: stone stag antler ----
    if obj == "stone_stag":
        if ent.props.get("pulled", False):
            return "You already pulled it. Nothing more falls.", False

        ent.props["pulled"] = True

        # Move the display_key from "hidden" into the trophy room.
        move_entity(world, "display_key", "trophy_room")
        world.note_ref([obj, "display_key"])

        return (
            "You grab the heavy antler and pull hard. With a grinding crack, it swings "
            "downward on a hidden pivot — and a small tarnished key drops from inside "
            "the stag's hollow neck, clattering to the floor at your feet."
        ), True

    # ---- Puzzle 1: cellar lever ----
    if obj == "cellar_lever":
        # Light is required to find and use the lever.
        if not player_has_lit_lamp(world):
            return (
                "You can sense something in the darkness, but you can't make it out. "
                "You'd need a light source to work with it safely."
            ), False

        if ent.props.get("pulled", False):
            return "You've already pulled it. The passage stands open.", False

        ent.props["pulled"] = True

        # Open the passage: add "west" exit to the hall, and a reciprocal
        # "east" exit would lead back to the cellar top, but we instead route
        # it back to the cellar for simplicity (the foyer route still works).
        world.rooms["hall_3"].exits["north"]        = "cellar_passage"
        world.rooms["cellar_passage"].exits["south"] = "hall_3"
        world.rooms["cellar"].exits["north"]        = "cellar_passage"
        world.note_ref([obj])

        return (
            "You haul on the iron lever with both hands. Somewhere deep in the wall, "
            "something heavy shifts. A low rumble travels through the stone, climbing "
            "upward through the floor and into your boots. From somewhere above — "
            "distant, muffled by the intervening rock — comes the groan of something "
            "large moving that has not moved in a very long time."
        ), True

    # Generic pullable (future-proofing)
    world.note_ref([obj])
    return "You pull it. Something shifts, but nothing dramatic happens.", True


def _apply_liquid_to_vessel(
    world: World,
    vessel_eid: str,
    source_eid: str,
) -> Tuple[str, bool]:
    """
    Core liquid-transfer logic shared by handle_fill and handle_pour.

    Transfers liquid from source_eid into vessel_eid.  Both entities must
    already be confirmed as valid and in-scope before this is called —
    all precondition checks (visibility, inventory, empty-source) live in
    the calling handler.

    Handles:
      - Puzzle 1: oil into oil_lamp  (fuelling)
      - Puzzle 3: water into stone_basin  (ring-activated reveal)
      - Generic: any liquid into any open container
    """
    vessel_ent = world.entity(vessel_eid)
    source_ent = world.entity(source_eid)
    liquid = source_ent.props.get("liquid")

    # ---- Puzzle 1: oil into the lamp ----
    if vessel_eid == "oil_lamp" and liquid == "oil":
        if vessel_ent.props.get("fuelled", False):
            return "The lamp is already full of oil.", False

        vessel_ent.props["fuelled"] = True
        source_ent.props["empty"] = True
        world.note_ref([vessel_eid, source_eid])
        return (
            "You carefully tip the flask. Oil flows into the lamp's reservoir "
            "with a quiet glug. The flask is now empty."
        ), True

    # Reject wrong liquid type for a lightable vessel.
    if "lightable" in vessel_ent.tags:
        return "That doesn't take that kind of liquid.", False

    # ---- Puzzle 3: water into stone basin ----
    if vessel_eid == "stone_basin" and liquid == "water":
        if vessel_ent.props.get("activated", False):
            return "The basin already holds the water. The serpents are still.", False

        # Check whether the player is wearing the silver ring.
        ring = world.entities.get("silver_ring")
        ring_worn = (
            ring is not None
            and ring.location == "player"
            and ring.props.get("worn", False)
        )

        if not ring_worn:
            # Water goes in but nothing magical happens yet.
            vessel_ent.props["liquid"] = "water"
            source_ent.props["empty"] = True
            world.note_ref([vessel_eid, source_eid])
            return (
                "You pour the water into the basin. It sits there, cold and still. "
                "The serpent carvings seem to watch, unimpressed. "
                "Perhaps something is missing."
            ), True

        # Ring worn — trigger the puzzle payoff.
        vessel_ent.props["activated"] = True
        vessel_ent.props["liquid"] = "water"
        source_ent.props["empty"] = True
        move_entity(world, "ancient_scroll", "stone_basin")
        world.note_ref([vessel_eid, source_eid, "ancient_scroll"])
        return (
            "The moment the water touches the basin, the ring on your finger grows warm. "
            "The carved serpents seem to writhe — a trick of the light, surely — and "
            "the water begins to glow with a faint green luminescence.\n\n"
            "Something rises from beneath the water: a tightly rolled scroll, bone dry "
            "despite the liquid around it. It comes to rest at the basin's rim as if "
            "placed there by an invisible hand."
        ), True

    # Generic: pour any liquid into any open container.
    vessel_ent.props["liquid"] = liquid
    source_ent.props["empty"] = True
    world.note_ref([vessel_eid, source_eid])
    return narrate(["Done.", f"You pour the {liquid} in."]), True


def handle_fill(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Fill a vessel from a liquid source.

    Canonical syntax: FILL <vessel> WITH <source>
      e.g. "fill lamp with oil", "fill lamp with flask"

    Also accepts the reversed pour-style framing by detecting when obj is a
    liquid source and iobj is the vessel, and swapping them before delegating
    to _apply_liquid_to_vessel.  This means "fill flask into lamp" is handled
    gracefully even though it is grammatically fill-shaped.
    """
    obj = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Fill what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    visible = visible_entities_for_room(world)

    obj_ent = world.entity(obj)
    iobj_ent = world.entity(iobj) if iobj and iobj in world.entities else None

    # Detect reversed framing: "fill [source] into [vessel]"
    # e.g. the player wrote "fill flask into lamp" meaning pour flask into lamp.
    # Swap the slots so vessel=iobj, source=obj and proceed normally.
    if (
        iobj_ent is not None
        and "liquid_source" in obj_ent.tags
        and "liquid_source" not in iobj_ent.tags
    ):
        obj, iobj = iobj, obj
        obj_ent, iobj_ent = iobj_ent, obj_ent

    # Now obj = vessel, iobj = source.
    vessel_eid = obj
    source_eid = iobj

    vessel_ent = world.entity(vessel_eid)
    # Portable vessels must be in inventory; fixed/scenery containers
    # (like the stone basin) may be in the room instead.
    vessel_visible = vessel_eid in visible_entities_for_room(world)
    if vessel_eid not in world.player.inventory and not vessel_visible:
        return "You don't see that here.", False
    if "portable" in vessel_ent.tags and vessel_eid not in world.player.inventory:
        return "You'd need to be holding it to fill it.", False

    if not source_eid:
        return "Fill it with what?", False
    if source_eid not in world.entities:
        return "You don't have that.", False

    source_ent = world.entity(source_eid)
    if source_eid not in world.player.inventory and source_eid not in visible:
        return "You don't have that.", False

    if "liquid_source" not in source_ent.tags:
        return "That's not something you can pour from.", False

    if source_ent.props.get("empty", False):
        return "It's empty — there's nothing left to pour.", False

    return _apply_liquid_to_vessel(world, vessel_eid, source_eid)


def handle_pour(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Pour liquid from a carried vessel into a target container.

    Syntax: POUR <source> INTO <target>
    e.g.   "pour water into basin"

    Puzzle hook:
      - Pouring water into the stone_basin while wearing the silver_ring
        activates the basin and reveals the ancient_scroll (Puzzle 3 payoff).
    """
    obj = ir.get("obj")   # the thing being poured (source)
    iobj = ir.get("iobj") # the target container

    if not obj:
        return "Pour what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You aren't holding that.", False

    source_ent = world.entity(obj)

    if "liquid_source" not in source_ent.tags and "container" not in source_ent.tags:
        return "There's nothing to pour from that.", False

    if source_ent.props.get("empty", False):
        return "It's empty — there's nothing left to pour.", False

    if not iobj:
        return "Pour it into what?", False
    if iobj not in world.entities:
        return "You don't see that here.", False

    # Target must be visible (in the room or in inventory).
    visible = visible_entities_for_room(world)
    if iobj not in world.player.inventory and iobj not in visible:
        return "You don't see that here.", False

    target_ent = world.entity(iobj)

    # If the target is a lightable vessel (e.g. the oil lamp), treat this
    # as a fuelling action and delegate to the shared helper.  This makes
    # "pour flask into lamp" equivalent to "fill lamp with flask".
    if "lightable" in target_ent.tags:
        return _apply_liquid_to_vessel(world, iobj, obj)

    if "container" not in target_ent.tags:
        return "You can't pour things into that.", False

    # All container targets (including stone_basin with its Puzzle 3 hook)
    # are now handled centrally in _apply_liquid_to_vessel.
    return _apply_liquid_to_vessel(world, iobj, obj)


def handle_wear(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Wear a 'wearable'-tagged entity from inventory.

    Sets props["worn"] = True and records it in discourse memory.
    """
    obj = ir.get("obj")

    if not obj:
        return "Wear what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You aren't carrying that.", False

    ent = world.entity(obj)

    if "wearable" not in ent.tags:
        return "You can't wear that.", False
    if ent.props.get("worn", False):
        return "You're already wearing it.", False

    # Shield cannot be worn while a two-handed weapon is wielded
    if obj == "kite_shield" and world.player.wielded_weapon:
        wielded = world.entities.get(world.player.wielded_weapon)
        if wielded and wielded.props.get("two_handed", False):
            return (
                "You can't use a shield while wielding a two-handed weapon. "
                "Sheathe the weapon first."
            ), False

    ent.props["worn"] = True
    if obj not in world.player.worn_armour:
        world.player.worn_armour.append(obj)
    world.note_ref([obj])
    return narrate([
        f"You slip on {ent.name}.",
        f"You put on {ent.name}.",
        f"Worn.",
    ]), True


def handle_remove(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Remove a worn item.

    Sets props["worn"] = False.  The item stays in inventory.
    """
    obj = ir.get("obj")

    if not obj:
        return "Remove what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    ent = world.entity(obj)

    # If the target is mounted on the wall (not a worn item), redirect
    # to unmount — "remove X" is natural for wall-mounted items.
    # BUT: if the item is currently worn, treat it as a worn item removal
    # regardless of the mounted tag (items can be unmounted then worn).
    if "mounted" in ent.tags and not ent.props.get("worn", False):
        return handle_unmount(world, ir)

    if obj not in world.player.inventory:
        return "You aren't carrying that.", False

    if "wearable" not in ent.tags:
        return "You can't remove that.", False
    if not ent.props.get("worn", False):
        return "You aren't wearing it.", False

    ent.props["worn"] = False
    if obj in world.player.worn_armour:
        world.player.worn_armour.remove(obj)
    world.note_ref([obj])
    return narrate([
        f"You take off {ent.name}.",
        f"Removed.",
    ]), True


def handle_use(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Generic USE verb: "use X with/on Y".

    This acts as a flexible combiner for cases where the player uses a more
    natural phrasing than the specific verb.  We try to infer what the
    intended action is based on the tags of obj and iobj, then delegate to
    the appropriate specific handler.

    Delegation table:
      obj has "lightable" and iobj has "liquid_source" (oil) -> handle_fill
      obj has "liquid_source" (water) and iobj is "stone_basin"  -> handle_pour
      obj has key_id and iobj has "lockable"                     -> handle_unlock
      obj has "wearable"                                         -> handle_wear
    """
    obj = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Use what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    obj_ent = world.entity(obj)

    # If there's no iobj, treat it as an examine.
    if not iobj:
        return handle_examine(world, {"obj": obj})

    if iobj not in world.entities:
        return "You don't see that here.", False

    iobj_ent = world.entity(iobj)

    # Delegate: key + lockable -> unlock
    if obj_ent.props.get("key_id") is not None and "lockable" in iobj_ent.tags:
        return handle_unlock(world, {**ir, "verb": "unlock"})

    # Delegate: liquid source + lightable lamp -> fill
    if "liquid_source" in obj_ent.tags and "lightable" in iobj_ent.tags:
        # The player said "use oil with lamp" — remap to fill(lamp, oil)
        return handle_fill(world, {**ir, "obj": iobj, "iobj": obj})

    # Delegate: liquid source + basin -> pour
    if "liquid_source" in obj_ent.tags and "container" in iobj_ent.tags:
        return handle_pour(world, {**ir, "verb": "pour"})

    # Delegate: wearable + no real iobj context -> wear
    if "wearable" in obj_ent.tags and not iobj:
        return handle_wear(world, ir)

    return (
        f"You're not sure how to use {obj_ent.name} with {iobj_ent.name}."
    ), False


def handle_unmount(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Take down a mounted item (weapon or armour) from the wall.

    Items tagged "mounted" cannot be picked up with TAKE — the player must
    use TAKE DOWN, REMOVE FROM MOUNT, or UNMOUNT to free them first.  Once
    unmounted the "mounted" tag is removed and the item becomes portable,
    so a subsequent TAKE command works normally.

    The player does not automatically receive the item; they must pick it
    up separately after unmounting.  This mirrors the physical reality of
    lifting something off a wall hook and setting it down.
    """
    obj = ir.get("obj")

    if not obj:
        return "Take down what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "mounted" not in ent.tags:
        return "That isn't mounted on anything.", False

    # Remove the mounted tag and add portable so TAKE now works.
    ent.tags.discard("mounted")
    ent.tags.add("portable")
    world.note_ref([obj])

    return (
        f"You lift {ent.name} down from the wall and set it on the floor."
    ), True


def handle_wield(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Wield a weapon from inventory.

    Enforces the two-handed / shield mutual exclusion:
    - Two-handed weapons (broadsword, iron_mace) cannot be wielded while
      the kite_shield is worn.
    - Attempting to wield a two-handed weapon while the shield is worn
      produces a refusal asking the player to remove the shield first.
    Sets world.player.wielded_weapon on success.
    """
    obj = ir.get("obj")

    if not obj:
        return "Wield what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You'll need to be holding it first.", False

    ent = world.entity(obj)
    if "weapon" not in ent.tags:
        return f"{ent.name.capitalize()} isn't a weapon.", False

    # Enforce two-handed / shield mutual exclusion
    if ent.props.get("two_handed", False):
        shield = world.entities.get("kite_shield")
        if shield and shield.props.get("worn", False):
            return (
                "You can't wield a two-handed weapon while carrying the shield. "
                "Remove the shield first."
            ), False

    # Un-wield any previously wielded weapon
    if world.player.wielded_weapon and world.player.wielded_weapon in world.entities:
        prev = world.entities[world.player.wielded_weapon]
        prev.props["wielded"] = False

    ent.props["wielded"] = True
    world.player.wielded_weapon = obj
    world.note_ref([obj])
    return narrate([
        f"You ready {ent.name}.",
        f"You raise {ent.name}.",
        f"You grip {ent.name} firmly.",
    ]), True


def handle_pet(world: World, ir: dict) -> tuple:
    """
    Pet / stroke a living NPC.  Routes to the NPC interaction layer.
    Only works on entities tagged 'npc'; for non-NPC targets produces
    a gentle redirect.
    """
    obj = ir.get("obj")
    if not obj:
        return "Pet what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    ent = world.entity(obj)
    if "npc" not in ent.tags:
        return f"You give {ent.name} an affectionate pat. Nothing happens.", True
    npcs = get_npc_instances(world)
    npc  = npcs.get(obj)
    if not npc:
        return "You don't see that here.", False
    return handle_pet_npc(world, npc, NPC_MEMORY)


def handle_feed(world: World, ir: dict) -> tuple:
    """
    Feed an NPC a food item.  Expects obj=NPC, iobj=food item.
    Also accepts 'feed cat' with a single food item in inventory.
    """
    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Feed what?", False

    # If only one argument, check if it's the NPC or the food
    target_eid = None
    food_eid   = None

    if obj and obj in world.entities:
        ent = world.entity(obj)
        if "npc" in ent.tags:
            target_eid = obj
            food_eid   = iobj
        elif "food" in ent.tags or "catnip" in ent.tags:
            # 'feed cat food to cat' or 'feed food' — swap obj/iobj
            food_eid   = obj
            target_eid = iobj

    if not target_eid:
        return "Feed what to whom?", False

    # If food not specified, try to find one in inventory
    if not food_eid:
        food_candidates = [
            eid for eid in world.player.inventory
            if "food" in world.entity(eid).tags
            or "catnip" in world.entity(eid).tags
        ]
        if not food_candidates:
            return "You aren't carrying anything to feed it.", False
        if len(food_candidates) > 1:
            return "What do you want to feed it — be specific.", False
        food_eid = food_candidates[0]

    npcs = get_npc_instances(world)
    npc  = npcs.get(target_eid)
    if not npc:
        return "You don't see that here.", False

    return handle_feed_npc(world, npc, NPC_MEMORY, food_eid)


def handle_offer(world: World, ir: dict) -> tuple:
    """
    Offer an item to an NPC — builds trust without consuming the item.
    """
    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj or not iobj:
        return "Offer what to whom?", False

    # Determine which is the NPC and which is the item
    if obj in world.entities and "npc" in world.entity(obj).tags:
        target_eid = obj
        item_eid   = iobj
    elif iobj in world.entities and "npc" in world.entity(iobj).tags:
        target_eid = iobj
        item_eid   = obj
    else:
        return "Offer it to what?", False

    npcs = get_npc_instances(world)
    npc  = npcs.get(target_eid)
    if not npc:
        return "You don't see that here.", False

    return handle_offer_npc(world, npc, NPC_MEMORY, item_eid)


def handle_call(world: World, ir: dict) -> tuple:
    """
    Call out to or speak to an NPC.  Purely narrative — no trust change.
    """
    obj = ir.get("obj")
    if not obj:
        return "Call to what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    ent = world.entity(obj)
    if "npc" not in ent.tags:
        return f"{ent.name.capitalize()} doesn't respond.", True
    if world.player.location != world.entities[obj].location:
        return "It isn't here right now.", False
    npcs = get_npc_instances(world)
    npc  = npcs.get(obj)
    if not npc:
        return "You don't see that here.", False
    return handle_call_npc(world, npc, NPC_MEMORY)


def handle_attack(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Attack, kick, or otherwise act aggressively toward an entity.

    For NPC targets: records a hostile event, drastically reducing trust.
    For non-NPC targets: produces a flavour response (combat system TBD).
    """
    obj = ir.get("obj")

    if not obj:
        return "Attack what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if obj == "slime_golem" or "hostile" in ent.tags:
        # Enter combat with the golem
        global _COMBAT_SESSION
        if _COMBAT_SESSION is not None:
            # Already in combat — route to combat action
            return _execute_combat_action(world, "attack"), True
        golem = world.entities.get("slime_golem")
        if not golem or not golem.props.get("alive", True):
            return "The golem is already dead.", False
        _COMBAT_SESSION = CombatSession(
            player_hp     = world.player.hp,
            player_max_hp = world.player.max_hp,
            player_stamina= world.player.stamina,
            golem_hp      = golem.props.get("hp", 120),
            golem_max_hp  = golem.props.get("max_hp", 120),
        )
        weapon_id     = _get_player_weapon_id(world)
        wearing_coif  = "chain_coif" in world.player.worn_armour
        wearing_shield= "kite_shield" in world.player.worn_armour
        jasper_present= _get_jasper_present(world)
        opening = start_combat(
            _COMBAT_SESSION, weapon_id, wearing_coif,
            wearing_shield, jasper_present
        )
        world.player.hp = _COMBAT_SESSION.player_hp
        return opening, True
    if "npc" in ent.tags:
        # Hostile act against an NPC — large trust penalty
        NPC_MEMORY.record(obj, "player_struck")
        disp = NPC_MEMORY.disposition(obj)
        npcs = get_npc_instances(world)
        npc  = npcs.get(obj)
        if npc:
            # NPC flees immediately regardless of disposition
            from npc import _choose_wander_destination, _move_npc
            dest = _choose_wander_destination(
                npc, world, away_from=world.player.location
            )
            if dest:
                _move_npc(npc, dest, world)
                npc.just_fled = True
        responses = [
            f"You kick {ent.name}. It bolts from the room instantly.",
            f"You strike {ent.name}. It is gone in an instant.",
            f"You lash out at {ent.name}. It flees without a sound.",
        ]
        return narrate(responses), True

    if "scenery" in ent.tags:
        return f"Attacking {ent.name} accomplishes nothing.", False

    # Non-NPC portable entity — placeholder for future combat
    return (
        f"You take a swing at {ent.name}. "
        "Nothing dramatic happens. (Combat system coming soon.)"
    ), False


# ============================================================
# Handler dispatch table
# ============================================================

def handle_block(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Block — only meaningful during combat.  Outside combat it produces
    a gentle redirect.  During combat, routing is handled by
    process_player_combat_action before this handler is reached.
    """
    global _COMBAT_SESSION
    if _COMBAT_SESSION is not None:
        return _execute_combat_action(world, "block"), True
    return "There is nothing to block.", False


def _get_player_weapon_id(world: World) -> str:
    """Return the entity id of the wielded weapon, or 'bare_hands'."""
    wid = world.player.wielded_weapon
    if wid and wid in world.entities:
        return wid
    return "bare_hands"


def _get_jasper_present(world: World) -> bool:
    """Return True if a devoted Jasper is in the same room as the player."""
    if "jasper" not in world.entities:
        return False
    jasper_ent = world.entities["jasper"]
    if jasper_ent.location != world.player.location:
        return False
    disp = NPC_MEMORY.disposition("jasper")
    return disp == "devoted"


def _execute_combat_action(world: World, action: str) -> str:
    """
    Process one player combat action and return the narrative.
    Handles session lifecycle: death, victory, and flight.
    """
    global _COMBAT_SESSION
    if _COMBAT_SESSION is None:
        return "You are not in combat."

    learner = COMBAT_MEMORY.learner("slime_golem")

    # Refresh Jasper's combat presence each round — he may arrive mid-fight
    if _COMBAT_SESSION is not None:
        _COMBAT_SESSION.update_jasper(_get_jasper_present(world))

    narrative, outcome = process_player_combat_action(
        session      = _COMBAT_SESSION,
        player_input = action,
        learner      = learner,
    )
    COMBAT_MEMORY.save()

    # Sync player vitals from session before potentially clearing it
    world.player.hp      = _COMBAT_SESSION.player_hp
    world.player.stamina = _COMBAT_SESSION.player_stamina

    # Always write current golem HP back to entity so it persists
    golem = world.entities.get("slime_golem")
    if golem:
        golem.props["hp"] = _COMBAT_SESSION.golem_hp

    if outcome == "player_dead":
        _COMBAT_SESSION = None

    elif outcome == "golem_dead":
        _COMBAT_SESSION = None
        if golem:
            golem.props["alive"] = False
            golem.props["hp"]    = 0
            _move_entity_to(world, "slime_golem", "hidden")
            remains  = world.entities.get("golem_remains")
            treasure = world.entities.get("secret_treasure")
            if remains:
                remains.location = world.player.location
                room = world.rooms.get(world.player.location)
                if room and "golem_remains" not in room.entities:
                    room.entities.append("golem_remains")
            if treasure:
                treasure.location = world.player.location
                room = world.rooms.get(world.player.location)
                if room and "secret_treasure" not in room.entities:
                    room.entities.append("secret_treasure")

    elif outcome == "fled":
        _COMBAT_SESSION = None
        # Partial HP regeneration for the golem between encounters —
        # it recovers 10% of max HP, rounded up, but never above max.
        if golem and golem.props.get("alive", True):
            regen = max(1, int(golem.props.get("max_hp", 120) * 0.10))
            golem.props["hp"] = min(
                golem.props.get("max_hp", 120),
                golem.props.get("hp", 120) + regen
            )
        # Move player to an adjacent room
        current = world.player.location
        room = world.rooms.get(current)
        if room:
            for dest in room.exits.values():
                if dest != current:
                    world.player.location = dest
                    break

    return narrative


# Rooms the golem cannot enter (too big / wrong terrain)
_GOLEM_FORBIDDEN = {"upstairs_landing", "bedroom_east", "bedroom_west",
                    "forest_edge", "forest_a", "forest_b",
                    "forest_c", "forest_d"}


def _golem_tick(world: World, golem, player_moved: bool) -> None:
    """
    Advance the golem one tick:
    - If aware and not in combat, pursue the player.
    - If not aware, check adjacent rooms for the player (smell).
    - Otherwise wander randomly with 30% probability.
    Forbidden rooms are never entered.
    """
    import random as _random
    player_room = world.player.location
    golem_room  = golem.location

    def _allowed_exits(room_id: str):
        room = world.rooms.get(room_id)
        if not room:
            return []
        return [dest for dest in room.exits.values()
                if dest not in _GOLEM_FORBIDDEN and dest in world.rooms]

    # Smell detection: player in adjacent room
    adjacent = _allowed_exits(golem_room)
    if player_room in adjacent and not golem.props.get("aware"):
        golem.props["aware"] = True

    # If aware, pursue
    if golem.props.get("aware") and golem_room != player_room:
        # Move toward player: pick exit closest to player (BFS one step)
        if player_room in adjacent:
            _move_entity_to(world, "slime_golem", player_room)
        else:
            # Pick a random allowed exit as a rough pursuit
            if adjacent:
                dest = _random.choice(adjacent)
                _move_entity_to(world, "slime_golem", dest)
        return

    # Not aware — 30% random wander
    if golem_room == player_room:
        # Already with player — awareness triggered
        golem.props["aware"] = True
        return
    if _random.random() < 0.30 and adjacent:
        dest = _random.choice(adjacent)
        _move_entity_to(world, "slime_golem", dest)


def _move_entity_to(world: World, eid: str, dest: str) -> None:
    """Move an entity to a new location, updating room entity lists."""
    ent = world.entities.get(eid)
    if not ent:
        return
    old = ent.location
    if old and old in world.rooms:
        if eid in world.rooms[old].entities:
            world.rooms[old].entities.remove(eid)
    ent.location = dest
    if dest in world.rooms:
        if eid not in world.rooms[dest].entities:
            world.rooms[dest].entities.append(eid)


def handle_answer(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Player answers the troll's riddle.

    The raw answer text is passed to troll.handle_troll_answer, which
    normalises it and checks against each riddle's accepted-answer list.
    If the bridge is newly opened, the east exit is added to the bridge room.
    """
    if world.player.location != "bridge":
        return "There is nothing here to answer.", False

    answer_text = ir.get("obj") or ir.get("raw") or ""
    if not answer_text:
        return "Answer what, exactly?", False

    state = TROLL_MEMORY.state()
    response, correct = handle_troll_answer(state, str(answer_text))

    # Open bridge east exit the moment the last riddle is answered correctly.
    if state.bridge_open and "east" not in world.rooms["bridge"].exits:
        world.rooms["bridge"].exits["east"] = "bridge_far_bank"
        # Also open the vault in the cellar and reveal the vault door
        if "south" not in world.rooms["cellar"].exits:
            world.rooms["cellar"].exits["south"] = "vault"
        vault_door = world.entities.get("vault_door")
        if vault_door:
            vault_door.location = "cellar"
            if "vault_door" not in world.rooms["cellar"].entities:
                world.rooms["cellar"].entities.append("vault_door")

    TROLL_MEMORY.save()
    return response, True


ACTION_HANDLERS: Dict[str, Callable[[World, dict], Tuple[str, bool]]] = {
    # Original verbs
    "go":         handle_go,
    "enter":      handle_enter,
    "examine":    handle_examine,
    "take":       handle_take,
    "drop":       handle_drop,
    "open":       handle_open,
    "close":      handle_close,
    "put":        handle_put,
    "unlock":     handle_unlock,
    # New verbs
    "read":       handle_read,
    "light":      handle_light,
    "extinguish": handle_extinguish,
    "push":       handle_push,
    "pull":       handle_pull,
    "fill":       handle_fill,
    "pour":       handle_pour,
    "wear":       handle_wear,
    "remove":     handle_remove,
    "use":        handle_use,
    "unmount":    handle_unmount,
    "wield":      handle_wield,
    "attack":     handle_attack,
    "lock":       handle_lock,
    "drink":      handle_drink,
    "eat":        handle_eat,
    "pet":        handle_pet,
    "feed":       handle_feed,
    "offer":      handle_offer,
    "call":       handle_call,
    "answer":     handle_answer,
    "block":      handle_block,
}


def exec_action(world: World, ir: dict) -> Tuple[str, bool]:
    """Dispatch a grounded action IR to its handler."""
    if ir.get("type") != "action":
        return "Nothing happens.", False

    verb = ir.get("verb")
    handler = ACTION_HANDLERS.get(str(verb))
    if handler is None:
        return "Nothing happens.", False

    return handler(world, ir)


# ============================================================
# Clarification resolution
# ============================================================

def resolve_clarification(world: World, clar: dict, user_reply: str) -> dict:
    """
    Resolve a pending clarification by matching the user's reply to one of
    the offered entity options (by number or by name).
    """
    options = clar["options"]
    reply = normalize(user_reply)

    # Numeric reply: "1", "2", etc.
    match = re.match(r"^\s*(\d+)\s*$", reply)
    if match:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(options):
            chosen = options[idx]
            pending = dict(clar["pending"])
            if pending.get("obj") not in world.entities:
                pending["obj"] = chosen
            else:
                pending["iobj"] = chosen
            return pending

    # Name reply: match against entity names/aliases.
    for eid in options:
        ent = world.entity(eid)
        if reply in ent.all_names() or reply == ent.name.lower():
            pending = dict(clar["pending"])
            if pending.get("obj") not in world.entities:
                pending["obj"] = eid
            else:
                pending["iobj"] = eid
            return pending

    # Could not resolve — re-issue the same clarification.
    return clarify_ir(
        question=clar["question"],
        options=options,
        pending=clar["pending"],
    )


# ============================================================
# Input processing — main entry point
# ============================================================

def process_input(
    world: World,
    parser_system: ParserSystem,
    text: str,
    pending_clarify: Optional[dict]
) -> Tuple[str, Optional[dict]]:
    """
    Process one line of player input.

    If a clarification is pending, resolve it first.
    Otherwise, split compound commands, parse each segment, ground entity
    references, and dispatch to the appropriate handler.

    Returns (output_text, new_pending_clarification_or_None).
    """
    global _COMBAT_SESSION
    # ---- Resolve a pending clarification ----
    if pending_clarify is not None:
        grounded = resolve_clarification(world, pending_clarify, text)

        if grounded["type"] == "clarify":
            return format_clarification(world, grounded), grounded

        # resolve_clarification fills in only the one ambiguous slot that was
        # asked about.  The other slot (e.g. iobj) may still be a raw phrase
        # rather than a grounded entity id.  We pass the result back through
        # ground_intent so both slots are fully resolved before the handler runs.
        parser_system.semantic_entity_index.rebuild_for_visible(world)
        fully_grounded = ground_intent(
            world=world,
            intent=grounded,
            semantic_index=parser_system.semantic_entity_index,
        )

        # If grounding the second slot also produces a clarification, surface it.
        if fully_grounded.get("type") == "clarify":
            return format_clarification(world, fully_grounded), fully_grounded

        out, consumed = exec_action(world, fully_grounded)
        if consumed:
            world.clock.advance(1)
        return out, None

    # ---- Combat intercept: if in combat, all input routes to combat ----
    if _COMBAT_SESSION is not None:
        normalised_input = normalize(text)
        if normalised_input in {"look", "l", "inventory", "inv", "i"}:
            pass   # allow look/inv during combat
        else:
            narrative = _execute_combat_action(world, normalised_input)
            world.clock.advance(1)
            return narrative, None

    # ---- Split "open door then go north" style compound commands ----
    segments = split_compound(text)
    if not segments:
        return "Say something.", None

    # Expand coordinated objects: "get key and box" -> ["get key", "get box"]
    expanded_segments: List[str] = []
    for seg in segments:
        expanded_segments.extend(expand_coordinated_objects(seg))

    outputs: List[str] = []
    any_consumed    = False
    player_moved    = False
    location_before = world.player.location

    for seg in expanded_segments:
        # Rebuild the semantic entity index for the current visibility state.
        # We use visible_entities_for_room() so dark-cellar items are excluded
        # from the index when the lamp is unlit.
        parser_system.semantic_entity_index.rebuild_for_visible(world)

        # Attach world temporarily so parse_to_candidates can pass it
        # to semantic_slot_fill (Improvement B3) without changing the
        # public API of parse_to_candidates.
        parser_system._current_world = world
        candidates = parse_to_candidates(seg, parser_system=parser_system)
        parser_system._current_world = None

        if not candidates:
            outputs.append("I beg your pardon?")
            continue

        intent = candidates[0]

        if intent["type"] == "missing_verb":
            outputs.append(f"What do you want to do with the {intent['text']}?")
            continue

        if intent["type"] == "meta":
            if intent["verb"] == "look":
                outputs.append(do_look(world))
            elif intent["verb"] == "inventory":
                outputs.append(do_inventory(world))
            else:
                outputs.append("Not implemented.")
            continue

        grounded = ground_intent(
            world=world,
            intent=intent,
            semantic_index=parser_system.semantic_entity_index,
        )

        if grounded["type"] == "clarify":
            outputs.append(format_clarification(world, grounded))
            return "\n".join(outputs), grounded

        out, consumed = exec_action(world, grounded)
        outputs.append(out)

        if consumed:
            world.clock.advance(1)
            any_consumed = True
            if world.player.location != location_before:
                player_moved    = True
                location_before = world.player.location

    # ---- Golem tick: wander, detect, pursue ----
    if any_consumed:
        golem = world.entities.get("slime_golem")
        vault_open = "south" in world.rooms.get("cellar", type("R", (), {"exits": {}})()).exits
        if golem and golem.props.get("alive", True) and golem.location != "hidden" and vault_open:
            _golem_tick(world, golem, player_moved)
            # Check if golem just entered player's room (combat trigger)
            if (golem.location == world.player.location
                    and _COMBAT_SESSION is None
                    and golem.props.get("aware", False)):
                session = CombatSession(
                    player_hp      = world.player.hp,
                    player_max_hp  = world.player.max_hp,
                    player_stamina = world.player.stamina,
                    golem_hp       = golem.props.get("hp", 120),
                    golem_max_hp   = golem.props.get("max_hp", 120),
                )
                _COMBAT_SESSION = session
                opening = start_combat(
                    session,
                    _get_player_weapon_id(world),
                    "chain_coif" in world.player.worn_armour,
                    "kite_shield" in world.player.worn_armour,
                    _get_jasper_present(world),
                )
                outputs.append(
                    "The slime golem surges into the room. "
                    "It has found you.\n\n" + opening
                )

    # ---- NPC tick: runs once per turn after all segments execute ----
    # Only ticks when at least one action was consumed (so meta commands
    # like "look" and "inventory" do not advance NPC state).
    if any_consumed:
        npcs = get_npc_instances(world)
        for npc in npcs.values():
            npc_msgs = npc_tick(
                world        = world,
                npc          = npc,
                memory       = NPC_MEMORY,
                player_moved = player_moved,
            )
            outputs.extend(npc_msgs)
        # Persist trust changes after every consumed action.
        NPC_MEMORY.save()

        # Troll tick: runs when player is at the bridge.
        if world.player.location == "bridge":
            troll_msgs = troll_encounter(
                state        = TROLL_MEMORY.state(),
                player_moved = player_moved,
            )
            outputs.extend(troll_msgs)
            TROLL_MEMORY.save()

    return "\n".join(outputs), None