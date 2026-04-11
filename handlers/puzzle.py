# handlers/puzzle.py
#
# Puzzle-specific handlers: pull, fill, pour.
#
# Contains _apply_liquid_to_vessel, the shared liquid-transfer logic
# used by both fill and pour.  Puzzle state machines (lever, antler,
# basin) live here rather than in generic verb handlers.

from typing import Tuple

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


# ── Shared liquid transfer ───────────────────────────────────────────────

def _apply_liquid_to_vessel(
    world: World,
    vessel_eid: str,
    source_eid: str,
) -> Tuple[str, bool]:
    """
    Core liquid-transfer logic shared by handle_fill and handle_pour.

    Both entities must already be confirmed as valid and in-scope.

    Handles:
      - Puzzle 1: oil into oil_lamp  (fuelling)
      - Puzzle 3: water into stone_basin  (ring-activated reveal)
      - Generic: any liquid into any open container
    """
    import engine as _engine

    vessel_ent = world.entity(vessel_eid)
    source_ent = world.entity(source_eid)
    liquid = source_ent.props.get("liquid")

    # ── Puzzle 1: oil into the lamp ──────────────────────────────────
    if vessel_eid == "oil_lamp" and liquid == "oil":
        if vessel_ent.props.get("fuelled", False):
            return "The lamp is already full of oil.", False

        vessel_ent.props["fuelled"] = True
        source_ent.props["empty"] = True
        world.note_ref([vessel_eid, source_eid])
        return (
            "You carefully tip the flask. Oil flows into the lamp's "
            "reservoir with a quiet glug. The flask is now empty."
        ), True

    if "lightable" in vessel_ent.tags:
        return "That doesn't take that kind of liquid.", False

    # ── Puzzle 3: water into stone basin ─────────────────────────────
    if vessel_eid == "stone_basin" and liquid == "water":
        if vessel_ent.props.get("activated", False):
            return ("The basin already holds the water. The serpents "
                    "are still."), False

        ring = world.entities.get("silver_ring")
        ring_worn = (
            ring is not None
            and ring.location == "player"
            and ring.props.get("worn", False)
        )

        if not ring_worn:
            vessel_ent.props["liquid"] = "water"
            source_ent.props["empty"] = True
            world.note_ref([vessel_eid, source_eid])
            return (
                "You pour the water into the basin. It sits there, cold "
                "and still. The serpent carvings seem to watch, "
                "unimpressed. Perhaps something is missing."
            ), True

        # Ring worn — trigger the puzzle payoff.
        vessel_ent.props["activated"] = True
        vessel_ent.props["liquid"] = "water"
        source_ent.props["empty"] = True
        move_entity(world, "jeweled_amulet", "stone_basin")
        world.note_ref([vessel_eid, source_eid, "jeweled_amulet"])
        _engine.SCORE_TRACKER.award("basin_activated")
        return (
            "The moment the water touches the basin, the ring on your "
            "finger grows warm. The carved serpents seem to writhe — a "
            "trick of the light, surely — and the water begins to glow "
            "with a faint green luminescence.\n\n"
            "Something rises from beneath the water: a heavy amulet of "
            "green-black stone, bone dry despite the water around it. "
            "It comes to rest at the basin's rim as if placed there by "
            "an invisible hand."
        ), True

    # ── Generic: pour any liquid into any open container ─────────────
    vessel_ent.props["liquid"] = liquid
    source_ent.props["empty"] = True
    world.note_ref([vessel_eid, source_eid])
    return narrate(["Done.", f"You pour the {liquid} in."]), True


# ── Pull ─────────────────────────────────────────────────────────────────

