"""
main.py

Entry point for the Bafflehouse interactive fiction engine.

Features
────────
- Splash screen: ASCII art title displayed at startup.
- Session seed: a random seed is generated from the current datetime and
  printed at startup.  The player can supply a specific seed at the prompt
  to reproduce a previous session's random behaviour.
- Session log: every line of output (and every command) is written to a
  timestamped log file in the logs/ directory alongside the game files.
- In-game help: typing "help" prints the contents of help.txt.

Run:
    python main.py
"""

from __future__ import annotations

import os
import random
import shutil
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

# ────────────────────────────────────────────────────────────────────────────
# Force local / offline Hugging Face behaviour BEFORE any HF imports.
# ────────────────────────────────────────────────────────────────────────────
os.environ["HF_HUB_OFFLINE"]           = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from content import build_demo_world
from engine import do_look, process_input
from parser import ParserSystem, normalize


# ============================================================
# Text formatting
# ============================================================

_MARGIN        = 2
_FALLBACK_WIDTH = 80


def terminal_width() -> int:
    cols = shutil.get_terminal_size(fallback=(_FALLBACK_WIDTH, 24)).columns
    return max(40, cols - _MARGIN)


def wrap(text: str) -> str:
    width = terminal_width()
    wrapped = []
    for para in text.split("\n"):
        if not para.strip():
            wrapped.append("")
        elif len(para) <= width:
            wrapped.append(para)
        else:
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


# ============================================================
# Session log
# ============================================================

class SessionLog:
    """
    Writes all output and player commands to a timestamped file in logs/.
    """

    def __init__(self, seed: int) -> None:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        timestamp  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path  = log_dir / f"bafflehouse_{timestamp}.log"
        self._file: TextIO = self.path.open("w", encoding="utf-8")

        self._file.write("BAFFLEHOUSE session log\n")
        self._file.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._file.write(f"Seed:    {seed}\n")
        self._file.write("\u2500" * 72 + "\n\n")
        self._file.flush()

    def log_output(self, text: str) -> None:
        self._file.write(text + "\n")
        self._file.flush()

    def log_command(self, clock: int, text: str) -> None:
        self._file.write(f"\n[{clock}] > {text}\n")
        self._file.flush()

    def close(self) -> None:
        self._file.write("\n" + "\u2500" * 72 + "\n")
        self._file.write(
            f"Session ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        self._file.close()


# ============================================================
# Splash screen
# ============================================================

_SPLASH = r"""
╔════════════════════════════════════════════════════════╗
║                                                        ║
║    ██████╗  █████╗ ███████╗███████╗██╗     ███████╗    ║
║    ██╔══██╗██╔══██╗██╔════╝██╔════╝██║     ██╔════╝    ║
║    ██████╔╝███████║█████╗  █████╗  ██║     █████╗      ║
║    ██╔══██╗██╔══██║██╔══╝  ██╔══╝  ██║     ██╔══╝      ║
║    ██████╔╝██║  ██║██║     ██║     ███████╗███████╗    ║
║    ╚═════╝ ╚═╝  ╚═╝╚═╝     ╚═╝     ╚══════╝╚══════╝    ║
║                                                        ║
║    ██╗  ██╗ ██████╗ ██╗   ██╗███████╗███████╗          ║
║    ██║  ██║██╔═══██╗██║   ██║██╔════╝██╔════╝          ║
║    ███████║██║   ██║██║   ██║███████╗█████╗            ║
║    ██╔══██║██║   ██║██║   ██║╚════██║██╔══╝            ║
║    ██║  ██║╚██████╔╝╚██████╔╝███████║███████╗          ║
║    ╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝╚══════╝          ║
║                                                        ║
║                 An Interactive Fiction                 ║
║                                                        ║
╚════════════════════════════════════════════════════════╝
"""


def print_splash() -> None:
    width = terminal_width()
    for line in _SPLASH.split("\n"):
        if len(line) <= width:
            print(line.center(width) if line.strip() else line)
        else:
            print(line)


# ============================================================
# Seed management
# ============================================================

def generate_default_seed() -> int:
    """Generate a seed from the current datetime (YYYYMMDDHHMMSS)."""
    return int(datetime.now().strftime("%Y%m%d%H%M%S"))


def prompt_for_seed() -> int:
    """
    Show the default seed and allow the player to override it.
    Pressing Enter accepts the default.
    """
    default = generate_default_seed()
    print(f"  Session seed: {default}")
    print(f"  Press Enter to use this seed, or type a different number")
    print(f"  to reproduce a specific previous session.")
    print()

    while True:
        try:
            raw = input("  Seed > ").strip()
        except (EOFError, KeyboardInterrupt):
            return default

        if not raw:
            return default

        try:
            return int(raw)
        except ValueError:
            print("  Please enter a whole number, or press Enter for the default.")


# ============================================================
# Help command
# ============================================================

_HELP_FILE = Path(__file__).parent / "help.txt"


def load_help() -> str:
    """Return the contents of help.txt, or a brief fallback."""
    if _HELP_FILE.exists():
        return _HELP_FILE.read_text(encoding="utf-8").rstrip()
    return (
        "Help file not found.\n"
        "Basic commands: look, examine <thing>, take <thing>,\n"
        "go <direction>, open <thing>, read <thing>, inventory, quit."
    )


# ============================================================
# Output helpers
# ============================================================

def print_and_log(text: str, log: SessionLog) -> None:
    """Wrap, print, and log a block of text."""
    wrapped = wrap(text)
    print(wrapped)
    log.log_output(wrapped)


# ============================================================
# Main loop
# ============================================================

def main() -> None:
    # ── Splash ───────────────────────────────────────────────────────────
    print_splash()
    print()

    # ── Seed ─────────────────────────────────────────────────────────────
    seed = prompt_for_seed()
    random.seed(seed)
    print(f"  Starting session with seed {seed}.")
    print()

    # ── Session log ───────────────────────────────────────────────────────
    log = SessionLog(seed)

    # ── World and parser ──────────────────────────────────────────────────
    world         = build_demo_world()
    parser_system = ParserSystem.build_default(
        local_model_dir="./models/all-MiniLM-L6-v2"
    )
    pending_clarify: Optional[dict] = None

    if parser_system.embedder.enabled():
        msg = "Semantic parser enabled (local model only)."
    else:
        msg = "Semantic parser unavailable; using symbolic parser only."
        if parser_system.embedder.load_error:
            msg += f"\nReason: {parser_system.embedder.load_error}"

    print_and_log(msg, log)
    print()

    initial_look = do_look(world)
    print_and_log(initial_look, log)

    # ── Game loop ─────────────────────────────────────────────────────────
    while True:
        prompt = f"\n[{world.clock.now}] > "

        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            farewell = "Farewell."
            print(f"\n{farewell}")
            log.log_output(f"\n{farewell}")
            break

        if not line.strip():
            continue

        log.log_command(world.clock.now, line)
        normalised = normalize(line)

        # ── Meta commands ─────────────────────────────────────────────────
        if normalised in {"quit", "exit"}:
            farewell = "Farewell."
            print(farewell)
            log.log_output(farewell)
            break

        if normalised in {"help", "h", "?"}:
            print_and_log(load_help(), log)
            continue

        # ── Engine ────────────────────────────────────────────────────────
        output, pending_clarify = process_input(
            world           = world,
            parser_system   = parser_system,
            text            = line,
            pending_clarify = pending_clarify,
        )
        print_and_log(output, log)

    log.close()


if __name__ == "__main__":
    main()