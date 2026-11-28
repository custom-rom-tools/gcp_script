"""
ROM Build Menu (cli/builds/rom.py)
Android 16 / 17 ROM submenus for Evolution X, LineageOS and Lunaris.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_rom_device, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def _run_rom_build(vm: dict, rom_key: str, label: str, version: str) -> None:
    clear_screen()
    print_header(f"{label} - Android {version}")
    device = prompt_rom_device(label, rom_key, version)
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
        target_type="rom",
        name=rom_key,
        version=version,
        device=device,
        variant=variant,
        attach_live=True,
        **options,
    )
    if not started:
        pause()


def submenu_evolution(vm: dict, version: str = "16"):
    """Submenu for compiling Evolution X (Android 16 or 17)."""
    _run_rom_build(vm, "evolution", "Evolution X", version)


def submenu_lineage(vm: dict, version: str = "16"):
    """Submenu for compiling LineageOS (Android 16 or 17)."""
    _run_rom_build(vm, "lineage", "LineageOS", version)


def submenu_lunaris(vm: dict, version: str = "16"):
    """Submenu for compiling Lunaris AOSP (Android 16 or 17)."""
    _run_rom_build(vm, "lunaris", "Lunaris AOSP", version)


def submenu_android_builds(vm: dict, version: str = "16"):
    """Unified submenu for Android ROMs and Kernel of a specific version."""
    from cli.base import print_divider, read_input, GREEN, CYAN, YELLOW, MAGENTA, RED, BOLD, NC
    from cli.builds import kernel

    while True:
        clear_screen()
        ver_color = GREEN if version == "16" else CYAN
        print_header(f"ANDROID {version} BUILDS (ROMS & KERNEL)")
        print(f" {BOLD}[ROMS - ANDROID {version}]{NC}")
        print(f"   {ver_color}1){NC} Evolution X {version}")
        print(f"   {ver_color}2){NC} LineageOS {version}")
        print(f"   {ver_color}3){NC} Lunaris AOSP {version}")
        print()
        print(f" {BOLD}[KERNEL - ANDROID {version}]{NC}")
        print(f"   {MAGENTA}4){NC} Android {version} Kernel (boot.img)")
        print()
        print(f"   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Back to Main Menu")
        print_divider()

        choice = read_input(f" Select Android {version} target [1-4, r, q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit", "back"):
            return
        if choice in ("r", "reload", "refresh"):
            continue
        if choice == "1":
            submenu_evolution(vm, version)
        elif choice == "2":
            submenu_lineage(vm, version)
        elif choice == "3":
            submenu_lunaris(vm, version)
        elif choice == "4":
            kernel.submenu_atoll_kernel(vm, version)
