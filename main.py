"""
main.py

Entry point for the interactive fiction engine.

This script wires together:
- the demo world from content.py
- the parser system from parser.py
- the game loop and command processing from engine.py

Output text is word-wrapped to fit the current terminal width, measured
each time a response is printed so it adapts if the window is resized.
A fallback width of 80 columns is used if the terminal size cannot be
determined (e.g. when output is piped).

Run:
    python main.py
"""

from __future__ import annotations

import os
import shutil
import textwrap
from typing import Optional


# ------------------------------------------------------------
# Force local/offline Hugging Face behavior BEFORE imports.
# ------------------------------------------------------------
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from content import build_demo_world
from engine import do_look, process_input
from parser import ParserSystem, normalize


# ============================================================
# Text formatting
# ============================================================

# Leave a small margin so text does not run hard against the terminal edge.
_MARGIN = 2
_FALLBACK_WIDTH = 80


def terminal_width() -> int:
    """
    Return the current terminal width in columns minus a small margin.

    shutil.get_terminal_size() queries the OS at call time, so this
    reflects the current window size rather than the size at startup.
    The fallback value is used when stdout is not a TTY (e.g. piped).
    """
    cols = shutil.get_terminal_size(fallback=(_FALLBACK_WIDTH, 24)).columns
    return max(40, cols - _MARGIN)


def wrap(text: str) -> str:
    """
    Word-wrap text to fit the current terminal width.

    Each paragraph (separated by a blank line or a single \\n) is wrapped
    independently so that short lines — room titles, single-line responses
    like "Taken." — are never merged with the paragraph that follows them.

    A paragraph that is already shorter than the terminal width is returned
    unchanged.  Leading whitespace within a paragraph is preserved so that
    any intentional indentation (e.g. numbered clarification lists) is kept.
    """
    width = terminal_width()
    paragraphs = text.split("\n")
    wrapped = []
    for para in paragraphs:
        if not para.strip():
            # Preserve blank lines between paragraphs.
            wrapped.append("")
        elif len(para) <= width:
            # Short line — no wrapping needed.
            wrapped.append(para)
        else:
            # Determine indentation of this paragraph so wrapped continuation
            # lines are indented to the same level.
            indent = len(para) - len(para.lstrip())
            wrapped.append(
                textwrap.fill(
                    para,
                    width=width,
                    initial_indent=" " * indent,
                    subsequent_indent=" " * indent,
                )
            )
    return "\n".join(wrapped)


def print_output(text: str) -> None:
    """Wrap and print a block of engine output."""
    print(wrap(text))


# ============================================================
# Main loop
# ============================================================

def main() -> None:
    world = build_demo_world()
    parser_system = ParserSystem.build_default(
        local_model_dir="./models/all-MiniLM-L6-v2"
    )
    pending_clarify: Optional[dict] = None

    if parser_system.embedder.enabled():
        print_output("Semantic parser enabled (local model only).")
    else:
        print_output("Semantic parser unavailable; using symbolic parser only.")
        if parser_system.embedder.load_error:
            print_output(f"Reason: {parser_system.embedder.load_error}")

    print()
    print_output(do_look(world))

    while True:
        try:
            line = input(f"\n[{world.clock.now}] > ")
        except (EOFError, KeyboardInterrupt):
            print("\nFarewell.")
            break

        if not line.strip():
            continue

        if normalize(line) in {"quit", "exit"}:
            print("Farewell.")
            break

        output, pending_clarify = process_input(
            world=world,
            parser_system=parser_system,
            text=line,
            pending_clarify=pending_clarify,
        )
        print_output(output)


if __name__ == "__main__":
    main()