# handlers/inventory.py
#
# Inventory and object-manipulation handlers: take, drop, open, close,
# put, unlock, lock, wield, wear, remove, unmount, use.

from typing import Optional, Tuple

from model import World
from handlers.registry import register
from handlers.helpers import (
    move_entity,
    narrate,
    phrase_in_room_text,
    player_has_lit_lamp,
    require_visible,
    visible_entities_for_room,
)


# ── Inventory display (used by meta handler and engine) ───────────────────

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


def do_status(world: World) -> str:
    """Report player HP, stamina, and equipped items."""
    hp     = world.player.hp
    max_hp = world.player.max_hp
    st     = world.player.stamina
    max_st = world.player.max_stamina

    wid = world.player.wielded_weapon
    weapon_str = (world.entities[wid].name
                  if wid and wid in world.entities
                  else "your bare hands")

    worn = [world.entities[eid].name
            for eid in world.player.worn_armour
            if eid in world.entities]
    worn_str = ", ".join(worn) if worn else "nothing"

    return (
        f"HP: {hp}/{max_hp}  Stamina: {st}/{max_st}\n"
        f"Wielding: {weapon_str}\n"
        f"Wearing:  {worn_str}"
    )


# ── Take / Drop ──────────────────────────────────────────────────────────

@register("take")
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
    return narrate([
        f"You take {ent.name}.",
        f"You pick up {ent.name}.",
        f"You grab {ent.name}.",
    ]), True


@register("drop")
def handle_drop(world: World, ir: dict) -> Tuple[str, bool]:
    """Drop a carried entity into the current room."""
    obj = ir.get("obj")

    if not obj:
        return "Drop what?", False
    if obj not in world.entities or obj not in world.player.inventory:
        return "You aren't holding that.", False

    ent = world.entity(obj)
    if ent.props.get("lit", False):
        return "You'd rather not put down a lit lamp.", False

    move_entity(world, obj, world.player.location)
    world.note_ref([obj])
    return narrate(["Dropped.", "Done."]), True


# ── Open / Close ─────────────────────────────────────────────────────────

@register("open")
def handle_open(world: World, ir: dict) -> Tuple[str, bool]:
    """Open an openable entity (door, container, etc.)."""
    import engine as _engine

    obj  = ir.get("obj")
    iobj = ir.get("iobj")
    prep = ir.get("prep")
    raw  = ir.get("raw", "")

    if not obj:
        return "Open what?", False

    # Extract instrument from raw phrase if not grounded
    if not iobj:
        raw_str = str(raw)
        if " with " in raw_str:
            after_with = raw_str.split(" with ", 1)[1].strip()
            if after_with:
                iobj = after_with

    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)

    # Items with a tool_required prop
    tool_id = ent.props.get("tool_required")
    if tool_id:
        if ent.props.get("opened", False):
            return "It's already open.", False
        tool_eid = iobj
        if tool_eid and tool_eid not in world.entities:
            for eid, ent2 in world.entities.items():
                if any(tool_eid in a.lower() or a.lower() in tool_eid
                       for a in ent2.all_names()):
                    if eid in world.player.inventory:
                        tool_eid = eid
                        break
        has_tool = (
            (tool_eid and tool_eid == tool_id
             and tool_id in world.player.inventory)
            or (not tool_eid and tool_id in world.player.inventory)
        )
        if not has_tool:
            tool_ent = world.entities.get(tool_id)
            tool_name = tool_ent.name if tool_ent else "the right tool"
            return f"You'll need {tool_name} to open that.", False
        ent.props["opened"] = True
        world.note_ref([obj])
        return narrate([
            "You work the can opener around the lid. It peels back with a "
            "sharp metallic snap. The smell hits you immediately.",
            "The can opener does its job. The lid comes free with a ragged edge.",
        ]), True

    if iobj is not None:
        if iobj not in world.entities:
            for eid, ent2 in world.entities.items():
                if (eid in world.player.inventory
                        and any(iobj.lower() == a.lower()
                                or iobj.lower() in a.lower()
                                for a in ent2.all_names())):
                    iobj = eid
                    break

        if ("lockable" in ent.tags and ent.props.get("locked", False)
                and iobj in world.entities
                and "key_id" in world.entities[iobj].props):
            unlock_ir = {**ir, "iobj": iobj}
            unlock_msg, unlocked = handle_unlock(world, unlock_ir)
            if not unlocked:
                return unlock_msg, False
            open_msg, opened = handle_open(
                world, {**ir, "prep": None, "iobj": None}
            )
            return unlock_msg + "\n" + open_msg, opened
        if prep is not None:
            return ("You can't open things with that. "
                    "Perhaps you mean UNLOCK something WITH it."), False

    if "openable" not in ent.tags:
        return "That's not something you can open.", False
    if ent.props.get("open", False):
        return "It's already open.", False
    if ent.props.get("locked", False):
        return "It seems to be locked.", False

    ent.props["open"] = True
    world.note_ref([obj])

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
        base = narrate([
            "The heavy door swings inward.",
            "The door opens with a reluctant creak.",
            "The door gives way, swinging open.",
        ])
        pts = _engine.SCORE_TRACKER.award("secret_study_found")
        return (base + "\n" + pts) if pts else base, True

    pts = (_engine.SCORE_TRACKER.award("display_case_opened")
           if obj == "display_case" else None)
    base = narrate([
        "Opened.", "The thing opens.",
        "With a modest show of cooperation, it opens.",
    ])
    return (base + "\n" + pts) if pts else base, True


