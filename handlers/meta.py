# handlers/meta.py
#
# Meta-command handlers: save.
#
# "look", "inventory", and "status" are handled directly in
# process_input's meta branch since they don't go through the
# normal action dispatch.  Save is registered here so it can
# also be dispatched as a normal verb.

from typing import Tuple

from model import World
from handlers.registry import register


@register("save")
def handle_save(world: World, ir: dict) -> Tuple[str, bool]:
    """
    Save the current game state to bafflehouse_save.json.
    Blocked during active combat.
    """
    import engine as _engine
    from savegame import save_game

    in_combat = _engine._COMBAT_SESSION is not None
    msg = save_game(world, in_combat=in_combat)
    return msg, not in_combat
