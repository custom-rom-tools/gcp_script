"""
Artifact Downloads Menu (cli/ops/download.py)
Pulls ROM zips, recovery packages, kernel boot images and build logs from the
VM's mounted disks into ~/Downloads.
"""

from cli.base import clear_screen, print_header, print_divider, pause, read_input, GREEN, CYAN, YELLOW, RED, BOLD, NC
from cli.services.download import download_artifacts
from cli.services.vm import is_vm_running, start_vm, get_vm_state


def _ensure_vm_online(vm: dict, action: str) -> bool:
    """Offer to power on a stopped VM (compute billing is paused)."""
    if is_vm_running(vm):
        return True

    status, _ = get_vm_state(vm)
    print(f"\n{YELLOW}{'=' * 61}{NC}")
    print(f" {BOLD}>>> [VM STATUS: {status} / OFF]{NC}")
    print(f"     VM '{vm['instance']}' is currently powered OFF.")
    print("     Compute charges are paused.")
    print(f"{YELLOW}{'=' * 61}{NC}")

    answer = read_input(f" Power on VM to {action}? (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print(">>> Aborted. VM remains powered OFF to pause compute billing.")
        return False
    return start_vm(vm) and is_vm_running(vm)


def _download(vm: dict, artifact_type: str, label: str):
    clear_screen()
    print_header(f"DOWNLOAD {label.upper()} TO MAC")
    if not _ensure_vm_online(vm, f"download {label}"):
        pause()
        return
    print()
    download_artifacts(vm, artifact_type)
    print_divider()
    pause()


def submenu_download_rom(vm: dict):
    """Download compiled ROM zip packages."""
    _download(vm, "rom", "ROM (.zip)")


def submenu_download_recovery(vm: dict):
    """Download compiled recovery images and packages."""
    _download(vm, "recovery", "Recovery (.img/.zip)")


def submenu_download_kernel(vm: dict):
    """Download compiled kernel boot images."""
    _download(vm, "kernel", "Kernel (boot.img)")


def submenu_download_log(vm: dict):
    """Download the build log of the most recent build."""
    clear_screen()
    print_header("DOWNLOAD BUILD LOG (.log)")
    if not _ensure_vm_online(vm, "download the build log"):
        pause()
        return
    print()
    download_artifacts(vm, "log")
    print_divider()
    pause()
