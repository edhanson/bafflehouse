# handlers/registry.py
#
# Central handler registry.
#
# Each verb handler decorates itself with @register("verb_name").
# At import time the decorator populates _HANDLERS.  The engine
# calls get_handler(verb) to retrieve the right function without
# any if/elif chain.
#
# Adding a new verb:
#   1. Write a handler function in the appropriate handlers/*.py file.
#   2. Decorate it with @register("your_verb").
#   3. Done — no changes to engine.py or this file.

from typing import Callable, Dict, Optional, Tuple

from model import World

# Type alias: every handler takes (World, ir_dict) and returns
# (response_text, action_consumed_flag).
HandlerFunc = Callable[[World, dict], Tuple[str, bool]]

# The registry — populated at import time by @register decorators.
_HANDLERS: Dict[str, HandlerFunc] = {}


def register(verb: str):
    """
    Decorator that registers a function as the handler for *verb*.

    Usage::

        @register("take")
        def handle_take(world: World, ir: dict) -> Tuple[str, bool]:
            ...

    If the same verb is registered twice the later registration wins
    (useful for overriding during testing).
    """
    def decorator(func: HandlerFunc) -> HandlerFunc:
        _HANDLERS[verb] = func
        return func
    return decorator


def get_handler(verb: str) -> Optional[HandlerFunc]:
    """Look up the handler for *verb*.  Returns None if unregistered."""
    return _HANDLERS.get(verb)


def all_handlers() -> Dict[str, HandlerFunc]:
    """Return a shallow copy of the full registry (for introspection)."""
    return dict(_HANDLERS)
