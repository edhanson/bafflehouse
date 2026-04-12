# savegame.py
#
# Save and load game state for Bafflehouse.
#
# Design
# ──────
# Saving calls build_demo_world() to establish a clean baseline, then
# serialises only the mutable world state — room exits, entity locations
# and props, player vitals, and the clock.  Static data (names, aliases,
# tags, descriptions) is not stored; it is reconstructed from content.py
# on load.  This means saves are compact and remain valid after content
# updates that don't change entity IDs.
#
# The NPC trust model (npc_memory.json) and Q-learner (combat_memory.json)
# are separate files managed by their own modules — they are not touched
# by save/load.
#
# Format
# ──────
# One save slot: bafflehouse_save.json in the working directory.
# The file is plain JSON and can be inspected or hand-edited if needed.
#
# Restrictions
# ────────────
# Saving during active combat is blocked — mid-combat state is too
# transient to serialise cleanly and saving before a hard round would
# undermine the encounter design.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from model import World

# Imported at call time to avoid circular imports
# (engine imports savegame; savegame must not import engine at module level)

SAVE_PATH = Path("./bafflehouse_save.json")
SAVE_VERSION = 1


# ── Serialisation ─────────────────────────────────────────────────────────

def _serialise_world(world: "World") -> dict:
    """
    Extract only the mutable state from a World into a plain dict.

    Room exits are saved because some are added dynamically (lever puzzle,
    troll bridge, vault).  Entity props and locations are saved because
    they capture all puzzle state.  Tags, names, and descriptions are
    static and reconstructed from content.py on load.
    """
    # ── Rooms: only exits (descriptions and titles are static) ───────────
    rooms: Dict[str, dict] = {}
    for rid, room in world.rooms.items():
        rooms[rid] = {"exits": dict(room.exits)}

    # ── Entities: location, props, contains ──────────────────────────────
    entities: Dict[str, dict] = {}
    for eid, ent in world.entities.items():
        entities[eid] = {
            "location": ent.location,
            "props":    _serialise_props(ent.props),
            "contains": list(ent.contains),
        }

    # ── Player ────────────────────────────────────────────────────────────
    player = {
        "location":       world.player.location,
        "inventory":      list(world.player.inventory),
        "hp":             world.player.hp,
        "max_hp":         world.player.max_hp,
        "stamina":        world.player.stamina,
        "max_stamina":    world.player.max_stamina,
        "wielded_weapon": world.player.wielded_weapon,
        "worn_armour":    list(world.player.worn_armour),
    }

    # ── NPC trust and instance state ─────────────────────────────────
    # Trust (confirmations/disconfirmations) is excluded from the
    # standard npc_memory.json by design, but must be saved here so
    # the save/load cycle preserves Jasper's relationship state.
    npc_state: dict = {}
    try:
        from engine import NPC_MEMORY, get_npc_instances
        for npc_id, rep in NPC_MEMORY._store.items():
            npc_state[npc_id] = {
                "confirmations":    rep.confirmations,
                "disconfirmations": rep.disconfirmations,
                "persistent_data":  rep.persistent_data,
            }
        # NPC instance state (revealed_name, location)
        instances = get_npc_instances(world)
        for npc_id, npc in instances.items():
            if npc_id not in npc_state:
                npc_state[npc_id] = {}
            npc_state[npc_id]["revealed_name"] = npc.revealed_name
            npc_state[npc_id]["location"]      = npc.location
    except Exception:
        pass  # NPC state is best-effort

    # ── Troll state ───────────────────────────────────────────────────────
    # TrollMemory is intentionally session-only (no file persistence), but
    # must be saved here so the save/load cycle preserves puzzle progress.
    troll_state: dict = {}
    try:
        from engine import TROLL_MEMORY
        ts = TROLL_MEMORY.state()
        troll_state = {
            "correct_count": ts.correct_count,
            "solved":        list(ts.solved),
            "seen":          list(ts.seen),
            "weights":       dict(ts.weights),
            "bridge_open":   ts.bridge_open,
            "current_rid":   ts.current_rid,
        }
    except Exception:
        pass  # troll state is best-effort

    # ── Scoring milestones ────────────────────────────────────────────────
    # The TRACKER singleton lives in memory only; save the achieved list so
    # resuming a session restores the correct score and milestone state.
    try:
        from scoring import TRACKER
        achieved_milestones = TRACKER.achieved()
    except Exception:
        achieved_milestones = []

    return {
        "version":             SAVE_VERSION,
        "clock":               world.clock.now,
        "player":              player,
        "rooms":               rooms,
        "entities":            entities,
        "npc_state":           npc_state,
        "troll_state":         troll_state,
        "achieved_milestones": achieved_milestones,
    }


def _serialise_props(props: dict) -> dict:
    """
    Serialise entity props to a JSON-safe dict.

    Most props are already JSON-safe primitives.  We explicitly exclude
    any non-serialisable values (functions, objects) with a fallback to
    str() so the save never crashes on unexpected prop types.
    """
    out: Dict[str, Any] = {}
    for k, v in props.items():
        if isinstance(v, (bool, int, float, str, type(None))):
            out[k] = v
        elif isinstance(v, list):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = v
        else:
            # Non-serialisable — skip silently.  These are typically
            # computed or transient values that don't need saving.
            pass
    return out


# ── Save ──────────────────────────────────────────────────────────────────

