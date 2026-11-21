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
    disable_all_swap, find_existing_swapfiles, delete_swap,
)
from cli.services.vm import is_vm_running


def _prompt_size(default: int) -> int:
    raw = read_input(f" Enter swap size in GB [default: {default}, q=abort]: ").strip()
    if raw.lower() in ("q", "quit", "exit"):
        return -1
    if not raw:
        return default
    try:
        size = int(raw)
        return size if size > 0 else 0
    except ValueError:
        return 0


def submenu_delete_swap(vm: dict) -> None:
    """Interactively select and delete an existing swapfile on the VM."""
    print(f"\n{YELLOW}>>> Searching for existing swapfiles on VM...{NC}")
    swapfiles = find_existing_swapfiles(vm)

    print_divider()
    if swapfiles:
        print(f" {BOLD}Existing Swapfiles on VM:{NC}\n")
        for idx, item in enumerate(swapfiles, 1):
            status = f"{GREEN}[ACTIVE]{NC}" if item.get("active") else f"{YELLOW}[INACTIVE]{NC}"
            print(f"  {GREEN}{idx}){NC} {BOLD}{item['path']}{NC} ({item['size']}) {status}")
        print()
        print(f"  {CYAN}c){NC} Enter custom swapfile path")
        print()
        print(f"  {RED}0 / q){NC} Cancel / Back")
        print_divider()

        choice = read_input(
            f" Select swapfile to DELETE [1-{len(swapfiles)}, c, 0/q, default: 1]: "
        ).strip().lower()
        if not choice:
            choice = "1"
        if choice in ("0", "q", "quit", "exit"):
            return
        if choice == "c":
            path = read_input(" Enter full path of swapfile to DELETE [q=abort]: ").strip()
            if not path or path.lower() in ("q", "quit", "exit"):
                return
        elif choice.isdigit() and 1 <= int(choice) <= len(swapfiles):
            path = swapfiles[int(choice) - 1]["path"]
        else:
            print(">>> Invalid selection.")
            pause()
            return
    else:
        print(f" {YELLOW}No swapfiles found on VM.{NC}\n")
        print(f"  {CYAN}c){NC} Enter custom swapfile path")
        print()
        print(f"  {RED}0 / q){NC} Cancel / Back")
        print_divider()
        choice = read_input(" Select option [c, 0/q, default: 0]: ").strip().lower()
        if choice != "c":
            return
        path = read_input(" Enter full path of swapfile to DELETE [q=abort]: ").strip()
        if not path or path.lower() in ("q", "quit", "exit"):
            return

    confirm = read_input(
        f" Are you sure you want to delete '{path}'? [y/N, default: N, q=abort]: "
    ).strip().lower()
    if confirm not in ("y", "yes"):
        print(">>> Swapfile deletion cancelled.")
        pause()
        return

    if delete_swap(vm, path):
        print(f">>> Swapfile '{path}' deleted successfully.")
    pause()


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
        print(f" {BOLD}1){NC} {GREEN}Create Swap on ROM Mount (/mnt/rom){NC}")
        print(f" {BOLD}2){NC} {GREEN}Create Swap on Recovery Mount (/mnt/recovery){NC}")
        print(f" {BOLD}3){NC} {GREEN}Create Swap on Boot Disk (/){NC}")
        print(f" {BOLD}4){NC} {YELLOW}Turn Off All Swap (swapoff -a){NC}")
        print(f" {BOLD}5){NC} {RED}Delete Swapfile{NC}")
        print()
        print(f" {YELLOW}r){NC} Reload / Refresh Swap Status")
        print(f" {RED}0 / q){NC} Back to Main Menu / Quit")
        print_divider()

        choice = read_input(" Select option [0-5, r, q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return
        if choice in ("r", "reload", "refresh"):
            continue

        if choice in ("1", "2"):
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            mount = {"1": "/mnt/rom", "2": "/mnt/recovery"}[choice]
            size = _prompt_size(16)
            if size < 0:
                print(">>> Swap creation cancelled.")
                pause()
                continue
            if size == 0:
                print(">>> Error: Swap size must be a positive number.")
                pause()
                continue
            if create_swap(vm, mount, size):
                print(f">>> {size}GB swap active at {mount}/swapfile.")
            pause()
        elif choice == "3":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            size = _prompt_size(4)
            if size < 0:
                print(">>> Swap creation cancelled.")
                pause()
                continue
            if size == 0:
                print(">>> Error: Swap size must be a positive number.")
                pause()
                continue
            if create_boot_swap(vm, size):
                print(f">>> {size}GB swap active at /swapfile.")
            pause()
        elif choice == "4":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            if disable_all_swap(vm):
                print(">>> All swap disabled.")
            pause()
        elif choice == "5":
            if not is_vm_running(vm):
                print(">>> VM is powered OFF. Start it first.")
                pause()
                continue
            submenu_delete_swap(vm)
