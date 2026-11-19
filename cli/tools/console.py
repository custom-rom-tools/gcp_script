"""
SSH Console Menu (cli/tools/console.py)
Opens an interactive SSH login shell on the active VM.
"""

from cli.base import clear_screen, print_header, pause
from cli.services.vm import vm_exec_tty
from cli.ops.monitor import require_vm_running


def submenu_ssh_console(vm: dict):
    """Open an interactive SSH console session."""
    clear_screen()
    print_header("SSH CONSOLE", vm["instance"])
    if not require_vm_running(vm, "open an SSH console"):
        pause()
        return
    print(">>> Connecting to VM console... (type 'exit' to return)\n")
    vm_exec_tty(vm, "bash -l")
