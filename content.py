# content.py
#
# World definition for the manor interactive fiction game.
#
# This file is the single authoritative source for all rooms, entities, and
# the starting player state.  The engine and parser never hard-code world
# knowledge — they operate only on the data structures built here.
#
# ROOM MAP (compass directions)
#
#         [Secret study]          <- puzzle-gated (iron key)
#               |  N/S
#         [Trophy room]           <- new, north of Hall
#               |  N/S
#   [Library] - [Hall] -          <- Library new, east of Hall
#               |  N/S
#   [Cellar]  - [Foyer]           <- Cellar new, west of Foyer
#
# PUZZLE OVERVIEW
#   Puzzle 0 (warm-up / gate):
#     Brass key (foyer) -> unlock oak door (hall) -> enter Hall
#
#   Puzzle 1 (Cellar — light + combination):
#     Fill lamp with oil -> light lamp -> explore cellar fully ->
#     pull lever -> secret passage opens into hall's west wall
#
#   Puzzle 2 (Library — read + pull + lock-and-key):
#     Read journal (library) -> go to trophy room -> pull antler ->
#     collect display key -> unlock display case (library) ->
#     take silver ring inside
#
#   Puzzle 3 (Secret study — wear + pour + combination):
#     Iron key (foyer) -> unlock study door (trophy room) ->
#     wear silver ring -> pour water (ewer, cellar) into stone basin ->
#     basin reacts, hidden contents revealed

from __future__ import annotations
from model import Entity, Player, Room, World


# ============================================================
# World builder
# ============================================================

