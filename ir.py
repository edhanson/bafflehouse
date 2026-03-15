from __future__ import annotations
from typing import List, Optional

# ============================================================
# IR / DSL
# ============================================================

def action_ir(
    verb: str,
    obj: Optional[str] = None,
    prep: Optional[str] = None,
    iobj: Optional[str] = None,
    raw: str = "",
) -> dict:
    return {
        "type": "action",
        "verb": verb,
        "obj": obj,
        "prep": prep,
        "iobj": iobj,
        "raw": raw,
    }


def meta_ir(verb: str, raw: str = "") -> dict:
    return {
        "type": "meta",
        "verb": verb,
        "raw": raw,
    }


def clarify_ir(question: str, options: List[str], pending: dict) -> dict:
    return {
        "type": "clarify",
        "question": question,
        "options": options,
        "pending": pending,
    }