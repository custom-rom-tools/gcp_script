"""
Recovery Build Menu (cli/builds/recovery.py)
OrangeFox, TWRP and the combined batch pipeline.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_recovery_device, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def _run_recovery_build(vm: dict, name: str, label: str, device: str) -> None:
    variant = prompt_build_variant("eng")
    options = prompt_build_options()
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
