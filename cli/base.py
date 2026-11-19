"""
Base UI Styling & Terminal Helpers (cli/base.py)
Provides ANSI color constants, screen clearing, terminal restoration and uniform
header rendering shared by every menu.
"""

import os
import subprocess
import sys

# ANSI Color Codes
CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
BLUE = "\033[1;34m"
MAGENTA = "\033[1;35m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

DIVIDER = "=" * 65
THIN_DIVIDER = "-" * 65


def clear_screen():
    """Clear the visible screen and scrollback (falls back to raw ANSI)."""
    if os.environ.get("TERM") and _which("tput"):
        subprocess.run(["tput", "clear"], capture_output=True)
    print("\033[H\033[2J\033[3J", end="", flush=True)


def _which(program: str) -> bool:
    from shutil import which

    return which(program) is not None


def disable_mouse_reporting():
    """Disable every common xterm mouse-reporting mode.

    A tmux/htop session killed mid-run can leave the host terminal in mouse
    mode, which makes click-drag send escape codes instead of selecting text.
    """
    print(
        "\033[?9l\033[?1000l\033[?1001l\033[?1002l\033[?1003l"
        "\033[?1005l\033[?1006l\033[?1015l\033[?1016l",
        end="",
        flush=True,
    )


def restore_local_terminal():
    """Restore the local terminal after an interactive SSH/tmux/htop session."""
    try:
        subprocess.run("stty sane", shell=True, capture_output=True)
    except Exception:
        pass
    disable_mouse_reporting()
    print("\033[?1049l\033[0m", end="", flush=True)


def print_divider():
    """Print standard horizontal divider."""
    print(f"{CYAN}{DIVIDER}{NC}", flush=True)


def print_thin_divider():
    """Print secondary horizontal divider."""
    print(f"{CYAN}{THIN_DIVIDER}{NC}", flush=True)


def print_header(title: str, subtitle: str = None):
    """Render a styled header block."""
    print_divider()
    print(f"{BOLD}{title.center(65)}{NC}", flush=True)
    if subtitle:
        print(f"{subtitle.center(65)}", flush=True)
    print_divider()


def print_section(title: str):
    """Print a compact menu section title."""
    print(f"\n{BOLD}{CYAN}{title}{NC}", flush=True)


def print_status(label: str, value: str, color: str = GREEN):
    """Print a compact label-value status line."""
    print(f"{label:<12} {color}{value}{NC}", flush=True)


def print_kv(label: str, value: str, color: str = CYAN, width: int = 14):
    """Print a consistently aligned key/value line."""
    print(f" {BOLD}{label:<{width}}{NC}: {color}{value}{NC}", flush=True)


def pause(message: str = "Press Enter to continue..."):
    """Pause until user presses Enter."""
    read_input(f"\n{message}")


def read_input(prompt: str) -> str:
    """Read a line of input.

    ``q`` is handled by the top-level menus instead of terminating the process
    here; this keeps value prompts (paths, timezones, ...) safe.
    """
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


def is_interactive() -> bool:
    """Return True when stdin is attached to a terminal."""
    return sys.stdin.isatty()
