"""
ROM Build Menu (cli/builds/rom.py)
Handles Android 16 and Android 17 ROM build submenus for Evolution X, LineageOS, and Lunaris.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_device_selection, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def submenu_evolution(vm: dict, version: str = "16"):
    """Submenu for compiling Evolution X (Android 16 or 17)."""
    clear_screen()
    print_header(f"Evolution X Android {version}")
    dev = prompt_device_selection("Evolution X", version, "evolution")
    if not dev:
        return
    variant = prompt_build_variant("userdebug")

    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="rom",
        name="evolution",
        version=version,
        device=dev,
        variant=variant,
        attach_live=True,
        **opts
    )
    pause()


def submenu_lineage(vm: dict, version: str = "16"):
    """Submenu for compiling LineageOS (Android 16 or 17)."""
    clear_screen()
    print_header(f"LineageOS Android {version}")
    dev = prompt_device_selection("LineageOS", version, "lineage")
    if not dev:
        return
    variant = prompt_build_variant("userdebug")

    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="rom",
        name="lineage",
        version=version,
        device=dev,
        variant=variant,
        attach_live=True,
        **opts
    )
    pause()


def submenu_lunaris(vm: dict, version: str = "16"):
    """Submenu for compiling Lunaris AOSP (Android 16)."""
    clear_screen()
    print_header(f"Lunaris AOSP Android {version}")
    dev = prompt_device_selection("Lunaris AOSP", version, "lunaris")
    if not dev:
        return
    variant = prompt_build_variant("userdebug")

    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="rom",
        name="lunaris",
        version=version,
        device=dev,
        variant=variant,
        attach_live=True,
        **opts
    )
    pause()
