"""
Swap Memory Service (cli/services/swap.py)
Creates, lists and removes swapfiles on the VM.  Swapfiles on build mounts are
refused unless the mount is actually active, preventing accidental writes to
the small boot disk.
"""

from __future__ import annotations

import shlex

from cli.services.vm import is_vm_running, vm_exec

SWAP_PATHS = ("/swapfile", "/mnt/a16/swapfile", "/mnt/a17/swapfile", "/mnt/recovery/swapfile")


def get_swap_status(vm: dict) -> str:
    """Return the currently configured swap size (or a descriptive status)."""
    if not is_vm_running(vm):
        return "VM Offline"
    res = vm_exec(vm, "free -h | awk '/Swap:/ {print $2}'", check=False, timeout=20)
    return (res.stdout or "").replace("\r", "").strip() or "Unknown"


def get_swap_details(vm: dict) -> str:
    """Return the memory table plus active swap devices."""
    if not is_vm_running(vm):
        return "VM is powered OFF (TERMINATED). Swap configuration stays on disk."
    res = vm_exec(
        vm,
        "free -h; echo ''; echo 'Active Swap:'; "
        "swapon --show 2>/dev/null || echo 'None (Pure 64GB RAM mode)'",
        check=False,
        timeout=20,
    )
    return (res.stdout or "").replace("\r", "").strip()


def _mount_is_active(vm: dict, mount_point: str) -> bool:
    res = vm_exec(vm, f"mountpoint -q {shlex.quote(mount_point)}",
                  check=False, timeout=20)
    return res.returncode == 0


def _create_swapfile(vm: dict, path: str, size_gb: int) -> bool:
    quoted = shlex.quote(path)
    cmd = (
        f"sudo swapoff {quoted} 2>/dev/null || true; "
        f"sudo rm -f {quoted}; "
        f"sudo fallocate -l {int(size_gb)}G {quoted} && "
        f"sudo chmod 600 {quoted} && "
        f"sudo mkswap {quoted} && "
        f"sudo swapon {quoted} && swapon --show"
    )
    res = vm_exec(vm, cmd, check=False, timeout=180)
    if res.returncode != 0:
        message = (res.stderr or res.stdout or "").strip()
        if message:
            print(message.splitlines()[-1])
        return False
    return True


def create_swap(vm: dict, mount_point: str, size_gb: int = 16,
                allow_boot: bool = False) -> bool:
    """Create and activate ``<mount_point>/swapfile``."""
    if not is_vm_running(vm):
        print(">>> VM is offline. Power it on first.")
        return False
    if size_gb <= 0:
        print(">>> Swap size must be greater than zero.")
        return False

    mount_point = mount_point.rstrip("/") or "/"
    if mount_point == "/":
        path = "/swapfile"
    else:
        if not allow_boot and not _mount_is_active(vm, mount_point):
            print(f">>> Error: '{mount_point}' is not mounted on {vm['instance']}.")
            print("    Start a build or attach/mount the specific disk first "
                  "(prevents filling the boot disk).")
            return False
        path = f"{mount_point}/swapfile"

    print(f">>> Allocating {size_gb}GB swapfile at {path} and activating...")
    return _create_swapfile(vm, path, size_gb)


def create_boot_swap(vm: dict, size_gb: int = 4) -> bool:
    """Create a swapfile on the boot disk (``/swapfile``)."""
    return create_swap(vm, "/", size_gb, allow_boot=True)


def disable_all_swap(vm: dict) -> bool:
    """Disable every active swap device."""
    if not is_vm_running(vm):
        print(">>> VM is offline. Power it on first.")
        return False
    print(">>> Disabling all active swap...")
    res = vm_exec(vm, "sudo swapoff -a; free -h", check=False, timeout=120)
    return res.returncode == 0


def list_swapfiles(vm: dict) -> str:
    """List the common swapfile locations."""
    if not is_vm_running(vm):
        return ""
    res = vm_exec(
        vm,
        "ls -lh /swapfile /mnt/a16/swapfile /mnt/a17/swapfile "
        "/mnt/recovery/swapfile 2>/dev/null || echo 'No common swapfiles found.'",
        check=False,
        timeout=20,
    )
    return (res.stdout or "").replace("\r", "").strip()


def delete_swap(vm: dict, path: str) -> bool:
    """Disable and delete a specific swapfile."""
    if not is_vm_running(vm):
        print(">>> VM is offline. Power it on first.")
        return False
    path = (path or "").strip()
    if not path:
        return False
    print(f">>> Disabling and deleting swapfile {path}...")
    res = vm_exec(
        vm,
        f"sudo swapoff {shlex.quote(path)} 2>/dev/null || true; "
        f"sudo rm -f {shlex.quote(path)}; free -h",
        check=False,
        timeout=120,
    )
    return res.returncode == 0


def remove_swap(vm: dict, mount_point: str) -> bool:
    """Compatibility wrapper: delete ``<mount_point>/swapfile``."""
    path = f"{mount_point.rstrip('/')}/swapfile" if mount_point != "/" else "/swapfile"
    return delete_swap(vm, path)
