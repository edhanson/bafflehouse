# engine.py
#
# Game logic orchestrator — input processing, combat lifecycle, NPC ticks.
#
# Architecture (post-refactor):
#   - All verb handlers live in the handlers/ package, registered via
#     @register decorators.  Adding a new verb never touches this file.
#   - This module owns: module-level singletons (NPC_MEMORY, COMBAT_MEMORY,
#     TROLL_MEMORY, etc.), the main process_input loop, combat session
#     lifecycle management, NPC/golem tick orchestration, and the
#     archway/win-state check.
#   - exec_action() is now a single-line dispatch via get_handler().

import random
import re
from typing import Callable, Dict, List, Optional, Tuple

from ir import clarify_ir
from model import World
from npc import NPC, NPC_REGISTRY, npc_tick
from troll import TrollMemory, troll_encounter
from npc_qlearning import CombatMemory
from combat import (
    CombatSession, start_combat, golem_action_round, _combat_prompt,
)
from npc_bayesian import NPCMemory
from savegame import save_game
from scoring import TRACKER as SCORE_TRACKER
from parser import (
    DIRECTIONS,
    ParserSystem,
    expand_coordinated_objects,
    ground_intent,
    normalize,
    parse_to_candidates,
    split_compound,
)

# Import the handler package — this triggers registration of all verbs.
from handlers import get_handler

# Re-export helpers that other modules (main.py, savegame.py, tests)
# may still import from engine.
from handlers.helpers import (
    move_entity,
    move_entity_to as _move_entity_to,
    narrate,
    phrase_in_room_text,
    player_has_lit_lamp,
    require_visible,
    visible_entities_for_room,
)
from handlers.movement import do_look, traverse_door
from handlers.inventory import do_inventory, do_status
from handlers.combat_actions import (
    execute_combat_action as _execute_combat_action,
    get_jasper_present as _get_jasper_present,
    get_player_weapon_id as _get_player_weapon_id,
    golem_tick as _golem_tick,
)


# ============================================================
# Module-level singletons
# ============================================================

NPC_MEMORY      = NPCMemory("./npc_memory.json")
TROLL_MEMORY    = TrollMemory()
COMBAT_MEMORY   = CombatMemory()
_COMBAT_SESSION: Optional[CombatSession] = None
_GAME_WON: bool = False

# Win narrative — used by handle_enter in handlers/movement.py
_WIN_NARRATIVE = (
    "You step through the portal.\n\n"
    "The light is warm, and for a moment everything is white and "
    "weightless and perfectly still.\n\n"
    "Then it passes, and you are somewhere else entirely — somewhere "
    "familiar, somewhere that smells of coffee and cold air and "
    "ordinary life. The archway is gone. The manor is gone. "
    "Whatever the Bafflehouse was, and whatever brought you there, "
    "it has released you.\n\n"
    "You are home."
)

# Register Jasper's custom event table before any reputation is loaded.
from npc import JASPER_EVENTS
NPC_MEMORY.register_events("jasper", JASPER_EVENTS)

# Runtime NPC instances — keyed by npc_id.
_NPC_INSTANCES: dict = {}


def get_npc_instances(world: World) -> dict:
    """
    Return (creating if absent) the runtime NPC instances for the world.
    """
    global _NPC_INSTANCES
    if not _NPC_INSTANCES:
        for npc_id, defn in NPC_REGISTRY.items():
            _NPC_INSTANCES[npc_id] = NPC(
                defn     = defn,
                location = defn.start_room,
            )
            if npc_id in world.entities:
                world.entities[npc_id].location = defn.start_room
            start_room = world.rooms.get(defn.start_room)
            if start_room and npc_id not in start_room.entities:
                start_room.entities.append(npc_id)
    return _NPC_INSTANCES


# ============================================================
# Output helpers
# ============================================================

def format_clarification(world: World, clar: dict) -> str:
    """Format a clarification prompt with numbered entity names."""
    lines = [clar["question"], ""]
    for i, eid in enumerate(clar["options"], start=1):
        lines.append(f"{i}) {world.entity(eid).name}")
    lines.append("")
    lines.append("Please reply with a number or a short name.")
    return "\n".join(lines)


# ============================================================
# Action dispatch — now a single registry lookup
# ============================================================

def exec_action(world: World, ir: dict) -> Tuple[str, bool]:
    """Dispatch a grounded action IR to the registered handler."""
    if ir.get("type") != "action":
        return "Nothing happens.", False

    verb = ir.get("verb")
    handler = get_handler(str(verb))
    if handler is None:
        return "Nothing happens.", False

    return handler(world, ir)


# ============================================================
# Archway activation check
# ============================================================

