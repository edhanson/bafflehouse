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
                "A broad staircase to the west descends to the cellar and rises to the "
                "upper floor. To the south, the manor's front entrance opens onto what "
                "appears to be an overgrown garden."
            ),
            exits={"west": "cellar", "south": "entryway", "up": "upstairs_landing"}
            # NOTE: "north" is added dynamically when the oak door is opened.
        ),
        # The original single hall is now three sections running north-south.
        # hall_1 is the southernmost (adjacent to the foyer oak door).
        # hall_2 is the middle section.
        # hall_3 is the northernmost (trophy room west, cellar passage north).
        "hall_1": Room(
            rid="hall_1",
            title="South Hall",
            desc=(
                "A long hall runs northward into shadow. Stone walls are hung with faded "
                "hunting portraits. The oak door to the south leads back toward the foyer. "
                "A narrow side table stands near the door. The hall continues north."
            ),
            exits={"east": "library", "north": "hall_2"}
            # NOTE: "south" added dynamically when oak door is opened.
        ),
        "hall_2": Room(
            rid="hall_2",
            title="Central Hall",
            desc=(
                "The central stretch of the manor hall. Portraits of stern-faced ancestors "
                "line the walls, their painted eyes tracking you with practised disapproval. "
                "A narrow servants' staircase rises to the upper floor in the corner. "
                "The hall continues north and south."
            ),
            exits={"north": "hall_3", "south": "hall_1", "up": "upstairs_landing"}
        ),
        "hall_3": Room(
            rid="hall_3",
            title="North Hall",
            desc=(
                "The northernmost reach of the hall. The air here is colder and the "
                "portraits have given way to mounted weapons and shields. A heavy door "
                "to the west leads into what looks like a trophy room. "
                "A section of the north wall looks subtly different from the rest — "
                "the stonework is newer, as if something was once bricked over."
            ),
            exits={"south": "hall_2", "west": "trophy_room"}
            # NOTE: "north" to cellar passage added dynamically by lever puzzle.
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
            exits={"west": "hall_1"}
        ),
        "trophy_room": Room(
            rid="trophy_room",
            title="Trophy Room",
            desc=(
                "A broad chamber lined with trophies of past hunts and battles. Mounted "
                "weapons and pieces of armour hang on every wall alongside the animal "
                "heads. In the centre stands a large stone stag, one antler conspicuously "
                "heavier than the other. A door to the south is fitted with an iron lock. "
                "The north hall is to the east."
            ),
            exits={"east": "hall_3"}
            # NOTE: "south" to secret study added dynamically by handle_open(study_door).
        ),
        "secret_study": Room(
            rid="secret_study",
            title="Secret Study",
            desc=(
                "A small, airless room. Shelves of leather-bound ledgers line the walls. "
                "In the centre, a shallow stone basin sits on a plinth. "
                "The basin is carved with intertwined serpents and looks very old. "
                "The door back north leads to the trophy room."
            ),
            exits={"north": "trophy_room"}
        ),
        "entryway": Room(
            rid="entryway",
            title="Overgrown Garden",
            desc=(
                "The manor's former approach garden, now entirely reclaimed by nature. "
                "Flagstones heave under pressure from roots; ornamental hedges have "
                "become formless walls of dark green. A rusted iron gate to the west "
                "leads toward a wooded path. To the east, the old gatehouse is just "
                "visible through the overgrowth. The manor entrance is back to the north."
            ),
            exits={"north": "foyer", "east": "gatehouse", "west": "wooded_path"}
        ),
        "gatehouse": Room(
            rid="gatehouse",
            title="Gatehouse",
            desc=(
                "The old gatehouse straddles what was once the main carriage road. Its "
                "portcullis has long since rusted open, the road beyond it disappearing "
                "into trees. An arched passage leads east through the gatehouse itself. "
                "The garden lies to the west."
            ),
            exits={"west": "entryway", "east": "gatehouse_interior"}
        ),
        "wooded_path": Room(
            rid="wooded_path",
            title="Wooded Path",
            desc=(
                "A narrow path winds into dense woodland. The trees press close on both "
                "sides, their branches interlocking overhead. The path continues west "
                "into deepening shadow. Behind you to the east, the overgrown garden "
                "is still visible."
            ),
            exits={"east": "entryway", "west": "forest_edge"}
        ),
        # ── UPSTAIRS ─────────────────────────────────────────────────────
        # Accessible via "up" from the foyer staircase.
        # The cat's home_rooms include these rooms.
        "upstairs_landing": Room(
            rid="upstairs_landing",
            title="Upper Landing",
            desc=(
                "A wide landing connecting both staircases. The floorboards "
                "creak underfoot. Two doors lead off the landing — one to the east "
                "and one to the west. The main staircase descends to the foyer; "
                "a narrow servants' stair in the south corner drops back down "
                "to the central hall."
            ),
            exits={"down": "foyer", "south": "hall_2",
                   "east": "bedroom_east", "west": "bedroom_west"}
        ),
        "bedroom_east": Room(
            rid="bedroom_east",
            title="East Bedroom",
            desc=(
                "A modest bedroom, cold and long unslept-in. A brass bed frame "
                "stands against one wall, its mattress collapsed and mouse-eaten. "
                "A nightstand beside the bed still holds a few objects. "
                "The landing lies to the west."
            ),
            exits={"west": "upstairs_landing"}
        ),
        "bedroom_west": Room(
            rid="bedroom_west",
            title="West Bedroom",
            desc=(
                "A larger bedroom at the corner of the manor. Mildew has claimed "
                "most of the wallpaper, leaving dark patches across the plaster. "
                "A wardrobe stands against the far wall, its door hanging open "
                "on a broken hinge. The landing lies to the east."
            ),
            exits={"east": "upstairs_landing"}
        ),

        # ── GATEHOUSE INTERIOR AND ROAD ───────────────────────────────────
        "gatehouse_interior": Room(
            rid="gatehouse_interior",
            title="Gatehouse Interior",
            desc=(
                "The inside of the gatehouse is a single vaulted room, its stone "
                "floor worn smooth by centuries of foot traffic. Rusted iron "
                "mechanisms for the portcullis take up one wall. A heavy timber "
                "shelf along another wall holds various old stores. Arrow slits "
                "let in thin bars of light. The arched passage leads west back "
                "toward the garden; the road continues east."
            ),
            exits={"west": "gatehouse", "east": "cobbled_road"}
        ),
        "cobbled_road": Room(
            rid="cobbled_road",
            title="Cobbled Road",
            desc=(
                "The old carriage road stretches east and west, its cobblestones "
                "heaved and split by decades of neglect. Weeds push through every "
                "gap. The gatehouse lies to the west; the road disappears into "
                "forest shadow to the east."
            ),
            exits={"west": "gatehouse_interior", "east": "forest_path"}
        ),
        "forest_path": Room(
            rid="forest_path",
            title="Forest Path",
            desc=(
                "The cobbled road gives way to a dirt path where the forest closes "
                "in on both sides. The trees are old and densely planted, blocking "
                "most of the sky. The road back west is just visible through the "
                "trunks. To the east the path descends toward the sound of water."
            ),
            exits={"west": "cobbled_road", "east": "bridge"}
        ),
        "bridge": Room(
            rid="bridge",
            title="Old Bridge",
            desc=(
                "A stone bridge arches over a fast-moving stream. The far bank is "
                "obscured by mist and overgrowth — whatever lies beyond is not "
                "visible from here. The bridge itself looks intact, but something "
                "about the far side discourages crossing. The forest path leads "
                "back west."
            ),
            exits={"west": "forest_path"}
            # NOTE: east exit added dynamically when troll puzzle is solved.
        ),
        # Stub room — placeholder for future content beyond the bridge.
        # Accessible only after solving the troll's riddles.
        "bridge_far_bank": Room(
            rid="bridge_far_bank",
            title="Far Bank",
            desc=(
                "The far bank is a clearing hemmed in by ancient trees, their "
                "roots breaking through dark soil. The mist that hangs over the "
                "stream does not penetrate here — the air is preternaturally still. "
                "At the centre of the clearing stands a stone archway, freestanding "
                "and clearly not of recent construction. The bridge lies back to "
                "the west."
            ),
            exits={"west": "bridge"}
        ),

        # ── FOREST MAZE ───────────────────────────────────────────────────
        # Entry point from wooded_path. Distinct enough that the player
        # can recognise it as the way out.
        "forest_edge": Room(
            rid="forest_edge",
            title="Forest Edge",
            desc=(
                "The path from the manor dissolves here into trackless woodland. "
                "A faint trail is still visible heading east through the trees. "
                "Behind you to the east, the wooded path back toward the manor "
                "is just visible."
            ),
            exits={"east": "wooded_path", "north": "forest_a",
                   "west": "forest_b", "south": "forest_c"}
        ),
        # Four maze rooms. Exits are deliberately non-reciprocal so that
        # going north then south does not always return the player to where
        # they started. All rooms have similar descriptions by design —
        # the player is supposed to feel disoriented.
        "forest_a": Room(
            rid="forest_a",
            title="Deep Forest",
            desc=(
                "You are lost in the forest. Your sense of direction has become "
                "scrambled. The trees look the same in every direction — "
                "tall, grey-barked, silent. The light that filters through the "
                "canopy gives no indication of where the sun is."
            ),
            exits={"north": "forest_b", "south": "forest_c",
                   "east": "forest_b", "west": "forest_d"}
        ),
        "forest_b": Room(
            rid="forest_b",
            title="Deep Forest",
            desc=(
                "You are lost in the forest. Your sense of direction has become "
                "scrambled. A fallen log lies across what might be a path, "
                "though it is impossible to say which direction it runs."
            ),
            exits={"north": "forest_d", "south": "forest_a",
                   "east": "forest_c", "west": "forest_a"}
        ),
        "forest_c": Room(
            rid="forest_c",
            title="Deep Forest",
            desc=(
                "You are lost in the forest. Your sense of direction has become "
                "scrambled. The undergrowth is thicker here. Something "
                "has disturbed the leaf litter recently, but you cannot tell "
                "what — or from which direction it came."
            ),
            exits={"north": "forest_a", "south": "forest_d",
                   "east": "forest_d", "west": "forest_b"}
        ),
        "forest_d": Room(
            rid="forest_d",
            title="Deep Forest",
            desc=(
                "You are lost in the forest. Your sense of direction has become "
                "scrambled. A pale fungus grows on the bark of a nearby tree. "
                "The silence here is deeper than it should be. To the west, "
                "the trees may be thinning slightly — or it may be wishful thinking."
            ),
            exits={"north": "forest_c", "south": "forest_b",
                   "east": "forest_a", "west": "forest_edge"}
        ),

        # Kitchen — west of the cellar passage; accessible only after
        # the lever puzzle opens the north wall of hall_3.
        # The cat cannot reach this room (not in its home_rooms).
        "kitchen": Room(
            rid="kitchen",
            title="Old Kitchen",
            desc=(
                "A large stone-flagged kitchen, cold and long disused. A heavy "
                "iron range squats against the far wall, its grate choked with "
                "ash. Shelves still hold a scatter of earthenware pots and "
                "rusted implements. A wooden door to the east leads back to "
                "the passage."
            ),
            exits={"east": "cellar_passage"}
        ),
        # Cellar passage — the room revealed when the lever is pulled.
        # Connects hall_3 (north) to the wine cellar (south) to the kitchen (west).
        # NOTE: "south" exit to hall_3 is added dynamically by the lever puzzle.
        "cellar_passage": Room(
            rid="cellar_passage",
            title="Cellar Passage",
            desc=(
                "A low stone passage smelling of damp and old wood. "
                "Steps to the south descend to the wine cellar. "
                "A door to the west stands open onto what was once the kitchen. "
                "A bricked-over arch in the north wall hints at a former connection "
                "to the hall above."
            ),
            exits={"south": "cellar", "west": "kitchen"}
            # NOTE: "north" to hall_3 added dynamically by lever puzzle.
        ),
        "cellar": Room(
            rid="cellar",
            title="Wine Cellar",
            # desc is selected dynamically in do_look() based on lamp state.
            # desc_dark is used when the player has no lit lamp;
            # desc_lit  is used when they do.
            desc=(
                "A vaulted cellar. Stone racks hold the dusty ghosts of wine bottles, "
                "most long since emptied or broken. Without light, the far end of the "
                "room is impenetrably dark — you can tell something is there but cannot "
                "make it out. The foyer is back up the stairs to the east."
            ),
            exits={"east": "foyer"}
            # NOTE: "north" to cellar_passage added dynamically by lever puzzle.
            # NOTE: "south" to vault added dynamically when troll puzzle is solved;
            #        see handle_answer in engine.py.
        ),
        # The vault — locked behind the troll puzzle.
        # Accessible only after the bridge opens (troll defeated).
        # The slime golem spawns here and may roam from this room.
        "vault": Room(
            rid="vault",
            title="The Vault",
            desc=(
                "A low chamber cut from raw stone, clearly not part of the original "
                "cellar construction. The walls are smooth and faintly warm to the "
                "touch. A smell like hot iron and something organic hangs in the air. "
                "Whatever was kept here, it was not wine."
            ),
            exits={"north": "cellar"}
        ),
    }

    # Dynamic room descriptions referenced by do_look() in engine.py.
    # The cellar has two descriptions depending on whether the player
    # is carrying a lit lamp.
    rooms["cellar"].desc = (
        "A vaulted cellar. Stone racks hold the dusty ghosts of wine bottles, "
        "most long since emptied or broken. Without light, the far end of the "
        "room is impenetrably dark — you can tell something is there but cannot "
        "make it out. The foyer is back up the stairs to the east."
    )
    # Store the lit description as a room attribute so engine.py can
    # retrieve it without hard-coding strings outside of content.py.
    rooms["cellar"].desc_lit = (
        "A vaulted cellar. Stone racks hold the dusty ghosts of wine bottles, "
        "most long since emptied or broken. By the light of the lamp the far "
        "end of the room resolves into view: rough stone walls, a few broken "
        "crates, and what looks like an iron lever set into the far wall. "
        "The foyer is back up the stairs to the east."
    )

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
                "desc": "A small wooden box with a hinged lid. Something rattles faintly inside.",
                "desc_empty": "A small wooden box with a hinged lid, now empty.",
                "desc_closed_empty": "A small wooden box with a hinged lid. It feels light — nothing inside.",
                "open": False
            },
            location="foyer"
        ),
        # A box of matches inside the wooden box.
        # Required to light the oil lamp (Puzzle 1).
        # matches_remaining tracks how many are left; the box is reusable
        # until the count reaches zero, at which point the player is stuck.
        "matchbox": Entity(
            eid="matchbox",
            name="a box of matches",
            aliases=["matches", "match", "matchbox", "box of matches", "match box"],
            tags={"portable", "fire_source"},
            props={
                "desc": "A small cardboard box of safety matches. Several have been used.",
                "desc_empty": "An empty matchbox. Every last match has been spent.",
                "matches_remaining": 10,
            },
            location="wooden_box"
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

        # ── hall_1 scenery ────────────────────────────────────────────────
        "hall_portraits_1": Entity(
            eid="hall_portraits_1",
            name="the hunting portraits",
            aliases=["portraits", "hunting portraits", "paintings", "pictures",
                     "portrait", "painting", "picture", "frames"],
            tags={"scenery"},
            props={
                "desc": (
                    "Faded oil paintings of men and women on horseback, or standing "
                    "over fallen stags with satisfied expressions. The paint has "
                    "darkened with age and the subjects have become difficult to "
                    "distinguish from one another. Whoever they were, they are "
                    "long gone."
                ),
            },
            location="hall_1"
        ),
        "side_table": Entity(
            eid="side_table",
            name="the side table",
            aliases=["side table", "table", "dusty table", "small table"],
            tags={"scenery"},
            props={
                "desc": (
                    "A narrow side table pushed against the wall near the south door. "
                    "Its surface is thick with dust."
                ),
            },
            location="hall_1"
        ),

        # ── hall_2 scenery ────────────────────────────────────────────────
        "hall_portraits_2": Entity(
            eid="hall_portraits_2",
            name="the portraits",
            aliases=["portraits", "paintings", "pictures", "ancestors",
                     "portrait", "painting", "picture", "stern faces",
                     "stern-faced portraits", "ancestor portraits"],
            tags={"scenery"},
            props={
                "desc": (
                    "Row upon row of stern-faced ancestors rendered in oil. Each "
                    "portrait has the same quality of faint disapproval, as though "
                    "the subjects were asked to sit for their likeness and found the "
                    "experience beneath them. Their painted eyes do seem to follow "
                    "you as you move — a trick of the light, surely."
                ),
            },
            location="hall_2"
        ),

        # ── hall_3 scenery ────────────────────────────────────────────────
        "hall_displays_3": Entity(
            eid="hall_displays_3",
            name="the weapon displays",
            aliases=["weapons", "shields", "mounted weapons", "mounted shields",
                     "wall weapons", "displays", "weapon displays",
                     "spears", "bucklers", "helm", "helmet"],
            tags={"scenery"},
            props={
                "desc": (
                    "Here the portraits have been replaced by displays of weapons "
                    "and shields — older and more functional-looking than those in "
                    "the trophy room. Spear hafts, rusted bucklers, a dented kettle "
                    "helm. None of them appear to be in usable condition."
                ),
            },
            location="hall_3"
        ),
        "bricked_wall": Entity(
            eid="bricked_wall",
            name="the north wall",
            aliases=["north wall", "wall", "bricked wall", "stonework",
                     "newer stonework", "section of wall", "different stonework"],
            tags={"scenery"},
            props={
                "desc": (
                    "A section of the north wall where the stonework is noticeably "
                    "newer than its surroundings — lighter in colour, the mortar "
                    "less weathered. Something was sealed up here, and not too "
                    "long ago in the life of this building. There is no visible "
                    "mechanism to open it from this side."
                ),
                "desc_open": (
                    "The passage that was hidden behind the newer stonework stands "
                    "open. Cold air drifts through from the cellar below."
                ),
            },
            location="hall_3"
        ),

        # ── entryway scenery ──────────────────────────────────────────────
        "garden_flagstones": Entity(
            eid="garden_flagstones",
            name="the flagstones",
            aliases=["flagstones", "stones", "paving", "path", "ground",
                     "flags", "paved ground"],
            tags={"scenery"},
            props={
                "desc": (
                    "Large flat stones that once formed a formal approach to the "
                    "manor. Many have cracked and shifted as roots have pushed up "
                    "beneath them. Moss fills the gaps. In places the ground has "
                    "swallowed them entirely."
                ),
            },
            location="entryway"
        ),
        "garden_hedges": Entity(
            eid="garden_hedges",
            name="the hedges",
            aliases=["hedges", "hedge", "overgrown hedges", "bushes",
                     "shrubs", "greenery", "walls of green"],
            tags={"scenery"},
            props={
                "desc": (
                    "What were once neatly trimmed ornamental hedges have grown "
                    "into irregular dark-green walls, easily twice your height. "
                    "They close in the garden on all sides except where the iron "
                    "gate and the gatehouse provide gaps. Pressing close to the "
                    "base of the hedge, you notice a patch of small silvery-green "
                    "plants growing wild — catnip, by the smell of it."
                ),
            },
            location="entryway"
        ),
        # Catnip — hidden until the player examines garden_hedges.
        # Starts with props["visible"] = False and location "entryway" but
        # absent from room.entities.  handle_examine sets visible=True and
        # appends it to the room's entity list when hedges are examined.
        "catnip": Entity(
            eid="catnip",
            name="a sprig of catnip",
            aliases=["catnip", "sprig of catnip", "sprig", "catnip plant",
                     "silvery-green plant", "plant", "herb", "nip"],
            tags={"portable", "catnip"},
            props={
                "desc": (
                    "A small bunch of catnip pulled from the base of the hedge. "
                    "The silvery-green leaves are pungent even to your senses. "
                    "A cat would find this irresistible."
                ),
                "visible": False,   # hidden until hedges are examined
            },
            location="hidden"       # kept out of room.entities at startup
        ),
        "iron_gate": Entity(
            eid="iron_gate",
            name="the iron gate",
            aliases=["iron gate", "gate", "rusted gate", "rusted iron gate",
                     "garden gate", "west gate"],
            tags={"scenery"},
            props={
                "desc": (
                    "A tall iron gate set into the hedge, its bars eaten through "
                    "with rust. It hangs permanently open — the hinges have long "
                    "since fused in that position. The wooded path lies beyond."
                ),
            },
            location="entryway"
        ),

        # ── gatehouse scenery ─────────────────────────────────────────────
        "portcullis": Entity(
            eid="portcullis",
            name="the portcullis",
            aliases=["portcullis", "gate", "gatehouse gate",
                     "rusted portcullis", "iron portcullis"],
            tags={"scenery"},
            props={
                "desc": (
                    "The portcullis is raised and rusted solid in that position. "
                    "Its iron teeth point downward, suspended above the road. "
                    "The mechanism that would lower it is somewhere in the "
                    "gatehouse structure above, but whatever chain or counterweight "
                    "operated it has long since failed."
                ),
            },
            location="gatehouse"
        ),
        "carriage_road": Entity(
            eid="carriage_road",
            name="the old road",
            aliases=["road", "carriage road", "old road", "track", "lane"],
            tags={"scenery"},
            props={
                "desc": (
                    "The road passes beneath the gatehouse arch and disappears "
                    "into the trees to the east. Wheel ruts are still faintly "
                    "visible in the packed earth, though grass has begun to "
                    "reclaim them. It leads somewhere — but not somewhere you "
                    "need to go just yet."
                ),
            },
            location="gatehouse"
        ),

        # ── wooded_path scenery ───────────────────────────────────────────
        "woodland": Entity(
            eid="woodland",
            name="the trees",
            aliases=["trees", "woodland", "woods", "forest", "undergrowth",
                     "branches", "tree", "dark trees", "canopy"],
            tags={"scenery"},
            props={
                "desc": (
                    "The trees press close on both sides of the narrow path, their "
                    "branches interlocking overhead to form a low canopy. The light "
                    "here is greenish and uncertain. Further west the path bends "
                    "out of sight. It is very quiet."
                ),
            },
            location="wooded_path"
        ),

        # ── trophy room additional scenery ────────────────────────────────
        "animal_heads": Entity(
            eid="animal_heads",
            name="the animal heads",
            aliases=["animal heads", "heads", "trophies", "mounted heads",
                     "stag heads", "boar heads", "hunting trophies",
                     "animal head", "trophy", "head"],
            tags={"scenery"},
            props={
                "desc": (
                    "A collection of mounted animal heads — stags, boars, a wolf "
                    "with glass eyes that catch the light unpleasantly. They are "
                    "dusty and several have lost patches of fur. Someone spent "
                    "considerable time and effort acquiring these, and now no one "
                    "tends them."
                ),
            },
            location="trophy_room"
        ),

        # The oak door connects hall_1 <-> foyer.
        # Locked with key_id 1 (brass key).
        "oak_door": Entity(
            eid="oak_door",
            name="an oak door",
            aliases=["door", "oak door"],
            tags={"door", "openable", "lockable", "scenery"},
            props={
                "desc": "A sturdy oak door. It looks unimpressed.",
                "open": False,
                "locked": True,
                "key_id": 1,
                "room_a": "hall_1",
                "room_b": "foyer"
            },
            location="hall_1"
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
                    "A glass-fronted display case with a small brass clasp. "
                    "The glass is dusty but intact."
                ),
                "desc_empty": (
                    "A glass-fronted display case with a small brass clasp. "
                    "The glass is dusty but intact."
                ),
                "open": False,
                "locked": True,
                "key_id": 3
            },
            location="library"
        ),
        # The journal has been moved to the hall — the player finds it
        # after unlocking the oak door, before reaching the trophy room.
        # This ensures the antler clue is discovered before the display
        # case puzzle rather than being locked inside it.
        "journal": Entity(
            eid="journal",
            name="an old journal",
            aliases=["journal", "old journal", "book", "leather journal", "diary"],
            tags={"portable", "readable"},
            props={
                "desc": "A leather-bound journal with a worn cover. The entries inside are cramped and hurried.",
                "readable_text": (
                    "The handwriting is cramped and hurried. Most entries are mundane "
                    "household accounts. Near the back, one entry is written in a "
                    "much smaller hand — too small to read without a lens."
                ),
                "readable_text_magnified": (
                    "The handwriting is cramped and hurried. Most entries are mundane "
                    "household accounts, but near the back you find an entry that reads:\n\n"
                    "\"I have hidden the reserve key in the old way — the stag knows "
                    "where it rests. A firm pull on the heavy antler will remind him.\""
                ),
            },
            location="hall_1"
        ),

        # ======================================================
        # TROPHY ROOM entities  (Puzzle 2 clue + Puzzle 3 gate)
        # ======================================================

        # ── Weapons and armour ───────────────────────────────────────────
        # All are tagged "mounted" — they require TAKE DOWN / REMOVE FROM
        # MOUNT before becoming portable.  Stat props (damage, defense) are
        # included now so the combat system can read them without needing a
        # content update later.

        "broadsword": Entity(
            eid="broadsword",
            name="a broadsword",
            aliases=["sword", "broadsword", "blade", "long sword", "longsword"],
            tags={"mounted", "weapon", "portable"},
            props={
                "desc": (
                    "A broad-bladed sword, the steel dulled with age but the edge "
                    "still serviceable. A faded crest is etched into the forte."
                ),
                "damage": 4,
                "damage_type": "slash",
                "two_handed": True,
                "weight": "heavy",
            },
            location="trophy_room"
        ),
        "hunting_knife": Entity(
            eid="hunting_knife",
            name="a hunting knife",
            aliases=["knife", "hunting knife", "dagger", "short blade"],
            tags={"mounted", "weapon", "portable"},
            props={
                "desc": (
                    "A long hunting knife with a bone handle, well-balanced and "
                    "light enough to throw. The blade curves slightly toward the tip."
                ),
                "damage": 2,
                "damage_type": "pierce",
                "two_handed": False,
                "weight": "light",
                "throwable": True,
            },
            location="trophy_room"
        ),
        "iron_mace": Entity(
            eid="iron_mace",
            name="an iron mace",
            aliases=["mace", "iron mace", "club", "bludgeon"],
            tags={"mounted", "weapon", "portable"},
            props={
                "desc": (
                    "A flanged iron mace, heavy and unsubtle. The haft is wrapped "
                    "in cracked leather. It looks like it has seen genuine use."
                ),
                "damage": 5,
                "damage_type": "blunt",
                "two_handed": True,
                "weight": "heavy",
            },
            location="trophy_room"
        ),
        "kite_shield": Entity(
            eid="kite_shield",
            name="a kite shield",
            aliases=["shield", "kite shield", "buckler"],
            tags={"mounted", "armor", "wearable", "portable"},
            props={
                "desc": (
                    "A kite-shaped shield of banded iron over oak. The painted device "
                    "on its face has faded to an unreadable smear. Still solid."
                ),
                "defense": 3,
                "defense_type": "physical",
                "two_handed": False,
                "weight": "medium",
                "worn": False,
            },
            location="trophy_room"
        ),
        "chain_coif": Entity(
            eid="chain_coif",
            name="a chain coif",
            aliases=["coif", "chain coif", "mail coif", "chainmail hood", "hood"],
            tags={"mounted", "armor", "wearable", "portable"},
            props={
                "desc": (
                    "A hood of riveted chainmail protecting head and neck. Heavy, "
                    "but it would still turn a glancing blow."
                ),
                "defense": 2,
                "defense_type": "physical",
                "weight": "medium",
                "worn": False,
            },
            location="trophy_room"
        ),
        "weapon_rack": Entity(
            eid="weapon_rack",
            name="the weapon rack",
            aliases=["rack", "weapon rack", "mount", "wall mount", "display"],
            tags={"scenery"},
            props={
                "desc": (
                    "A heavy iron rack bolted to the stone wall, holding an assortment "
                    "of weapons and armour. Each item hangs on pegs or hooks. "
                    "They look old but not entirely decorative."
                ),
            },
            location="trophy_room"
        ),

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
            aliases=["door", "iron door", "study door", "south door"],
            tags={"door", "openable", "lockable", "scenery"},
            props={
                "desc": "A heavy door fitted with a large iron lock. It looks serious.",
                "open": False,
                "locked": True,
                "key_id": 2,
                # study_door: room_a is trophy_room (north), room_b is secret_study (south)
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
        # then moves jeweled_amulet from "hidden" into the basin.
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
        "jeweled_amulet": Entity(
            eid="jeweled_amulet",
            name="a jeweled amulet",
            aliases=["amulet", "jeweled amulet", "jewelled amulet", "necklace",
                     "pendant", "medallion", "serpent amulet"],
            tags={"portable", "wearable", "magical"},
            props={
                "desc": (
                    "A heavy amulet of green-black stone set in tarnished silver, "
                    "carved in the shape of two coiled serpents. It is cold to the touch "
                    "but seems to pulse faintly when held — or perhaps that is just "
                    "your heartbeat. Whatever it is, it did not come from this world "
                    "to be left in a basin."
                ),
                "worn":         False,
                "damage_bonus": 1,     # +1 damage on successful attacks when worn
            },
            location="hidden"
        ),

        # ======================================================
        # CELLAR entities  (Puzzle 1)
        # ======================================================

        # The far wall of the cellar — scenery, requires light.
        # In the dark, require_visible returns a darkness message.
        # When lit, examining the wall describes it and mentions the lever.
        "cellar_wall": Entity(
            eid="cellar_wall",
            name="the far wall",
            aliases=["wall", "far wall", "stone wall", "cellar wall", "walls"],
            tags={"scenery"},
            props={
                "desc": (
                    "The far wall is rough-hewn stone, damp with age. "
                    "An iron lever protrudes from the rock, crusted with old rust. "
                    "A counterweight mechanism behind it suggests it controls "
                    "something elsewhere in the house."
                ),
                "requires_light": True,
            },
            location="cellar"
        ),
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
            location="bedroom_east"
        ),
        # Flask of lamp oil — consumed (empty=True) when used to fill the lamp.
        "lamp_oil": Entity(
            eid="lamp_oil",
            name="a flask of lamp oil",
            aliases=["oil", "lamp oil", "flask", "flask of oil", "fuel", "oil flask"],
            tags={"portable", "liquid_source"},
            props={
                "desc": "A small glass flask, half-full of clear lamp oil.",
                "desc_empty": "A small glass flask, now empty.",
                "liquid": "oil",
                "empty": False,
            },
            location="gatehouse_interior"
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
        # A folded letter inside the display case — found alongside the ring.
        # It hints at the stone basin and the significance of the serpent ring,
        # giving the player a clue for Puzzle 3 before they reach the study.
        "folded_letter": Entity(
            eid="folded_letter",
            name="a folded letter",
            aliases=["letter", "folded letter", "note", "paper", "folded note"],
            tags={"portable", "readable"},
            props={
                "desc": "A sheet of paper folded into thirds, slightly yellowed.",
                "readable_text": (
                    "The note is written in a precise, careful hand:\n\n"
                    "\"The ring must be worn when the basin is fed. Water alone "
                    "will not wake it — the serpents must recognise their bearer. "
                    "The study above the trophy room is where the old work was done. "
                    "Wear the ring. Bring water. The rest will follow.\""
                ),
            },
            location="display_case"
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
                "worn":     False,
                "hp_regen": 1,   # +1 HP per turn when worn, up to max
            },
            location="display_case"
        ),

        # ======================================================
        # UPSTAIRS ENTITIES
        # ======================================================

        "servants_stair": Entity(
            eid="servants_stair",
            name="the servants' staircase",
            aliases=["servants stair", "servants staircase", "narrow stair",
                     "back stair", "back stairs", "narrow stairs",
                     "staircase", "stairs", "stair"],
            tags={"scenery"},
            props={"desc": (
                "A narrow staircase tucked into the corner of the hall. "
                "The treads are bare wood, worn smooth in the centre from long use. "
                "It rises steeply to the upper floor."
            )},
            location="hall_2"
        ),
        "upstairs_nightstand": Entity(
            eid="upstairs_nightstand",
            name="the nightstand",
            aliases=["nightstand", "night stand", "bedside table",
                     "side table", "small table"],
            tags={"scenery"},
            props={"desc": (
                "A small wooden nightstand beside the bed. Its surface is dusty "
                "but a few objects remain on top — someone left in a hurry, or "
                "simply stopped coming back."
            )},
            location="bedroom_east"
        ),
        "magnifying_glass": Entity(
            eid="magnifying_glass",
            name="a magnifying glass",
            aliases=["magnifying glass", "magnifier", "lens", "glass",
                     "loupe", "reading glass", "hand lens"],
            tags={"portable"},
            props={"desc": (
                "A small brass-handled magnifying glass. The lens is clear and "
                "strong — useful for reading cramped or faded text."
            )},
            location="bedroom_west"
        ),
        "upstairs_wardrobe": Entity(
            eid="upstairs_wardrobe",
            name="the wardrobe",
            aliases=["wardrobe", "armoire", "cupboard", "closet"],
            tags={"scenery"},
            props={"desc": (
                "A tall oak wardrobe, its door hanging open on a broken hinge. "
                "The interior holds nothing but a few bent coat hangers and the "
                "smell of old cedar."
            )},
            location="bedroom_west"
        ),
        "upstairs_bed": Entity(
            eid="upstairs_bed",
            name="the bed frame",
            aliases=["bed", "bed frame", "brass bed", "bedframe"],
            tags={"scenery"},
            props={"desc": (
                "A brass bed frame, tarnished green with age. The mattress has "
                "long since collapsed into a heap of fabric and horsehair."
            )},
            location="bedroom_east"
        ),
        "upstairs_window": Entity(
            eid="upstairs_window",
            name="the window",
            aliases=["window", "windowsill", "sill"],
            tags={"scenery"},
            props={"desc": (
                "A tall sash window, its glass thick and wavy with age. Below, "
                "you can see the overgrown garden and, beyond it, the gatehouse "
                "and the road disappearing into the trees."
            )},
            location="upstairs_landing"
        ),

        # ======================================================
        # GATEHOUSE AND ROAD ENTITIES
        # ======================================================

        "gatehouse_portcullis": Entity(
            eid="gatehouse_portcullis",
            name="the portcullis",
            aliases=["portcullis", "iron gate", "gate", "rusted gate",
                     "iron portcullis", "rusted portcullis"],
            tags={"scenery"},
            props={"desc": (
                "A heavy iron portcullis, its teeth pointing downward, frozen "
                "in the open position by a century of rust. The mechanism that "
                "once raised and lowered it is visible through the arch above — "
                "a system of chains and counterweights, all seized solid."
            )},
            location="gatehouse"
        ),
        "gatehouse_mechanism": Entity(
            eid="gatehouse_mechanism",
            name="the portcullis mechanism",
            aliases=["mechanism", "winch", "chains", "counterweights",
                     "iron mechanism", "portcullis mechanism"],
            tags={"scenery"},
            props={"desc": (
                "A complex arrangement of iron wheels, chains, and counterweights "
                "for raising and lowering the portcullis. Every moving part has "
                "seized with rust. It has not moved in a very long time."
            )},
            location="gatehouse_interior"
        ),
        "gatehouse_shelf": Entity(
            eid="gatehouse_shelf",
            name="the timber shelf",
            aliases=["shelf", "timber shelf", "shelves", "stores",
                     "old shelf", "wooden shelf"],
            tags={"scenery"},
            props={"desc": (
                "A heavy timber shelf running the length of one wall. Whatever "
                "was stored here has long since been used, spoiled, or taken. "
                "A few containers remain — a flask of lamp oil among them."
            )},
            location="gatehouse_interior"
        ),
        "troll": Entity(
            eid="troll",
            name="the troll",
            aliases=["troll", "bridge troll", "creature", "guard"],
            tags={"scenery"},   # not portable, not npc-system — handled by troll.py
            props={
                "desc": (
                    "It is large. Larger than seems reasonable, really. Its skin "
                    "is the same grey as the bridge stones, which probably is not "
                    "a coincidence. It watches you with small, clever eyes that "
                    "do not miss much."
                ),
            },
            location="bridge"
        ),
        "bridge_stream": Entity(
            eid="bridge_stream",
            name="the stream",
            aliases=["stream", "water", "river", "brook", "current"],
            tags={"scenery"},
            props={"desc": (
                "The stream runs fast and clear over mossy stones far below. "
                "It looks cold. There is no obvious way down to it from here."
            )},
            location="bridge"
        ),
        "bridge_far_bank": Entity(
            eid="bridge_far_bank",
            name="the far bank",
            aliases=["far bank", "far side", "other side", "other bank",
                     "mist", "overgrowth", "far shore"],
            tags={"scenery"},
            props={"desc": (
                "The far bank is hidden in low mist. You can make out "
                "dense vegetation and what might be a structure further back, "
                "but the details are unclear. Something about crossing feels "
                "inadvisable, though you cannot say exactly why."
            )},
            location="bridge"
        ),

        # ======================================================
        # BRIDGE FAR BANK ENTITIES  (end-game)
        # ======================================================

        "stone_archway": Entity(
            eid="stone_archway",
            name="a stone archway",
            aliases=["archway", "stone archway", "arch", "stone arch",
                     "ancient archway", "old archway", "carved archway",
                     "inscription", "carved inscription"],
            tags={"scenery", "readable"},
            props={
                "desc": (
                    "A freestanding archway of dark stone, each block fitted "
                    "without mortar to its neighbour. Serpent carvings wind up "
                    "both pillars and meet at the keystone. The opening frames "
                    "nothing but the far trees. An inscription is carved into "
                    "the lintel."
                ),
                "desc_active": (
                    "The archway pulses with soft light, the serpent carvings "
                    "tracing lines of cold fire up both pillars. Where the opening "
                    "once showed only trees, there is now something else entirely — "
                    "a shimmering portal, and beyond it, unmistakably, home."
                ),
                "readable_text": (
                    "The inscription reads, in plain and careful letters:\n\n"
                    "\"Bring the three magical artifacts to open the way home.\""
                ),
                "active": False,
            },
            location="bridge_far_bank"
        ),

        "home_portal": Entity(
            eid="home_portal",
            name="the portal",
            aliases=["portal", "shimmer", "shimmering portal", "gateway",
                     "home", "way home", "opening", "light"],
            tags={"scenery"},
            props={
                "desc": (
                    "The portal shimmers with quiet light. Beyond it you can see "
                    "something familiar — warmth, comfort, a world that makes sense. "
                    "It will not stay open forever."
                ),
            },
            location="hidden"
        ),

        # ======================================================
        # FOREST ENTITIES
        # ======================================================

        "forest_trees": Entity(
            eid="forest_trees",
            name="the trees",
            aliases=["trees", "tree", "forest", "woods", "woodland",
                     "canopy", "branches", "trunks", "bark"],
            tags={"scenery"},
            props={"desc": (
                "Grey-barked and ancient, the trees crowd close on every side. "
                "Their branches interlace overhead, blocking direct sunlight. "
                "There is nothing distinctive about any of them."
            )},
            location="forest_a"
        ),
        "forest_log": Entity(
            eid="forest_log",
            name="a fallen log",
            aliases=["log", "fallen log", "fallen tree", "dead tree"],
            tags={"scenery"},
            props={"desc": (
                "A large tree, long fallen, its bark soft with rot and "
                "covered in pale lichen. It lies at an angle that suggests "
                "a path, but following it leads nowhere useful."
            )},
            location="forest_b"
        ),
        "forest_fungus": Entity(
            eid="forest_fungus",
            name="the pale fungus",
            aliases=["fungus", "mushroom", "mushrooms", "pale fungus",
                     "fungal growth", "lichen"],
            tags={"scenery"},
            props={"desc": (
                "A large bracket fungus, pale as bone, growing from the bark of "
                "a nearby tree. It is entirely unremarkable — just another part "
                "of a forest that all looks the same."
            )},
            location="forest_d"
        ),

        # ======================================================
        # CAN OPENER
        # ======================================================

        "can_opener": Entity(
            eid="can_opener",
            name="a can opener",
            aliases=["can opener", "opener", "tin opener", "tin key"],
            tags={"portable"},
            props={"desc": (
                "A sturdy steel can opener, old but functional. Someone kept "
                "it in the kitchen for a reason."
            )},
            location="kitchen"
        ),

        # ======================================================
        # VAULT AND GOLEM ENTITIES
        # ======================================================

        "vault_door": Entity(
            eid="vault_door",
            name="the vault door",
            aliases=["vault door", "stone door", "hidden door",
                     "door in the wall", "door", "vault"],
            tags={"scenery"},
            props={
                "desc": (
                    "A section of the cellar wall that does not quite match the rest. "
                    "The stone is slightly smoother, the mortar slightly newer. "
                    "A passage lies beyond — opened now, though by what mechanism "
                    "is unclear."
                ),
            },
            location="hidden"   # revealed when troll puzzle solved
        ),
        "slime_golem": Entity(
            eid="slime_golem",
            name="the slime golem",
            aliases=["golem", "slime golem", "creature", "hellspawn",
                     "abomination", "thing", "monster"],
            tags={"hostile", "living"},   # not "npc" — managed by combat.py
            props={
                "desc": (
                    "An amorphous mass in a vaguely humanoid shape, dark and "
                    "iridescent, like oil on water. It has no face, but there is "
                    "something in the way it orients toward you that is unmistakably "
                    "attentive. It smells of sulphur and hot metal. It is larger "
                    "than a person."
                ),
                "hp":         120,
                "max_hp":     120,
                "alive":      True,
                "aware":      False,    # True once it detects the player
                "home_room":  "vault",
            },
            location="vault"
        ),
        "golem_remains": Entity(
            eid="golem_remains",
            name="a pool of iridescent goo",
            aliases=["goo", "remains", "pool", "slime", "puddle",
                     "iridescent pool", "golem remains"],
            tags={"scenery"},
            props={
                "desc": (
                    "A spreading pool of dark, iridescent fluid — all that remains "
                    "of the slime golem. The smell of sulphur lingers. Something "
                    "glitters at the centre of the pool."
                ),
            },
            location="hidden"
        ),
        "secret_treasure": Entity(
            eid="secret_treasure",
            name="a strange metallic object",
            aliases=["treasure", "object", "metallic object", "strange object",
                     "glittering object", "artefact", "artifact"],
            tags={"portable"},
            props={
                "desc": (
                    "A small object of unfamiliar manufacture — metal, but not any "
                    "metal with an obvious name. Its surface is covered in markings "
                    "that might be writing, or might be circuit traces. It is "
                    "slightly warm."
                ),
            },
            location="hidden"
        ),

        # ======================================================
        # NPC ENTITIES
        # ======================================================

        # Jasper — a grey cat who wanders hall_1/hall_2/hall_3/library.
        # The NPC system manages his location each turn; this entity
        # is the world-model anchor used for visibility and examine.
        "jasper": Entity(
            eid="jasper",
            name="a grey cat",
            aliases=["cat", "grey cat", "jasper", "kitty",
                     "the cat", "jasper the cat"],
            tags={"npc", "living"},
            props={
                "desc": (
                    "A lean grey cat with pale yellow eyes. Its fur is "
                    "clean and well-kept despite the state of the manor, "
                    "suggesting it has been here long enough to make itself "
                    "comfortable. It regards you with careful, unhurried "
                    "attention."
                ),
            },
            location="hall_2"
        ),

        # ======================================================
        # KITCHEN entities
        # ======================================================

        "cat_food": Entity(
            eid="cat_food",
            name="a tin of cat food",
            aliases=["cat food", "tin of cat food", "tin", "food",
                     "pet food", "cat tin"],
            tags={"portable", "food"},
            props={
                "desc": (
                    "A small tin with a paper label, miraculously intact. "
                    "The label shows a contented-looking cat. "
                    "It smells strongly even through the sealed lid."
                ),
                "desc_opened": (
                    "A tin of cat food, lid peeled back. "
                    "The smell is immediate and pungent."
                ),
                "opened": False,    # must be opened with can_opener before feeding
                "tool_required": "can_opener",
            },
            location="forest_b"
        ),
        "kitchen_range": Entity(
            eid="kitchen_range",
            name="the iron range",
            aliases=["range", "iron range", "stove", "oven",
                     "grate", "hearth", "fireplace"],
            tags={"scenery"},
            props={
                "desc": (
                    "A massive iron range, cold for decades. The grate is "
                    "packed with grey ash and the iron is furred with rust. "
                    "Someone once cooked serious quantities of food on this."
                ),
            },
            location="kitchen"
        ),
        "kitchen_shelves": Entity(
            eid="kitchen_shelves",
            name="the shelves",
            aliases=["shelves", "shelf", "kitchen shelves", "pots",
                     "earthenware", "implements"],
            tags={"scenery"},
            props={
                "desc": (
                    "Wooden shelves still holding a scatter of earthenware "
                    "storage jars and rusted kitchen implements. The tin of "
                    "cat food stands out as clearly more recent."
                ),
            },
            location="kitchen"
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
        # NPC entities are placed at runtime by get_npc_instances();
        # skip them here to avoid pre-populating room.entities.
        if "npc" in ent.tags:
            continue
        if ent.location in rooms:
            rooms[ent.location].entities.append(ent.eid)
        elif ent.location in entities:
            container = entities[ent.location]
            if ent.eid not in container.contains:
                container.contains.append(ent.eid)
        # location == "hidden": intentionally omitted

    player = Player(location="foyer")
    return World(rooms=rooms, entities=entities, player=player)