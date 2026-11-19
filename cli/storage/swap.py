"""
Swap Memory Management Menu (cli/storage/swap.py)
Create swapfiles on the build mounts or boot disk, disable all swap, or
delete an existing swapfile.
"""

from cli.base import (
    clear_screen, print_divider, print_header, pause, read_input,
    GREEN, CYAN, YELLOW, RED, BOLD, NC,
)
from cli.services.swap import (
    get_swap_details, create_swap, create_boot_swap,
    disable_all_swap, list_swapfiles, delete_swap,
)
from cli.services.vm import is_vm_running


def _prompt_size(default: int) -> int:
    raw = read_input(f" Enter swap size in GB [default: {default}]: ").strip()
    if not raw:
        return default
    try:
        size = int(raw)
        return size if size > 0 else 0
    except ValueError:
        return 0


def submenu_swap_manager(vm: dict):
    """Interactive swap manager."""
    while True:
        clear_screen()
        print_header("VM SWAP MEMORY MANAGER", vm["instance"])
        if is_vm_running(vm):
            print(get_swap_details(vm))
        else:
            print(f" {YELLOW}>{NC} VM is powered OFF (TERMINATED). Swap is configured on disk.")
        print()
        print(f" {BOLD}1){NC} {GREEN}Create Swap on A16 Mount (/mnt/a16){NC}")
        print(f" {BOLD}2){NC} {GREEN}Create Swap on A17 Mount (/mnt/a17){NC}")
        print(f" {BOLD}3){NC} {GREEN}Create Swap on Recovery Mount (/mnt/recovery){NC}")
        print(f" {BOLD}4){NC} {GREEN}Create Swap on Boot Disk (/){NC}")
        print(f" {BOLD}5){NC} {YELLOW}Turn Off All Swap (swapoff -a){NC}")
        print(f" {BOLD}6){NC} {RED}Delete Swapfile{NC}")
        print(f" {BOLD}0){NC} Back to Main Menu")
        print_divider()

        choice = read_input(" Select option [0-6]: ").strip().lower()
        if choice in ("0", "q", ""):
            return

        if choice in ("1", "2", "3"):
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            mount = {"1": "/mnt/a16", "2": "/mnt/a17", "3": "/mnt/recovery"}[choice]
            size = _prompt_size(16)
            if size <= 0:
                print(">>> Error: Swap size must be a positive number.")
                pause()
                continue
            if create_swap(vm, mount, size):
                print(f">>> {size}GB swap active at {mount}/swapfile.")
            pause()
        elif choice == "4":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            size = _prompt_size(4)
            if size <= 0:
                print(">>> Error: Swap size must be a positive number.")
                pause()
                continue
            if create_boot_swap(vm, size):
                print(f">>> {size}GB swap active at /swapfile.")
            pause()
        elif choice == "5":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            if disable_all_swap(vm):
                print(">>> All swap disabled.")
            pause()
        elif choice == "6":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            print(f"{YELLOW}>>> Searching for existing swapfiles on VM...{NC}")
            print(list_swapfiles(vm) or "No common swapfiles found.")
            path = read_input(" Enter full path of swapfile to DELETE [default: /mnt/a16/swapfile]: ").strip() or "/mnt/a16/swapfile"
            if path:
                if delete_swap(vm, path):
                    print(f">>> Swapfile {path} deleted.")
            pause()
