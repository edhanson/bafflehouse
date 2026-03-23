"""
test_suite.py

Consolidated regression and improvement test suite for the bafflehouse
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

    for rid in ("foyer", "hall", "library", "trophy_room",
                "secret_study", "cellar"):
        s.check(f"room '{rid}' exists", rid in w.rooms)

    s.check("display_key starts hidden",
            w.entities["display_key"].location == "hidden")
    s.check("ancient_scroll starts hidden",
            w.entities["ancient_scroll"].location == "hidden")

    s.check("journal location is hall",
            w.entities["journal"].location == "hall")
    s.check("matchbox inside wooden_box",
            "matchbox" in w.entities["wooden_box"].contains)
    s.check("journal in hall.entities",
            "journal" in w.rooms["hall"].entities)
    s.check("silver_ring inside display_case",
            "silver_ring" in w.entities["display_case"].contains)
    s.check("folded_letter inside display_case",
            "folded_letter" in w.entities["display_case"].contains)
    s.check("journal NOT inside display_case",
            "journal" not in w.entities["display_case"].contains)

    s.check("foyer has no north exit at start",
            "north" not in w.rooms["foyer"].exits)
    s.check("hall has no south exit at start",
            "south" not in w.rooms["hall"].exits)
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
    s.check("oak: passage after unlock + open", w.player.location == "hall")

    cmd(w, "close door")
    cmd(w, "go south")
    s.check("oak: blocked after close", w.player.location == "hall")

    # Study door (trophy_room <-> secret_study)
    w = door_world("trophy_room")
    cmd(w, "go north")
    s.check("study: blocked before unlock",
            w.player.location == "trophy_room")

    w = door_world("trophy_room")
    move_entity(w, "iron_key", "player")
    cmd(w, "unlock door with iron key")
    cmd(w, "go north")
    s.check("study: blocked after unlock but before open",
            w.player.location == "trophy_room")

    w = door_world("trophy_room")
    move_entity(w, "iron_key", "player")
    cmd(w, "unlock door with iron key")
    cmd(w, "open door")
    cmd(w, "go north")
    s.check("study: passage after unlock + open",
            w.player.location == "secret_study")

    cmd(w, "go south")
    s.check("study: return south through open door",
            w.player.location == "trophy_room")

    cmd(w, "go north")
    cmd(w, "close door")
    cmd(w, "go south")
    s.check("study: blocked after close",
            w.player.location == "secret_study")

    w2 = fresh()
    s.check("foyer north absent at start",
            "north" not in w2.rooms["foyer"].exits)
    s.check("hall south absent at start",
            "south" not in w2.rooms["hall"].exits)
    s.check("trophy_room north absent at start",
            "north" not in w2.rooms["trophy_room"].exits)
    s.check("secret_study south always present",
            "south" in w2.rooms["secret_study"].exits)

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
    s.check("oil_lamp always visible",             "oil_lamp"     in  vis)
    s.check("lamp_oil always visible",             "lamp_oil"     in  vis)

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
    s.check("puzzle 0: reach hall via oak door",
            w.player.location == "hall")

    # Puzzle 1: fill + light + pull lever
    w = fresh()
    w.player.location = "cellar"
    move_entity(w, "oil_lamp", "player")
    move_entity(w, "lamp_oil", "player")
    move_entity(w, "matchbox", "player")

    out, _ = cmd(w, "pull lever")
    s.check("puzzle 1: lever blocked without light",
            w.rooms["hall"].exits.get("west") is None)

    cmd(w, "fill lamp with oil")
    cmd(w, "light lamp")
    s.check("puzzle 1: lamp is lit",
            w.entities["oil_lamp"].props.get("lit"))
    s.check("puzzle 1: match consumed",
            w.entities["matchbox"].props["matches_remaining"] == 9)

    cmd(w, "pull lever")
    s.check("puzzle 1: hall west passage opened",
            w.rooms["hall"].exits.get("west") == "cellar")

    # Puzzle 2: journal -> antler -> display key -> case
    w = fresh()
    w.player.location = "hall"
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
    w.player.location = "cellar"
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

    # Read
    w = fresh()
    w.player.location = "hall"
    out, _ = cmd(w, "read journal")
    s.check("read journal -> antler clue",
            "antler" in out.lower() or "stag" in out.lower(), out)

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
    w.player.location = "cellar"
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

    # B2: synonym in cellar context
    w = fresh()
    w.player.location = "cellar"
    out, p = cmd(w, "retrieve the lamp")
    s.check('B2: "retrieve the lamp" -> takes lamp',
            "oil_lamp" in w.player.inventory and p is None, out)

    w = fresh()
    w.player.location = "hall"
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