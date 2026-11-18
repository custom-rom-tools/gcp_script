"""
Base UI Styling & Terminal Helpers (cli/base.py)
Provides ANSI color constants, screen clearing, and uniform header rendering.
"""

import sys

# ANSI Color Codes
CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
BLUE = "\033[1;34m"
MAGENTA = "\033[1;35m"
BOLD = "\033[1m"
NC = "\033[0m"


def clear_screen():
    """Clear terminal screen."""
    print("\033[H\033[2J", end="", flush=True)


def print_divider():
    """Print standard horizontal divider."""
    print(f"{CYAN}================================================================={NC}", flush=True)


def print_thin_divider():
    """Print secondary horizontal divider."""
    print(f"{CYAN}-----------------------------------------------------------------{NC}", flush=True)


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


def pause(message: str = "Press Enter to continue..."):
    """Pause until user presses Enter."""
    read_input(f"\n{message}")


def read_input(prompt: str) -> str:
    """Read menu input and exit the script immediately when q is entered."""
    value = input(prompt)
    if value.strip().lower() in ("q", "quit", "exit"):
        print("\nGoodbye!")
        sys.exit(0)
    return value