@register("pull")
def handle_pull(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Pull a 'pullable'-tagged entity.

    Puzzle hooks:
      - stone_stag antler -> drops display_key  (Puzzle 2)
      - cellar_lever      -> opens hall passage  (Puzzle 1)
    """
    import engine as _engine

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

    # ── Puzzle 2: stone stag antler ──────────────────────────────────
    if obj == "stone_stag":
        if ent.props.get("pulled", False):
            return "You already pulled it. Nothing more falls.", False

        ent.props["pulled"] = True
        move_entity(world, "display_key", "trophy_room")
        world.note_ref([obj, "display_key"])

        return (
            "You grab the heavy antler and pull hard. With a grinding "
            "crack, it swings downward on a hidden pivot — and a small "
            "tarnished key drops from inside the stag's hollow neck, "
            "clattering to the floor at your feet."
        ), True

    # ── Puzzle 1: cellar lever ───────────────────────────────────────
    if obj == "cellar_lever":
        if not player_has_lit_lamp(world):
            return (
                "You can sense something in the darkness, but you can't "
                "make it out. You'd need a light source to work with it "
                "safely."
            ), False

        if ent.props.get("pulled", False):
            return ("You've already pulled it. The passage stands open."
                    ), False

        ent.props["pulled"] = True

        world.rooms["hall_3"].exits["down"]       = "cellar_passage"
        world.rooms["cellar_passage"].exits["up"]  = "hall_3"
        world.rooms["cellar"].exits["north"]       = "cellar_passage"
        _engine.SCORE_TRACKER.award("kitchen_reached")

        world.rooms["hall_3"].desc_alt = (
            "The northernmost reach of the hall. The air here is colder "
            "and the portraits have given way to mounted weapons and "
            "shields. A heavy door to the west stands open into the "
            "trophy room. A narrow stone staircase descends in the "
            "north wall — the brickwork that once sealed it lies in a "
            "heap on the floor."
        )
        world.note_ref([obj])

        return (
            "You haul on the iron lever with both hands. Somewhere deep "
            "in the wall, something heavy shifts. A low rumble travels "
            "through the stone, climbing upward through the floor and "
            "into your boots. From somewhere above — distant, muffled "
            "by the intervening rock — comes the groan of something "
            "large moving that has not moved in a very long time."
        ), True

    # ── Generic pullable ─────────────────────────────────────────────
    world.note_ref([obj])
    return ("You pull it. Something shifts, but nothing dramatic "
            "happens."), True


# ── Fill ─────────────────────────────────────────────────────────────────

@register("fill")
def handle_fill(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Fill a vessel from a liquid source.

    Canonical syntax: FILL <vessel> WITH <source>
    Also accepts reversed pour-style framing.
    """
    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Fill what?", False
    if obj not in world.entities:
        return "You don't see that here.", False

    visible = visible_entities_for_room(world)

    obj_ent  = world.entity(obj)
    iobj_ent = (world.entity(iobj)
                if iobj and iobj in world.entities else None)

    # Detect reversed framing: "fill [source] into [vessel]"
    if (iobj_ent is not None
            and "liquid_source" in obj_ent.tags
            and "liquid_source" not in iobj_ent.tags):
        obj, iobj = iobj, obj
        obj_ent, iobj_ent = iobj_ent, obj_ent

    vessel_eid = obj
    source_eid = iobj

    vessel_ent = world.entity(vessel_eid)
    vessel_visible = vessel_eid in visible_entities_for_room(world)
    if (vessel_eid not in world.player.inventory
            and not vessel_visible):
        return "You don't see that here.", False
    if ("portable" in vessel_ent.tags
            and vessel_eid not in world.player.inventory):
        return "You'd need to be holding it to fill it.", False

    if not source_eid:
        return "You need to specify what to fill it with.", False
    if source_eid not in world.entities:
        return "You don't have that.", False

    source_ent = world.entity(source_eid)
    if (source_eid not in world.player.inventory
            and source_eid not in visible):
        return "You don't have that.", False

    if "liquid_source" not in source_ent.tags:
        return "That's not something you can pour from.", False

    if source_ent.props.get("empty", False):
        return "It's empty — there's nothing left to pour.", False

    return _apply_liquid_to_vessel(world, vessel_eid, source_eid)


# ── Pour ─────────────────────────────────────────────────────────────────

@register("pour")
def handle_pour(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Pour liquid from a carried vessel into a target container.

    Syntax: POUR <source> INTO <target>
    """
    obj  = ir.get("obj")   # the thing being poured (source)
    iobj = ir.get("iobj")  # the target container

    if not obj:
        return "Pour what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You aren't holding that.", False

    source_ent = world.entity(obj)

    if ("liquid_source" not in source_ent.tags
            and "container" not in source_ent.tags):
        return "There's nothing to pour from that.", False

    if source_ent.props.get("empty", False):
        return "It's empty — there's nothing left to pour.", False

    if not iobj:
        return "Pour it into what?", False
    if iobj not in world.entities:
        return "You don't see that here.", False

    visible = visible_entities_for_room(world)
    if iobj not in world.player.inventory and iobj not in visible:
        return "You don't see that here.", False

    target_ent = world.entity(iobj)

    if "lightable" in target_ent.tags:
        return _apply_liquid_to_vessel(world, iobj, obj)

    if "container" not in target_ent.tags:
        return "You can't pour things into that.", False

    return _apply_liquid_to_vessel(world, iobj, obj)
