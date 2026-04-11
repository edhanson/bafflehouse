# handlers/combat_actions.py
#
# Combat-related handlers: attack, block, rest.
# Also contains _execute_combat_action and the golem tick logic.

import random as _random
from typing import Optional, Tuple

from model import World
from handlers.registry import register
from handlers.helpers import (
    move_entity_to,
    narrate,
    require_visible,
)


# ── Combat helpers ───────────────────────────────────────────────────────

def get_player_weapon_id(world: World) -> str:
    """Return the entity id of the wielded weapon, or 'bare_hands'."""
    wid = world.player.wielded_weapon
    if wid and wid in world.entities:
        return wid
    return "bare_hands"


def get_jasper_present(world: World) -> bool:
    """Return True if a devoted Jasper is in the same room as the player."""
    import engine as _engine
    if "jasper" not in world.entities:
        return False
    jasper_ent = world.entities["jasper"]
    if jasper_ent.location != world.player.location:
        return False
    disp = _engine.NPC_MEMORY.disposition("jasper")
    return disp == "devoted"


def execute_combat_action(world: World, action: str) -> str:
    """
    Process one player combat action and return the narrative.
    Handles session lifecycle: death, victory, and flight.
    """
    import engine as _engine
    from combat import process_player_combat_action, _combat_prompt

    session = _engine._COMBAT_SESSION
    if session is None:
        return "You are not in combat."

    learner = _engine.COMBAT_MEMORY.learner("slime_golem")

    # Refresh Jasper and amulet state each round
    session.update_jasper(get_jasper_present(world))
    session.wearing_amulet = (
        "jeweled_amulet" in world.player.worn_armour
    )

    narrative, outcome = process_player_combat_action(
        session      = session,
        player_input = action,
        learner      = learner,
    )
    _engine.COMBAT_MEMORY.save()

    # Sync player vitals
    world.player.hp      = session.player_hp
    world.player.stamina = session.player_stamina

    # Ring regen during combat
    regen_lines = []
    if outcome == "continue":
        for eid in world.player.worn_armour:
            ent = world.entities.get(eid)
            if not ent:
                continue
            regen = ent.props.get("hp_regen", 0)
            if regen and world.player.hp < world.player.max_hp:
                world.player.hp = min(
                    world.player.max_hp, world.player.hp + regen
                )
                session.player_hp = world.player.hp
                display = ent.name
                for article in ("a ", "an ", "the "):
                    if display.lower().startswith(article):
                        display = display[len(article):]
                        break
                regen_lines.append(
                    f"{display.capitalize()} pulses warmly. "
                    f"(HP +{regen})"
                )

    if regen_lines:
        regen_text = "\n".join(regen_lines)
        if "\n\n" in narrative:
            body, prompt_block = narrative.rsplit("\n\n", 1)
            narrative = body + "\n" + regen_text + "\n\n" + prompt_block
        else:
            narrative = narrative + "\n" + regen_text

    # Write golem HP back to entity
    golem = world.entities.get("slime_golem")
    if golem:
        golem.props["hp"] = session.golem_hp

    if outcome == "player_dead":
        _engine._COMBAT_SESSION = None

    elif outcome == "golem_dead":
        _engine._COMBAT_SESSION = None
        _engine.SCORE_TRACKER.award("golem_defeated")
        if golem:
            golem.props["alive"] = False
            golem.props["hp"]    = 0
            move_entity_to(world, "slime_golem", "hidden")
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
        _engine._COMBAT_SESSION = None
        if golem and golem.props.get("alive", True):
            regen_amt = max(
                1, int(golem.props.get("max_hp", 120) * 0.10)
            )
            golem.props["hp"] = min(
                golem.props.get("max_hp", 120),
                golem.props.get("hp", 120) + regen_amt,
            )
        current = world.player.location
        room = world.rooms.get(current)
        if room:
            for dest in room.exits.values():
                if dest != current:
                    world.player.location = dest
                    break

    return narrative


# ── Golem tick ───────────────────────────────────────────────────────────

