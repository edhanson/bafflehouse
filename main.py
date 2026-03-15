"""
main.py

Entry point for the interactive fiction engine.

This script wires together:
- the demo world from content.py
- the parser system from parser.py
- the game loop and command processing from engine.py

Run:
    python main.py
"""

from __future__ import annotations

import os
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
# Main loop
# ============================================================

def main() -> None:
    world = build_demo_world()
    parser_system = ParserSystem.build_default(local_model_dir="./models/all-MiniLM-L6-v2")
    pending_clarify: Optional[dict] = None

    if parser_system.embedder.enabled():
        print("Semantic parser enabled (local model only).")
    else:
        print("Semantic parser unavailable; using symbolic parser only.")
        if parser_system.embedder.load_error:
            print(f"Reason: {parser_system.embedder.load_error}")

    print()
    print(do_look(world))

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
        print(output)


if __name__ == "__main__":
    main()