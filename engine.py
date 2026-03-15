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
# Visibility helpers
# ============================================================

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


def do_look(world: World) -> str:
    """
    Describe the current room.

    Uses visible_entities_for_room() so that dark-cellar items are correctly
    hidden when the lamp is unlit.
    """
    room = world.room()
    lines = [room.title, room.desc]

    visible = visible_entities_for_room(world)

    # Show non-scenery, non-inventory items in the room.
    visible_non_scenery = [
        eid for eid in visible
        if "scenery" not in world.entity(eid).tags
        and eid not in world.player.inventory
    ]

    if visible_non_scenery:
        things = ", ".join(world.entity(eid).name for eid in visible_non_scenery)
        lines.append(f"You see {things}.")
    else:
        lines.append("You see nothing of interest.")

    exits = ", ".join(sorted(room.exits.keys())) if room.exits else "none"
    lines.append(f"Exits: {exits}.")
    return "\n".join(lines)


def do_inventory(world: World) -> str:
    """List carried items, noting which ones are currently worn."""
    if not world.player.inventory:
        return "You are empty-handed."

    parts = []
    for eid in world.player.inventory:
        ent = world.entity(eid)
        label = ent.name
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
    """
    if eid not in visible_entities_for_room(world):
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
        return do_look(world), True

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
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)
    world.note_ref([obj])

    lines = [ent.props.get("desc", "You see nothing special.")]

    if "openable" in ent.tags:
        lines.append("It is open." if ent.props.get("open", False) else "It is closed.")

    if "container" in ent.tags and ent.props.get("open", False):
        if ent.contains:
            contents = ", ".join(world.entity(cid).name for cid in ent.contains)
            lines.append(f"It contains {contents}.")
        else:
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

    return "\n".join(lines), True


def handle_take(world: World, ir: dict) -> Tuple[str, bool]:
    """Pick up a portable entity and add it to inventory."""
    obj = ir.get("obj")

    if not obj:
        return "Take what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    if "portable" not in ent.tags or "scenery" in ent.tags:
        return "You can't take that.", False

    if obj in world.player.inventory or ent.location == "player":
        return "You already have it.", False

    move_entity(world, obj, "player")
    world.note_ref([obj])
    return narrate(["Taken.", "Okay.", "You take it."]), True


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

    # Special case: unlocking the study door reveals the north exit.
    # The door connects trophy_room <-> secret_study, so we add "north"
    # to the trophy room's exits so the player can walk through normally.
    if obj == "study_door":
        world.rooms["trophy_room"].exits["north"] = "secret_study"
        return (
            "The lock clicks open. The heavy door swings inward, revealing a dark "
            "passage to the north."
        ), True

    return narrate(["Unlocked.", "The lock clicks open."]), True


# ============================================================
# Action handlers — new verbs
# ============================================================

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


def handle_light(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Light a 'lightable'-tagged entity.

    Prerequisites:
      - Entity must be in inventory (you can't light something at arm's length).
      - Entity must have "fuelled": True.
    """
    obj = ir.get("obj")

    if not obj:
        return "Light what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You'd need to be holding it to light it.", False

    ent = world.entity(obj)

    if "lightable" not in ent.tags:
        return "That's not something you can light.", False
    if ent.props.get("lit", False):
        return "It's already lit.", False
    if not ent.props.get("fuelled", False):
        return "It has no fuel. You'll need to fill it with oil first.", False

    ent.props["lit"] = True
    world.note_ref([obj])
    return (
        "You strike a spark. The lamp catches with a warm, steady flame, "
        "pushing the darkness back."
    ), True


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
        world.rooms["hall"].exits["west"] = "cellar"
        world.note_ref([obj])

        return (
            "You haul on the iron lever with both hands. Somewhere deep in the wall, "
            "something heavy shifts. A low rumble travels through the stone — and from "
            "far above, you hear the sound of a door grinding open."
            "\n\nIt sounds like the passage in the hall's west wall has been unsealed."
        ), True

    # Generic pullable (future-proofing)
    world.note_ref([obj])
    return "You pull it. Something shifts, but nothing dramatic happens.", True