def save_game(world: "World", in_combat: bool = False) -> str:
    """
    Write current world state to the save file.

    Returns a status message suitable for printing to the player.
    Refuses to save during active combat.
    """
    if in_combat:
        return "You can't save in the middle of a fight."

    try:
        data = _serialise_world(world)
        SAVE_PATH.write_text(json.dumps(data, indent=2))
        turn = world.clock.now
        return f"Game saved. (Turn {turn})"
    except Exception as e:
        return f"Save failed: {e}"


# ── Load ──────────────────────────────────────────────────────────────────

def load_game(world: "World", data: dict) -> str:
    """
    Apply saved state onto an existing World built by build_demo_world().

    Mutates world in-place.  Returns a status message.
    """
    try:
        # ── Version check ─────────────────────────────────────────────────
        version = data.get("version", 0)
        if version != SAVE_VERSION:
            return (
                f"Save file version {version} is not compatible with "
                f"this version of Bafflehouse (expected {SAVE_VERSION})."
            )

        # ── Clock ─────────────────────────────────────────────────────────
        world.clock.now = data["clock"]

        # ── Player ────────────────────────────────────────────────────────
        p = data["player"]
        world.player.location       = p["location"]
        world.player.inventory      = list(p["inventory"])
        world.player.hp             = p.get("hp",             100)
        world.player.max_hp         = p.get("max_hp",         100)
        world.player.stamina        = p.get("stamina",        100)
        world.player.max_stamina    = p.get("max_stamina",    100)
        world.player.wielded_weapon = p.get("wielded_weapon", None)
        world.player.worn_armour    = list(p.get("worn_armour", []))

        # ── Rooms: restore dynamic exits ──────────────────────────────────
        for rid, rdata in data.get("rooms", {}).items():
            if rid in world.rooms:
                world.rooms[rid].exits = dict(rdata["exits"])

        # ── Entities: restore location, props, contains ───────────────────
        for eid, edata in data.get("entities", {}).items():
            if eid not in world.entities:
                continue   # entity added in newer content — skip gracefully
            ent = world.entities[eid]
            ent.location = edata.get("location", ent.location)
            ent.contains = list(edata.get("contains", []))
            # Merge saved props over defaults — saved values take priority,
            # but new props added since the save was made are kept.
            saved_props = edata.get("props", {})
            ent.props.update(saved_props)

        # ── NPC trust and instance state ─────────────────────────────
        npc_state = data.get("npc_state", {})
        if npc_state:
            try:
                from engine import NPC_MEMORY, get_npc_instances
                from npc import JASPER_EVENTS
                NPC_MEMORY.register_events("jasper", JASPER_EVENTS)
                for npc_id, ns in npc_state.items():
                    rep = NPC_MEMORY.reputation(npc_id)
                    if "confirmations" in ns:
                        rep.confirmations    = ns["confirmations"]
                    if "disconfirmations" in ns:
                        rep.disconfirmations = ns["disconfirmations"]
                    if "persistent_data" in ns:
                        rep.persistent_data  = ns["persistent_data"]
                # Restore NPC instance state
                instances = get_npc_instances(world)
                for npc_id, ns in npc_state.items():
                    if npc_id in instances:
                        npc = instances[npc_id]
                        if "revealed_name" in ns:
                            npc.revealed_name = ns["revealed_name"]
                        if "location" in ns:
                            npc.location = ns["location"]
                            world.entities[npc_id].location = ns["location"]
                # Sync npc_memory.json with the restored trust state
                NPC_MEMORY.save()
            except Exception:
                pass  # NPC restore is best-effort

        # ── Troll state ───────────────────────────────────────────────────
        ts_data = data.get("troll_state", {})
        if ts_data:
            try:
                from engine import TROLL_MEMORY
                ts = TROLL_MEMORY.state()
                ts.correct_count = ts_data.get("correct_count", 0)
                ts.solved        = list(ts_data.get("solved", []))
                ts.seen          = list(ts_data.get("seen", []))
                ts.weights       = dict(ts_data.get("weights", {}))
                ts.bridge_open   = ts_data.get("bridge_open", False)
                ts.current_rid   = ts_data.get("current_rid", None)
            except Exception:
                pass  # troll restore is best-effort

        # ── Scoring milestones ────────────────────────────────────────────
        achieved = data.get("achieved_milestones", [])
        if achieved:
            try:
                from scoring import TRACKER
                TRACKER.reset()
                for mid in achieved:
                    TRACKER.award(mid)
            except Exception:
                pass  # scoring restore is best-effort

        return "ok"

    except (KeyError, TypeError, ValueError) as e:
        return f"Load failed: {e}"


# ── Public helpers ────────────────────────────────────────────────────────

def save_exists() -> bool:
    """Return True if a save file is present on disk."""
    return SAVE_PATH.exists()


def read_save_file() -> Optional[dict]:
    """
    Read and parse the save file.  Returns None on any error.
    Callers should check save_exists() first.
    """
    try:
        return json.loads(SAVE_PATH.read_text())
    except Exception:
        return None


def save_summary(data: dict) -> str:
    """
    Return a one-line human-readable summary of a save file for the
    'resume?' prompt at startup.
    """
    turn     = data.get("clock", "?")
    location = data.get("player", {}).get("location", "unknown")
    hp       = data.get("player", {}).get("hp", "?")
    return f"Turn {turn}, {location.replace('_', ' ')}, {hp} HP"