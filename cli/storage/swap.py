"""
Swap Memory Management Menu (cli/storage/swap.py)
Provides submenus for on-demand swapfile creation, status checking, and removal.
"""

from cli.base import clear_screen, print_divider, print_header, pause, CYAN, BOLD, NC, read_input
from cli.services.swap import create_swap, get_swap_status, remove_swap


def submenu_swap_manager(vm: dict):
    """Submenu for on-demand swapfile creation, status checking, and removal."""
    while True:
        clear_screen()
        print_header("SWAP MEMORY MANAGER", vm['instance'])
        sw = get_swap_status(vm)
        print(f" Current Swap: {sw}\n")
        print(" 1) A16 Mount")
        print(" 2) A17 Mount")
        print(" 3) Recovery Mount")
        print(" 4) Boot Disk")
        print(" 5) Turn Off All Swap")
        print(" 6) Delete Swapfile")
        print("")
        print(" 0) Back")
        print_divider()

        choice = read_input("\nSelect 0-6: ").strip()
        if choice == "0":
            break
        if choice in ("1", "2", "3"):
            mount = { "1": "/mnt/a16", "2": "/mnt/a17", "3": "/mnt/recovery" }[choice]
            sz = read_input("Swap size GB: ").strip() or "16"
            try:
                size_gb = int(sz)
                if size_gb < 1:
                    raise ValueError
            except ValueError:
                print(">>> Error: Swap size must be a number.")
                pause()
                continue
            create_swap(vm, mount, size_gb)
            pause()
        elif choice == "4":
            sz = read_input("Swap size GB: ").strip() or "4"
            try:
                size_gb = int(sz)
                if size_gb < 1:
                    raise ValueError
            except ValueError:
                print(">>> Error: Swap size must be a number.")
                pause()
                continue
            create_swap(vm, "/", size_gb)
            pause()
        elif choice == "5":
            from cli.services.vm import vm_exec
            vm_exec(vm, "sudo swapoff -a", check=False)
            pause()
        elif choice == "6":
            path = read_input("Swapfile path: ").strip()
            if path:
                remove_swap(vm, path.rsplit("/", 1)[0] or "/")
