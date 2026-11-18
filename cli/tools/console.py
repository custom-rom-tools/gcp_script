"""
SSH Console Menu (cli/tools/console.py)
Opens an interactive SSH login console session to the target VM.
"""

from cli.base import clear_screen, print_header
from cli.services.vm import vm_exec_tty


def submenu_ssh_console(vm: dict):
    """Submenu to open an interactive SSH login console."""
    clear_screen()
    print_header("Opening SSH Console to VM")
    print("Type 'exit' to return to menu.\n")
    vm_exec_tty(vm)
