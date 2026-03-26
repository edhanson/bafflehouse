# parser.py

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ir import action_ir, clarify_ir, meta_ir
from model import Entity, World
from semantics import Embedder, SemanticEntityIndex, SemanticIntentRouter


# ============================================================
# Verb registry
# ============================================================

@dataclass(frozen=True)
class VerbDefinition:
    """
    Declarative definition of a supported verb family.

    shape options:
        - verb_only
        - verb_obj
        - verb_obj_prep_iobj
        - verb_direction_or_target
    """
    verb_id: str
    literal_forms: List[str]
    semantic_examples: List[str]
    shape: str
    preferred_preps: List[str] = field(default_factory=list)
    allow_coordination: bool = False


VERB_DEFS: Dict[str, VerbDefinition] = {
    "look": VerbDefinition(
        verb_id="look",
        literal_forms=["look", "l"],
        semantic_examples=["look around", "describe the room", "survey the room"],
        shape="verb_only",
    ),
    "inventory": VerbDefinition(
        verb_id="inventory",
        literal_forms=["inventory", "inv", "i"],
        semantic_examples=["what am i carrying", "show my items", "check inventory"],
        shape="verb_only",
    ),
    "go": VerbDefinition(
        verb_id="go",
        literal_forms=["go", "walk", "run", "head", "move"],
        semantic_examples=[
            "move in a direction",
            "travel north",
            "travel south",
            "travel east",
            "travel west",
            "move to a place",
        ],
        shape="verb_direction_or_target",
    ),
    "enter": VerbDefinition(
        verb_id="enter",
        literal_forms=["enter"],
        semantic_examples=[
            "go through a door",
            "walk through a doorway",
            "step into something",
        ],
        shape="verb_obj",
    ),
    "take": VerbDefinition(
        verb_id="take",
        literal_forms=["take", "get", "grab", "pick up", "pickup"],
        semantic_examples=[
            "obtain an item",
            "obtain object",
            "obtain key",
            "acquire an object",
            "acquire key",
            "collect an object",
            "pick something up",
            "add an item to inventory",
            "take object",
            "get object",
            "grab object",
        ],
        shape="verb_obj",
        allow_coordination=True,
    ),
    "drop": VerbDefinition(
        verb_id="drop",
        literal_forms=["drop", "discard", "throw"],
        semantic_examples=[
            "put down an item",
            "remove an item from inventory",
            "leave an item here",
        ],
        shape="verb_obj",
        allow_coordination=True,
    ),
    "examine": VerbDefinition(
        verb_id="examine",
        literal_forms=["examine", "inspect", "x", "look at"],
        semantic_examples=[
            "study an object",
            "check out an object",
            "look closely at an object",
            "inspect something in detail",
        ],
        shape="verb_obj",
        allow_coordination=False,
    ),
    "open": VerbDefinition(
        verb_id="open",
        literal_forms=["open"],
        semantic_examples=[
            "unseal something",
            "open a container",
            "open a door",
        ],
        shape="verb_obj",
    ),
    "close": VerbDefinition(
        verb_id="close",
        literal_forms=["close", "shut"],
        semantic_examples=[
            "shut something",
            "close a container",
            "close a door",
        ],
        shape="verb_obj",
    ),
    "put": VerbDefinition(
        verb_id="put",
        literal_forms=["put", "place", "insert", "stash"],
        semantic_examples=[
            "put something in a container",
            "place something on a surface",
            "stash an item somewhere",
        ],
        shape="verb_obj_prep_iobj",
        preferred_preps=["in", "into", "inside", "on", "onto"],
    ),
    "unlock": VerbDefinition(
        verb_id="unlock",
        literal_forms=["unlock"],
        semantic_examples=[
            "use a key to open a lock",
            "unlock something with a key",
        ],
        shape="verb_obj_prep_iobj",
        preferred_preps=["with"],
    ),

    # --------------------------------------------------------
    # New verbs added for the expanded world
    # --------------------------------------------------------

    # READ — returns the "readable_text" prop of an entity tagged "readable".
    "read": VerbDefinition(
        verb_id="read",
        literal_forms=["read", "peruse", "skim"],
        semantic_examples=[
            "read a book",
            "read an inscription",
            "read a note",
            "look at the writing",
            "decipher text",
        ],
        shape="verb_obj",
    ),

    # LIGHT — sets "lit": True on a "lightable" entity (requires "fuelled": True).
    "light": VerbDefinition(
        verb_id="light",
        literal_forms=["light", "ignite", "kindle", "illuminate", "turn on", "strike"],
        semantic_examples=[
            "light a candle",
            "light a lamp",
            "set something on fire",
            "ignite a torch",
        ],
        shape="verb_obj",
    ),

    # EXTINGUISH — sets "lit": False on a lit entity.
    "extinguish": VerbDefinition(
        verb_id="extinguish",
        literal_forms=["extinguish", "douse", "snuff", "blow out", "put out", "turn off"],
        semantic_examples=[
            "extinguish a flame",
            "put out a candle",
            "snuff a lamp",
            "douse a torch",
        ],
        shape="verb_obj",
    ),

    # PUSH — interacts with "pushable"-tagged entities (levers, statues, buttons).
    "push": VerbDefinition(
        verb_id="push",
        literal_forms=["push", "press", "shove", "nudge"],
        semantic_examples=[
            "push a button",
            "press a switch",
            "shove something",
            "push a lever",
        ],
        shape="verb_obj",
    ),

    # PULL — interacts with "pullable"-tagged entities (levers, antlers, handles).
    "pull": VerbDefinition(
        verb_id="pull",
        literal_forms=["pull", "tug", "yank", "heave"],
        semantic_examples=[
            "pull a lever",
            "tug on something",
            "yank a handle",
            "pull an antler",
        ],
        shape="verb_obj",
    ),

    # POUR — transfers liquid from one entity to another.
    # Shape: verb_obj_prep_iobj  (e.g. "pour oil into lamp", "pour water into basin")
    "pour": VerbDefinition(
        verb_id="pour",
        literal_forms=["pour", "tip", "decant", "empty into"],
        semantic_examples=[
            "pour liquid into a container",
            "pour water into a basin",
            "pour oil into a lamp",
            "empty a flask into something",
            "tip liquid into a vessel",
        ],
        shape="verb_obj_prep_iobj",
        preferred_preps=["into", "in", "on", "onto"],
    ),

    # FILL — fills a vessel from a liquid source.
    # Shape: verb_obj_prep_iobj  (e.g. "fill lamp with oil", "fill jug with water")
    "fill": VerbDefinition(
        verb_id="fill",
        literal_forms=["fill", "load", "charge"],
        semantic_examples=[
            "fill a lamp with oil",
            "fill a container with liquid",
            "load a vessel with fuel",
            "top up a lamp",
        ],
        shape="verb_obj_prep_iobj",
        preferred_preps=["with", "from"],
    ),

    # WEAR — equips a "wearable" entity, setting its "worn" prop to True.
    "wear": VerbDefinition(
        verb_id="wear",
        literal_forms=["wear", "put on", "don", "equip"],
        semantic_examples=[
            "wear a ring",
            "put on a ring",
            "don a cloak",
            "equip an item",
            "wear jewelry",
        ],
        shape="verb_obj",
        allow_coordination=True,
    ),

    # REMOVE — unequips a worn item, setting "worn" to False.
    # "remove" is also an alias-form for "take off".
    "remove": VerbDefinition(
        verb_id="remove",
        literal_forms=["remove", "take off", "doff", "unequip"],
        semantic_examples=[
            "take off a ring",
            "remove jewelry",
            "doff a hat",
            "unequip an item",
        ],
        shape="verb_obj",
        allow_coordination=True,
    ),

    # USE — generic combination verb: "use X with/on Y".
    # Acts as a fallback combiner when a more specific verb is not used.
    # Shape: verb_obj_prep_iobj  (e.g. "use key with door", "use oil on lamp")
    "use": VerbDefinition(
        verb_id="use",
        literal_forms=["use", "apply", "combine"],
        semantic_examples=[
            "use an item on something",
            "apply an object to another",
            "combine two items",
            "use key with door",
            "use oil on lamp",
        ],
        shape="verb_obj_prep_iobj",
        preferred_preps=["with", "on", "in", "into"],
    ),

    # UNMOUNT — take a mounted weapon or armour piece off the wall.
    # Separate from TAKE so mounted items have a distinct first interaction.
    "unmount": VerbDefinition(
        verb_id="unmount",
        literal_forms=["unmount", "take down", "detach"],
        semantic_examples=[
            "take something off the wall",
            "remove a weapon from its mount",
            "unhook something from the wall",
            "lift a sword off the rack",
        ],
        shape="verb_obj",
    ),
}


