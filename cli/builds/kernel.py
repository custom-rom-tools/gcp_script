"""
Kernel Build Menu (cli/builds/kernel.py)
Handles Realme Atoll (sm7125) kernel builds producing boot.img flashable via recovery.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_kernel_device_selection, prompt_build_options
from cli.services.runner import dispatch_build


def submenu_atoll_kernel(vm: dict, version: str = "16"):
    """Submenu for compiling Realme Atoll kernel."""
    clear_screen()
    print_header(f"Realme Atoll Kernel (Android {version})")
    dev = prompt_kernel_device_selection("Realme Atoll Kernel", "atoll", version)
    if not dev:
        return
    opts = prompt_build_options()
    dispatch_build(
        vm=vm,
        target_type="kernel",
        name="atoll",
        version=version,
        device=dev,
        variant="user",
        attach_live=True,
        **opts
    )
    pause()
