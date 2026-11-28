"""
Kernel Build Menu (cli/builds/kernel.py)
Builds the Realme Atoll (sm7125) boot image through the Evolution X ROM
workspace (``m bootimage``) for Android 16 and 17.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_kernel_device, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def submenu_atoll_kernel(vm: dict, version: str = "16"):
    """Submenu for compiling the Realme Atoll kernel."""
    clear_screen()
    print_header(f"Realme Atoll Kernel - Android {version}")
    device = prompt_kernel_device(version, "atoll")
    if not device:
        return

    variant = prompt_build_variant("userdebug")
    if not variant:
        print(">>> Build cancelled.")
        pause()
        return

    options = prompt_build_options()
    if not options:
        print(">>> Build cancelled.")
        pause()
        return
    started = dispatch_build(
        vm=vm,
        target_type="kernel",
        name="atoll",
        version=version,
        device=device,
        variant=variant,
        attach_live=True,
        **options,
    )
    if not started:
        pause()


def submenu_kernel_builds(vm: dict) -> None:
    """Unified submenu for Realme Atoll Kernel builds (Android 16 or 17)."""
    from cli.base import print_divider, read_input, MAGENTA, YELLOW, RED, BOLD, NC

    while True:
        clear_screen()
        print_header("REALME ATOLL KERNEL BUILDS (boot.img)")
        print(f" {BOLD}[VERSIONS]{NC}")
        print(f"   {MAGENTA}1){NC} Android 16 Kernel (boot.img)")
        print(f"   {MAGENTA}2){NC} Android 17 Kernel (boot.img)")
        print()
        print(f"   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Back to Main Menu")
        print_divider()

        choice = read_input(" Select kernel version [1-2, r, q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit", "back"):
            return
        if choice in ("r", "reload", "refresh"):
            continue
        if choice == "1":
            submenu_atoll_kernel(vm, "16")
        elif choice == "2":
            submenu_atoll_kernel(vm, "17")
