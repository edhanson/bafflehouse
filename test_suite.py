"""
test_suite.py

Consolidated regression and improvement test suite for the manor
interactive fiction engine.

USAGE
-----
    python test_suite.py                    # all sections (symbolic mode)
    python test_suite.py --semantic         # all sections (with embedding model)
    python test_suite.py world doors        # specific sections only
    python test_suite.py --semantic parser  # one section in semantic mode

SECTION TAGS
------------
Each section is marked as either "fast" (no parser needed) or "parser"
(requires ParserSystem).  Parser sections are further marked "symbolic"
(works without the embedding model) or "semantic" (needs the model).

    fast      — Sections 1, 2, 3: world structure, doors, dark cellar
    symbolic  — Sections 4, 5, 6, 8: puzzles, verbs, disambiguation, B
    semantic  — Sections 7: Improvement A (description/locative grounding)

In CI (GitHub Actions), the embedding model is not available, so semantic
sections are automatically skipped unless --semantic is passed.  All other
sections run normally.

CI BEHAVIOUR
------------
The suite exits with code 0 if all *eligible* tests pass, 1 otherwise.
The final line of stdout is always:

    RESULT: <passed>/<total> [semantic=<on|off>]

This is intended to be parseable by a badge or notification step.

ADDING TESTS
------------
Each section is a plain function returning a Suite.  Call s.check(label,
condition, optional_detail) anywhere in the function.  Register the
function in SECTIONS with a tag set drawn from {"fast","parser","symbolic",
"semantic"}.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional, Set

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from content import build_demo_world
from engine import (
    do_look,
    move_entity,
    player_has_lit_lamp,
    process_input,
    visible_entities_for_room,
)
from parser import (
    ParserSystem,
    _strip_locative,
    ground_intent,
    normalize,
    parse_to_candidates,
)


# ============================================================
# Configuration
# ============================================================

MODEL_DIR = "./models/all-MiniLM-L6-v2"

# Parsed from argv in main(); sections check this before using the model.
_SEMANTIC_MODE: bool = False
_PS: Optional[ParserSystem] = None


# ============================================================
# Test runner
# ============================================================

class Suite:
    """Lightweight test collector and reporter."""

    def __init__(self, name: str, skipped: bool = False) -> None:
        self.name    = name
        self.skipped = skipped
        self.results: list[tuple[str, bool, str]] = []

    def check(self, label: str, cond: bool, detail: str = "") -> None:
        self.results.append((label, bool(cond), detail))

    def passed(self) -> int:
        return sum(ok for _, ok, _ in self.results)

    def report(self) -> None:
        print(f"\n{'─' * 62}")
        print(f"  {self.name}")
        print(f"{'─' * 62}")
        if self.skipped:
            print("  SKIPPED — requires embedding model (pass --semantic)")
            return
        for label, ok, detail in self.results:
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if not ok and detail:
                print(f"        → {detail!r}")
        n = len(self.results)
        p = self.passed()
        print(f"\n  {p}/{n} passed.")


def skipped_suite(name: str) -> Suite:
    """Return a Suite that reports itself as skipped with zero tests."""
    return Suite(name, skipped=True)


# ============================================================
# Shared fixtures
# ============================================================

def get_parser() -> ParserSystem:
    """
    Return the shared ParserSystem, building it once on first call.

    In symbolic mode (no model) this is fast (~0.1 s).
    In semantic mode this may take 20-30 s on first call.
    """
    global _PS
    if _PS is None:
        _PS = ParserSystem.build_default(local_model_dir=MODEL_DIR)
    return _PS


def fresh():
    """Return a freshly built world."""
    return build_demo_world()


def cmd(world, text: str, pending=None):
    """Run one command through process_input and return (output, pending)."""
    ps = get_parser()
    result = process_input(world, ps, text, pending)
    # Clear _current_world after each call so stale world state
    # does not leak between test sections via the shared singleton.
    ps._current_world = None
    return result


def ground(world, command: str, slot: str = "obj"):
    """Parse and ground a command; return the entity id in the named slot."""
    ps = get_parser()
    ps.semantic_entity_index.rebuild_for_visible(world)
    cands = parse_to_candidates(command, parser_system=ps)
    if not cands or cands[0]["type"] in ("missing_verb", "meta"):
        return None
    g = ground_intent(world=world, intent=cands[0],
                      semantic_index=ps.semantic_entity_index)
    return g.get(slot)


# ============================================================
# SECTION 1 — World structure  [fast]
# ============================================================

def test_world_structure() -> Suite:
    s = Suite("SECTION 1 — World structure")
    w = fresh()

    for rid in ("foyer", "hall_1", "hall_2", "hall_3", "library", "trophy_room",
                "secret_study", "cellar", "entryway", "gatehouse", "wooded_path"):
        s.check(f"room '{rid}' exists", rid in w.rooms)

    s.check("display_key starts hidden",
            w.entities["display_key"].location == "hidden")
    s.check("ancient_scroll starts hidden",
            w.entities["ancient_scroll"].location == "hidden")

    s.check("journal location is hall_1",
            w.entities["journal"].location == "hall_1")
    s.check("matchbox inside wooden_box",
            "matchbox" in w.entities["wooden_box"].contains)
    s.check("journal in hall_1.entities",
            "journal" in w.rooms["hall_1"].entities)
    s.check("silver_ring inside display_case",
            "silver_ring" in w.entities["display_case"].contains)
    s.check("folded_letter inside display_case",
            "folded_letter" in w.entities["display_case"].contains)
    s.check("journal NOT inside display_case",
            "journal" not in w.entities["display_case"].contains)

    s.check("foyer has no north exit at start",
            "north" not in w.rooms["foyer"].exits)
    s.check("hall_1 has no south exit at start",
            "south" not in w.rooms["hall_1"].exits)
    s.check("trophy_room has no north exit at start",
            "north" not in w.rooms["trophy_room"].exits)
    s.check("cellar has east exit",
            "east" in w.rooms["cellar"].exits)

    s.check("matchbox starts with 10 matches",
            w.entities["matchbox"].props["matches_remaining"] == 10)
    s.check("matchbox has fire_source tag",
            "fire_source" in w.entities["matchbox"].tags)
    s.check("cellar has desc_lit attribute",
            hasattr(w.rooms["cellar"], "desc_lit"))

    return s


# ============================================================
# SECTION 2 — Door mechanics  [fast]
# ============================================================

def test_door_mechanics() -> Suite:
    s = Suite("SECTION 2 — Door mechanics")

    def door_world(start_room: str):
        w = fresh()
        w.player.location = start_room
        return w

    # Oak door (foyer <-> hall)
    w = door_world("foyer")
    cmd(w, "go north")
    s.check("oak: blocked before unlock", w.player.location == "foyer")

    w = door_world("foyer")
    move_entity(w, "brass_key", "player")
    cmd(w, "unlock door with brass key")
    cmd(w, "go north")
    s.check("oak: blocked after unlock but before open",
            w.player.location == "foyer")

    w = door_world("foyer")
    move_entity(w, "brass_key", "player")
    cmd(w, "unlock door with brass key")
    cmd(w, "open door")
    cmd(w, "go north")
    s.check("oak: passage after unlock + open", w.player.location == "hall_1")

    cmd(w, "close door")
    cmd(w, "go south")
    s.check("oak: blocked after close", w.player.location == "hall_1")

    # Study door (trophy_room <-> secret_study)
    w = door_world("trophy_room")
    cmd(w, "go south")
    s.check("study: blocked before unlock",
            w.player.location == "trophy_room")

    w = door_world("trophy_room")
    move_entity(w, "iron_key", "player")
    cmd(w, "unlock door with iron key")
    cmd(w, "go south")
    s.check("study: blocked after unlock but before open",
            w.player.location == "trophy_room")

    w = door_world("trophy_room")
    move_entity(w, "iron_key", "player")
    cmd(w, "unlock door with iron key")
    cmd(w, "open door")
    cmd(w, "go south")
    s.check("study: passage after unlock + open",
            w.player.location == "secret_study")

    cmd(w, "go north")
    s.check("study: return north through open door",
            w.player.location == "trophy_room")

    cmd(w, "go south")
    cmd(w, "close door")
    cmd(w, "go north")
    s.check("study: blocked after close",
            w.player.location == "secret_study")

    w2 = fresh()
    s.check("foyer north absent at start",
            "north" not in w2.rooms["foyer"].exits)
    s.check("hall_1 south absent at start",
            "south" not in w2.rooms["hall_1"].exits)
    s.check("trophy_room south absent at start",
            "south" not in w2.rooms["trophy_room"].exits)
    s.check("secret_study north always present",
            "north" in w2.rooms["secret_study"].exits)

    return s


# ============================================================
# SECTION 3 — Dark cellar visibility  [fast]
# ============================================================

def test_dark_cellar() -> Suite:
    s = Suite("SECTION 3 — Dark cellar visibility")

    def dark():
        w = fresh()
        w.player.location = "cellar"
        return w

    def lit():
        w = dark()
        move_entity(w, "oil_lamp", "player")
        move_entity(w, "lamp_oil", "player")
        move_entity(w, "matchbox", "player")
        cmd(w, "fill lamp with oil")
        cmd(w, "light lamp")
        assert player_has_lit_lamp(w), "lamp not lit in lit() fixture"
        return w

    w = dark()
    vis = visible_entities_for_room(w)
    s.check("cellar_lever invisible without lamp", "cellar_lever" not in vis)
    s.check("water_ewer invisible without lamp",   "water_ewer"   not in vis)
    # oil_lamp and lamp_oil have moved to bedroom_east and gatehouse_interior
    # respectively — no longer in cellar, so dark-cellar visibility tests
    # for those items are no longer applicable.
    w_bed = fresh(); w_bed.player.location = "bedroom_east"
    vis_bed = visible_entities_for_room(w_bed)
    s.check("oil_lamp visible in bedroom_east",   "oil_lamp"  in vis_bed)
    w_gate = fresh(); w_gate.player.location = "gatehouse_interior"
    vis_gate = visible_entities_for_room(w_gate)
    s.check("lamp_oil visible in gatehouse_interior", "lamp_oil" in vis_gate)

    w2 = lit()
    vis2 = visible_entities_for_room(w2)
    s.check("cellar_lever visible with lit lamp",  "cellar_lever" in  vis2)
    s.check("water_ewer visible with lit lamp",    "water_ewer"   in  vis2)

    w3 = dark()
    look_dark = do_look(w3)
    s.check("dark cellar desc mentions darkness",
            "dark" in look_dark.lower())
    s.check("dark cellar desc does not mention lever",
            "lever" not in look_dark.lower())
    s.check("dark cellar entity list excludes ewer",
            "clay ewer" not in look_dark.lower())

    w4 = lit()
    look_lit = do_look(w4)
    s.check("lit cellar desc does not say impenetrably dark",
            "impenetrably" not in look_lit.lower())
    s.check("lit cellar desc mentions lever or far wall",
            "lever" in look_lit.lower() or "far wall" in look_lit.lower())
    s.check("lit cellar entity list includes ewer",
            "clay ewer" in look_lit.lower())

    w5 = dark()
    out, _ = cmd(w5, "examine wall")
    s.check("examine wall in dark -> darkness message",
            "dark" in out.lower())
    s.check("examine wall in dark -> not generic 'not here'",
            out != "You don't see that here.")

    w6 = lit()
    out6, _ = cmd(w6, "examine wall")
    s.check("examine wall when lit -> describes wall",
            "wall" in out6.lower() or "lever" in out6.lower())
    s.check("examine wall when lit -> not darkness message",
            "too dark" not in out6.lower())

    return s


# ============================================================
# SECTION 4 — Puzzle sequences  [symbolic]
# ============================================================

def test_puzzles() -> Suite:
    s = Suite("SECTION 4 — Puzzle sequences (end-to-end)")

    # Puzzle 0: brass key / oak door gate
    w = fresh()
    w.player.location = "foyer"
    move_entity(w, "brass_key", "player")
    cmd(w, "unlock door with brass key")
    cmd(w, "open door")
    cmd(w, "go north")
    s.check("puzzle 0: reach hall_1 via oak door",
            w.player.location == "hall_1")

    # Puzzle 1: fill + light + pull lever
    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    move_entity(w, "matchbox", "player")

    out, _ = cmd(w, "pull lever")
    s.check("puzzle 1: lever blocked without light",
            w.rooms["hall_3"].exits.get("north") is None)

    cmd(w, "fill lamp with oil")
    cmd(w, "light lamp")
    s.check("puzzle 1: lamp is lit",
            w.entities["oil_lamp"].props.get("lit"))
    s.check("puzzle 1: match consumed",
            w.entities["matchbox"].props["matches_remaining"] == 9)

    cmd(w, "pull lever")
    s.check("puzzle 1: hall_3 north passage opened",
            w.rooms["hall_3"].exits.get("north") == "cellar_passage")

    # Puzzle 2: journal -> antler -> display key -> case
    w = fresh()
    w.player.location = "hall_1"
    move_entity(w, "magnifying_glass", "player")  # needed to read small text
    out, _ = cmd(w, "read journal")
    s.check("puzzle 2: read journal gives antler clue",
            "antler" in out.lower() or "stag" in out.lower())

    w.player.location = "trophy_room"
    cmd(w, "pull antler")
    s.check("puzzle 2: display_key drops after pull",
            w.entities["display_key"].location == "trophy_room")

    move_entity(w, "display_key", "player")
    w.player.location = "library"
    cmd(w, "unlock case with display key")
    cmd(w, "open case")
    move_entity(w, "silver_ring", "player")
    s.check("puzzle 2: silver_ring obtainable after unlock",
            "silver_ring" in w.player.inventory)

    move_entity(w, "folded_letter", "player")
    out, _ = cmd(w, "read letter")
    s.check("puzzle 2: folded_letter hints at basin puzzle",
            "basin" in out.lower() or "ring" in out.lower())

    # Puzzle 3: wear ring + pour water + scroll
    w2 = fresh()
    w2.player.location = "secret_study"
    move_entity(w2, "water_ewer", "player")
    cmd(w2, "pour water into basin")
    s.check("puzzle 3: basin not activated without ring",
            not w2.entities["stone_basin"].props.get("activated"))
    s.check("puzzle 3: scroll still hidden without ring",
            w2.entities["ancient_scroll"].location == "hidden")

    w3 = fresh()
    w3.player.location = "secret_study"
    move_entity(w3, "water_ewer", "player")
    move_entity(w3, "silver_ring", "player")
    cmd(w3, "wear ring")
    s.check("puzzle 3: ring worn",
            w3.entities["silver_ring"].props.get("worn"))

    out3, _ = cmd(w3, "pour water into basin")
    s.check("puzzle 3: basin activated with ring",
            w3.entities["stone_basin"].props.get("activated"))
    s.check("puzzle 3: scroll revealed in basin",
            w3.entities["ancient_scroll"].location == "stone_basin")

    move_entity(w3, "ancient_scroll", "player")
    out4, _ = cmd(w3, "read scroll")
    s.check("puzzle 3: scroll is readable",
            "cellar" in out4.lower() or "vault" in out4.lower())

    w4 = fresh()
    w4.player.location = "secret_study"
    move_entity(w4, "water_ewer", "player")
    move_entity(w4, "silver_ring", "player")
    w4.entities["silver_ring"].props["worn"] = True
    cmd(w4, "fill basin with ewer")
    s.check("puzzle 3: fill path also activates basin",
            w4.entities["stone_basin"].props.get("activated"))

    return s


# ============================================================
# SECTION 5 — Verb handlers  [symbolic]
# ============================================================

def test_verb_handlers() -> Suite:
    s = Suite("SECTION 5 — Verb handlers")

    # Fill / pour symmetry
    for command in [
        "fill lamp with flask",
        "fill lamp with oil",
        "fill lamp with lamp oil",
        "pour flask into lamp",
        "pour oil into lamp",
    ]:
        w = fresh()
        w.player.location = "cellar"
        move_entity(w, "oil_lamp", "player")
        move_entity(w, "lamp_oil", "player")
        out, p = cmd(w, command)
        s.check(f'"{command}" fuels lamp',
                w.entities["oil_lamp"].props.get("fuelled") and p is None, out)

    # Wrong liquid rejected
    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "water_ewer", "player")
    w.entities["water_ewer"].props["requires_light"] = False
    out, _ = cmd(w, "pour water into lamp")
    s.check("pour water into lamp -> rejected",
            not w.entities["oil_lamp"].props.get("fuelled"), out)

    # Light requires matchbox
    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    cmd(w, "fill lamp with oil")
    out, _ = cmd(w, "light lamp")
    s.check("light lamp without matchbox -> blocked",
            not w.entities["oil_lamp"].props.get("lit"), out)

    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    move_entity(w, "matchbox", "player")
    cmd(w, "fill lamp with oil")
    out, _ = cmd(w, "light lamp")
    s.check("light lamp with matchbox -> lit",
            w.entities["oil_lamp"].props.get("lit"), out)
    s.check("match count decremented",
            w.entities["matchbox"].props["matches_remaining"] == 9)

    # Light with trailing "with matches" still works
    for command in ["light lamp with match",
                    "light lamp with matches",
                    "light lamp with a match"]:
        w = fresh()
        w.player.location = "cellar"
        move_entity(w, "oil_lamp", "player")
        move_entity(w, "lamp_oil", "player")
        move_entity(w, "matchbox", "player")
        cmd(w, "fill lamp with oil")
        out, p = cmd(w, command)
        s.check(f'"{command}" -> lamp lit',
                w.entities["oil_lamp"].props.get("lit") and p is None, out)

    # Standalone match striking
    for command in ["strike match", "light match", "light matches",
                    "strike a match", "light a match"]:
        w = fresh()
        w.player.location = "cellar"
        move_entity(w, "matchbox", "player")
        out, p = cmd(w, command)
        s.check(f'"{command}" -> standalone strike',
                p is None and "flame" in out.lower()
                and w.entities["matchbox"].props["matches_remaining"] == 9,
                out)

    # Last match warning
    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    move_entity(w, "matchbox", "player")
    w.entities["matchbox"].props["matches_remaining"] = 1
    cmd(w, "fill lamp with oil")
    out, _ = cmd(w, "light lamp")
    s.check("last match message in output",
            "last match" in out.lower(), out)

    # Empty matchbox message
    cmd(w, "extinguish lamp")
    w.entities["matchbox"].props["matches_remaining"] = 0
    out, _ = cmd(w, "light lamp")
    s.check("empty matchbox -> informative message",
            "empty" in out.lower(), out)

    # Take response names the item
    w = fresh()
    w.player.location = "bedroom_east"
    out, _ = cmd(w, "get lamp")
    s.check("take names item (not bare 'it')",
            "take it" not in out.lower() and "lamp" in out.lower(), out)

    w = fresh()
    w.player.location = "foyer"
    w.entities["wooden_box"].props["open"] = True
    out, _ = cmd(w, "get matches")
    s.check("take matchbox -> no 'them' pronoun",
            "take them" not in out.lower() and "match" in out.lower(), out)

    # Wear / remove
    w = fresh()
    w.player.location = "secret_study"
    move_entity(w, "silver_ring", "player")
    cmd(w, "wear ring")
    s.check("wear ring -> worn prop set",
            w.entities["silver_ring"].props.get("worn"))
    cmd(w, "remove ring")
    s.check("remove ring -> worn prop cleared",
            not w.entities["silver_ring"].props.get("worn"))

    # Read (requires magnifying glass for the key passage)
    w = fresh()
    w.player.location = "hall_1"
    move_entity(w, "magnifying_glass", "player")
    out, _ = cmd(w, "read journal")
    s.check("read journal -> antler clue (with magnifying glass)",
            "antler" in out.lower() or "stag" in out.lower(), out)
    # Without magnifying glass, key passage is illegible
    w2 = fresh()
    w2.player.location = "hall_1"
    out2, _ = cmd(w2, "read journal")
    s.check("read journal without lens -> no antler clue",
            "antler" not in out2.lower() and "stag" not in out2.lower(), out2)

    # Pull stag
    w = fresh()
    w.player.location = "trophy_room"
    out, _ = cmd(w, "pull antler")
    s.check("pull antler -> display_key revealed",
            w.entities["display_key"].location == "trophy_room", out)
    out2, _ = cmd(w, "pull antler")
    s.check("pull antler again -> already pulled",
            "already" in out2.lower() or "nothing" in out2.lower(), out2)

    # Examine container contents
    w = fresh()
    w.player.location = "library"
    w.entities["display_case"].props["locked"] = False
    w.entities["display_case"].props["open"]   = True
    out, _ = cmd(w, "examine case")
    s.check("examine open display_case -> lists ring",
            "ring" in out.lower(), out)

    w = fresh()
    w.player.location = "secret_study"
    move_entity(w, "water_ewer", "player")
    out, _ = cmd(w, "examine ewer")
    s.check("examine full ewer -> reports water",
            "water" in out.lower() and "empty" not in out.lower(), out)

    w = fresh()
    w.player.location = "secret_study"
    move_entity(w, "water_ewer", "player")
    cmd(w, "pour water into basin")
    out, _ = cmd(w, "examine ewer")
    s.check("examine emptied ewer -> reports empty",
            "empty" in out.lower() and "water" not in out.lower(), out)

    w = fresh()
    w.player.location = "secret_study"
    move_entity(w, "water_ewer", "player")
    move_entity(w, "silver_ring", "player")
    w.entities["silver_ring"].props["worn"] = True
    cmd(w, "pour water into basin")
    out, _ = cmd(w, "examine basin")
    s.check("examine activated basin -> lists scroll",
            "scroll" in out.lower(), out)

    return s


# ============================================================
# SECTION 6 — Parser: disambiguation  [symbolic]
# ============================================================

def test_parser_disambiguation() -> Suite:
    s = Suite("SECTION 6 — Parser: disambiguation and clarification")

    w = fresh()
    w.player.location = "foyer"
    _, p = cmd(w, "take key")
    s.check("take key (two keys in room) -> clarification", p is not None)

    w = fresh()
    w.player.location = "foyer"
    _, p = cmd(w, "take brass key")
    s.check("take brass key -> no clarification",
            p is None and "brass_key" in w.player.inventory)

    w = fresh()
    w.player.location = "foyer"
    _, p = cmd(w, "take iron key")
    s.check("take iron key -> no clarification",
            p is None and "iron_key" in w.player.inventory)

    w = fresh()
    w.player.location = "foyer"
    _, p = cmd(w, "take key")
    assert p is not None
    out2, p2 = cmd(w, "1", p)
    s.check("clarification answer '1' resolves and takes a key",
            p2 is None and len(w.player.inventory) == 1)

    w = fresh()
    w.player.location = "foyer"
    _, p = cmd(w, "take key")
    assert p is not None
    out3, p3 = cmd(w, "iron key", p)
    s.check("clarification answer 'iron key' takes iron_key",
            p3 is None and "iron_key" in w.player.inventory, out3)

    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    out, p = cmd(w, "fill lamp with oil")
    s.check("fill lamp with oil -> no clarification",
            p is None and w.entities["oil_lamp"].props.get("fuelled"), out)

    w2 = fresh()
    w2.player.location = "cellar"
    move_entity(w2, "oil_lamp", "player")
    move_entity(w2, "lamp_oil", "player")
    out, p = cmd(w2, "pour flask into lamp")
    s.check("pour flask into lamp -> no clarification",
            p is None and w2.entities["oil_lamp"].props.get("fuelled"), out)

    return s


# ============================================================
# SECTION 7 — Parser: Improvement A  [semantic]
# ============================================================

def test_parser_improvement_a() -> Suite:
    if not _SEMANTIC_MODE:
        return skipped_suite(
            "SECTION 7 — Parser: Improvement A (semantic grounding) [SKIPPED]"
        )

    s = Suite("SECTION 7 — Parser: Improvement A (richer entity grounding)")

    def foyer_world():
        w = fresh()
        w.player.location = "foyer"
        move_entity(w, "brass_key", "player")
        move_entity(w, "iron_key", "player")
        move_entity(w, "wooden_box", "player")
        return w

    w = foyer_world()
    s.check("examine the small metal key -> brass_key",
            ground(w, "examine the small metal key") == "brass_key")

    w = foyer_world()
    s.check("take the worn key -> brass_key",
            ground(w, "take the worn key") == "brass_key")

    w = foyer_world()
    s.check("examine the brass thing -> brass_key",
            ground(w, "examine the brass thing") == "brass_key")

    w = foyer_world()
    s.check("take the iron object -> iron_key",
            ground(w, "take the iron object") == "iron_key")

    w = foyer_world()
    s.check("put the small key in the box -> brass_key (obj)",
            ground(w, "put the small key in the box", "obj") == "brass_key")

    w = foyer_world()
    s.check("put the heavy key in the wooden crate -> iron_key (obj)",
            ground(w, "put the heavy key in the wooden crate", "obj") == "iron_key")

    w = fresh()
    w.player.location = "bedroom_east"  # lamp moved from cellar
    s.check("examine the tin lamp -> oil_lamp",
            ground(w, "examine the tin lamp") == "oil_lamp")

    s.check("_strip_locative strips 'near the door'",
            _strip_locative("key near the door") == "key")
    s.check("_strip_locative strips 'by the table'",
            _strip_locative("the lamp by the table") == "the lamp")
    s.check("_strip_locative leaves plain phrases unchanged",
            _strip_locative("brass key") == "brass key")

    w = foyer_world()
    s.check("take the brass key by the table -> brass_key",
            ground(w, "take the brass key by the table") == "brass_key")

    w = foyer_world()
    _, p = cmd(w, "examine the key near the door")
    s.check("key near the door with two keys -> clarification (correct)",
            p is not None)

    w = foyer_world()
    s.check("exact 'brass key' still resolves unambiguously",
            ground(w, "examine brass key") == "brass_key")

    w = foyer_world()
    s.check("exact 'iron key' still resolves unambiguously",
            ground(w, "take iron key") == "iron_key")

    return s


# ============================================================
# SECTION 8 — Parser: Improvement B  [symbolic]
# ============================================================

def test_parser_improvement_b() -> Suite:
    s = Suite("SECTION 8 — Parser: Improvement B (novel verbs + natural sentences)")

    def foyer_world():
        w = fresh()
        w.player.location = "foyer"
        move_entity(w, "brass_key", "player")
        move_entity(w, "iron_key", "player")
        move_entity(w, "wooden_box", "player")
        return w

    # B1: preamble stripping
    for phrase, _ in [
        ("I want to pick up the brass key",   "brass_key"),
        ("can you take the iron key please",  "iron_key"),
        ("I would like to examine the box",   "wooden_box"),
        ("let me look at the wooden box",     "wooden_box"),
        ("please take the brass key",         "brass_key"),
        ("try to open the wooden box",        "wooden_box"),
    ]:
        w = foyer_world()
        out, p = cmd(w, phrase)
        ok = (p is None
              and "beg your pardon" not in out.lower()
              and "don't" not in out.lower())
        s.check(f'B1: "{phrase}"', ok, out)

    # B2: synonym verbs — single object
    for phrase, expected_eid in [
        ("retrieve the brass key",  "brass_key"),
        ("snatch the brass key",    "brass_key"),
        ("pocket the iron key",     "iron_key"),
        ("study the wooden box",    "wooden_box"),
        ("toss the iron key",       "iron_key"),
    ]:
        w = foyer_world()
        result = ground(w, phrase)
        s.check(f'B2: "{phrase}" -> {expected_eid}',
                result == expected_eid)

    # B2: synonym verbs — two objects
    for phrase, exp_obj, exp_iobj in [
        ("deposit the brass key in the box", "brass_key",  "wooden_box"),
        ("stow the iron key inside the box", "iron_key",   "wooden_box"),
        ("slip the brass key into the box",  "brass_key",  "wooden_box"),
    ]:
        w = foyer_world()
        obj_eid  = ground(w, phrase, "obj")
        iobj_eid = ground(w, phrase, "iobj")
        s.check(f'B2: "{phrase}"',
                obj_eid == exp_obj and iobj_eid == exp_iobj,
                f"obj={obj_eid} iobj={iobj_eid}")

    # B2: synonym in bedroom context (lamp moved from cellar)
    w = fresh()
    w.player.location = "bedroom_east"
    out, p = cmd(w, "retrieve the lamp")
    s.check('B2: "retrieve the lamp" -> takes lamp',
            "oil_lamp" in w.player.inventory and p is None, out)

    w = fresh()
    w.player.location = "hall_1"
    out, p = cmd(w, "inspect the journal")
    s.check('B2: "inspect the journal" -> examines journal',
            p is None and (
                "handwriting" in out.lower() or "journal" in out.lower()
            ), out)

    # B1 + B2 combined
    w = foyer_world()
    out, p = cmd(w, "I want to retrieve the brass key")
    s.check('B1+B2: "I want to retrieve the brass key"',
            "brass_key" in w.player.inventory and p is None, out)

    # Regressions
    w = foyer_world()
    out, p = cmd(w, "take brass key")
    s.check("B regression: exact 'take' unaffected",
            "brass_key" in w.player.inventory and p is None, out)

    w = foyer_world()
    out, p = cmd(w, "examine wooden box")
    s.check("B regression: exact 'examine' unaffected",
            p is None and (
                "box" in out.lower() or "lid" in out.lower()
            ), out)

    w = foyer_world()
    out, p = cmd(w, "drop the brass key")
    s.check("B regression: 'drop' not captured by synonym",
            p is None and "brass_key" not in w.player.inventory, out)

    return s


# ============================================================
# SECTION 9 — Map expansion and weapons  [symbolic]
# ============================================================

def test_map_expansion() -> Suite:
    s = Suite("SECTION 9 — Map expansion and weapons")

    w = fresh()

    # ── New rooms exist ───────────────────────────────────────
    for rid in ("hall_1", "hall_2", "hall_3", "entryway",
                "gatehouse", "wooded_path"):
        s.check(f"room '{rid}' exists", rid in w.rooms)
    s.check("old 'hall' removed", "hall" not in w.rooms)

    # ── Hall connectivity ─────────────────────────────────────
    s.check("hall_1 north -> hall_2",
            w.rooms["hall_1"].exits.get("north") == "hall_2")
    s.check("hall_2 north -> hall_3",
            w.rooms["hall_2"].exits.get("north") == "hall_3")
    s.check("hall_3 west -> trophy_room",
            w.rooms["hall_3"].exits.get("west") == "trophy_room")
    s.check("hall_1 east -> library",
            w.rooms["hall_1"].exits.get("east") == "library")
    s.check("library west -> hall_1",
            w.rooms["library"].exits.get("west") == "hall_1")

    # ── Outdoor connectivity ──────────────────────────────────
    s.check("foyer south -> entryway",
            w.rooms["foyer"].exits.get("south") == "entryway")
    s.check("entryway north -> foyer",
            w.rooms["entryway"].exits.get("north") == "foyer")
    s.check("entryway east -> gatehouse",
            w.rooms["entryway"].exits.get("east") == "gatehouse")
    s.check("entryway west -> wooded_path",
            w.rooms["entryway"].exits.get("west") == "wooded_path")
    s.check("gatehouse west -> entryway",
            w.rooms["gatehouse"].exits.get("west") == "entryway")
    s.check("wooded_path east -> entryway",
            w.rooms["wooded_path"].exits.get("east") == "entryway")

    # ── Study door now south of trophy room ───────────────────
    s.check("trophy_room has no south exit at start",
            "south" not in w.rooms["trophy_room"].exits)
    s.check("secret_study north -> trophy_room",
            w.rooms["secret_study"].exits.get("north") == "trophy_room")

    # ── Navigation: foyer south -> entryway -> outdoor areas ──
    w = fresh()
    w.player.location = "foyer"
    cmd(w, "go south")
    s.check("walk foyer south -> entryway",
            w.player.location == "entryway")
    cmd(w, "go east")
    s.check("walk entryway east -> gatehouse",
            w.player.location == "gatehouse")
    cmd(w, "go west")
    cmd(w, "go west")
    s.check("walk entryway west -> wooded_path",
            w.player.location == "wooded_path")

    # ── Full hall traversal (requires oak door) ───────────────
    w = fresh()
    w.player.location = "foyer"
    move_entity(w, "brass_key", "player")
    cmd(w, "unlock door with brass key")
    cmd(w, "open door")
    cmd(w, "go north")
    s.check("oak door: foyer north -> hall_1",
            w.player.location == "hall_1")
    cmd(w, "go north")
    s.check("hall_1 north -> hall_2",
            w.player.location == "hall_2")
    cmd(w, "go north")
    s.check("hall_2 north -> hall_3",
            w.player.location == "hall_3")
    cmd(w, "go west")
    s.check("hall_3 west -> trophy_room",
            w.player.location == "trophy_room")

    # ── Study door is now south of trophy room ────────────────
    w = fresh()
    w.player.location = "trophy_room"
    move_entity(w, "iron_key", "player")
    cmd(w, "unlock door with iron key")
    cmd(w, "open door")
    cmd(w, "go south")
    s.check("study door: trophy_room south -> secret_study",
            w.player.location == "secret_study")
    cmd(w, "go north")
    s.check("study door: secret_study north -> trophy_room",
            w.player.location == "trophy_room")

    # ── Lever opens hall_3 north ──────────────────────────────
    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    move_entity(w, "matchbox", "player")
    cmd(w, "fill lamp with oil")
    cmd(w, "light lamp")
    cmd(w, "pull lever")
    s.check("lever opens hall_3 north passage",
            w.rooms["hall_3"].exits.get("north") == "cellar_passage")

    # ── Weapons and armor in trophy room ─────────────────────
    w = fresh()
    for eid in ("broadsword", "hunting_knife", "iron_mace",
                "kite_shield", "chain_coif"):
        s.check(f"{eid} starts in trophy_room",
                w.entities[eid].location == "trophy_room")
        s.check(f"{eid} starts mounted",
                "mounted" in w.entities[eid].tags)

    # Weapons have stat props
    s.check("broadsword has damage prop",
            w.entities["broadsword"].props.get("damage") == 4)
    s.check("iron_mace has damage prop",
            w.entities["iron_mace"].props.get("damage") == 5)
    s.check("kite_shield has defense prop",
            w.entities["kite_shield"].props.get("defense") == 3)
    s.check("chain_coif has defense prop",
            w.entities["chain_coif"].props.get("defense") == 2)

    # ── Unmount mechanics ─────────────────────────────────────
    w = fresh()
    w.player.location = "trophy_room"
    out, _ = cmd(w, "take broadsword")
    s.check("take mounted item -> blocked",
            "broadsword" not in w.player.inventory)
    s.check("take blocked message mentions wall or take down",
            "wall" in out.lower() or "take down" in out.lower(), out)

    out, _ = cmd(w, "take down broadsword")
    s.check("take down -> unmounts",
            "mounted" not in w.entities["broadsword"].tags)
    s.check("unmounted item stays in room",
            w.entities["broadsword"].location == "trophy_room")
    s.check("unmounted item is now portable",
            "portable" in w.entities["broadsword"].tags)

    out, _ = cmd(w, "take broadsword")
    s.check("take after unmount -> inventory",
            "broadsword" in w.player.inventory)

    # Unmount synonyms
    w = fresh()
    w.player.location = "trophy_room"
    cmd(w, "remove iron mace from wall")
    s.check("remove from wall -> unmounts",
            "mounted" not in w.entities["iron_mace"].tags)

    w = fresh()
    w.player.location = "trophy_room"
    cmd(w, "unhook hunting knife")
    s.check("unhook -> unmounts",
            "mounted" not in w.entities["hunting_knife"].tags)

    w = fresh()
    w.player.location = "trophy_room"
    cmd(w, "unmount kite shield")
    s.check("unmount -> unmounts shield",
            "mounted" not in w.entities["kite_shield"].tags)

    # Unmount already-unmounted item
    w = fresh()
    w.player.location = "trophy_room"
    cmd(w, "take down chain coif")
    out, _ = cmd(w, "take down chain coif")
    s.check("unmount already-unmounted -> not mounted message",
            "mounted" not in out.lower() or "isn't" in out.lower(), out)

    # Wearable armor can be worn after unmounting
    w = fresh()
    w.player.location = "trophy_room"
    cmd(w, "take down kite shield")
    cmd(w, "take kite shield")
    out, _ = cmd(w, "wear shield")
    s.check("wear kite shield after unmount",
            w.entities["kite_shield"].props.get("worn"), out)

    return s


# ============================================================
# SECTION 10 — NPC system (Jasper the cat)  [symbolic]
# ============================================================

def test_npc_jasper() -> Suite:
    s = Suite("SECTION 10 — NPC system (Jasper the cat)")
    import pathlib, engine
    from npc import JASPER_EVENTS

    def _npc_cleanup():
        """Wipe all NPC state — called before each test world and at the end."""
        engine._NPC_INSTANCES.clear()
        pathlib.Path("./npc_memory.json").unlink(missing_ok=True)
        engine.NPC_MEMORY._store.clear()
        engine.NPC_MEMORY.register_events("jasper", JASPER_EVENTS)

    def npc_fresh():
        """Fresh world with clean NPC state."""
        _npc_cleanup()
        return fresh()


    def set_trust(neutral=False, friendly=False):
        """Convenience: set Jasper's trust to a useful tier.
        Uses low-precision states to avoid diluting subsequent event deltas.
        """
        if friendly:
            # 15/(15+6) = 0.714 -> friendly
            engine.NPC_MEMORY.reputation("jasper").confirmations    = 15.0
            engine.NPC_MEMORY.reputation("jasper").disconfirmations = 6.0
        elif neutral:
            # 5/(5+6) = 0.545 -> neutral, precision=11 (close to natural game arc)
            engine.NPC_MEMORY.reputation("jasper").confirmations    = 5.0
            engine.NPC_MEMORY.reputation("jasper").disconfirmations = 6.0

    def place_jasper(w, room_id):
        """Move Jasper to a room and sync all data structures."""
        from engine import get_npc_instances
        npcs = get_npc_instances(w)
        jasper = npcs["jasper"]
        if jasper.location in w.rooms and "jasper" in w.rooms[jasper.location].entities:
            w.rooms[jasper.location].entities.remove("jasper")
        jasper.location = room_id
        w.entities["jasper"].location = room_id
        if "jasper" not in w.rooms[room_id].entities:
            w.rooms[room_id].entities.append("jasper")
        return jasper

    # ── World content ─────────────────────────────────────────
    w = npc_fresh()
    s.check("kitchen room added",       "kitchen"        in w.rooms)
    s.check("cellar_passage added",     "cellar_passage" in w.rooms)
    s.check("jasper entity present",    "jasper"         in w.entities)
    s.check("cat_food in forest_b",     w.entities["cat_food"].location == "forest_b")
    s.check("catnip starts hidden",     w.entities["catnip"].location == "hidden")
    s.check("catnip not visible yet",   not w.entities["catnip"].props.get("visible"))
    # cellar north exit is added dynamically by the lever — absent at startup
    s.check("cellar has no north exit at start",
            "north" not in w.rooms["cellar"].exits)
    s.check("passage south->cellar",    w.rooms["cellar_passage"].exits.get("south") == "cellar")
    s.check("passage west->kitchen",    w.rooms["cellar_passage"].exits.get("west") == "kitchen")

    # ── Catnip reveal ─────────────────────────────────────────
    w = npc_fresh(); w.player.location = "entryway"
    out, _ = cmd(w, "examine hedges")
    s.check("examine hedges reveals catnip", w.entities["catnip"].props.get("visible"), out)
    s.check("catnip added to entryway",      "catnip" in w.rooms["entryway"].entities)
    out, _ = cmd(w, "take catnip")
    s.check("catnip takeable after reveal",  "catnip" in w.player.inventory, out)

    # Second examine does not duplicate catnip in room
    w = npc_fresh(); w.player.location = "entryway"
    cmd(w, "examine hedges")
    cmd(w, "examine hedges")
    count = w.rooms["entryway"].entities.count("catnip")
    s.check("catnip not duplicated on second examine", count == 1)

    # ── Kitchen access ────────────────────────────────────────
    w = npc_fresh(); w.player.location = "cellar"
    for item in ("oil_lamp","lamp_oil","matchbox"):
        move_entity(w, item, "player")
    cmd(w, "fill lamp with oil")
    cmd(w, "light lamp")
    cmd(w, "pull lever")
    cmd(w, "go north")
    s.check("cellar north -> cellar_passage", w.player.location == "cellar_passage")
    cmd(w, "go west")
    s.check("cellar_passage west -> kitchen", w.player.location == "kitchen")
    # cat_food moved to forest_b — kitchen access test just confirms kitchen reachable
    s.check("kitchen reachable via cellar passage", w.player.location == "kitchen")

    # ── Jasper starting disposition ───────────────────────────
    w = npc_fresh()
    from engine import get_npc_instances, NPC_MEMORY
    get_npc_instances(w)
    s.check("jasper starts cautious", NPC_MEMORY.disposition("jasper") == "cautious")
    s.check("starting trust = 0.25",
            abs(NPC_MEMORY.trust("jasper") - 0.25) < 0.01)

    # ── Cautious: flee on player presence ─────────────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    out, _ = cmd(w, "examine portraits")
    # At 50% flee chance, Jasper should flee at least once in 20 trials.
    # We run multiple trials to make this deterministic regardless of seed.
    fled_once = False
    for _ in range(20):
        place_jasper(w, "hall_2")
        cmd(w, "examine portraits")
        if jasper.location != "hall_2":
            fled_once = True
    s.check("cautious jasper flees at least once in 20 tries",
            fled_once or NPC_MEMORY.trust("jasper") > 0.35,
            f"trust={NPC_MEMORY.trust('jasper'):.3f}")

    # ── Pet rejected when cautious ────────────────────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    out, _ = cmd(w, "pet cat")
    s.check("pet cautious jasper -> refused",
            any(x in out.lower() for x in
                ["won't","flattens","retreats","backs","isn't here"]), out)

    # ── Call verb ─────────────────────────────────────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    out, _ = cmd(w, "call cat")
    s.check("call cat -> narrative (not error)",
            len(out) > 3 and "beg your pardon" not in out.lower(), out)

    # ── Feed at neutral: consumes food, increases trust ───────
    w = npc_fresh(); w.player.location = "hall_1"
    jasper = place_jasper(w, "hall_1")
    set_trust(neutral=True)
    move_entity(w, "cat_food", "player")
    w.entities["cat_food"].props["opened"] = True  # bypass can-opener for this test
    t0 = NPC_MEMORY.trust("jasper")
    out, _ = cmd(w, "feed cat food to cat")
    s.check("feed -> food consumed",
            w.entities["cat_food"].location == "consumed", out)
    s.check("feed -> trust increases to neutral+",
            NPC_MEMORY.trust("jasper") >= 0.55)

    # ── Feed catnip: natural arc — food first then catnip ────
    # Start fresh so precision is the natural prior, not the
    # synthetic set_trust precision which dilutes the catnip delta.
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    move_entity(w, "cat_food", "player")
    w.entities["cat_food"].props["opened"] = True  # bypass can-opener for this test
    cmd(w, "feed cat food to cat")   # -> neutral
    w.player.location = "entryway"
    jasper = place_jasper(w, "entryway")
    cmd(w, "examine hedges")
    cmd(w, "take catnip")
    t0 = NPC_MEMORY.trust("jasper")
    out, _ = cmd(w, "feed catnip to cat")
    s.check("catnip consumed when fed", w.entities["catnip"].location == "consumed", out)
    s.check("catnip increases trust to friendly+",
            NPC_MEMORY.trust("jasper") >= 0.70,
            f"trust={NPC_MEMORY.trust('jasper'):.3f}")

    # ── Offer: trust increases, item not consumed ─────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    set_trust(neutral=True)
    move_entity(w, "brass_key", "player")
    t0 = NPC_MEMORY.trust("jasper")
    out, _ = cmd(w, "offer brass key to cat")
    s.check("offer -> trust increases",     NPC_MEMORY.trust("jasper") > t0, out)
    s.check("offer -> item not consumed",   "brass_key" in w.player.inventory, out)

    # ── Pet succeeds at friendly ──────────────────────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    set_trust(friendly=True)
    s.check("friendly disposition confirmed",
            NPC_MEMORY.disposition("jasper") in ("friendly","devoted"))
    out, _ = cmd(w, "pet cat")
    s.check("pet friendly jasper -> success response",
            any(x in out.lower() for x in
                ["leans","purr","hand","closes","eye","scratch",
                 "head","ear","weight","presses"]), out)

    # ── do_look shows NPC presence ────────────────────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    from engine import do_look
    look = do_look(w)
    s.check("do_look shows cat when present",
            "cat" in look.lower() or "jasper" in look.lower(), look)
    s.check("do_look shows disposition context",
            any(x in look.lower() for x in
                ["distance", "watching", "here", "nearby", "wall"]), look)

    # Disposition changes look message
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    set_trust(friendly=True)
    look_friendly = do_look(w)
    s.check("friendly jasper gets warmer look message",
            any(x in look_friendly.lower() for x in
                ["tail", "bright", "side", "close", "here"]), look_friendly)

    # ── Movement suppresses NPC presence line ────────────────
    # When the player moves into a room, the NPC tick fires an
    # enters_room message.  do_look (called by handle_go) should
    # NOT also add a presence line — that would double-describe.
    w = npc_fresh(); w.player.location = "hall_1"
    jasper = place_jasper(w, "hall_2")
    # Set wary so cat stays and tick fires enters_room message
    NPC_MEMORY.reputation("jasper").confirmations    = 5.0
    NPC_MEMORY.reputation("jasper").disconfirmations = 6.0
    out, _ = cmd(w, "go north")
    # Room desc + exits should appear, but NOT both a presence
    # line in the room desc AND a tick message.
    room_section = out.split("Exits:")[0] if "Exits:" in out else out
    cat_count = room_section.lower().count("cat")
    s.check("movement: at most one cat mention before Exits line",
            cat_count <= 1, f"count={cat_count} in: {room_section!r}")

    # Explicit look shows presence line
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    from engine import do_look
    look = do_look(w, show_npcs=True)
    s.check("explicit look: cat presence line present",
            "cat" in look.lower(), look)
    look_no_npc = do_look(w, show_npcs=False)
    room_part = look_no_npc.split("Exits:")[0]
    s.check("do_look show_npcs=False: no cat in room description",
            "cat" not in room_part.lower(), room_part)

    # ── No hardcoded Jasper name in messages ──────────────────
    from npc import JASPER_MESSAGES, get_message
    jasper_in_pool = any(
        "Jasper" in msg
        for pool in JASPER_MESSAGES.values()
        for msg in pool
    )
    s.check("no hardcoded Jasper name in message pools",
            not jasper_in_pool)

    # All devoted messages use neutral cat references
    devoted_msg = get_message("jasper", "enters_room", "devoted")
    s.check("devoted enters_room: no Jasper name",
            devoted_msg is not None and "Jasper" not in devoted_msg,
            devoted_msg)

    # ── Devoted: follows player anywhere ──────────────────────
    w = npc_fresh()
    w.player.location = "foyer"
    move_entity(w, "brass_key", "player")
    cmd(w, "unlock door with brass key")
    cmd(w, "open door")
    jasper = place_jasper(w, "hall_2")
    NPC_MEMORY.reputation("jasper").confirmations    = 35.0
    NPC_MEMORY.reputation("jasper").disconfirmations = 4.0
    s.check("devoted disposition confirmed",
            NPC_MEMORY.disposition("jasper") == "devoted")
    import random as _rnd
    _rnd.seed(1)  # seed avoids the 5% devoted wander firing
    w.player.location = "hall_2"
    cmd(w, "go south")  # hall_2 -> hall_1
    s.check("devoted jasper follows to hall_1",
            jasper.location == "hall_1")
    cmd(w, "go south")  # hall_1 -> foyer
    s.check("devoted jasper follows outside home_rooms (foyer)",
            jasper.location == "foyer")
    cmd(w, "go south")  # foyer -> entryway
    s.check("devoted jasper follows to outdoor area",
            jasper.location == "entryway")

    # ── Trust does not persist between sessions ────────────────
    from npc_bayesian import NPCMemory as _NPCMemory
    import pathlib as _pathlib
    _pathlib.Path("./npc_memory.json").unlink(missing_ok=True)

    # Session 1: build trust and save
    mem1 = _NPCMemory("./npc_memory.json")
    mem1.register_events("jasper", JASPER_EVENTS)
    mem1.reputation("jasper").confirmations    = 20.0
    mem1.reputation("jasper").disconfirmations = 4.0
    mem1.save()
    s.check("npc_memory.json not created (no persistent_data)",
            not _pathlib.Path("./npc_memory.json").exists())

    # Session 2: reload — trust should be default
    mem2 = _NPCMemory("./npc_memory.json")
    mem2.register_events("jasper", JASPER_EVENTS)
    s.check("trust resets to prior on reload",
            abs(mem2.trust("jasper") - 2.0/8.0) < 0.001,
            f"trust={mem2.trust('jasper'):.3f}")
    s.check("disposition is cautious after reload",
            mem2.disposition("jasper") == "cautious")

    # Session 1b: persistent_data survives reload
    mem3 = _NPCMemory("./npc_memory.json")
    mem3.register_events("jasper", JASPER_EVENTS)
    mem3.reputation("jasper").persistent_data["test_key"] = "test_value"
    mem3.save()
    s.check("npc_memory.json created with persistent_data",
            _pathlib.Path("./npc_memory.json").exists())

    mem4 = _NPCMemory("./npc_memory.json")
    mem4.register_events("jasper", JASPER_EVENTS)
    s.check("persistent_data survives reload",
            mem4.reputation("jasper").persistent_data.get("test_key") == "test_value")
    s.check("trust still resets with persistent_data",
            abs(mem4.trust("jasper") - 2.0/8.0) < 0.001)
    _pathlib.Path("./npc_memory.json").unlink(missing_ok=True)

    # ── "follows" message pool ────────────────────────────────
    from npc import get_message
    follows_msgs = [get_message("jasper", "follows", "devoted")
                    for _ in range(10)]
    s.check("follows pool returns messages",
            any(m is not None for m in follows_msgs))
    s.check("follows messages describe arrival",
            any(m and any(w in m.lower() for w in
                          ["pads", "follows", "slips", "trots", "rounds"])
                for m in follows_msgs))
    s.check("follows messages do not describe cat already present",
            not any(m and "sitting" in m.lower() for m in follows_msgs))

    # "enters_room" at devoted — check all pool messages, not a random draw
    er_devoted_pool = JASPER_MESSAGES.get(("enters_room", "devoted"), [])
    s.check("enters_room/devoted: cat already present phrasing",
            len(er_devoted_pool) > 0 and
            any(p in m.lower()
                for m in er_devoted_pool
                for p in ["is here", "gets up", "comes to meet", "pads toward"]))

    # ── Devoted wander (5%) fires across many turns ───────────
    import random as _random
    _random.seed(99)
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    NPC_MEMORY.reputation("jasper").confirmations    = 35.0
    NPC_MEMORY.reputation("jasper").disconfirmations = 6.0
    wanders = 0
    for _ in range(100):
        place_jasper(w, "hall_2")
        cmd(w, "examine portraits")
        if jasper.location != "hall_2":
            wanders += 1
    s.check("devoted wander fires 1-20 times in 100 turns",
            1 <= wanders <= 20, f"wandered {wanders}/100")

    # ── Non-devoted wander stays in home_rooms ────────────────
    w = npc_fresh()
    w.player.location = "foyer"
    jasper = place_jasper(w, "hall_2")
    NPC_MEMORY.reputation("jasper").confirmations    = 5.0
    NPC_MEMORY.reputation("jasper").disconfirmations = 6.0
    home_rooms = jasper.defn.home_rooms
    for _ in range(50):
        cmd(w, "examine portraits")
    s.check("non-devoted wander stays in home_rooms",
            jasper.location in home_rooms,
            f"ended in {jasper.location}")

    # ── Bug: non-food items rejected ──────────────────────────
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    set_trust(neutral=True)
    move_entity(w, "matchbox", "player")
    out, _ = cmd(w, "feed cat with matches")
    s.check("non-food item refused",
            "isn't interested" in out.lower() or
            "not interested" in out.lower(), out)
    s.check("non-food item not consumed",
            w.entities["matchbox"].location != "consumed")

    # Cat food still accepted
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    set_trust(neutral=True)
    move_entity(w, "cat_food", "player")
    w.entities["cat_food"].props["opened"] = True  # bypass can-opener for this test
    out, _ = cmd(w, "feed cat food to cat")
    s.check("cat food accepted",
            w.entities["cat_food"].location == "consumed", out)

    # ── Bug: devoted wander produces departure message ─────────
    import random as _random
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    NPC_MEMORY.reputation("jasper").confirmations    = 35.0
    NPC_MEMORY.reputation("jasper").disconfirmations = 4.0
    wander_msg_found = False
    for _seed in range(200):
        _random.seed(_seed)
        place_jasper(w, "hall_2")
        out, _ = cmd(w, "pet cat")
        if jasper.location != "hall_2" and "slips away" in out.lower():
            wander_msg_found = True
            break
    s.check("devoted wander produces departure message", wander_msg_found)

    # ── Bug: kick does not return cat to same room same turn ───
    w = npc_fresh(); w.player.location = "hall_2"
    jasper = place_jasper(w, "hall_2")
    _random.seed(0)
    out, _ = cmd(w, "kick cat")
    s.check("cat leaves room after kick",
            jasper.location != "hall_2", jasper.location)

    # ── Cleanup ───────────────────────────────────────────────
    _npc_cleanup()

    return s


# ============================================================
# SECTION 11 — Descriptive-noun fallback  [symbolic]
# ============================================================

def test_descriptive_noun_fallback() -> Suite:
    s = Suite("SECTION 11 — Descriptive-noun fallback")

    # ── phrase_in_room_text utility ───────────────────────────────
    from engine import phrase_in_room_text

    w = fresh()
    w.player.location = "entryway"
    s.check("overgrowth found in entryway desc",
            phrase_in_room_text(w, "overgrowth"))
    s.check("flagstones found in entryway desc",
            phrase_in_room_text(w, "flagstones"))
    s.check("roots found in entryway desc",
            phrase_in_room_text(w, "roots"))
    s.check("dragon not found in entryway desc",
            not phrase_in_room_text(w, "dragon"))
    s.check("random not found in entryway desc",
            not phrase_in_room_text(w, "random word xyz"))

    w2 = fresh(); w2.player.location = "foyer"
    s.check("chandelier found in foyer (scenery entity name)",
            phrase_in_room_text(w2, "chandelier"))

    # ── examine: soft response for descriptive nouns ──────────────
    w = fresh(); w.player.location = "entryway"
    out, ok = cmd(w, "examine overgrowth")
    s.check("examine overgrowth: soft response (not hard denial)",
            "don't see" not in out.lower(), out)
    s.check("examine overgrowth: no pending clarification",
            ok is None)

    w = fresh(); w.player.location = "entryway"
    out, _ = cmd(w, "examine roots")
    s.check("examine roots: soft response", "don't see" not in out.lower(), out)

    # ── examine: genuine missing entity still hard-denies ─────────
    w = fresh(); w.player.location = "entryway"
    out, ok = cmd(w, "examine dragon")
    s.check("examine dragon: hard denial", "don't see" in out.lower(), out)
    s.check("examine dragon: not consumed (False)", not ok)

    # ── take: soft response for descriptive nouns ─────────────────
    w = fresh(); w.player.location = "entryway"
    out, ok = cmd(w, "take overgrowth")
    s.check("take overgrowth: soft response",
            "don't see" not in out.lower() and len(out) > 0, out)
    s.check("take overgrowth: not consumed", not ok)

    w = fresh(); w.player.location = "entryway"
    out, _ = cmd(w, "take dragon")
    s.check("take dragon: hard denial", "don't see" in out.lower(), out)

    # ── Real entities still examined normally ─────────────────────
    w = fresh(); w.player.location = "entryway"
    out, ok = cmd(w, "examine hedges")
    s.check("examine hedges (real entity): desc returned",
            "hedge" in out.lower() or "ornamental" in out.lower(), out)
    s.check("examine hedges: no pending clarification", ok is None)

    # ── push/pull: soft response for descriptive nouns ────────────
    w = fresh(); w.player.location = "entryway"
    out, ok = cmd(w, "push flagstones")
    # flagstones is a real entity so will get entity response, not soft
    # but overgrowth is not an entity so should get soft
    w2 = fresh(); w2.player.location = "entryway"
    out2, ok2 = cmd(w2, "push overgrowth")
    s.check("push descriptive noun: soft response",
            "don't see" not in out2.lower() and len(out2) > 0, out2)
    s.check("push descriptive noun: not consumed", not ok2)

    return s


# ============================================================
# SECTION 12 — Parser fixes: lock, synonyms, plurals  [symbolic]
# ============================================================

def test_parser_fixes() -> Suite:
    s = Suite("SECTION 12 — Parser fixes: lock, synonyms, plurals")

    # ── #1: lock verb works ───────────────────────────────────
    w = fresh(); w.player.location = "foyer"
    move_entity(w, "brass_key", "player")
    cmd(w, "unlock door with brass key")
    cmd(w, "open door")
    cmd(w, "close door")
    out, _ = cmd(w, "lock door with brass key")
    s.check("#1 lock door: sensible response",
            "aren't holding that" not in out.lower(), out)
    s.check("#1 lock door: lock-related response",
            any(x in out.lower() for x in
                ["lock", "doesn't", "locked", "need to", "close"]), out)

    # ── #4: unlight → extinguish ──────────────────────────────
    w = fresh(); w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    move_entity(w, "matchbox", "player")
    cmd(w, "fill lamp with oil")
    cmd(w, "light lamp")
    out, _ = cmd(w, "unlight lamp")
    s.check("#4 unlight lamp: extinguishes (not 'already lit')",
            "already lit" not in out.lower(), out)
    s.check("#4 unlight lamp: lamp is extinguished",
            any(x in out.lower() for x in
                ["flame", "dies", "dark", "out", "snuff", "rushes",
                 "extinguish"]), out)

    # ── #16: dismount synonym ─────────────────────────────────
    w = fresh(); w.player.location = "trophy_room"
    out, _ = cmd(w, "dismount broadsword")
    s.check("#16 dismount: not missing_verb",
            "want to do with the dismount" not in out.lower(), out)
    s.check("#16 dismount: broadsword taken down",
            any(x in out.lower() for x in ["broadsword", "wall", "down"]), out)

    # ── #10: plural entity references ─────────────────────────
    w = fresh(); w.player.location = "foyer"
    out, _ = cmd(w, "grab the keys")
    s.check("#10 grab the keys: not hard denial",
            "don't see that here" not in out.lower(), out)

    w = fresh(); w.player.location = "entryway"
    out, _ = cmd(w, "examine hedges")
    s.check("#10 examine hedges (plural): resolves to garden_hedges",
            "hedge" in out.lower() or "ornamental" in out.lower() or
            "catnip" in out.lower(), out)

    # ── #14a: drink returns sensible refusal ──────────────────
    w = fresh(); w.player.location = "foyer"
    move_entity(w, "lamp_oil", "player")
    out, _ = cmd(w, "drink lamp oil")
    s.check("#14a drink lamp oil: not routed to fill/pour",
            "reservoir" not in out.lower(), out)
    s.check("#14a drink lamp oil: sensible refusal",
            any(x in out.lower() for x in
                ["drink", "terrible", "can't", "would"]), out)

    # ── #14b: eat cat returns sensible refusal ────────────────
    w = fresh(); w.player.location = "hall_2"
    from engine import get_npc_instances
    import engine as _eng
    _eng._NPC_INSTANCES.clear()  # ensure fresh NPC placement
    get_npc_instances(w)
    out, _ = cmd(w, "eat cat")
    s.check("#14b eat cat: not routed to attack",
            "bolts" not in out.lower() and "kick" not in out.lower(), out)
    s.check("#14b eat cat: sensible refusal",
            any(x in out.lower() for x in
                ["eat", "cant", "edible", "can"]), out)

    # ── #11: try X to unlock Y ────────────────────────────────
    w = fresh(); w.player.location = "foyer"
    move_entity(w, "brass_key", "player")
    out, _ = cmd(w, "try the brass key to unlock the oak door")
    s.check("#11 try key: not 'aren't holding that'",
            "aren't holding that" not in out.lower(), out)
    s.check("#11 try key: meaningful response",
            any(x in out.lower() for x in
                ["lock", "door", "key", "unlocked", "unlock"]), out)

    # ── slip should NOT route to drink ────────────────────────
    w = fresh(); w.player.location = "foyer"
    move_entity(w, "brass_key", "player")
    move_entity(w, "wooden_box", "player")
    out, _ = cmd(w, "slip the brass key into the box")
    s.check("slip into box: not routed to drink",
            "drink" not in out.lower() and
            "sip" not in out.lower(), out)

    return s


# ============================================================
# SECTION 13 — World expansion (upstairs, gatehouse, forest)  [symbolic]
# ============================================================

def test_world_expansion() -> Suite:
    s = Suite("SECTION 13 — World expansion")

    # ── Room existence ────────────────────────────────────────
    w = fresh()
    for rid in ["upstairs_landing", "bedroom_east", "bedroom_west",
                "gatehouse_interior", "cobbled_road", "forest_path",
                "bridge", "forest_edge",
                "forest_a", "forest_b", "forest_c", "forest_d"]:
        s.check(f"{rid} exists", rid in w.rooms, rid)

    # ── Connection integrity ──────────────────────────────────
    s.check("foyer up -> upstairs_landing",
            w.rooms["foyer"].exits.get("up") == "upstairs_landing")
    s.check("upstairs_landing down -> foyer",
            w.rooms["upstairs_landing"].exits.get("down") == "foyer")
    s.check("upstairs_landing east -> bedroom_east",
            w.rooms["upstairs_landing"].exits.get("east") == "bedroom_east")
    s.check("upstairs_landing west -> bedroom_west",
            w.rooms["upstairs_landing"].exits.get("west") == "bedroom_west")
    s.check("gatehouse east -> gatehouse_interior",
            w.rooms["gatehouse"].exits.get("east") == "gatehouse_interior")
    s.check("road chain: interior->road->path->bridge",
            w.rooms["gatehouse_interior"].exits.get("east") == "cobbled_road" and
            w.rooms["cobbled_road"].exits.get("east") == "forest_path" and
            w.rooms["forest_path"].exits.get("east") == "bridge")
    s.check("bridge has no east exit (placeholder)",
            "east" not in w.rooms["bridge"].exits)
    s.check("wooded_path west -> forest_edge",
            w.rooms["wooded_path"].exits.get("west") == "forest_edge")

    # ── Item locations ────────────────────────────────────────
    s.check("oil_lamp in bedroom_east",
            w.entities["oil_lamp"].location == "bedroom_east")
    s.check("lamp_oil in gatehouse_interior",
            w.entities["lamp_oil"].location == "gatehouse_interior")
    s.check("can_opener in kitchen",
            w.entities["can_opener"].location == "kitchen")

    # ── Jasper home_rooms include upstairs ────────────────────
    from npc import JASPER
    s.check("upstairs_landing in jasper home_rooms",
            "upstairs_landing" in JASPER.home_rooms)
    s.check("bedroom_east in jasper home_rooms",
            "bedroom_east" in JASPER.home_rooms)
    s.check("bedroom_west in jasper home_rooms",
            "bedroom_west" in JASPER.home_rooms)

    # ── Navigation ────────────────────────────────────────────
    w = fresh(); w.player.location = "foyer"
    cmd(w, "go up")
    s.check("go up -> upstairs_landing", w.player.location == "upstairs_landing")
    cmd(w, "go down")
    s.check("go down -> foyer", w.player.location == "foyer")
    cmd(w, "go upstairs")
    s.check("go upstairs idiom", w.player.location == "upstairs_landing")

    w = fresh(); w.player.location = "entryway"
    cmd(w, "go east")   # -> gatehouse
    cmd(w, "go east")   # -> gatehouse_interior
    cmd(w, "go east")   # -> cobbled_road
    cmd(w, "go east")   # -> forest_path
    cmd(w, "go east")   # -> bridge
    s.check("full road chain navigable", w.player.location == "bridge")
    out, _ = cmd(w, "go east")  # bridge east blocked
    s.check("bridge east blocked",
            w.player.location == "bridge" and
            "can't" in out.lower(), out)

    w = fresh(); w.player.location = "wooded_path"
    cmd(w, "go west")   # -> forest_edge
    s.check("wooded_path -> forest_edge", w.player.location == "forest_edge")
    cmd(w, "go north")  # -> forest_a
    s.check("forest_edge -> forest_a", w.player.location == "forest_a")

    # ── Forest maze: all four rooms reachable from edge ───────
    reachable = set()
    w = fresh(); w.player.location = "forest_edge"
    for direction in ["north", "west", "south"]:
        w2 = fresh(); w2.player.location = "forest_edge"
        cmd(w2, f"go {direction}")
        reachable.add(w2.player.location)
    s.check("three directions from edge reach three maze rooms",
            len(reachable) == 3 and all(r.startswith("forest_") for r in reachable))

    # Maze has an exit: forest_d west leads back to forest_edge
    s.check("forest_d west -> forest_edge (escape route)",
            w.rooms["forest_d"].exits.get("west") == "forest_edge")
    # Player can navigate from edge into maze and back out
    w2 = fresh(); w2.player.location = "forest_edge"
    cmd(w2, "go north")  # -> forest_a
    cmd(w2, "go west")   # -> forest_d  (forest_a west = forest_d)
    cmd(w2, "go west")   # -> forest_edge (forest_d west = forest_edge)
    s.check("player can escape maze via forest_d west",
            w2.player.location == "forest_edge",
            w2.player.location)

    # ── Servants staircase ───────────────────────────────────
    w = fresh()
    s.check("hall_2 up -> upstairs_landing",
            w.rooms["hall_2"].exits.get("up") == "upstairs_landing")
    s.check("upstairs_landing south -> hall_2",
            w.rooms["upstairs_landing"].exits.get("south") == "hall_2")
    s.check("upstairs_landing down -> foyer (main stair)",
            w.rooms["upstairs_landing"].exits.get("down") == "foyer")
    s.check("servants_stair entity in hall_2",
            "servants_stair" in w.rooms["hall_2"].entities)

    # Jasper can wander from hall_2 to upstairs via servants stair
    from npc import _choose_wander_destination, JASPER, JASPER_EVENTS as _JE
    import engine as _engine
    import random as _rnd
    _engine._NPC_INSTANCES.clear()
    _engine.NPC_MEMORY._store.clear()
    _engine.NPC_MEMORY.register_events("jasper", _JE)
    w2 = build_demo_world()
    npcs2 = _engine.get_npc_instances(w2)
    jasper2 = npcs2["jasper"]
    jasper2.location = "hall_2"
    _rnd.seed(42)
    dests = set(_choose_wander_destination(jasper2, w2) for _ in range(50))
    dests.discard(None)
    s.check("jasper wander from hall_2 includes upstairs_landing",
            "upstairs_landing" in dests, str(dests))
    s.check("jasper wander stays in home_rooms",
            all(d in JASPER.home_rooms for d in dests),
            str(dests - JASPER.home_rooms))

    # ── Puzzle: items needed before lamp useful ───────────────
    # Player must fetch lamp from bedroom_east and oil from gatehouse_interior
    w = fresh(); w.player.location = "bedroom_east"
    out, _ = cmd(w, "take lamp")
    s.check("lamp takeable in bedroom_east",
            "oil_lamp" in w.player.inventory, out)

    return s


# ============================================================
# SECTION 14 — Troll NPC (bridge riddle)  [symbolic]
# ============================================================

def test_troll() -> Suite:
    import pathlib as _pl
    import random as _rnd
    import engine as _eng
    from troll import (
        TrollMemory as _TM, check_answer as _ca,
        get_riddle_by_id as _gri, RIDDLES_TO_PASS as _RTP,
    )

    s = Suite("SECTION 14 — Troll NPC")

    def troll_fresh():
        """Return a fresh world with player at bridge and troll memory reset."""
        _eng._NPC_INSTANCES.clear()
        _pl.Path("./troll_memory.json").unlink(missing_ok=True)
        _pl.Path("./npc_memory.json").unlink(missing_ok=True)
        _eng.NPC_MEMORY._store.clear()
        from npc import JASPER_EVENTS as _JEV
        _eng.NPC_MEMORY.register_events("jasper", _JEV)
        _eng.TROLL_MEMORY.reset()
        w = build_demo_world()
        w.player.location = "bridge"
        return w

    # ── Entity and world structure ────────────────────────────
    w = troll_fresh()
    s.check("troll entity at bridge", "troll" in w.rooms["bridge"].entities)
    s.check("bridge has no east exit initially",
            "east" not in w.rooms["bridge"].exits)

    # ── Troll tick poses a riddle ─────────────────────────────
    w = troll_fresh()
    _rnd.seed(42)
    cmd(w, "examine stream")  # triggers tick
    s.check("riddle is posed after tick",
            _eng.TROLL_MEMORY.state().current_rid is not None)

    # ── Answer normalisation ──────────────────────────────────
    r01 = _gri("r01")  # "a map"
    s.check("bare answer accepted",        _ca(r01, "map"))
    s.check("article accepted",            _ca(r01, "a map"))
    s.check("preamble stripped",           _ca(r01, "the answer is a map"))
    s.check("i think it is stripped",      _ca(r01, "i think it is a map"))
    s.check("wrong answer rejected",       not _ca(r01, "banana"))
    r02 = _gri("r02")  # "footsteps"
    s.check("synonym accepted (steps)",    _ca(r02, "steps"))
    r03 = _gri("r03")  # "an echo"
    s.check("echo accepted",               _ca(r03, "echo"))
    r05 = _gri("r05")  # "a clock"
    s.check("watch accepted as clock",     _ca(r05, "watch"))

    # ── Wrong answer: troll gloats ────────────────────────────
    w = troll_fresh()
    state = _eng.TROLL_MEMORY.state()
    state.current_rid = "r01"
    out, _ = cmd(w, "answer banana")
    s.check("wrong answer: gloat message",
            any(x in out.lower() for x in
                ["wrong", "no", "not right", "ohhh", "close"]), out)
    s.check("wrong answer: weight increases",
            _eng.TROLL_MEMORY.state().weights.get("r01", 1.0) > 1.0)

    # ── Correct answer ────────────────────────────────────────
    w = troll_fresh()
    state = _eng.TROLL_MEMORY.state()
    state.current_rid = "r01"
    out, _ = cmd(w, "answer a map")
    s.check("correct answer: success message",
            any(x in out.lower() for x in
                ["correct", "right", "hm", "recalculating"]), out)
    s.check("correct_count incremented",
            _eng.TROLL_MEMORY.state().correct_count == 1)
    s.check("solved list updated",
            "r01" in _eng.TROLL_MEMORY.state().solved)
    s.check("solved riddle weight zeroed",
            _eng.TROLL_MEMORY.state().weights.get("r01", 1.0) == 0.0)

    # ── Full progression: 3 correct opens bridge ──────────────
    w = troll_fresh()
    _answers = {"r01": "map", "r02": "footsteps", "r03": "echo"}
    for rid, ans in _answers.items():
        _eng.TROLL_MEMORY.state().current_rid = rid
        _eng.TROLL_MEMORY.save()
        cmd(w, f"answer {ans}")
    s.check("bridge opens after 3 correct",
            _eng.TROLL_MEMORY.state().bridge_open)
    s.check("east exit added to bridge room",
            "east" in w.rooms["bridge"].exits)

    # ── Weighted sampler never picks solved riddles ───────────
    w = troll_fresh()
    state = _eng.TROLL_MEMORY.state()
    state.solved = ["r01", "r02"]
    state.weights["r01"] = 0.0
    state.weights["r02"] = 0.0
    _rnd.seed(7)
    picks = set()
    for _ in range(30):
        r = state.pick_riddle()
        if r: picks.add(r.rid)
    s.check("solved riddles not re-asked",
            "r01" not in picks and "r02" not in picks, str(picks))

    # ── Examine troll ─────────────────────────────────────────
    w = troll_fresh()
    out, _ = cmd(w, "examine troll")
    s.check("examine troll: description returned",
            any(x in out.lower() for x in
                ["large", "grey", "eyes", "skin"]), out)

    # Cleanup
    _pl.Path("./troll_memory.json").unlink(missing_ok=True)
    _pl.Path("./npc_memory.json").unlink(missing_ok=True)

    return s


# ============================================================
# SECTION 15 — Combat system (slime golem)  [symbolic]
# ============================================================

def test_combat() -> Suite:
    import pathlib as _pl
    import random as _rnd
    import engine as _eng
    from combat import (
        CombatSession, start_combat, process_player_combat_action,
        WEAPON_STATS,
    )
    from npc_qlearning import CombatMemory as _CM

    s = Suite("SECTION 15 — Combat system")

    def combat_fresh():
        """World with combat memory reset and player at vault."""
        _eng._NPC_INSTANCES.clear()
        _pl.Path("./npc_memory.json").unlink(missing_ok=True)
        _pl.Path("./combat_memory.json").unlink(missing_ok=True)
        _eng.NPC_MEMORY._store.clear()
        from npc import JASPER_EVENTS as _JEV
        _eng.NPC_MEMORY.register_events("jasper", _JEV)
        _eng.COMBAT_MEMORY = _CM()
        _eng._COMBAT_SESSION = None
        w = build_demo_world()
        # Open vault manually for testing
        w.rooms["cellar"].exits["south"] = "vault"
        w.player.location = "vault"
        return w

    # ── World structure ───────────────────────────────────────
    w = build_demo_world()
    s.check("vault room exists", "vault" in w.rooms)
    s.check("vault north -> cellar",
            w.rooms["vault"].exits.get("north") == "cellar")
    s.check("cellar south absent at start",
            "south" not in w.rooms["cellar"].exits)
    s.check("slime_golem entity exists",
            "slime_golem" in w.entities)
    s.check("golem starts in vault",
            w.entities["slime_golem"].location == "vault")
    s.check("golem_remains starts hidden",
            w.entities["golem_remains"].location == "hidden")
    s.check("vault_door starts hidden",
            w.entities["vault_door"].location == "hidden")

    # ── Weapon stats ──────────────────────────────────────────
    s.check("broadsword two_handed",
            w.entities["broadsword"].props.get("two_handed") == True)
    s.check("iron_mace two_handed",
            w.entities["iron_mace"].props.get("two_handed") == True)
    s.check("hunting_knife one_handed",
            w.entities["hunting_knife"].props.get("two_handed") == False)
    s.check("mace deals more base damage than knife",
            WEAPON_STATS["iron_mace"]["damage_range"][0] >
            WEAPON_STATS["hunting_knife"]["damage_range"][0])
    s.check("mace costs more stamina than knife",
            WEAPON_STATS["iron_mace"]["stamina_cost"] >
            WEAPON_STATS["hunting_knife"]["stamina_cost"])

    # ── Equipment exclusion ───────────────────────────────────
    w2 = build_demo_world()
    # Get broadsword into inventory
    from engine import move_entity
    move_entity(w2, "broadsword", "player")
    move_entity(w2, "kite_shield", "player")
    move_entity(w2, "hunting_knife", "player")
    move_entity(w2, "chain_coif", "player")
    w2.player.location = "trophy_room"
    # Wear shield, then try to wield broadsword
    cmd(w2, "wear shield")
    s.check("shield worn", w2.entities["kite_shield"].props.get("worn"))
    out, _ = cmd(w2, "wield broadsword")
    s.check("two-handed weapon blocked while shield worn",
            "two-handed" in out.lower() or "shield" in out.lower(), out)
    s.check("broadsword not wielded",
            w2.player.wielded_weapon != "broadsword")
    # Remove shield, now wield broadsword
    cmd(w2, "remove shield")
    cmd(w2, "wield broadsword")
    s.check("broadsword wielded after shield removed",
            w2.player.wielded_weapon == "broadsword")
    # Now try to wear shield while broadsword wielded
    out, _ = cmd(w2, "wear shield")
    s.check("shield blocked while two-handed weapon wielded",
            "two-handed" in out.lower() or "shield" in out.lower(), out)
    # Knife + shield: allowed
    cmd(w2, "wield hunting knife")
    cmd(w2, "wear shield")
    s.check("knife + shield allowed",
            w2.entities["kite_shield"].props.get("worn") and
            w2.player.wielded_weapon == "hunting_knife")

    # ── Combat session mechanics ──────────────────────────────
    _rnd.seed(42)
    session = CombatSession()
    learner = _eng.COMBAT_MEMORY.learner("slime_golem")
    # Attack with bare hands
    narr, outcome = process_player_combat_action(session, "attack", learner)
    s.check("attack produces narrative", len(narr) > 0)
    s.check("attack outcome is continue or terminal",
            outcome in ("continue","player_dead","golem_dead","fled","invalid"))
    s.check("golem took damage from attack",
            session.golem_hp < 120)

    # Dodge: recovers stamina
    session2 = CombatSession(player_stamina=50)
    process_player_combat_action(session2, "dodge", learner)
    s.check("dodge recovers stamina (net positive)",
            session2.player_stamina >= 50,
            str(session2.player_stamina))

    # Heavy attack: WEAPON_STATS verify heavier cost
    s.check("heavy attack total cost > normal attack cost (bare hands)",
            (WEAPON_STATS["bare_hands"]["stamina_cost"] +
             WEAPON_STATS["bare_hands"]["heavy_cost"]) >
             WEAPON_STATS["bare_hands"]["stamina_cost"])
    s.check("heavy attack total cost > normal attack cost (broadsword)",
            (WEAPON_STATS["broadsword"]["stamina_cost"] +
             WEAPON_STATS["broadsword"]["heavy_cost"]) >
             WEAPON_STATS["broadsword"]["stamina_cost"])

    # Stamina exhaustion gate
    session4 = CombatSession(player_stamina=0)
    _, outcome4 = process_player_combat_action(session4, "attack", learner)
    s.check("exhausted player cannot attack",
            outcome4 == "invalid")

    # ── Coif reduces damage ───────────────────────────────────
    import random as _r
    _r.seed(99)
    dmg_bare  = []
    dmg_coif  = []
    for i in range(20):
        _r.seed(i)
        s_bare = CombatSession(wearing_coif=False)
        process_player_combat_action(s_bare, "dodge", learner)
        dmg_bare.append(100 - s_bare.player_hp)
        _r.seed(i)
        s_coif = CombatSession(wearing_coif=True)
        process_player_combat_action(s_coif, "dodge", learner)
        dmg_coif.append(100 - s_coif.player_hp)
    avg_bare = sum(dmg_bare) / len(dmg_bare)
    avg_coif = sum(dmg_coif) / len(dmg_coif)
    s.check("chain coif reduces average incoming damage",
            avg_coif <= avg_bare,
            f"bare={avg_bare:.1f} coif={avg_coif:.1f}")

    # ── Vault opens when troll bridge opens ───────────────────
    w3 = build_demo_world()
    from troll import TrollMemory as _TM
    _eng.TROLL_MEMORY.reset()
    _answers = {"r01": "map", "r02": "footsteps", "r03": "echo"}
    w3.player.location = "bridge"
    for rid, ans in _answers.items():
        _eng.TROLL_MEMORY.state().current_rid = rid
        _eng.TROLL_MEMORY.save()
        cmd(w3, f"answer {ans}")
    s.check("vault opens when troll solved",
            "south" in w3.rooms["cellar"].exits)
    s.check("vault door revealed",
            w3.entities["vault_door"].location == "cellar")

    # ── Q-learner updates ─────────────────────────────────────
    _rnd.seed(7)
    learner2 = _CM().learner("slime_golem")
    s5 = CombatSession()
    state_before = dict(learner2._q)
    process_player_combat_action(s5, "attack", learner2)
    s.check("Q-table updated after combat round",
            learner2._q != state_before or s5.round_num > 1)

    # Cleanup
    _pl.Path("./npc_memory.json").unlink(missing_ok=True)
    _pl.Path("./combat_memory.json").unlink(missing_ok=True)
    _pl.Path("./troll_memory.json").unlink(missing_ok=True)
    _eng._COMBAT_SESSION = None

    return s


# ============================================================
# Registry
# ============================================================

# Each entry: section_key -> (function, tags)
# Tags govern when a section is eligible to run:
#   "fast"     — no parser needed; always eligible
#   "symbolic" — needs parser but not the embedding model; always eligible
#   "semantic" — needs the embedding model; skipped unless --semantic passed
SECTIONS: dict[str, tuple[Callable, Set[str]]] = {
    "world":         (test_world_structure,      {"fast"}),
    "doors":         (test_door_mechanics,        {"fast"}),
    "cellar":        (test_dark_cellar,           {"fast"}),
    "puzzles":       (test_puzzles,               {"symbolic"}),
    "verbs":         (test_verb_handlers,         {"symbolic"}),
    "parser":        (test_parser_disambiguation, {"symbolic"}),
    "improvement_a": (test_parser_improvement_a,  {"semantic"}),
    "improvement_b": (test_parser_improvement_b,  {"symbolic"}),
    "map_expansion": (test_map_expansion,          {"symbolic"}),
    "npc_jasper":    (test_npc_jasper,              {"symbolic"}),
    "desc_fallback": (test_descriptive_noun_fallback, {"symbolic"}),
    "parser_fixes":  (test_parser_fixes,               {"symbolic"}),
    "world_expansion":(test_world_expansion,            {"symbolic"}),
    "troll":          (test_troll,                      {"symbolic"}),
    "combat":         (test_combat,                     {"symbolic"}),
}


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    global _SEMANTIC_MODE

    args = sys.argv[1:]

    # Parse flags
    _SEMANTIC_MODE = "--semantic" in args
    args = [a for a in args if a != "--semantic"]

    # Determine which sections to run
    if args:
        to_run = []
        for arg in args:
            key = arg.lower()
            if key not in SECTIONS:
                print(f"Unknown section '{arg}'. "
                      f"Available: {', '.join(SECTIONS.keys())}")
                sys.exit(1)
            to_run.append(key)
    else:
        to_run = list(SECTIONS.keys())

    # Report mode
    mode_str = "semantic" if _SEMANTIC_MODE else "symbolic"
    print(f"\nRunning {len(to_run)} section(s) [{mode_str} mode]...")
    if not _SEMANTIC_MODE:
        semantic_sections = [k for k in to_run
                             if "semantic" in SECTIONS[k][1]]
        if semantic_sections:
            print(f"  Skipping semantic sections: {', '.join(semantic_sections)}")
            print(f"  (pass --semantic to include them)")

    # Run
    all_suites = []
    for key in to_run:
        fn, _ = SECTIONS[key]
        suite = fn()
        suite.report()
        all_suites.append(suite)

    # Count only non-skipped suites
    eligible = [s for s in all_suites if not s.skipped]
    total_passed = sum(s.passed() for s in eligible)
    total_tests  = sum(len(s.results) for s in eligible)
    skipped_count = len(all_suites) - len(eligible)

    print(f"\n{'=' * 62}")
    print(f"  Total: {total_passed}/{total_tests} passed", end="")
    if skipped_count:
        print(f"  ({skipped_count} section(s) skipped)", end="")
    print()
    if total_passed == total_tests:
        print("  All eligible tests passed.")
    else:
        failed = total_tests - total_passed
        print(f"  {failed} test(s) failed.")
    print(f"{'=' * 62}")

    # Machine-readable summary line — parseable by CI steps
    print(f"RESULT: {total_passed}/{total_tests} [semantic={'on' if _SEMANTIC_MODE else 'off'}]")

    sys.exit(0 if total_passed == total_tests else 1)


if __name__ == "__main__":
    main()