# Rooms the golem cannot enter (too big / wrong terrain)
_GOLEM_FORBIDDEN = {
    "upstairs_landing", "bedroom_east", "bedroom_west",
    "forest_edge", "forest_a", "forest_b",
    "forest_c", "forest_d",
}


def golem_tick(world: World, golem, player_moved: bool) -> None:
    """
    Advance the golem one tick:
    - If aware and not in combat, pursue the player.
    - If not aware, check adjacent rooms for the player (smell).
    - Otherwise wander randomly with 30% probability.
    """
    player_room = world.player.location
    golem_room  = golem.location

    def _allowed_exits(room_id: str):
        room = world.rooms.get(room_id)
        if not room:
            return []
        return [dest for dest in room.exits.values()
                if dest not in _GOLEM_FORBIDDEN
                and dest in world.rooms]

    adjacent = _allowed_exits(golem_room)
    if player_room in adjacent and not golem.props.get("aware"):
        golem.props["aware"] = True

    if golem.props.get("aware") and golem_room != player_room:
        if player_room in adjacent:
            move_entity_to(world, "slime_golem", player_room)
        else:
            if adjacent:
                dest = _random.choice(adjacent)
                move_entity_to(world, "slime_golem", dest)
        return

    if golem_room == player_room:
        golem.props["aware"] = True
        return
    if _random.random() < 0.30 and adjacent:
        dest = _random.choice(adjacent)
        move_entity_to(world, "slime_golem", dest)


# ── Verb handlers ────────────────────────────────────────────────────────

@register("attack")
def handle_attack(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Attack an entity.  Routes to combat for hostile targets,
    trust penalty for NPC targets, flavour text for others.
    """
    import engine as _engine
    from combat import CombatSession, start_combat

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
        if _engine._COMBAT_SESSION is not None:
            return execute_combat_action(world, "attack"), True
        golem = world.entities.get("slime_golem")
        if not golem or not golem.props.get("alive", True):
            return "The golem is already dead.", False
        _engine._COMBAT_SESSION = CombatSession(
            player_hp      = world.player.hp,
            player_max_hp  = world.player.max_hp,
            player_stamina = world.player.stamina,
            golem_hp       = golem.props.get("hp", 120),
            golem_max_hp   = golem.props.get("max_hp", 120),
        )
        weapon_id      = get_player_weapon_id(world)
        wearing_coif   = "chain_coif"     in world.player.worn_armour
        wearing_shield = "kite_shield"    in world.player.worn_armour
        wearing_amulet = "jeweled_amulet" in world.player.worn_armour
        jasper_present = get_jasper_present(world)
        opening = start_combat(
            _engine._COMBAT_SESSION, weapon_id, wearing_coif,
            wearing_shield, jasper_present,
            wearing_amulet=wearing_amulet,
        )
        world.player.hp = _engine._COMBAT_SESSION.player_hp
        return opening, True

    if "npc" in ent.tags:
        _engine.NPC_MEMORY.record(obj, "player_struck")
        npcs = _engine.get_npc_instances(world)
        npc  = npcs.get(obj)
        if npc:
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

    return f"You can't attack {ent.name}.", False


@register("block")
def handle_block(world: World, ir: dict) -> Tuple[str, bool]:
    """Block — only meaningful during combat."""
    import engine as _engine
    if _engine._COMBAT_SESSION is not None:
        return execute_combat_action(world, "block"), True
    return "There is nothing to block.", False


@register("rest")
def handle_rest(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Rest for a turn — recovers stamina.
    Cannot be used during active combat.
    """
    import engine as _engine
    if _engine._COMBAT_SESSION is not None:
        return "There is no time to rest.", False

    STAMINA_REST = 25
    before = world.player.stamina
    world.player.stamina = min(
        world.player.max_stamina, world.player.stamina + STAMINA_REST
    )
    gained = world.player.stamina - before

    if gained == 0:
        return "You are already at full stamina.", False

    return narrate([
        f"You take a moment to catch your breath. (Stamina +{gained})",
        f"You lean against the wall and breathe slowly. "
        f"(Stamina +{gained})",
        f"You rest briefly. (Stamina +{gained})",
    ]), True
