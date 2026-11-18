from cli.services.vm import vm_exec, is_vm_running


def get_swap_status(vm: dict) -> str:
    if not is_vm_running(vm):
        return "VM Offline"
    res = vm_exec(vm, "free -h | awk '/Swap:/ {print $2}'", check=False)
    return res.stdout.strip() or "Unknown"


def create_swap(vm: dict, mount_point: str, size_gb: int = 16) -> bool:
    if not is_vm_running(vm):
        return False
    path = f"{mount_point.rstrip('/')}/swapfile"
    cmd = f"sudo swapoff '{path}' 2>/dev/null || true; sudo rm -f '{path}'; sudo fallocate -l {size_gb}G '{path}'; sudo chmod 600 '{path}'; sudo mkswap '{path}'; sudo swapon '{path}'"
    return vm_exec(vm, cmd, check=False).returncode == 0


def remove_swap(vm: dict, mount_point: str) -> bool:
    if not is_vm_running(vm):
        return False
    path = f"{mount_point.rstrip('/')}/swapfile"
    return vm_exec(vm, f"sudo swapoff '{path}' 2>/dev/null || true; sudo rm -f '{path}'", check=False).returncode == 0