@register("close")
def handle_close(world: World, ir: dict) -> Tuple[str, bool]:
    """Close an openable entity."""
    obj  = ir.get("obj")
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

    if obj == "oak_door":
        world.rooms["foyer"].exits.pop("north", None)
        world.rooms["hall_1"].exits.pop("south", None)
        return "The oak door swings shut with a heavy thud.", True

    if obj == "study_door":
        world.rooms["trophy_room"].exits.pop("south", None)
        world.rooms["secret_study"].exits.pop("north", None)
        return "The heavy door grinds shut behind you.", True

    return narrate(["Closed.", "The thing closes."]), True


# ── Put ──────────────────────────────────────────────────────────────────

@register("put")
def handle_put(world: World, ir: dict) -> Tuple[str, bool]:
    """Put a carried entity into a container or onto a surface."""
    obj  = ir.get("obj")
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


# ── Unlock / Lock ────────────────────────────────────────────────────────

@register("unlock")
def handle_unlock(world: World, ir: dict) -> Tuple[str, bool]:
    """Unlock a lockable entity with a key from inventory."""
    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Unlock what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    if not iobj:
        return "You need to specify what to unlock it with.", False
    if iobj not in world.entities:
        return f"You aren't holding any {iobj}.", False
    if iobj not in world.player.inventory:
        return "You aren't holding that.", False

    thing = world.entity(obj)
    key   = world.entity(iobj)

    if "lockable" not in thing.tags:
        return "That doesn't have a lock.", False
    if not thing.props.get("locked", False):
        return "It's not locked.", False

    needed = thing.props.get("key_id")
    if needed is not None and key.props.get("key_id") != needed:
        return "That key doesn't seem to fit.", False

    thing.props["locked"] = False
    world.note_ref([obj, iobj])
    return narrate(["Unlocked.", "The lock clicks open."]), True


@register("lock")
def handle_lock(world: World, ir: dict) -> Tuple[str, bool]:
    """Lock a lockable entity with a key from inventory."""
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
        return "You need to specify what to lock it with.", False
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


# ── Wield / Wear / Remove / Unmount ──────────────────────────────────────

@register("wield")
def handle_wield(world: World, ir: dict) -> Tuple[str, bool]:
    """Wield a weapon from inventory."""
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

    if ent.props.get("two_handed", False):
        shield = world.entities.get("kite_shield")
        if shield and shield.props.get("worn", False):
            return (
                "You can't wield a two-handed weapon while carrying the "
                "shield. Remove the shield first."
            ), False

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


@register("wear")
def handle_wear(world: World, ir: dict) -> Tuple[str, bool]:
    """Wear a 'wearable'-tagged entity from inventory."""
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


@register("remove")
def handle_remove(world: World, ir: dict) -> Tuple[str, bool]:
    """Remove a worn item.  The item stays in inventory."""
    obj = ir.get("obj")

    if not obj:
        return "Remove what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    ent = world.entity(obj)

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


@register("unmount")
def handle_unmount(world: World, ir: dict) -> Tuple[str, bool]:
    """Take down a mounted item from the wall."""
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

    ent.tags.discard("mounted")
    ent.tags.add("portable")
    world.note_ref([obj])
    return (
        f"You lift {ent.name} down from the wall and set it on the floor."
    ), True


# ── Use (generic delegator) ─────────────────────────────────────────────

@register("use")
def handle_use(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Generic USE verb: "use X with/on Y".
    Infers the intended action from entity tags and delegates.
    """
    # Import sibling handlers for delegation.
    from handlers.interaction import handle_examine
    from handlers.puzzle import handle_fill, handle_pour

    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Use what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    obj_ent = world.entity(obj)

    if not iobj:
        return handle_examine(world, {"obj": obj})

    if iobj not in world.entities:
        return "You don't see that here.", False

    iobj_ent = world.entity(iobj)

    # key + lockable -> unlock
    if obj_ent.props.get("key_id") is not None and "lockable" in iobj_ent.tags:
        return handle_unlock(world, {**ir, "verb": "unlock"})

    # liquid source + lightable -> fill
    if "liquid_source" in obj_ent.tags and "lightable" in iobj_ent.tags:
        return handle_fill(world, {**ir, "obj": iobj, "iobj": obj})

    # liquid source + container -> pour
    if "liquid_source" in obj_ent.tags and "container" in iobj_ent.tags:
        return handle_pour(world, {**ir, "verb": "pour"})

    # wearable
    if "wearable" in obj_ent.tags and not iobj:
        return handle_wear(world, ir)

    return (
        f"You're not sure how to use {obj_ent.name} with {iobj_ent.name}."
    ), False