def build_demo_world() -> World:
    """Construct and return the full starting world state."""

    # ----------------------------------------------------------
    # Rooms
    # ----------------------------------------------------------
    rooms = {
        # --- Existing rooms ---

        "foyer": Room(
            rid="foyer",
            title="Foyer",
            desc=(
                "You are standing in a small foyer. A dusty chandelier sulks overhead. "
                "A heavy oak door to the north stands between you and the rest of the manor. "
                "A narrow staircase descends to the west."
            ),
            exits={"west": "cellar"}  # "north" is added dynamically when the oak door is opened
        ),
        "hall": Room(
            rid="hall",
            title="Hall",
            desc=(
                "A long hall stretches into gloom. Stone walls are hung with faded "
                "hunting portraits. The oak door to the south leads back toward the foyer. "
                "Exits also lead east into what looks like a library and north toward a trophy room. "
                "A section of the west wall looks subtly different from the rest — "
                "the stonework is newer, as if something was once bricked over."
            ),
            exits={"east": "library", "north": "trophy_room"}
            # NOTE: "south" is added dynamically when the oak door is opened.
            # NOTE: "west" is added dynamically by the lever puzzle in handle_pull.
        ),

        # --- New rooms ---

        "library": Room(
            rid="library",
            title="Library",
            desc=(
                "Floor-to-ceiling shelves sag under the weight of neglected books. "
                "Dust motes drift in the thin light from a single high window. "
                "A locked glass display case stands against the far wall. "
                "The hall lies to the west."
            ),
            exits={"west": "hall"}
        ),
        "trophy_room": Room(
            rid="trophy_room",
            title="Trophy Room",
            desc=(
                "Animal heads and antique weapons cover every wall. In the centre of "
                "the room stands a large stone sculpture of a stag. Its proportions are "
                "slightly wrong — one antler looks heavier than the other, as if it were "
                "added later. A door to the north is fitted with an iron lock. "
                "The hall is south."
            ),
            exits={"south": "hall"}
            # NOTE: The "north" exit to the secret study is added dynamically
            # by handle_unlock when the iron key is used on the study door.
        ),
        "secret_study": Room(
            rid="secret_study",
            title="Secret Study",
            desc=(
                "A small, airless room. Shelves of leather-bound ledgers line the walls. "
                "In the centre, a shallow stone basin sits on a plinth. "
                "The basin is carved with intertwined serpents and looks very old. "
                "The door back south leads to the trophy room."
            ),
            exits={"south": "trophy_room"}
        ),
        "cellar": Room(
            rid="cellar",
            title="Wine Cellar",
            desc=(
                "A vaulted cellar. Stone racks hold the dusty ghosts of wine bottles, "
                "most long since emptied or broken. Without light, the far end of the "
                "room is impenetrably dark — you can tell something is there but cannot "
                "make it out. The foyer is back up the stairs to the east."
            ),
            exits={"east": "foyer"}
            # NOTE: The player can enter the cellar immediately, but entities
            # with "requires_light": True are only visible while carrying a lit
            # lamp.  The lever (and therefore the hall passage) requires light.
        ),
    }

    # ----------------------------------------------------------
    # Entities
    # ----------------------------------------------------------
    entities = {

        # ======================================================
        # FOYER entities (existing, unchanged)
        # ======================================================

        "brass_key": Entity(
            eid="brass_key",
            name="a brass key",
            aliases=["key", "brass key", "small key"],
            tags={"portable"},
            props={
                "desc": "A small brass key, worn smooth by anxious fingers.",
                "key_id": 1  # matches oak_door's key_id
            },
            location="foyer"
        ),
        "iron_key": Entity(
            eid="iron_key",
            name="an iron key",
            aliases=["key", "iron key", "heavy key"],
            tags={"portable"},
            props={
                "desc": "A heavy iron key. It looks like it could start arguments.",
                "key_id": 2  # matches study_door's key_id
            },
            location="foyer"
        ),
        "wooden_box": Entity(
            eid="wooden_box",
            name="a small wooden box",
            aliases=["box", "wooden box", "small box", "container", "crate"],
            tags={"openable", "container"},
            props={
                "desc": "A small wooden box with a hinged lid.",
                "open": False
            },
            location="foyer"
        ),
        "chandelier": Entity(
            eid="chandelier",
            name="a dusty chandelier",
            aliases=["chandelier", "dusty chandelier", "light fixture"],
            tags={"scenery"},
            props={
                "desc": (
                    "A dusty chandelier hangs overhead, affecting grandeur "
                    "and achieving dust."
                )
            },
            location="foyer"
        ),

        # The oak door connects hall <-> foyer.
        # Locked with key_id 1 (brass key).
        "oak_door": Entity(
            eid="oak_door",
            name="an oak door",
            aliases=["door", "oak door"],
            tags={"door", "openable", "lockable"},
            props={
                "desc": "A sturdy oak door. It looks unimpressed.",
                "open": False,
                "locked": True,
                "key_id": 1,
                "room_a": "hall",
                "room_b": "foyer"
            },
            location="hall"
        ),

        # ======================================================
        # LIBRARY entities  (Puzzle 2)
        # ======================================================

        # Locked display case — opened with display_key (key_id 3).
        # Tagged "scenery" so the player cannot pick it up.
        "display_case": Entity(
            eid="display_case",
            name="a glass display case",
            aliases=["case", "display case", "glass case", "cabinet"],
            tags={"container", "openable", "lockable", "scenery"},
            props={
                "desc": (
                    "A glass-fronted display case, locked with a small brass clasp. "
                    "Inside you can make out what looks like a leather journal "
                    "and something that gleams."
                ),
                "open": False,
                "locked": True,
                "key_id": 3
            },
            location="library"
        ),
        # The journal is inside the display case.
        # Reading it gives the clue about the stag antler.
        "journal": Entity(
            eid="journal",
            name="an old journal",
            aliases=["journal", "old journal", "book", "leather journal", "diary"],
            tags={"portable", "readable"},
            props={
                "desc": "A leather-bound journal, its pages brown with age.",
                "readable_text": (
                    "The handwriting is cramped and hurried. Most entries are mundane "
                    "household accounts, but near the back you find an entry that reads:\n\n"
                    "\"I have hidden the reserve key in the old way — the stag knows "
                    "where it rests. A firm pull on the heavy antler will remind him.\""
                ),
            },
            location="display_case"
        ),

        # ======================================================
        # TROPHY ROOM entities  (Puzzle 2 clue + Puzzle 3 gate)
        # ======================================================

        # The stone stag is scenery — cannot be taken.
        # "antler" aliases are added after the dict so we can reference it cleanly.
        # PULL stag/antler -> drops display_key, sets "pulled": True.
        "stone_stag": Entity(
            eid="stone_stag",
            name="a stone stag",
            aliases=["stag", "stone stag", "sculpture", "statue",
                     "antler", "stag antler", "heavy antler"],
            tags={"scenery", "pullable"},
            props={
                "desc": (
                    "A life-sized stone stag. The craftsmanship is impressive but one "
                    "antler looks heavier than the other, as if it were cast separately "
                    "and bolted on. It invites curiosity."
                ),
                "pulled": False,
            },
            location="trophy_room"
        ),
        # The display key starts in "hidden" (not any room or container).
        # engine.py's handle_pull moves it to trophy_room when the stag is pulled.
        "display_key": Entity(
            eid="display_key",
            name="a small tarnished key",
            aliases=["key", "tarnished key", "display key", "cabinet key"],
            tags={"portable"},
            props={
                "desc": "A small tarnished key on a short chain. Looks like it fits a cabinet clasp.",
                "key_id": 3  # matches display_case's key_id
            },
            location="hidden"
        ),

        # The study door connects trophy_room <-> secret_study.
        # Locked with key_id 2 (iron key).
        # Unlocking it also adds the "north" exit to trophy_room (see engine.py).
        "study_door": Entity(
            eid="study_door",
            name="a heavy iron door",
            aliases=["door", "iron door", "study door", "north door"],
            tags={"door", "openable", "lockable"},
            props={
                "desc": "A heavy door fitted with a large iron lock. It looks serious.",
                "open": False,
                "locked": True,
                "key_id": 2,
                "room_a": "trophy_room",
                "room_b": "secret_study"
            },
            location="trophy_room"
        ),

        # ======================================================
        # SECRET STUDY entities  (Puzzle 3 payoff)
        # ======================================================

        # The stone basin is the target of POUR water (while wearing ring).
        # engine.py's handle_pour checks for the ring and sets "activated": True,
        # then moves ancient_scroll from "hidden" into the basin.
        "stone_basin": Entity(
            eid="stone_basin",
            name="a stone basin",
            aliases=["basin", "stone basin", "plinth", "bowl", "carved basin"],
            tags={"scenery", "container"},
            props={
                "desc": (
                    "A shallow basin carved from a single piece of dark stone. "
                    "Serpents intertwine around its rim. It is dry and empty."
                ),
                "open": True,   # no lid — always accessible
                "activated": False,
                "liquid": None,
            },
            location="secret_study"
        ),
        # The ancient scroll starts hidden; revealed when Puzzle 3 fires.
        "ancient_scroll": Entity(
            eid="ancient_scroll",
            name="an ancient scroll",
            aliases=["scroll", "ancient scroll", "parchment", "roll of parchment"],
            tags={"portable", "readable"},
            props={
                "desc": "A tightly rolled scroll of yellowed parchment, sealed with wax.",
                "readable_text": (
                    "The text is written in an archaic hand, but legible:\n\n"
                    "\"To he who bears the Serpent Ring and brings the water of patience: "
                    "the lower vault is opened by speaking the three words carved into "
                    "the cellar's eastern wall. Go there now and look carefully.\""
                ),
            },
            location="hidden"
        ),

        # ======================================================
        # CELLAR entities  (Puzzle 1)
        # ======================================================

        # The oil lamp needs fuel before it can be lit.
        # States: fuelled=False/lit=False -> fuelled=True/lit=False -> lit=True
        "oil_lamp": Entity(
            eid="oil_lamp",
            name="an oil lamp",
            aliases=["lamp", "oil lamp", "lantern", "light", "tin lamp"],
            tags={"portable", "lightable"},
            props={
                "desc": "A battered tin oil lamp with a glass chimney. It needs fuel.",
                "lit": False,
                "fuelled": False,
            },
            location="cellar"
        ),
        # Flask of lamp oil — consumed (empty=True) when used to fill the lamp.
        "lamp_oil": Entity(
            eid="lamp_oil",
            name="a flask of lamp oil",
            aliases=["oil", "lamp oil", "flask", "flask of oil", "fuel", "oil flask"],
            tags={"portable", "liquid_source"},
            props={
                "desc": "A small glass flask, half-full of clear lamp oil.",
                "liquid": "oil",
                "empty": False,
            },
            location="cellar"
        ),
        # The clay ewer holds water for Puzzle 3.
        # It has "requires_light": True — only visible when carrying a lit lamp.
        "water_ewer": Entity(
            eid="water_ewer",
            name="a clay ewer",
            aliases=["ewer", "clay ewer", "jug", "water jug", "pitcher", "clay jug", "water"],
            tags={"portable", "container", "liquid_source"},
            props={
                "desc": "A heavy clay ewer. It sloshes when you move it — there is water inside.",
                "desc_empty": "A heavy clay ewer, now dry and light.",
                "liquid": "water",
                "empty": False,
                "open": True,
                "requires_light": True,  # only visible in dark cellar with lit lamp
            },
            location="cellar"
        ),
        # The cellar lever is hidden in the dark end of the cellar.
        # PULL lever (requires lit lamp) opens the hall west passage.
        "cellar_lever": Entity(
            eid="cellar_lever",
            name="an iron lever",
            aliases=["lever", "iron lever", "handle", "wall lever", "pull"],
            tags={"scenery", "pullable"},
            props={
                "desc": (
                    "An iron lever set into the cellar's far wall. It is crusted with "
                    "old rust but looks like it would still move. A counterweight "
                    "mechanism suggests it controls something elsewhere in the house."
                ),
                "pulled": False,
                "requires_light": True,  # only visible with lit lamp
            },
            location="cellar"
        ),
        # The silver ring is the reward from Puzzle 2 (inside display_case).
        # Must be WORN before pouring water into the basin for Puzzle 3.
        "silver_ring": Entity(
            eid="silver_ring",
            name="a silver ring",
            aliases=["ring", "silver ring", "serpent ring", "band", "engraved ring"],
            tags={"portable", "wearable"},
            props={
                "desc": (
                    "A heavy silver ring engraved with two intertwined serpents. "
                    "Their eyes are tiny chips of green stone. It has an air of "
                    "quiet significance."
                ),
                "worn": False,
            },
            location="display_case"
        ),
    }

    # ----------------------------------------------------------
    # Populate room.entities and container.contains lists.
    #
    # We iterate all entities and sort them into the right bucket:
    #   - location is a room id  -> add to room.entities
    #   - location is an entity id -> add to that entity's .contains
    #   - location is "hidden"   -> leave out of everything (invisible)
    # ----------------------------------------------------------
    for ent in entities.values():
        if ent.location in rooms:
            rooms[ent.location].entities.append(ent.eid)
        elif ent.location in entities:
            container = entities[ent.location]
            if ent.eid not in container.contains:
                container.contains.append(ent.eid)
        # location == "hidden": intentionally omitted

    player = Player(location="foyer")
    return World(rooms=rooms, entities=entities, player=player)