def handle_fill(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Fill a vessel from a liquid source.

    Syntax: FILL <vessel> WITH <source>
    e.g.   "fill lamp with oil"

    Puzzle hook:
      - Filling the oil_lamp with lamp_oil sets lamp.props["fuelled"] = True
        and marks the flask as empty.
    """
    obj = ir.get("obj")   # the vessel being filled
    iobj = ir.get("iobj") # the liquid source

    if not obj:
        return "Fill what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    # The target vessel must be in inventory.
    if obj not in world.player.inventory:
        return "You'd need to be holding it to fill it.", False

    if not iobj:
        return "Fill it with what?", False
    if iobj not in world.entities:
        return "You don't have that.", False

    # The source must also be in inventory (or visible — we allow either).
    source_ent = world.entity(iobj)
    vessel_ent = world.entity(obj)

    visible = visible_entities_for_room(world)
    if iobj not in world.player.inventory and iobj not in visible:
        return "You don't have that.", False

    if "liquid_source" not in source_ent.tags:
        return "That's not something you can pour from.", False

    if source_ent.props.get("empty", False):
        return "It's empty — there's nothing left to pour.", False

    # ---- Puzzle 1: fill lamp with oil ----
    if obj == "oil_lamp" and source_ent.props.get("liquid") == "oil":
        if vessel_ent.props.get("fuelled", False):
            return "The lamp is already full of oil.", False

        vessel_ent.props["fuelled"] = True
        source_ent.props["empty"] = True  # consume the flask
        world.note_ref([obj, iobj])

        return (
            "You carefully tip the flask. Oil flows into the lamp's reservoir "
            "with a quiet glug. The flask is now empty."
        ), True

    # Generic fill (future-proofing for other vessel/liquid combos)
    if "lightable" in vessel_ent.tags:
        return "That doesn't take that kind of liquid.", False

    return (
        f"You're not sure how to fill {vessel_ent.name} with {source_ent.name}."
    ), False


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

    if "container" not in target_ent.tags:
        return "You can't pour things into that.", False

    # ---- Puzzle 3: pour water into stone basin ----
    if iobj == "stone_basin" and source_ent.props.get("liquid") == "water":
        if target_ent.props.get("activated", False):
            return "The basin already holds the water. The serpents are still.", False

        # Check if the player is wearing the silver ring.
        ring = world.entities.get("silver_ring")
        ring_worn = (
            ring is not None
            and ring.location == "player"
            and ring.props.get("worn", False)
        )

        if not ring_worn:
            # The water goes in, but nothing special happens yet.
            target_ent.props["liquid"] = "water"
            source_ent.props["empty"] = True
            world.note_ref([obj, iobj])
            return (
                "You pour the water into the basin. It sits there, cold and still. "
                "The serpent carvings seem to watch, unimpressed. "
                "Perhaps something is missing."
            ), True

        # Ring is worn — trigger the puzzle.
        target_ent.props["activated"] = True
        target_ent.props["liquid"] = "water"
        source_ent.props["empty"] = True

        # Reveal the ancient scroll: move it from "hidden" into the basin.
        move_entity(world, "ancient_scroll", "stone_basin")
        # Also add it to the world's visible entity list via the basin's contains.
        # (move_entity already handles this since stone_basin is an entity.)

        world.note_ref([obj, iobj, "ancient_scroll"])

        return (
            "The moment the water touches the basin, the ring on your finger grows warm. "
            "The carved serpents seem to writhe — a trick of the light, surely — and "
            "the water begins to glow with a faint green luminescence.\n\n"
            "Something rises from beneath the water: a tightly rolled scroll, bone dry "
            "despite the liquid around it. It comes to rest at the basin's rim as if "
            "placed there by an invisible hand."
        ), True

    # Generic pour into a container.
    liquid = source_ent.props.get("liquid", "liquid")
    source_ent.props["empty"] = True
    target_ent.props["liquid"] = liquid
    world.note_ref([obj, iobj])
    return narrate(["Poured.", f"You pour the {liquid} in."]), True


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

    ent.props["worn"] = True
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
    if obj not in world.player.inventory:
        return "You aren't carrying that.", False

    ent = world.entity(obj)

    if "wearable" not in ent.tags:
        return "You can't remove that.", False
    if not ent.props.get("worn", False):
        return "You aren't wearing it.", False

    ent.props["worn"] = False
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


# ============================================================
# Handler dispatch table
# ============================================================

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
    # ---- Resolve a pending clarification ----
    if pending_clarify is not None:
        grounded = resolve_clarification(world, pending_clarify, text)

        if grounded["type"] == "clarify":
            return format_clarification(world, grounded), grounded

        out, consumed = exec_action(world, grounded)
        if consumed:
            world.clock.advance(1)
        return out, None

    # ---- Split "open door then go north" style compound commands ----
    segments = split_compound(text)
    if not segments:
        return "Say something.", None

    # Expand coordinated objects: "get key and box" -> ["get key", "get box"]
    expanded_segments: List[str] = []
    for seg in segments:
        expanded_segments.extend(expand_coordinated_objects(seg))

    outputs: List[str] = []

    for seg in expanded_segments:
        # Rebuild the semantic entity index for the current visibility state.
        # We use visible_entities_for_room() so dark-cellar items are excluded
        # from the index when the lamp is unlit.
        parser_system.semantic_entity_index.rebuild_for_visible(world)

        candidates = parse_to_candidates(seg, parser_system=parser_system)

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

    return "\n".join(outputs), None
