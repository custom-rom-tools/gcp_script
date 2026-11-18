"""
Recovery Build Menu (cli/builds/recovery.py)
Handles OrangeFox Recovery, TeamWin Recovery Project (TWRP), and batch compilation.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_device_selection, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def submenu_orangefox(vm: dict):
    """Submenu for compiling OrangeFox Recovery."""
    clear_screen()
    print_header("OrangeFox Recovery")
    dev = prompt_device_selection("OrangeFox Recovery", "16", "ofrp")
    if not dev:
        return
    variant = prompt_build_variant("eng")
    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="recovery",
        name="ofrp",
        device=dev,
        variant=variant,
        attach_live=True,
        **opts
    )
    pause()


def submenu_twrp(vm: dict):
    """Submenu for compiling TWRP Recovery."""
    clear_screen()
    print_header("TWRP Recovery")
    dev = prompt_device_selection("TWRP Recovery", "16", "twrp")
    if not dev:
        return
    variant = prompt_build_variant("eng")
    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="recovery",
        name="twrp",
        device=dev,
        variant=variant,
        attach_live=True,
        **opts
    )
    pause()


def submenu_batch_recoveries(vm: dict):
    """Submenu for batch building both OrangeFox and TWRP sequentially."""
    clear_screen()
    print_header("All Recoveries")
    dev = prompt_device_selection("Batch Recovery", "16", "ofrp")
    if not dev:
        return
    variant = prompt_build_variant("eng")
    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="recovery",
        name="all",
        device=dev,
        variant=variant,
        attach_live=True,
        **opts
    )
    pause()
