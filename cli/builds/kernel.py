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
