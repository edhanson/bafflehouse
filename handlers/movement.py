# handlers/movement.py
#
# Movement and navigation handlers: go, enter.
# Also contains traverse_door and do_look, which are used by other
# modules (e.g. enter uses traverse_door).

import random
from typing import List, Optional, Tuple

from model import World
from handlers.registry import register
from handlers.helpers import (
    narrate,
    player_has_lit_lamp,
    require_visible,
    other_side_of_door,
    visible_entities_for_room,
)
from parser import DIRECTIONS


# ── Room description ──────────────────────────────────────────────────────

def do_look(world: World, show_npcs: bool = True) -> str:
    """
    Describe the current room.

    Uses visible_entities_for_room() so that dark-cellar items are
    correctly hidden when the lamp is unlit.

    show_npcs: when False, suppresses the NPC presence line.  Set to
    False on movement so the NPC tick's enters_room message is the
    sole description — avoids doubling up on the same turn.
    """
    room = world.room()

    # Select dynamic description if available.
    if getattr(room, "desc_alt", None):
        desc = room.desc_alt
    elif hasattr(room, "desc_lit") and player_has_lit_lamp(world):
        desc = room.desc_lit
    else:
        desc = room.desc

    lines = [room.title, desc]

    visible = visible_entities_for_room(world)

    # Show non-scenery, non-NPC, non-inventory items in the room.
    visible_non_scenery = [
        eid for eid in visible
        if "scenery" not in world.entity(eid).tags
        and "npc"     not in world.entity(eid).tags
        and eid not in world.player.inventory
    ]

    if visible_non_scenery:
        mounted_items   = [eid for eid in visible_non_scenery
                           if "mounted" in world.entity(eid).tags]
        portable_items  = [eid for eid in visible_non_scenery
                           if "mounted" not in world.entity(eid).tags]
        parts = []
        if portable_items:
            parts.append(", ".join(world.entity(eid).name for eid in portable_items))
        if mounted_items:
            mounted_names = ", ".join(
                world.entity(eid).name + " (mounted)" for eid in mounted_items
            )
            parts.append(mounted_names)
        things = ", ".join(parts)
        lines.append(f"You see {things}.")

    # Describe any NPCs present in the room.
    npc_eids = list(dict.fromkeys(
        eid for eid in visible if "npc" in world.entity(eid).tags
    ))
    if npc_eids and show_npcs:
        # Import here to avoid circular dependency at module level.
        from npc_bayesian import trust_to_disposition
        # Access NPC_MEMORY through the engine module to avoid duplicating
        # the singleton.  This is a runtime import, safe because engine
        # is always loaded before any handler is called.
        import engine as _engine
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
        for eid in npc_eids:
            ent  = world.entity(eid)
            disp = _engine.NPC_MEMORY.disposition(eid)
            pool = npc_look_lines.get(disp, npc_look_lines["neutral"])
            name = ent.name.capitalize()
            lines.append(random.choice(pool).format(name=name))

    exits = ", ".join(sorted(room.exits.keys())) if room.exits else "none"
    lines.append(f"Exits: {exits}.")
    return "\n".join(lines)


# ── Door traversal ────────────────────────────────────────────────────────

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


# ── Verb handlers ─────────────────────────────────────────────────────────

@register("go")
def handle_go(world: World, ir: dict) -> Tuple[str, bool]:
    """Move the player in a compass direction or through a named door."""
    iobj = ir.get("iobj")

    if not iobj:
        room = world.room()
        exits = list(room.exits.keys())
        if exits:
            exit_str = ", ".join(exits)
            return f"Which direction? Available exits: {exit_str}.", False
        return "There is nowhere obvious to go.", False

    if iobj in DIRECTIONS.values():
        direction = iobj
        room = world.room()
        if direction not in room.exits:
            return "You can't go that way.", False
        world.player.location = room.exits[direction]
        return do_look(world, show_npcs=False), True

    if iobj in world.entities:
        return traverse_door(world, iobj)

    return "You can't quite manage that.", False


@register("enter")
def handle_enter(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Enter a location or object: "enter portal", "enter room",
    "go through door".

    Resolution order:
      1. Portal win condition
      2. Room name — if the named room is reachable via a current exit
      3. Open door entity — delegate to traverse_door
      4. Generic refusal
    """
    import engine as _engine

    obj = ir.get("obj") or ir.get("iobj")

    # ── 1. Portal win condition ──────────────────────────────────────
    portal    = world.entities.get("home_portal")
    portal_in_room = portal and portal.location == world.player.location
    archway = world.entities.get("stone_archway")
    archway_active = archway and archway.props.get("active", False)
    archway_here   = archway and archway.location == world.player.location

    target_is_portal = obj in (
        "home_portal", "portal", "shimmer", "light", "opening",
        "stone_archway", "archway", "arch", "gateway", "way home",
    )

    if (portal_in_room or (archway_here and archway_active)) and target_is_portal:
        _engine._GAME_WON = True
        _engine.SCORE_TRACKER.award("game_won")
        return _engine._WIN_NARRATIVE, True

    if not obj:
        return "Enter what?", False

    # Strip leading state adjectives
    obj_clean = str(obj)
    for adj in ("open ", "closed ", "the open ", "the closed ",
                "an open ", "a closed "):
        if obj_clean.lower().startswith(adj):
            obj_clean = obj_clean[len(adj):]
            break

    if obj_clean != str(obj) and obj_clean not in world.entities:
        for eid, ent in world.entities.items():
            if (obj_clean.lower() in ent.all_names()
                    and eid in world.visible_entities()):
                obj = eid
                break

    # ── 2. Room name ─────────────────────────────────────────────────
    current_room = world.room()
    for direction, dest_rid in current_room.exits.items():
        dest_room = world.rooms.get(dest_rid)
        if dest_room is None:
            continue
        if (obj == dest_rid
                or (isinstance(obj, str)
                    and dest_room.title.lower() == obj.lower())):
            world.player.location = dest_rid
            return do_look(world, show_npcs=False), True

    # ── 3. Door entity ───────────────────────────────────────────────
    if obj in world.entities:
        ent = world.entity(obj)
        if obj == "stone_archway":
            if ent.props.get("active", False):
                return "The portal is open. Step through it.", False
            return narrate([
                "You walk through the archway. The stone is cold and ancient "
                "under your fingertips. Nothing happens. Whatever power it "
                "holds is not yet awake.",
                "You pass beneath the keystone. The air between the pillars "
                "feels slightly different — thicker, perhaps, or just older. "
                "Nothing stirs.",
                "You step through the archway. The carved serpents seem to "
                "watch you. The opening shows only the far trees beyond. "
                "Something is missing.",
            ]), True
        if "door" in ent.tags:
            return traverse_door(world, obj)
        if "openable" in ent.tags and ent.props.get("open", False):
            return f"You can't go through {ent.name}.", False

    # ── 4. Generic refusal ───────────────────────────────────────────
    ent = world.entities.get(obj)
    name = ent.name if ent else str(obj)
    return f"You can't enter {name}.", False
