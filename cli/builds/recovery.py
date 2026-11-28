"""
Recovery Build Menu (cli/builds/recovery.py)
OrangeFox, TWRP and the combined batch pipeline.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_recovery_device, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def _run_recovery_build(vm: dict, name: str, label: str, device: str) -> None:
    variant = prompt_build_variant("eng")
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
        target_type="recovery",
        name=name,
        device=device,
        variant=variant,
        attach_live=True,
        **options,
    )
    if not started:
        pause()


def submenu_orangefox(vm: dict):
    """Submenu for compiling OrangeFox Recovery."""
    clear_screen()
    print_header("OrangeFox Recovery")
    device = prompt_recovery_device("OrangeFox Recovery", "ofrp")
    if not device:
        return
    _run_recovery_build(vm, "ofrp", "OrangeFox Recovery", device)


def submenu_twrp(vm: dict):
    """Submenu for compiling TWRP Recovery."""
    clear_screen()
    print_header("TWRP Recovery")
    device = prompt_recovery_device("TWRP Recovery", "twrp")
    if not device:
        return
    _run_recovery_build(vm, "twrp", "TWRP Recovery", device)


def submenu_batch_recoveries(vm: dict):
    """Submenu for batch building OrangeFox and TWRP sequentially."""
    clear_screen()
    print_header("All Recoveries (OFRP + TWRP)")
    device = prompt_recovery_device("All Recoveries Batch", "ofrp")
    if not device:
        return
    _run_recovery_build(vm, "all", "All Recoveries", device)


def submenu_recovery_builds(vm: dict) -> None:
    """Unified submenu for Recovery builds (OrangeFox, TWRP, Batch)."""
    from cli.base import print_divider, read_input, YELLOW, RED, BOLD, NC

    while True:
        clear_screen()
        print_header("RECOVERY BUILDS (/mnt/recovery)")
        print(f" {BOLD}[FLAVORS]{NC}")
        print(f"   {YELLOW}1){NC} OrangeFox Recovery")
        print(f"   {YELLOW}2){NC} TWRP Recovery")
        print(f"   {YELLOW}3){NC} All Recoveries Batch (OFRP + TWRP)")
        print()
        print(f"   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Back to Main Menu")
        print_divider()

        choice = read_input(" Select recovery target [1-3, r, q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit", "back"):
            return
        if choice in ("r", "reload", "refresh"):
            continue
        if choice == "1":
            submenu_orangefox(vm)
        elif choice == "2":
            submenu_twrp(vm)
        elif choice == "3":
            submenu_batch_recoveries(vm)
