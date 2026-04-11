# handlers/helpers.py
#
# Shared utility functions used by multiple handler modules.
#
# These were originally embedded in engine.py.  Extracting them here
# avoids circular imports: handler modules import from helpers, and
# engine.py imports the handler package — neither direction creates
# a cycle.

import random
import re
from typing import Dict, List, Optional, Tuple

from model import World


# ── Visibility helpers ────────────────────────────────────────────────────

def player_has_lit_lamp(world: World) -> bool:
    """
    Return True if the player is carrying the oil lamp and it is
    currently lit.  Used for the dark-cellar mechanic and as a
    prerequisite check in puzzle handlers.
    """
    lamp = world.entities.get("oil_lamp")
    if lamp is None:
        return False
    return (lamp.location == "player") and lamp.props.get("lit", False)


def visible_entities_for_room(world: World) -> List[str]:
    """
    Return the list of entity ids currently visible to the player.

    Wraps World.visible_entities() with an extra layer:
      - In the cellar, entities whose props["requires_light"] is True
        are only included when the player carries a lit lamp.
      - Entities in location "hidden" are never included.
    """
    base = world.visible_entities()

    if world.player.location != "cellar":
        return base

    has_light = player_has_lit_lamp(world)
    return [
        eid for eid in base
        if not world.entity(eid).props.get("requires_light", False)
        or has_light
    ]


def phrase_in_room_text(world: World, phrase: str) -> bool:
    """
    Return True if every normalised token of *phrase* appears in the
    combined text that the player can currently see: the room description
    plus scenery entity descriptions.
    """
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower())

    tokens = [t for t in _norm(phrase).split() if t]
    if not tokens:
        return False

    room = world.room()
    texts: list = []

    if getattr(room, "desc_alt", None):
        texts.append(room.desc_alt)
    elif hasattr(room, "desc_lit") and player_has_lit_lamp(world):
        texts.append(room.desc_lit)
    else:
        texts.append(room.desc)

    for eid in room.entities:
        ent = world.entities.get(eid)
        if ent and "scenery" in ent.tags:
            texts.append(ent.props.get("desc", ""))
            texts.append(ent.name)

    combined = _norm(" ".join(texts))
    return all(t in combined for t in tokens)


# ── Output helpers ────────────────────────────────────────────────────────

def narrate(options: List[str]) -> str:
    """Pick a random response string from a list of alternatives."""
    return random.choice(options)


# ── Entity movement ──────────────────────────────────────────────────────

def move_entity(world: World, eid: str, dest: str) -> None:
    """
    Move an entity from its current location to dest.

    dest can be:
      - a room id       -> entity goes into room.entities
      - "player"        -> entity goes into player.inventory
      - an entity id    -> entity goes into that entity's .contains list
      - "hidden"        -> entity is removed from everywhere
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

    # Place in new location.
    ent.location = dest
    if dest == "player":
        world.player.inventory.append(eid)
    elif dest in world.rooms:
        world.rooms[dest].entities.append(eid)
    elif dest in world.entities:
        world.entity(dest).contains.append(eid)


def move_entity_to(world: World, eid: str, dest: str) -> None:
    """
    Move an entity to a new location, updating room entity lists.
    Simpler variant that does not handle containers or inventory —
    used for NPC and golem movement.
    """
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


# ── Precondition checks ─────────────────────────────────────────────────

def require_visible(world: World, eid: str) -> Optional[str]:
    """
    Return an error message if eid is not currently visible, else None.
    """
    if eid not in visible_entities_for_room(world):
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
    Given a door entity, return the room on the other side from the
    player's current location.  Returns None if something is wrong.
    """
    if door_eid not in world.entities:
        return None
    door = world.entity(door_eid)
    room_a = door.props.get("room_a")
    room_b = door.props.get("room_b")
    current = world.player.location
    if not isinstance(room_a, str) or not isinstance(room_b, str):
        return None
    if room_a not in world.rooms or room_b not in world.rooms:
        return None
    if current == room_a:
        return room_b
    if current == room_b:
        return room_a
    return None