PREPS = {"in", "into", "inside", "on", "onto", "at", "to", "with", "from", "over", "using"}

FILLER = {
    "please", "could", "would", "you", "me", "the", "a", "an", "some",
    "kindly", "just", "really", "then",
}

DIRECTIONS = {
    "n": "north", "north": "north",
    "s": "south", "south": "south",
    "e": "east", "east": "east",
    "w": "west", "west": "west",
    "u": "up", "up": "up",
    "d": "down", "down": "down",
}

# Derived lookup tables so VERB_DEFS is the single source of truth.
VERB_FORM_TO_ID: Dict[str, str] = {}
SINGLE_WORD_VERB_FORM_TO_ID: Dict[str, str] = {}

for verb_id, verb_def in VERB_DEFS.items():
    for form in verb_def.literal_forms:
        VERB_FORM_TO_ID[form] = verb_id
        if " " not in form:
            SINGLE_WORD_VERB_FORM_TO_ID[form] = verb_id


# ============================================================
# Normalization and idiom rewriting
# ============================================================

def normalize(text: str) -> str:
    """Normalize user input while keeping it parser-friendly."""
    t = text.strip()
    t = re.sub(r"[^\w\s'\"]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.lower().strip()


def split_compound(text: str) -> List[str]:
    """
    Split a command line into command segments.

    Supports:
        "open door then go north" -> ["open door", "go north"]

    Does NOT split simple coordinated noun phrases like:
        "get brass key and iron key"

    Those are handled later by expand_coordinated_objects(...).
    """
    text = normalize(text)

    parts = re.split(r"\bthen\b", text)
    results: List[str] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        and_parts = re.split(r"\band\b", part)
        if len(and_parts) == 1:
            results.append(part)
            continue

        current = and_parts[0].strip()
        for next_part in and_parts[1:]:
            next_part = next_part.strip()
            if not next_part:
                continue

            next_first = next_part.split()[0] if next_part.split() else ""

            if next_first in VERB_FORM_TO_ID or next_first in SINGLE_WORD_VERB_FORM_TO_ID or next_first in DIRECTIONS:
                results.append(current)
                current = next_part
            else:
                current = f"{current} and {next_part}"

        results.append(current)

    return results


def tokenize_for_objects(text: str) -> List[str]:
    """Tokenize and remove filler words."""
    return [t for t in text.split() if t not in FILLER]


def extract_object_phrases(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Heuristic slot extraction.

    Returns:
        (left_phrase, preposition, right_phrase)
    """
    tokens = tokenize_for_objects(normalize(text))
    prep_idx = next((i for i, tok in enumerate(tokens) if tok in PREPS), None)

    if prep_idx is None:
        left = " ".join(tokens).strip() or None
        return left, None, None

    left = " ".join(tokens[:prep_idx]).strip() or None
    prep = tokens[prep_idx]
    right = " ".join(tokens[prep_idx + 1:]).strip() or None
    return left, prep, right


def rewrite_common_idioms(text: str) -> str:
    """
    Rewrite common natural-language idioms into simpler parser-friendly commands.
    """
    t = normalize(text)

    rewrites = [
        (r"^take\s+(a\s+)?look\s+at\s+(.+)$", r"examine \2"),
        (r"^have\s+(a\s+)?look\s+at\s+(.+)$", r"examine \2"),
        (r"^take\s+(a\s+)?closer\s+look\s+at\s+(.+)$", r"examine \2"),
        (r"^have\s+(a\s+)?closer\s+look\s+at\s+(.+)$", r"examine \2"),
        (r"^take\s+(a\s+)?close\s+look\s+at\s+(.+)$", r"examine \2"),
        (r"^have\s+(a\s+)?close\s+look\s+at\s+(.+)$", r"examine \2"),
        (r"^look\s+at\s+(.+)$", r"examine \1"),
        (r"^look\s+closely\s+at\s+(.+)$", r"examine \1"),
        (r"^look\s+carefully\s+at\s+(.+)$", r"examine \1"),
        (r"^check\s+out\s+(.+)$", r"examine \1"),
        (r"^please\s+look\s+at\s+(.+)$", r"examine \1"),
        (r"^please\s+examine\s+(.+)$", r"examine \1"),
    ]

    for pattern, replacement in rewrites:
        if re.match(pattern, t):
            return re.sub(pattern, replacement, t).strip()

    return t


def rewrite_movement_idioms(text: str) -> str:
    """
    Rewrite common movement phrasings into simpler parser-friendly commands.
    """
    t = normalize(text)

    rewrites = [
        (r"^go\s+through\s+(.+)$", r"enter \1"),
        (r"^walk\s+through\s+(.+)$", r"enter \1"),
        (r"^step\s+through\s+(.+)$", r"enter \1"),
        (r"^move\s+through\s+(.+)$", r"enter \1"),
        (r"^go\s+into\s+(.+)$", r"enter \1"),
        (r"^walk\s+into\s+(.+)$", r"enter \1"),
        (r"^step\s+into\s+(.+)$", r"enter \1"),
        (r"^head\s+(north|south|east|west|up|down)$", r"go \1"),
        (r"^walk\s+(north|south|east|west|up|down)$", r"go \1"),
        (r"^run\s+(north|south|east|west|up|down)$", r"go \1"),
        (r"^move\s+(north|south|east|west|up|down)$", r"go \1"),
        (r"^please\s+go\s+through\s+(.+)$", r"enter \1"),
        (r"^please\s+walk\s+through\s+(.+)$", r"enter \1"),
        (r"^please\s+enter\s+(.+)$", r"enter \1"),
    ]

    for pattern, replacement in rewrites:
        if re.match(pattern, t):
            return re.sub(pattern, replacement, t).strip()

    return t


def rewrite_interaction_idioms(text: str) -> str:
    """
    Rewrite natural phrasings for the new interaction verbs into canonical forms.

    Covers light/extinguish, pull/push, pour/fill, wear/remove, and use idioms.
    Called in parse_to_candidates after the existing rewrite passes.
    """
    t = normalize(text)

    rewrites = [
        # LIGHT idioms
        (r"^light\s+up\s+(.+)$",               r"light \1"),
        (r"^set\s+fire\s+to\s+(.+)$",           r"light \1"),
        (r"^set\s+(.+)\s+alight$",              r"light \1"),
        # EXTINGUISH idioms
        (r"^blow\s+out\s+(.+)$",                r"extinguish \1"),
        (r"^put\s+out\s+(.+)$",                 r"extinguish \1"),
        (r"^snuff\s+out\s+(.+)$",               r"extinguish \1"),
        # PULL / PUSH
        (r"^pull\s+on\s+(.+)$",                 r"pull \1"),
        (r"^tug\s+on\s+(.+)$",                  r"pull \1"),
        (r"^yank\s+on\s+(.+)$",                 r"pull \1"),
        (r"^push\s+on\s+(.+)$",                 r"push \1"),
        (r"^press\s+down\s+on\s+(.+)$",         r"push \1"),
        # POUR idioms
        (r"^pour\s+(.+)\s+out\s+into\s+(.+)$",  r"pour \1 into \2"),
        (r"^tip\s+(.+)\s+into\s+(.+)$",         r"pour \1 into \2"),
        (r"^empty\s+(.+)\s+into\s+(.+)$",       r"pour \1 into \2"),
        (r"^decant\s+(.+)\s+into\s+(.+)$",      r"pour \1 into \2"),
        # FILL idioms
        (r"^fill\s+up\s+(.+)\s+with\s+(.+)$",   r"fill \1 with \2"),
        (r"^top\s+up\s+(.+)\s+with\s+(.+)$",    r"fill \1 with \2"),
        # WEAR idioms — must come before REMOVE so "take off" does not match "take"
        (r"^put\s+on\s+(.+)$",                  r"wear \1"),
        (r"^slip\s+on\s+(.+)$",                 r"wear \1"),
        (r"^slip\s+(.+)\s+on$",                 r"wear \1"),
        # REMOVE idioms
        (r"^take\s+off\s+(.+)$",                r"remove \1"),
        (r"^slip\s+off\s+(.+)$",                r"remove \1"),
        (r"^slip\s+(.+)\s+off$",                r"remove \1"),
        # MATCH-SPECIFIC rewrites: route "strike/light a match" to the matchbox.
        # This allows the player to strike matches as a standalone action
        # without specifying what they intend to light.
        (r"^strike\s+a?\s*match(?:es)?$",          r"light matchbox"),
        (r"^light\s+a?\s*match(?:es)?$",           r"light matchbox"),
        (r"^ignite\s+a?\s*match(?:es)?$",          r"light matchbox"),
        (r"^strike\s+a?\s*match(?:es)?\s+(.+)$",  r"light matchbox"),

        # UNMOUNT idioms — all route to "unmount <obj>"
        (r"^take\s+down\s+(.+)$",                       r"unmount \1"),
        (r"^remove\s+(.+)\s+from\s+(the\s+)?wall$",   r"unmount \1"),
        (r"^remove\s+(.+)\s+from\s+(the\s+)?mount$",  r"unmount \1"),
        (r"^remove\s+(.+)\s+from\s+(the\s+)?rack$",   r"unmount \1"),
        (r"^lift\s+(.+)\s+off\s+(the\s+)?wall$",      r"unmount \1"),
        (r"^unhook\s+(.+)$",                              r"unmount \1"),

        # USE / APPLY as generic combiner
        (r"^apply\s+(.+)\s+to\s+(.+)$",         r"use \1 with \2"),
        (r"^use\s+(.+)\s+on\s+(.+)$",           r"use \1 with \2"),

        # LIGHT: strip trailing "with <fire-source>" phrasing.
        # "light lamp with matches" / "light lamp with a match" should
        # behave identically to "light lamp" — the fire source is found
        # automatically from inventory by handle_light in engine.py.
        (r"^light\s+(.+?)\s+with\s+\S.*$",     r"light \1"),
        (r"^ignite\s+(.+?)\s+with\s+\S.*$",    r"light \1"),
        (r"^kindle\s+(.+?)\s+with\s+\S.*$",    r"light \1"),
    ]

    for pattern, replacement in rewrites:
        if re.match(pattern, t):
            return re.sub(pattern, replacement, t).strip()

    return t


# ============================================================
# Verb identification and parsing helpers
# ============================================================

def typo_correct_verb_token(token: str) -> Optional[str]:
    """
    Return the canonical verb_id if token is a close typo of a known single-word verb form.
    """
    token = token.lower().strip()
    if not token:
        return None

    if token in SINGLE_WORD_VERB_FORM_TO_ID:
        return SINGLE_WORD_VERB_FORM_TO_ID[token]

    match = difflib.get_close_matches(
        token,
        list(SINGLE_WORD_VERB_FORM_TO_ID.keys()),
        n=1,
        cutoff=0.82,
    )
    if match:
        return SINGLE_WORD_VERB_FORM_TO_ID[match[0]]

    return None


def identify_literal_verb(text: str) -> Tuple[Optional[str], str]:
    """
    Identify a literal or typo-corrected verb at the start of the text.

    Returns:
        (verb_id, remainder_text)
    """
    t = normalize(text)
    if not t:
        return None, ""

    # Prefer longest multi-word forms first.
    multi_word_forms = sorted(
        (form for form in VERB_FORM_TO_ID if " " in form),
        key=len,
        reverse=True,
    )
    for form in multi_word_forms:
        if t.startswith(form):
            remainder = t[len(form):].strip()
            return VERB_FORM_TO_ID[form], remainder

    tokens = t.split()
    if not tokens:
        return None, ""

    first = tokens[0]

    if first in VERB_FORM_TO_ID:
        return VERB_FORM_TO_ID[first], " ".join(tokens[1:]).strip()

    corrected = typo_correct_verb_token(first)
    if corrected is not None:
        return corrected, " ".join(tokens[1:]).strip()

    return None, t


def looks_like_bare_noun_phrase(text: str) -> bool:
    """
    Return True if the input looks like a noun phrase with no obvious verb.
    """
    t = normalize(text)
    if not t:
        return False

    tokens = tokenize_for_objects(t)
    if not tokens:
        return False

    if len(tokens) == 1 and tokens[0] in DIRECTIONS:
        return False

    first = tokens[0]
    if first in VERB_FORM_TO_ID:
        return False
    if typo_correct_verb_token(first) is not None:
        return False

    return True


def semantic_intent_guess(
    text: str,
    semantic_router: Optional[SemanticIntentRouter],
    min_score: float = 0.42,
    margin: float = 0.03,
) -> Optional[str]:
    """
    Return the best semantic verb_id if it is confident enough.

    Strategy:
    1. Try the whole utterance.
    2. If that fails, try the leading action phrase (usually the first token).
    """
    if semantic_router is None:
        return None

    # First try the whole utterance.
    ranked = semantic_router.route(text)
    if ranked:
        best_name, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        if best_score >= min_score and (best_score - second_score) >= margin:
            return best_name

    # Fallback: try just the leading token / short action phrase.
    tokens = tokenize_for_objects(normalize(text))
    if not tokens:
        return None

    short_candidates = [
        tokens[0],
        " ".join(tokens[:2]) if len(tokens) >= 2 else tokens[0],
    ]

    for short_text in short_candidates:
        ranked = semantic_router.route(short_text)
        if not ranked:
            continue

        best_name, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        if best_score >= min_score and (best_score - second_score) >= margin:
            return best_name

    return None


def parse_by_shape(
    verb_def: VerbDefinition,
    remainder: str,
    raw: str,
) -> List[dict]:
    """
    Parse a command remainder according to a declarative argument shape.
    """
    shape = verb_def.shape
    verb_id = verb_def.verb_id
    remainder = remainder.strip()

    if shape == "verb_only":
        return [meta_ir(verb_id, raw=raw)]

    if shape == "verb_obj":
        obj = remainder or None
        return [action_ir(verb_id, obj=obj, raw=raw)]

    if shape == "verb_obj_prep_iobj":
        left, prep, right = extract_object_phrases(remainder)

        # If the player omitted the preposition but the verb prefers one, use the first preferred prep.
        if prep is None and verb_def.preferred_preps:
            prep = verb_def.preferred_preps[0]

        return [action_ir(verb_id, obj=left, prep=prep, iobj=right, raw=raw)]

    if shape == "verb_direction_or_target":
        tokens = tokenize_for_objects(remainder)
        if tokens and tokens[0] in DIRECTIONS:
            return [action_ir("go", iobj=DIRECTIONS[tokens[0]], raw=raw)]
        return [action_ir("go", prep="to", iobj=remainder or None, raw=raw)]

    return []


def expand_coordinated_objects(command: str) -> List[str]:
    """
    Expand simple coordinated-object commands based on the verb registry.

    Examples:
        "get brass key and iron key" -> ["get brass key", "get iron key"]
        "drop brass key and iron key" -> ["drop brass key", "drop iron key"]
    """
    command = normalize(command)
    if not command:
        return []

    verb_id, remainder = identify_literal_verb(command)
    if verb_id is None:
        return [command]

    verb_def = VERB_DEFS.get(verb_id)
    if verb_def is None or not verb_def.allow_coordination:
        return [command]

    if not remainder:
        return [command]

    # Do not try to expand prepositional phrases.
    if re.search(r"\b(in|into|inside|on|onto|with|from|to|at)\b", remainder):
        return [command]

    parts = [p.strip() for p in re.split(r"\band\b", remainder) if p.strip()]
    if len(parts) < 2:
        return [command]

    # Preserve the original leading literal form if possible.
    leading_text = command[: len(command) - len(remainder)].strip()
    if not leading_text:
        leading_text = verb_def.literal_forms[0]

    return [f"{leading_text} {part}" for part in parts]


# ============================================================
# Improvement B — novel verb handling
# ============================================================

# B1: Sentence-level preamble patterns to strip before parsing.
# Maps regex -> replacement so "I want to take the key" -> "take the key".
_PREAMBLE_REWRITES = [
    (r"^i\s+want\s+to\s+(.+)$",             r"\1"),
    (r"^i\s+would\s+like\s+to\s+(.+)$",    r"\1"),
    (r"^i\s+would\s+like\s+(.+)$",          r"\1"),
    (r"^i\s+d\s+like\s+to\s+(.+)$",        r"\1"),
    (r"^can\s+you\s+(.+?)\s+please\s*$",   r"\1"),
    (r"^can\s+you\s+(.+)$",                  r"\1"),
    (r"^could\s+you\s+(.+?)\s+please\s*$", r"\1"),
    (r"^could\s+you\s+(.+)$",                r"\1"),
    (r"^please\s+(.+)$",                      r"\1"),
    (r"^let\s+me\s+(.+)$",                   r"\1"),
    (r"^try\s+to\s+(.+)$",                   r"\1"),
    (r"^attempt\s+to\s+(.+)$",               r"\1"),
    (r"^i\s+will\s+(.+)$",                   r"\1"),
    (r"^i'll\s+(.+)$",                       r"\1"),
    (r"^maybe\s+(.+)$",                       r"\1"),
]


def strip_preamble(text: str) -> str:
    """
    Strip natural-language preamble from a command.

    "I want to take the key"    -> "take the key"
    "can you open the door"     -> "open the door"
    "let me look at the box"    -> "look at the box"
    "please examine the key"    -> "examine the key"

    Applied iteratively so nested preambles are resolved:
    "please try to take the key" -> "try to take the key" -> "take the key"
    """
    t = normalize(text)
    changed = True
    while changed:
        changed = False
        for pattern, replacement in _PREAMBLE_REWRITES:
            if re.match(pattern, t):
                t = re.sub(pattern, replacement, t).strip()
                changed = True
                break
    return t


# B2: Verb synonym table.
# Maps surface forms not in any VerbDefinition.literal_forms to a known verb_id.
# This is a curated list of common player vocabulary not already covered by
# literal_forms or idiom rewrites.  It is checked AFTER the main literal lookup
# so it never overrides an existing exact match.
_VERB_SYNONYMS: Dict[str, str] = {
    # take-family
    "retrieve":    "take",
    "snatch":      "take",
    "grab onto":   "take",
    "collect":     "take",
    "pocket":      "take",
    "acquire":     "take",
    "seize":       "take",
    "fetch":       "take",
    "lift":        "take",
    "nab":         "take",

    # drop-family
    "toss":        "drop",
    "discard":     "drop",
    "chuck":       "drop",
    "ditch":       "drop",
    "leave":       "drop",
    "abandon":     "drop",
    "release":     "drop",
    "set down":    "drop",
    "put down":    "drop",

    # examine-family
    "study":       "examine",
    "scrutinise":  "examine",
    "scrutinize":  "examine",
    "survey":      "examine",
    "observe":     "examine",
    "check":       "examine",
    "view":        "examine",
    "scan":        "examine",
    "peer at":     "examine",
    "gaze at":     "examine",

    # put-family
    "deposit":     "put",
    "stow":        "put",
    "store":       "put",
    "slip":        "put",
    "slide":       "put",
    "tuck":        "put",
    "lodge":       "put",
    "wedge":       "put",
    "rest":        "put",
    "set":         "put",

    # open-family
    "unseal":      "open",
    "unclose":     "open",
    "swing open":  "open",
    "pry open":    "open",
    "force open":  "open",
    "crack open":  "open",

    # unlock-family
    "unbolt":      "unlock",
    "unlatch":     "unlock",
    "undo":        "unlock",

    # pull-family
    "wrench":      "pull",
    "yank":        "pull",
    "haul":        "pull",
    "drag":        "pull",

    # push-family
    "prod":        "push",
    "shove":       "push",
    "press":       "push",

    # read-family
    "peruse":      "read",
    "skim":        "read",
    "decipher":    "read",
    "decode":      "read",

    # wear-family
    "equip":       "wear",
    "don":         "wear",
    "slip on":     "wear",

    # light-family
    "kindle":      "light",
    "ignite":      "light",
    "set alight":  "light",

    # use-family
    "apply":       "use",
    "employ":      "use",
    "utilise":     "use",
    "utilize":     "use",
    "wield":       "use",
    "brandish":    "use",
    # unmount-family
    "unstrap":     "unmount",
    "unbuckle":    "unmount",
    "pry off":     "unmount",

    # pour-family
    "decant":      "pour",
    "tip out":     "pour",

    # go-family
    "proceed":     "go",
    "advance":     "go",
    "wander":      "go",
    "stride":      "go",
    "step":        "go",
    "march":       "go",
    "trudge":      "go",
    "slink":       "go",
    "creep":       "go",
    "sneak":       "go",
}

# Pre-sort synonym keys by length descending so longer phrases match first.
_SYNONYM_KEYS_SORTED = sorted(_VERB_SYNONYMS.keys(), key=len, reverse=True)


def identify_synonym_verb(text: str) -> Tuple[Optional[str], str]:
    """
    Check whether the text begins with a known verb synonym.

    Returns (verb_id, remainder) if a synonym is found, else (None, text).
    Synonyms are checked longest-first to handle multi-word forms like
    "set down" before the single token "set".
    """
    t = normalize(text)
    for syn in _SYNONYM_KEYS_SORTED:
        if t == syn or t.startswith(syn + " "):
            verb_id = _VERB_SYNONYMS[syn]
            remainder = t[len(syn):].strip()
            return verb_id, remainder
    return None, text


def semantic_slot_fill(
    text: str,
    verb_def: "VerbDefinition",
    world: "World",
    semantic_index: Optional["SemanticEntityIndex"],
) -> List[dict]:
    """
    B3: Semantic slot filling.

    When a verb has been identified but parse_by_shape would extract the
    object phrase mechanically (by stripping the first token and splitting
    on prepositions), this function uses the semantic entity index to find
    the best matching entities for each argument slot instead.

    This handles cases like:
        "deposit the brass key in the box"
        -> verb=put identified, then semantic search finds brass_key for obj
           and wooden_box for iobj.

    Falls back to parse_by_shape when the embedder is unavailable or when
    semantic confidence is too low.

    Only called from parse_to_candidates when a synonym or semantic verb
    identification was used — never for exact literal matches, where
    parse_by_shape is always preferred.
    """
    shape = verb_def.shape
    raw   = text

    # verb_only and verb_direction shapes don't benefit from semantic filling.
    if shape in ("verb_only", "verb_direction_or_target"):
        # text is already the post-verb remainder; pass directly.
        return parse_by_shape(verb_def, text, raw)

    if semantic_index is None or not semantic_index.embedder.enabled():
        # Degraded mode: fall back to parse_by_shape with naive remainder.
        # text is already the post-verb remainder; pass directly.
        return parse_by_shape(verb_def, text, raw)

    visible = world.visible_entities()
    if not visible:
        # text is already the post-verb remainder; pass directly.
        return parse_by_shape(verb_def, text, raw)

    # Embed the full post-verb phrase against all visible entities.
    # We embed the whole text rather than trying to split it first,
    # letting the model find the most salient entity references.
    SLOT_FLOOR  = 0.25   # minimum cosine sim to accept any entity
    SLOT_MARGIN = 0.05   # top candidate must beat second by this much

    # Query the entity index with the full input.
    all_matches = semantic_index.match(text, top_k=len(visible))
    if not all_matches or all_matches[0][1] < SLOT_FLOOR:
        # text is already the post-verb remainder; pass directly.
        return parse_by_shape(verb_def, text, raw)

    # For verb_obj shape: pick the top entity as obj.
    if shape == "verb_obj":
        top_eid, top_sim = all_matches[0]
        second_sim = all_matches[1][1] if len(all_matches) > 1 else 0.0
        if top_sim >= SLOT_FLOOR and (top_sim - second_sim) >= SLOT_MARGIN:
            return [action_ir(verb_def.verb_id, obj=top_eid, raw=raw)]
        # Ambiguous — fall back to symbolic
        # text is already the post-verb remainder; pass directly.
        return parse_by_shape(verb_def, text, raw)

    # For verb_obj_prep_iobj shape: pick top two distinct entities.
    if shape == "verb_obj_prep_iobj":
        # First pick: best entity overall for obj slot.
        obj_eid  = None
        iobj_eid = None
        used     = set()

        for eid, sim in all_matches:
            if sim < SLOT_FLOOR:
                break
            if obj_eid is None:
                obj_eid = eid
                used.add(eid)
            elif eid not in used:
                iobj_eid = eid
                break

        # Determine prep from the text if possible; fall back to preferred.
        _, prep, _ = extract_object_phrases(text)
        if prep is None and verb_def.preferred_preps:
            prep = verb_def.preferred_preps[0]

        return [action_ir(verb_def.verb_id, obj=obj_eid,
                          prep=prep, iobj=iobj_eid, raw=raw)]

    # Fallback for any other shape.
    tokens = tokenize_for_objects(text)
    remainder = " ".join(tokens[1:]).strip() if tokens else ""
    return parse_by_shape(verb_def, remainder, raw)


def parse_to_candidates(text: str, parser_system: Optional["ParserSystem"] = None) -> List[dict]:
    """
    Parse text into candidate IRs.

    Priority:
    1. idiom rewrites
    2. exact meta commands / directions
    3. exact + typo-corrected literal verb identification
    4. semantic whole-utterance intent classification
    5. safe missing-verb / failure handling
    """
    raw = text

    # B1: Strip natural-language preamble before any other processing.
    # "I want to take the key" -> "take the key"
    text = strip_preamble(text)

    text = rewrite_common_idioms(text)
    text = rewrite_movement_idioms(text)
    text = rewrite_interaction_idioms(text)

    if text in ("look", "l"):
        return [meta_ir("look", raw=raw)]

    if text in ("inventory", "inv", "i"):
        return [meta_ir("inventory", raw=raw)]

    if text in DIRECTIONS:
        return [action_ir("go", iobj=DIRECTIONS[text], raw=raw)]

    # --------------------------------------------------------
    # Literal / typo-aware parsing first (unchanged)
    # --------------------------------------------------------
    literal_verb_id, literal_remainder = identify_literal_verb(text)
    if literal_verb_id is not None:
        verb_def = VERB_DEFS[literal_verb_id]
        return parse_by_shape(verb_def, literal_remainder, raw)

    # --------------------------------------------------------
    # B2: Verb synonym lookup
    # Checked before semantic routing so common synonyms ("retrieve",
    # "deposit", "study", etc.) resolve deterministically without the
    # embedding model.  Uses semantic_slot_fill (B3) for slot extraction
    # so the object phrase benefits from entity-index matching.
    # --------------------------------------------------------
    world = getattr(parser_system, "_current_world", None) if parser_system else None
    sem_index = (
        parser_system.semantic_entity_index
        if parser_system is not None else None
    )

    synonym_verb_id, synonym_remainder = identify_synonym_verb(text)
    if synonym_verb_id is not None:
        verb_def = VERB_DEFS[synonym_verb_id]
        if world is not None:
            return semantic_slot_fill(synonym_remainder, verb_def,
                                     world, sem_index)
        return parse_by_shape(verb_def, synonym_remainder, raw)

    # --------------------------------------------------------
    # Semantic whole-utterance intent classification
    # --------------------------------------------------------
    semantic_router = parser_system.semantic_router if parser_system is not None else None
    semantic_best = semantic_intent_guess(text, semantic_router)

    if semantic_best is not None:
        verb_def = VERB_DEFS[semantic_best]
        if world is not None:
            tokens = tokenize_for_objects(text)
            remainder_text = " ".join(tokens[1:]).strip() if tokens else ""
            return semantic_slot_fill(remainder_text, verb_def, world, sem_index)
        tokens = tokenize_for_objects(text)
        semantic_remainder = " ".join(tokens[1:]).strip() if len(tokens) >= 2 else ""
        return parse_by_shape(verb_def, semantic_remainder, raw)

    # --------------------------------------------------------
    # Only now treat it as a missing-verb noun phrase
    # --------------------------------------------------------
    if looks_like_bare_noun_phrase(text):
        return [{"type": "missing_verb", "text": text, "raw": raw}]

    return []


# ============================================================
# Grounding
# ============================================================

def entity_name_token_sets(ent: Entity) -> List[set[str]]:
    """
    Return token sets for all names/aliases of an entity.
    """
    return [set(name.split()) for name in ent.all_names()]


def phrase_matches_entity_literally(phrase_tokens: List[str], ent: Entity) -> bool:
    """
    Literal phrase matching policy:

    - For one-word phrases like ["key"], return True if any alias/name contains that token.
    - For multi-word phrases like ["brass", "key"], return True only if all tokens are
      contained in the same alias/name token set.
    """
    if not phrase_tokens:
        return False

    token_sets = entity_name_token_sets(ent)
    ptoks = set(phrase_tokens)

    if len(phrase_tokens) == 1:
        token = phrase_tokens[0]
        return any(token in token_set for token_set in token_sets)

    return any(ptoks.issubset(token_set) for token_set in token_sets)


def phrase_specificity_score(phrase_tokens: List[str], ent: Entity) -> float:
    """
    Reward entities whose aliases/names match the full phrase well,
    and penalize candidates that miss important descriptive words.
    """
    ptoks = set(phrase_tokens)
    best = 0.0

    for name in ent.all_names():
        ntoks = set(name.split())
        if not ntoks:
            continue

        overlap = len(ptoks & ntoks)
        if overlap == 0:
            continue

        phrase_coverage = overlap / max(len(ptoks), 1)
        name_coverage = overlap / max(len(ntoks), 1)

        score = 3.0 * phrase_coverage + 2.0 * name_coverage

        if " ".join(phrase_tokens) == name:
            score += 5.0

        missing_from_entity = ptoks - ntoks
        score -= 2.5 * len(missing_from_entity)

        best = max(best, score)

    return best


def score_symbolic_match(phrase_tokens: List[str], ent: Entity, world: World) -> float:
    """
    Symbolic match score that rewards specific phrase matches much more strongly
    than generic noun overlap.
    """
    if not phrase_tokens:
        return 0.0

    score = phrase_specificity_score(phrase_tokens, ent)

    names = " ".join(ent.all_names())
    name_tokens = set(names.split())
    overlap = len(set(phrase_tokens) & name_tokens)

    if overlap == 0 and score == 0.0:
        return 0.0

    score += 0.5 * overlap

    if ent.eid in world.visible_entities():
        score += 1.5

    if ent.eid in world.last_referred:
        score += 1.0 + (0.5 / (world.last_referred.index(ent.eid) + 1))

    return score


def affordance_bonus(verb: str, ent: Entity, slot_name: str, prep: Optional[str]) -> float:
    """
    Bonus (or penalty) score if the candidate entity fits the intended action.

    These bonuses break ties when a single token matches multiple entities.
    For example, "oil" matches both oil_lamp and lamp_oil — but for the iobj
    slot of FILL, a liquid_source scores +2.0 and a lightable scores -1.0,
    so the flask wins decisively without a clarification prompt.

    Rules are grouped by slot (obj vs iobj) and then by verb.
    """
    bonus = 0.0

    if slot_name == "obj":
        # --- Original verbs ---
        if verb == "take" and "portable" in ent.tags and "scenery" not in ent.tags:
            bonus += 2.0
        if verb in {"open", "close"} and "openable" in ent.tags:
            bonus += 2.0
        if verb == "unlock" and "lockable" in ent.tags:
            bonus += 2.0
        if verb in {"examine", "look"}:
            bonus += 0.5
        if "scenery" in ent.tags and verb == "take":
            bonus -= 2.0

        # --- New verbs ---
        # LIGHT / EXTINGUISH: prefer lightable entities in obj slot
        if verb == "light" and "lightable" in ent.tags:
            bonus += 2.0
        if verb == "extinguish" and "lightable" in ent.tags:
            bonus += 2.0

        # READ: prefer readable entities
        if verb == "read" and "readable" in ent.tags:
            bonus += 2.0

        # PULL / PUSH: prefer pullable/pushable entities
        if verb == "pull" and "pullable" in ent.tags:
            bonus += 2.0
        if verb == "push" and "pushable" in ent.tags:
            bonus += 2.0

        # FILL (obj slot = the vessel being filled): prefer lightable/container
        # The flask is the iobj (source), not the obj, so penalise liquid_source here.
        if verb == "fill" and "lightable" in ent.tags:
            bonus += 2.0
        if verb == "fill" and "liquid_source" in ent.tags:
            bonus -= 1.5   # flask is the iobj of fill, not the obj

        # POUR (obj slot = the source being poured from): prefer liquid_source
        if verb == "pour" and "liquid_source" in ent.tags:
            bonus += 2.0
        if verb == "pour" and "lightable" in ent.tags:
            bonus -= 1.5   # lamp is iobj of pour, not obj

        # WEAR / REMOVE: prefer wearable entities
        if verb == "wear" and "wearable" in ent.tags:
            bonus += 2.0
        if verb == "remove" and "wearable" in ent.tags:
            bonus += 2.0

    if slot_name == "iobj":
        # --- Original verbs ---
        if verb == "put" and prep in {"in", "into", "inside"} and "container" in ent.tags:
            bonus += 2.0
        if verb == "put" and prep in {"on", "onto"} and "support" in ent.tags:
            bonus += 2.0
        if verb == "unlock" and prep == "with" and "portable" in ent.tags:
            bonus += 1.0

        # --- New verbs ---
        # FILL (iobj slot = the liquid source): prefer liquid_source, penalise lightable
        if verb == "fill" and prep in {"with", "from"} and "liquid_source" in ent.tags:
            bonus += 2.5
        if verb == "fill" and "lightable" in ent.tags:
            bonus -= 2.0   # the lamp is the obj of fill, not the iobj

        # POUR (iobj slot = the target container): prefer containers/basins
        if verb == "pour" and "container" in ent.tags:
            bonus += 2.0
        if verb == "pour" and "liquid_source" in ent.tags:
            bonus -= 1.0   # source flasks are obj of pour, not iobj

        # UNLOCK (iobj slot = the key): prefer entities with key_id
        if verb == "unlock" and ent.props.get("key_id") is not None:
            bonus += 1.5

    return bonus


def phrase_matches_entity_by_description(phrase_tokens: List[str], ent: Entity) -> bool:
    """
    Return True if at least one phrase token appears in the entity's
    description text.  This is a third matching tier, weaker than alias
    matching but stronger than pure semantic similarity.  It handles
    references like "the rusty lever" or "the tin lamp" where the
    descriptive word comes from the entity's prose description rather
    than its name or alias list.
    """
    desc = ent.props.get("desc", "")
    if not isinstance(desc, str):
        return False
    desc_tokens = set(normalize(desc).split()) - FILLER
    return bool(set(phrase_tokens) & desc_tokens)


def resolve_phrase_to_entities(
    world: World,
    phrase: str,
    slot_name: str,
    verb: str,
    prep: Optional[str],
    semantic_index: Optional[SemanticEntityIndex] = None,
) -> List[Tuple[str, float]]:
    """
    Resolve a noun phrase to candidate entities, ranked by score.

    Matching tiers (in decreasing strength):
      1. Pronoun resolution  — "it/them/him/her" -> discourse memory
      2. Exact alias match   — all phrase tokens subset of one alias
      3. Description match   — at least one phrase token in entity desc text
      4. Pure semantic       — embedding cosine similarity, high threshold only

    For one-word phrases only tier 1 and 2 are used; semantic is too noisy
    for single tokens.  For multi-word phrases all tiers contribute, with
    semantic scores gated by a confidence floor and a margin requirement so
    a weakly-matching entity cannot beat a literal hit.

    Scoring:
      - Literal alias match  contributes via score_symbolic_match
      - Description match    contributes a partial symbolic score
      - Semantic similarity  contributes 2.0 * cosine, only for multi-word
        phrases and only when similarity >= SEMANTIC_ENTITY_FLOOR
      - Affordance bonus     applied on top regardless of which tier matched
    """
    SEMANTIC_ENTITY_FLOOR  = 0.30  # minimum cosine sim to count at all
    SEMANTIC_ENTITY_MARGIN = 0.08  # top candidate must beat second by this much
                                   # when the only match is semantic (no literal)

    if not phrase:
        return []

    phrase = normalize(phrase)

    # ── Tier 1: pronoun resolution ────────────────────────────────────────
    if phrase in {"it", "them", "him", "her"} and world.last_referred:
        return [(world.last_referred[0], 999.0)]

    visible = set(world.visible_entities())
    tokens = [t for t in phrase.split() if t not in FILLER]
    if not tokens:
        return []

    one_word = len(tokens) == 1
    scored: Dict[str, float] = {}
    literal_hits: set = set()   # eids that matched via alias or desc tokens

    # ── Tiers 2 & 3: literal and description matching ─────────────────────
    for eid in visible:
        ent = world.entity(eid)

        has_alias_match = phrase_matches_entity_literally(tokens, ent)
        has_desc_match  = (
            not one_word
            and not has_alias_match
            and phrase_matches_entity_by_description(tokens, ent)
        )

        if not has_alias_match and not has_desc_match:
            continue

        if has_alias_match:
            s = score_symbolic_match(tokens, ent, world)
            literal_hits.add(eid)
        else:
            # Description match: partial symbolic score.
            # We count the overlap with description tokens but apply a
            # lower base score than a full alias match.
            desc_tokens = set(normalize(ent.props.get("desc", "")).split()) - FILLER
            overlap = len(set(tokens) & desc_tokens)
            s = 1.0 + 0.4 * overlap   # weaker than a real alias match

        s += affordance_bonus(verb, ent, slot_name, prep)

        if one_word and s <= 0.0:
            s = 1.0

        if s > 0.0:
            scored[eid] = s

    # For one-word phrases, stop here — no semantic for single tokens.
    if one_word:
        results = list(scored.items())
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    # ── Tier 4: semantic similarity (multi-word phrases only) ─────────────
    if semantic_index is not None and semantic_index.embedder.enabled():
        semantic_matches = semantic_index.match(phrase, top_k=10)

        for eid, sim in semantic_matches:
            if eid not in visible:
                continue
            if sim < SEMANTIC_ENTITY_FLOOR:
                continue

            ent = world.entity(eid)
            sem_contribution = 2.0 * sim
            aff = affordance_bonus(verb, ent, slot_name, prep)

            if eid in scored:
                # Entity already scored by literal/desc — add semantic on top.
                scored[eid] += sem_contribution
            else:
                # Pure semantic hit — only accept if score is high enough.
                # We also enforce a margin check after full scoring below.
                scored[eid] = sem_contribution + aff

    if not scored:
        return []

    results = list(scored.items())
    results.sort(key=lambda x: x[1], reverse=True)

    # Apply margin check for pure-semantic-only candidates (no literal hit).
    # If the top result has no literal backing, require it to beat the next
    # candidate by SEMANTIC_ENTITY_MARGIN to avoid false positives.
    top_eid, top_score = results[0]
    if top_eid not in literal_hits:
        second_score = results[1][1] if len(results) > 1 else 0.0
        if (top_score - second_score) < SEMANTIC_ENTITY_MARGIN:
            # Remove all pure-semantic-only candidates that are tied.
            # Keep any literal-backed candidates even if scored lower.
            literal_results = [(e, s) for e, s in results if e in literal_hits]
            if literal_results:
                return literal_results
            # No literal hits at all — apply a higher confidence floor for
            # pure semantic resolution to reduce false positives.
            if top_score < 0.55:
                return []

    # Final minimum threshold — unchanged from original.
    if results and results[0][1] < 2.5:
        # Exception: if this was a pure semantic match with good confidence,
        # allow it through even below the literal threshold.
        if top_eid not in literal_hits and top_score >= 0.55:
            return results[:1]
        return []

    return results

def make_clarification_question(world: World, options: List[str]) -> str:
    """
    Build a natural clarification question from a list of candidate entity ids.
    """
    names = [world.entity(eid).name for eid in options]

    if not names:
        return "Which do you mean?"
    if len(names) == 1:
        return f"Did you mean {names[0]}?"
    if len(names) == 2:
        return f"Which do you mean: {names[0]} or {names[1]}?"

    return f"Which do you mean: {', '.join(names[:-1])} or {names[-1]}?"


# Prepositions that introduce locative/relational modifiers.
# These should be stripped from noun phrases before entity grounding
# to avoid PP-attachment errors ("key near the door" matching "door").
_LOCATIVE_PREPS = {
    "near", "by", "next", "beside", "behind", "under", "above",
    "against", "along", "around", "atop", "beneath", "between",
    "beyond", "inside", "outside", "over", "past", "through",
    "toward", "towards", "underneath",
}


def _strip_locative(phrase: str) -> str:
    """
    Remove a trailing locative prepositional phrase from a noun phrase.

    "the key near the door"  -> "the key"
    "the lamp by the table"  -> "the lamp"
    "the old brass key"      -> "the old brass key"  (no change)

    Only strips one trailing PP.  Does not strip prep phrases that are
    structural to the verb (those are handled by parse_by_shape).
    """
    tokens = phrase.split()
    for i, tok in enumerate(tokens):
        if tok in _LOCATIVE_PREPS and i > 0:
            return " ".join(tokens[:i]).strip()
    return phrase


def ground_intent(
    world: World,
    intent: dict,
    semantic_index: Optional[SemanticEntityIndex] = None,
) -> dict:
    """
    Ground obj/iobj phrases to entity ids or return a clarification IR.
    """
    if intent["type"] != "action":
        return intent

    pending = dict(intent)
    verb = pending["verb"]
    prep = pending.get("prep")

    if verb == "go" and isinstance(pending.get("iobj"), str) and pending["iobj"] in DIRECTIONS.values():
        return pending

    def ground_slot(slot_name: str) -> Tuple[Optional[str], Optional[dict]]:
        phrase = pending.get(slot_name)
        if not phrase:
            return None, None

        if phrase in world.entities:
            return phrase, None

        # Strip trailing locative/relational modifiers before grounding.
        # "the key near the door" -> "the key"
        # "the lamp by the wall"  -> "the lamp"
        # This avoids the PP-attachment problem where locative tokens
        # (like "door") incorrectly boost unrelated entities.
        clean_phrase = _strip_locative(phrase)

        matches = resolve_phrase_to_entities(
            world=world,
            phrase=clean_phrase,
            slot_name=slot_name,
            verb=verb,
            prep=prep,
            semantic_index=semantic_index,
        )

        if not matches:
            return None, None

        phrase_tokens = [t for t in normalize(str(phrase)).split() if t not in FILLER]
        one_word = len(phrase_tokens) == 1

        top_score = matches[0][1]

        # Compute a margin that is tighter for single-word phrases.
        # Multi-word phrases use 0.15; single-word phrases use 0.5 so that
        # affordance bonuses (which are ±1.0 to ±2.5) have room to break ties.
        # Only fall through to clarification when the top two scores are truly
        # indistinguishable after all bonuses are applied.
        tie_margin = 0.5 if one_word else 0.15

        tied = [eid for eid, score in matches if abs(score - top_score) < tie_margin]

        if len(tied) == 1:
            return tied[0], None

        # Genuine tie: ask the player to disambiguate.
        options = tied[:5]
        question = make_clarification_question(world, options)
        return None, clarify_ir(question=question, options=options, pending=pending)

    grounded_obj, clar1 = ground_slot("obj")
    if clar1:
        return clar1
    if grounded_obj:
        pending["obj"] = grounded_obj

    grounded_iobj, clar2 = ground_slot("iobj")
    if clar2:
        return clar2
    if grounded_iobj:
        pending["iobj"] = grounded_iobj

    return pending


# ============================================================
# Main parser system
# ============================================================

@dataclass
class ParserSystem:
    embedder: Embedder
    semantic_router: SemanticIntentRouter
    semantic_entity_index: SemanticEntityIndex

    @classmethod
    def build_default(cls, local_model_dir: str = "./models/all-MiniLM-L6-v2") -> "ParserSystem":
        embedder = Embedder(local_model_dir=local_model_dir)

        semantic_templates = {
            verb_id: list(verb_def.literal_forms) + list(verb_def.semantic_examples)
            for verb_id, verb_def in VERB_DEFS.items()
        }

        semantic_router = SemanticIntentRouter(embedder=embedder, templates=semantic_templates)
        semantic_router.build()
        semantic_entity_index = SemanticEntityIndex(embedder=embedder)

        return cls(
            embedder=embedder,
            semantic_router=semantic_router,
            semantic_entity_index=semantic_entity_index,
        )