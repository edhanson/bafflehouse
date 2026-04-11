# handlers/npc_actions.py
#
# Handlers for NPC interaction verbs: pet, feed, offer, call, say,
# answer.  These route through to the NPC behaviour layer in npc.py.

from typing import Tuple

from model import World
from handlers.registry import register
from handlers.helpers import narrate


@register("pet")
def handle_pet(world: World, ir: dict) -> Tuple[str, bool]:
    """Pet / stroke a living NPC."""
    import engine as _engine
    from npc import handle_pet_npc

    obj = ir.get("obj")
    if not obj:
        return "Pet what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    ent = world.entity(obj)
    if "npc" not in ent.tags:
        return (f"You give {ent.name} an affectionate pat. "
                "Nothing happens."), True
    npcs = _engine.get_npc_instances(world)
    npc  = npcs.get(obj)
    if not npc:
        return "You don't see that here.", False
    return handle_pet_npc(world, npc, _engine.NPC_MEMORY)


@register("feed")
def handle_feed(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Feed an NPC a food item.  Expects obj=NPC, iobj=food item.
    Also accepts 'feed cat' with a single food item in inventory.
    """
    import engine as _engine
    from npc import handle_feed_npc

    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj:
        return "Feed what?", False

    target_eid = None
    food_eid   = None

    if obj and obj in world.entities:
        ent = world.entity(obj)
        if "npc" in ent.tags:
            target_eid = obj
            food_eid   = iobj
        elif "food" in ent.tags or "catnip" in ent.tags:
            food_eid   = obj
            target_eid = iobj

    if not target_eid:
        return "Feed what to whom?", False

    if not food_eid:
        food_candidates = [
            eid for eid in world.player.inventory
            if "food" in world.entity(eid).tags
            or "catnip" in world.entity(eid).tags
        ]
        if not food_candidates:
            return "You aren't carrying anything to feed it.", False
        if len(food_candidates) > 1:
            return ("What do you want to feed it — be specific."
                    ), False
        food_eid = food_candidates[0]

    npcs = _engine.get_npc_instances(world)
    npc  = npcs.get(target_eid)
    if not npc:
        return "You don't see that here.", False

    return handle_feed_npc(world, npc, _engine.NPC_MEMORY, food_eid)


@register("offer")
def handle_offer(world: World, ir: dict) -> Tuple[str, bool]:
    """Offer an item to an NPC — builds trust without consuming it."""
    import engine as _engine
    from npc import handle_offer_npc

    obj  = ir.get("obj")
    iobj = ir.get("iobj")

    if not obj or not iobj:
        return "Offer what to whom?", False

    if obj in world.entities and "npc" in world.entity(obj).tags:
        target_eid = obj
        item_eid   = iobj
    elif iobj in world.entities and "npc" in world.entity(iobj).tags:
        target_eid = iobj
        item_eid   = obj
    else:
        return "Offer it to what?", False

    npcs = _engine.get_npc_instances(world)
    npc  = npcs.get(target_eid)
    if not npc:
        return "You don't see that here.", False

    return handle_offer_npc(world, npc, _engine.NPC_MEMORY, item_eid)


@register("say")
def handle_say(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Say something.  Routes to handle_call for NPC targets.
    At the bridge, routes to handle_answer.
    """
    obj = ir.get("obj")

    if world.player.location == "bridge":
        return handle_answer(world, ir)

    if obj and obj in world.entities:
        ent = world.entity(obj)
        if "npc" in ent.tags:
            return handle_call(world, ir)

    return narrate([
        "You speak into the silence. Nothing responds.",
        "Your words hang in the air and dissolve.",
        "You say something. The room is not impressed.",
    ]), True


@register("call")
def handle_call(world: World, ir: dict) -> Tuple[str, bool]:
    """Call out to or speak to an NPC."""
    import engine as _engine
    from npc import handle_call_npc

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
    npcs = _engine.get_npc_instances(world)
    npc  = npcs.get(obj)
    if not npc:
        return "You don't see that here.", False
    return handle_call_npc(world, npc, _engine.NPC_MEMORY)


@register("answer")
def handle_answer(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Player answers the troll's riddle.

    If the bridge is newly opened, the east exit is added to the
    bridge room and the vault is revealed in the cellar.
    """
    import engine as _engine
    from troll import handle_troll_answer

    if world.player.location != "bridge":
        return "There is nothing here to answer.", False

    answer_text = ir.get("obj") or ir.get("raw") or ""
    if not answer_text:
        return "Answer what, exactly?", False

    state = _engine.TROLL_MEMORY.state()
    response, correct = handle_troll_answer(state, str(answer_text))

    if state.bridge_open and "east" not in world.rooms["bridge"].exits:
        world.rooms["bridge"].exits["east"] = "bridge_far_bank"
        _engine.SCORE_TRACKER.award("troll_solved")
        if "south" not in world.rooms["cellar"].exits:
            world.rooms["cellar"].exits["south"] = "vault"
        vault_door = world.entities.get("vault_door")
        if vault_door:
            vault_door.location = "cellar"
            if "vault_door" not in world.rooms["cellar"].entities:
                world.rooms["cellar"].entities.append("vault_door")
        response = (
            response
            + "\n\nFrom somewhere deep in the manor you hear a heavy "
            + "crashing sound, followed by an eerie silence. An "
            + "overwhelming sense of dread settles over you."
        )

    _engine.TROLL_MEMORY.save()
    return response, True
