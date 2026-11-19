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
    """Submenu for compiling Lunaris AOSP (Android 16)."""
    _run_rom_build(vm, "lunaris", "Lunaris AOSP", version)
