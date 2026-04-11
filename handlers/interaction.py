# handlers/interaction.py
#
# Interaction handlers for examining, reading, lighting, and consuming
# entities.  These are the "general purpose" verbs that don't involve
# puzzle-specific state machines or inventory transfers.

from typing import Optional, Tuple

from model import World
from handlers.registry import register
from handlers.helpers import (
    narrate,
    phrase_in_room_text,
    player_has_lit_lamp,
    require_visible,
    visible_entities_for_room,
)


# ── Examine ──────────────────────────────────────────────────────────────

@register("examine")
def handle_examine(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Describe an entity in detail.

    Also enforces the dark-cellar mechanic: light-requiring entities
    return a "too dark" message unless the player has a lit lamp.
    """
    import engine as _engine

    obj = ir.get("obj")

    if not obj:
        return "Examine what?", False
    if obj not in world.entities:
        if phrase_in_room_text(world, str(obj)):
            phrase = str(obj).strip().rstrip(".")
            last_word = phrase.split()[-1] if phrase.split() else phrase
            _singular_endings = ("ss", "us", "is", "ness", "ess", "ous")
            is_plural = (
                last_word.endswith("s")
                and not any(last_word.endswith(e) for e in _singular_endings)
            )
            pronoun = "them" if is_plural else "it"
            return f"You notice nothing special about {pronoun}.", True
        return "You don't see that here.", False

    err = require_visible(world, obj)
    if err:
        return err, False

    ent = world.entity(obj)
    world.note_ref([obj])

    # ── Troll examine ────────────────────────────────────────────────
    from troll import TROLL_EXAMINE
    if obj == "troll":
        state = _engine.TROLL_MEMORY.state()
        if state.bridge_open:
            key = "bridge_open"
        elif state.correct_count > 0:
            key = "in_progress"
        else:
            key = "not_started"
        return TROLL_EXAMINE.get(key, "You see a troll."), False

    # ── NPC examine ──────────────────────────────────────────────────
    if "npc" in ent.tags:
        disposition = _engine.NPC_MEMORY.disposition(obj)
        npc_examine_suffix = {
            "cautious": (
                " It is watching the exits as much as it is watching you."
            ),
            "wary": (
                " It holds its ground but keeps you at a measured distance. "
                "It seems to be waiting to see what you do next."
            ),
            "neutral": (
                " It seems prepared to tolerate your presence, at least "
                "for now. It shows mild interest in what you're carrying."
            ),
            "friendly": (
                " It is watching you with open curiosity, tail moving "
                "slowly."
            ),
            "devoted": (
                " It stays close, alert to everything around you both."
            ),
        }
        suffix = npc_examine_suffix.get(disposition, "")
        base = ent.props.get("desc", "You see nothing special.")
        return base + suffix, True

    # ── Catnip reveal (garden hedges) ────────────────────────────────
    if obj == "garden_hedges" and "catnip" in world.entities:
        catnip = world.entities["catnip"]
        if not catnip.props.get("visible", False):
            catnip.props["visible"] = True
            catnip.location = world.player.location
            room = world.rooms.get(world.player.location)
            if room and "catnip" not in room.entities:
                room.entities.append("catnip")

    # ── State-aware description selection ────────────────────────────
    _is_empty_liquid = (ent.props.get("empty", False)
                        and "desc_empty" in ent.props)
    _is_empty_solid = (
        "container" in ent.tags
        and ent.props.get("open", False)
        and not ent.contains
        and not (ent.props.get("liquid")
                 and not ent.props.get("empty", False))
        and "desc_empty" in ent.props
    )
    _is_closed_empty = (
        "container" in ent.tags
        and not ent.props.get("open", False)
        and not ent.contains
        and "desc_closed_empty" in ent.props
    )

    if _is_empty_liquid or _is_empty_solid:
        desc = ent.props["desc_empty"]
    elif _is_closed_empty:
        desc = ent.props["desc_closed_empty"]
    elif ent.props.get("opened", False) and "desc_opened" in ent.props:
        desc = ent.props["desc_opened"]
    elif ("desc_open" in ent.props
          and world.rooms.get(world.player.location) is not None
          and world.rooms[world.player.location].exits.get("north")
              == "cellar"):
        desc = ent.props["desc_open"]
    elif obj == "stone_basin":
        if ent.props.get("activated") and "desc_activated" in ent.props:
            desc = ent.props["desc_activated"]
        elif ent.props.get("liquid") and "desc_water" in ent.props:
            desc = ent.props["desc_water"]
        else:
            desc = ent.props.get("desc", "You see nothing special.")
    elif obj == "oil_lamp":
        if ent.props.get("lit") and "desc_lit" in ent.props:
            desc = ent.props["desc_lit"]
        elif ent.props.get("fuelled") and "desc_fuelled" in ent.props:
            desc = ent.props["desc_fuelled"]
        else:
            desc = ent.props.get("desc", "You see nothing special.")
    else:
        desc = ent.props.get("desc", "You see nothing special.")

    lines = [desc]

    if "openable" in ent.tags:
        lines.append(
            "It is open." if ent.props.get("open", False)
            else "It is closed."
        )

    if "container" in ent.tags and ent.props.get("open", False):
        if ent.contains:
            contents = ", ".join(
                world.entity(cid).name for cid in ent.contains
            )
            lines.append(f"It contains {contents}.")

        liquid = ent.props.get("liquid")
        if liquid and not ent.props.get("empty", False):
            lines.append(f"It contains {liquid}.")
        elif not ent.contains and (not liquid
                                   or ent.props.get("empty", False)):
            lines.append("It is empty.")

    if "lightable" in ent.tags:
        if ent.props.get("lit", False):
            lines.append("It is burning steadily.")
        elif ent.props.get("fuelled", False):
            lines.append("It is filled with oil but unlit.")
        else:
            lines.append("It is empty of fuel.")

    if "wearable" in ent.tags:
        if ent.props.get("worn", False):
            lines.append("You are wearing it.")

    if "fire_source" in ent.tags:
        n = ent.props.get("matches_remaining", 0)
        if n == 0:
            lines.append("The box is empty. No matches remain.")
        elif n == 1:
            lines.append("One match remains.")
        else:
            lines.append(f"{n} matches remain.")

    return "\n".join(lines), True


# ── Read ─────────────────────────────────────────────────────────────────

@register("read")
def handle_read(world: World, ir: dict) -> Tuple[str, bool]:
    """Read a 'readable'-tagged entity."""
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

    has_lens = any(
        "lens" in world.entity(eid).tags or eid == "magnifying_glass"
        for eid in world.player.inventory
        if eid in world.entities
    )
    if has_lens and "readable_text_magnified" in ent.props:
        text = (
            "You hold the magnifying glass over the small text. "
            "The cramped letters resolve into legibility.\n\n"
            + ent.props["readable_text_magnified"]
        )
    else:
        text = ent.props.get("readable_text", "")

    if not text:
        return "The writing is too faded to make out.", False

    world.note_ref([obj])
    return text, True


# ── Light / Extinguish ───────────────────────────────────────────────────

def _find_fire_source(world: World) -> Optional[str]:
    """Return the eid of a usable fire source in inventory, or None."""
    for eid in world.player.inventory:
        ent = world.entity(eid)
        if ("fire_source" in ent.tags
                and ent.props.get("matches_remaining", 0) > 0):
            return eid
    return None


@register("light")
def handle_light(world: World, ir: dict) -> Tuple[str, bool]:
    """Light a lightable entity."""
    import engine as _engine

    obj = ir.get("obj")

    if not obj:
        return "Light what?", False
    if obj not in world.entities:
        return "You don't see that here.", False
    if obj not in world.player.inventory:
        return "You'd need to be holding it to light it.", False

    ent = world.entity(obj)

    # Striking the matchbox itself
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
        return ("It has no fuel. You'll need to fill it with oil first."
                ), False

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
    pts = _engine.SCORE_TRACKER.award("lamp_lit")
    if pts:
        msg += "\n" + pts
    return msg, True


@register("extinguish")
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
    return narrate([
        "The flame dies.", "Darkness rushes back in.", "Extinguished."
    ]), True


# ── Push ─────────────────────────────────────────────────────────────────

@register("push")
def handle_push(world: World, ir: dict) -> Tuple[str, bool]:
    """Push a 'pushable'-tagged entity."""
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


# ── Drink / Eat ──────────────────────────────────────────────────────────

@register("drink")
def handle_drink(world: World, ir: dict) -> Tuple[str, bool]:
    """Attempt to drink something."""
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
        return f"You drink {ent.name}.", True

    if "liquid_source" in ent.tags:
        liquid = ent.props.get("liquid", "")
        if ent.props.get("empty", False):
            return "There's nothing left to drink.", False
        if liquid == "water":
            return narrate([
                "You sip from the clay ewer. The water is stale and "
                "stagnant — it hasn't moved in ages. You gag and stop.",
                "You tip the ewer to your lips. The water tastes of old "
                "clay and something you'd rather not identify. Not worth "
                "it.",
            ]), True
        return f"Drinking {ent.name} would be a terrible idea.", False

    if "liquid" in ent.tags or ent.props.get("liquid"):
        return f"Drinking {ent.name} would be a terrible idea.", False

    if "living" in ent.tags or "npc" in ent.tags:
        return "That's not something you can drink.", False

    if "portable" not in ent.tags and "container" not in ent.tags:
        return "You can't drink that.", False

    return "That's not something you can drink.", False


@register("eat")
def handle_eat(world: World, ir: dict) -> Tuple[str, bool]:
    """Attempt to eat something."""
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
        return (
            f"You consider eating {ent.name}. "
            "Better to save it for a more pressing need."
        ), False

    if "scenery" in ent.tags:
        return "You can't eat that.", False

    return "That doesn't look edible.", False
