# handlers/__init__.py
#
# Handler package initialisation.
#
# Importing this package triggers registration of all verb handlers
# via their @register decorators.  After import, the registry dict
# is fully populated and ready for engine.exec_action() to query.

from handlers.registry import register, get_handler, all_handlers  # noqa: F401

# Import every handler module so their @register decorators fire.
# The order does not matter — each module registers its own verbs.
import handlers.movement       # noqa: F401
import handlers.inventory      # noqa: F401
import handlers.interaction    # noqa: F401
import handlers.puzzle         # noqa: F401
import handlers.combat_actions # noqa: F401
import handlers.npc_actions    # noqa: F401
import handlers.meta           # noqa: F401