def _check_archway_activation(world: World) -> Optional[str]:
    """
    Check whether the player has arrived at bridge_far_bank with all
    three magical artifacts.  If so, activate the stone archway and
    reveal the home portal.
    """
    if world.player.location != "bridge_far_bank":
        return None

    archway = world.entities.get("stone_archway")
    if not archway or archway.props.get("active", False):
        return None

    all_items = set(world.player.inventory) | set(world.player.worn_armour)
    required  = {"silver_ring", "jeweled_amulet", "secret_treasure"}
    if not required.issubset(all_items):
        return None

    archway.props["active"] = True
    portal = world.entities.get("home_portal")
    if portal:
        portal.location = "bridge_far_bank"
        room = world.rooms.get("bridge_far_bank")
        if room and "home_portal" not in room.entities:
            room.entities.append("home_portal")

    return (
        "The moment you step into the clearing, the serpent carvings on "
        "the archway begin to glow. The ring on your finger grows warm — "
        "the amulet at your chest pulses in answer — and the strange "
        "metallic object in your pocket vibrates with a low hum.\n\n"
        "The archway fills with light. A portal opens.\n\n"
        "Beyond it, unmistakably, is home."
    )


# ============================================================
# Clarification resolution
# ============================================================

def resolve_clarification(world: World, clar: dict,
                          user_reply: str) -> dict:
    """
    Resolve a pending clarification by matching the user's reply
    to one of the offered entity options (by number or by name).
    """
    options = clar["options"]
    reply = normalize(user_reply)

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

    for eid in options:
        ent = world.entity(eid)
        if reply in ent.all_names() or reply == ent.name.lower():
            pending = dict(clar["pending"])
            if pending.get("obj") not in world.entities:
                pending["obj"] = eid
            else:
                pending["iobj"] = eid
            return pending

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
    pending_clarify: Optional[dict],
) -> Tuple[str, Optional[dict]]:
    """
    Process one line of player input.

    If a clarification is pending, resolve it first.  Otherwise,
    split compound commands, parse each segment, ground entity
    references, and dispatch to the appropriate handler.

    Returns (output_text, new_pending_clarification_or_None).
    """
    global _COMBAT_SESSION

    # ── Resolve a pending clarification ──────────────────────────────
    if pending_clarify is not None:
        grounded = resolve_clarification(world, pending_clarify, text)

        if grounded["type"] == "clarify":
            return format_clarification(world, grounded), grounded

        parser_system.semantic_entity_index.rebuild_for_visible(world)
        fully_grounded = ground_intent(
            world=world,
            intent=grounded,
            semantic_index=parser_system.semantic_entity_index,
        )

        if fully_grounded.get("type") == "clarify":
            return (format_clarification(world, fully_grounded),
                    fully_grounded)

        out, consumed = exec_action(world, fully_grounded)
        if consumed:
            world.clock.advance(1)
        return out, None

    # ── Combat intercept ─────────────────────────────────────────────
    _equip_round = False
    if _COMBAT_SESSION is not None:
        normalised_input = normalize(text)
        if normalised_input in {"look", "l", "inventory", "inv", "i"}:
            pass  # allow look/inv during combat
        elif normalised_input == "save":
            from handlers.meta import handle_save
            return handle_save(world, {})[0], None
        elif normalised_input in {"rest", "z", "zz", "wait"}:
            return "There is no time to rest.", None
        elif normalised_input == "status":
            return do_status(world), None
        elif any(normalised_input.startswith(v) for v in
                 ("wield ", "wear ", "remove ", "take off ",
                  "unequip ")):
            _equip_round = True
        else:
            narrative = _execute_combat_action(world, normalised_input)
            world.clock.advance(1)
            return narrative, None

    # ── Split compound commands ──────────────────────────────────────
    segments = split_compound(text)
    if not segments:
        return "Say something.", None

    expanded_segments: List[str] = []
    for seg in segments:
        expanded_segments.extend(expand_coordinated_objects(seg))

    outputs: List[str]  = []
    any_consumed    = False
    player_moved    = False
    location_before = world.player.location

    for seg in expanded_segments:
        parser_system.semantic_entity_index.rebuild_for_visible(world)
        parser_system._current_world = world
        candidates = parse_to_candidates(
            seg, parser_system=parser_system
        )
        parser_system._current_world = None

        if not candidates:
            outputs.append("I beg your pardon?")
            continue

        intent = candidates[0]

        if intent["type"] == "missing_verb":
            outputs.append(
                "Your command couldn't be interpreted. "
                "(Did you mean something else?)"
            )
            continue

        if intent["type"] == "meta":
            if intent["verb"] == "look":
                outputs.append(do_look(world))
            elif intent["verb"] == "inventory":
                outputs.append(do_inventory(world))
            elif intent["verb"] == "save":
                from handlers.meta import handle_save
                outputs.append(handle_save(world, intent)[0])
            elif intent["verb"] == "status":
                outputs.append(do_status(world))
            elif intent["verb"] == "rest":
                from handlers.combat_actions import handle_rest
                msg, consumed = handle_rest(world, intent)
                outputs.append(msg)
                if consumed:
                    any_consumed = True
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

    # ── Sync equipment to combat session ─────────────────────────────
    if _COMBAT_SESSION is not None and any_consumed:
        _COMBAT_SESSION.weapon_id = _get_player_weapon_id(world)
        _COMBAT_SESSION.wearing_coif = (
            "chain_coif" in world.player.worn_armour
        )
        _COMBAT_SESSION.wearing_shield = (
            "kite_shield" in world.player.worn_armour
        )
        _COMBAT_SESSION.wearing_amulet = (
            "jeweled_amulet" in world.player.worn_armour
        )

    # ── Golem action for equipment rounds ────────────────────────────
    if _equip_round and _COMBAT_SESSION is not None and any_consumed:
        _COMBAT_SESSION.update_jasper(_get_jasper_present(world))
        _COMBAT_SESSION.last_player_action = "equip"
        learner = COMBAT_MEMORY.learner("slime_golem")
        golem_narrative, eq_outcome = golem_action_round(
            _COMBAT_SESSION, learner
        )
        world.player.hp = _COMBAT_SESSION.player_hp
        for eid in world.player.worn_armour:
            ent = world.entities.get(eid)
            if (ent and ent.props.get("hp_regen", 0)
                    and world.player.hp < world.player.max_hp):
                world.player.hp = min(
                    world.player.max_hp,
                    world.player.hp + ent.props["hp_regen"],
                )
                _COMBAT_SESSION.player_hp = world.player.hp
                display = ent.name
                for article in ("a ", "an ", "the "):
                    if display.lower().startswith(article):
                        display = display[len(article):]
                        break
                golem_narrative += (
                    f"\n{display.capitalize()} pulses warmly. "
                    f"(HP +{ent.props['hp_regen']})"
                )
        outputs.append(golem_narrative)
        if eq_outcome == "player_dead":
            world.player.hp = 0
            _COMBAT_SESSION = None
        elif _COMBAT_SESSION is not None:
            outputs.append(_combat_prompt(_COMBAT_SESSION))

    # ── Archway activation check ─────────────────────────────────────
    if any_consumed and player_moved:
        arch_msg = _check_archway_activation(world)
        if arch_msg:
            outputs.append(arch_msg)

    # ── Golem tick ───────────────────────────────────────────────────
    if any_consumed:
        golem = world.entities.get("slime_golem")
        vault_open = "south" in world.rooms.get(
            "cellar",
            type("R", (), {"exits": {}})()
        ).exits
        if (golem and golem.props.get("alive", True)
                and golem.location != "hidden" and vault_open):
            _golem_tick(world, golem, player_moved)
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
                    "chain_coif"     in world.player.worn_armour,
                    "kite_shield"    in world.player.worn_armour,
                    _get_jasper_present(world),
                    wearing_amulet=(
                        "jeweled_amulet" in world.player.worn_armour
                    ),
                )
                outputs.append(
                    "The slime golem surges into the room. "
                    "It has found you.\n\n" + opening
                )

    # ── NPC tick ─────────────────────────────────────────────────────
    if any_consumed and not _GAME_WON:
        npcs = get_npc_instances(world)
        for npc in npcs.values():
            npc_msgs = npc_tick(
                world        = world,
                npc          = npc,
                memory       = NPC_MEMORY,
                player_moved = player_moved,
            )
            outputs.extend(npc_msgs)
        NPC_MEMORY.save()

        # Troll tick
        if world.player.location == "bridge":
            troll_msgs = troll_encounter(
                state        = TROLL_MEMORY.state(),
                player_moved = player_moved,
            )
            outputs.extend(troll_msgs)
            TROLL_MEMORY.save()

        # Ring HP regen (outside combat)
        if not _GAME_WON:
            for eid in world.player.worn_armour:
                ent = world.entities.get(eid)
                if not ent:
                    continue
                regen = ent.props.get("hp_regen", 0)
                if regen and world.player.hp < world.player.max_hp:
                    world.player.hp = min(
                        world.player.max_hp,
                        world.player.hp + regen,
                    )
                    if _COMBAT_SESSION is not None:
                        _COMBAT_SESSION.player_hp = world.player.hp
                    display = ent.name
                    for article in ("a ", "an ", "the "):
                        if display.lower().startswith(article):
                            display = display[len(article):]
                            break
                    display = display.capitalize()
                    outputs.append(
                        f"{display} pulses warmly. (HP +{regen})"
                    )

        # Passive stamina recovery
        PASSIVE_STAMINA = 3
        if (_COMBAT_SESSION is None
                and world.player.stamina < world.player.max_stamina):
            world.player.stamina = min(
                world.player.max_stamina,
                world.player.stamina + PASSIVE_STAMINA,
            )

    return "\n".join(outputs), None