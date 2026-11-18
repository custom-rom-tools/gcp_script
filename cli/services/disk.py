import json
from pathlib import Path
from cli.services.vm import run_gcloud, vm_exec, is_vm_running

PRESET_CONFIG = Path(__file__).resolve().parent.parent.parent / "configs" / "vm_preset.json"


def load_disk_presets() -> dict:
    try:
        data = json.loads(PRESET_CONFIG.read_text(encoding="utf-8"))
        disks = data.get("disks", {})
        return {
            "general": {"name": disks["general"]["name"], "size_gb": disks["general"]["size_gb"], "type": disks["general"]["type"], "device_name": disks["general"].get("device_name", "rom-storage"), "mount_point": ""},
            "a16": {"name": disks["rom_a16"]["name"], "size_gb": disks["rom_a16"]["size_gb"], "type": disks["rom_a16"]["type"], "device_name": disks["rom_a16"].get("device_name", "rom-a16"), "mount_point": disks["rom_a16"]["default_mount"]},
            "a17": {"name": disks["rom_a17"]["name"], "size_gb": disks["rom_a17"]["size_gb"], "type": disks["rom_a17"]["type"], "device_name": disks["rom_a17"].get("device_name", "rom-a17"), "mount_point": disks["rom_a17"]["default_mount"]},
            "recovery": {"name": disks["recovery"]["name"], "size_gb": disks["recovery"]["size_gb"], "type": disks["recovery"]["type"], "device_name": disks["recovery"].get("device_name", "rom-recovery"), "mount_point": disks["recovery"]["default_mount"]},
        }
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


DISK_PRESETS = load_disk_presets()


def list_disks(vm: dict) -> list[dict]:
    args = ["compute", "disks", "list", f"--filter=zone:({vm['zone']})", "--format=json"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    try:
        res = run_gcloud(args, check=False)
        if res.returncode == 0:
            return json.loads(res.stdout)
    except Exception:
        pass
    return []


def get_attached_disks(vm: dict) -> list[dict]:
    args = ["compute", "instances", "describe", vm["instance"], f"--zone={vm['zone']}", "--format=json"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    try:
        res = run_gcloud(args, check=False)
        if res.returncode == 0:
            info = json.loads(res.stdout)
            result = []
            for disk in info.get("disks", []):
                source = disk.get("source", "")
                result.append({"name": source.rstrip("/").split("/")[-1], "device_name": disk.get("deviceName", ""), "boot": disk.get("boot", False)})
            return result
    except Exception:
        pass
    return []


def resolve_build_disk(vm: dict, target_category: str) -> dict:
    cat = target_category.lower()
    specific_disk = "gcp-rom-a16-disk"
    mount_point = "/mnt/a16"
    if cat in ("17", "a17"):
        specific_disk = "gcp-rom-a17-disk"
        mount_point = "/mnt/a17"
    elif cat in ("recovery", "rec", "ofrp", "twrp"):
        specific_disk = "gcp-recovery-disk"
        mount_point = "/mnt/recovery"
    existing = {d.get("name") for d in list_disks(vm)}
    if specific_disk in existing:
        return {"disk": specific_disk, "mount_point": mount_point, "mode": "specific"}
    general = vm.get("general_disk", "gcp-general-disk")
    return {"disk": general, "mount_point": mount_point, "mode": "general"}


def attach_disk(vm: dict, disk_name: str) -> bool:
    device_name = "rom-storage"
    for preset in DISK_PRESETS.values():
        if preset["name"] == disk_name:
            device_name = preset["device_name"]
            break
    args = ["compute", "instances", "attach-disk", vm["instance"], f"--disk={disk_name}", f"--device-name={device_name}", f"--zone={vm['zone']}", "--mode=rw"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    res = run_gcloud(args, check=False)
    return res.returncode == 0


def detach_disk(vm: dict, disk_name: str) -> bool:
    args = ["compute", "instances", "detach-disk", vm["instance"], f"--disk={disk_name}", f"--zone={vm['zone']}"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    return run_gcloud(args, check=False).returncode == 0


def is_mounted(vm: dict, mount_point: str) -> bool:
    """Checks if a mount point is active."""
    cmd = f"mountpoint -q '{mount_point}'"
    return vm_exec(vm, cmd, check=False).returncode == 0


def mount_disk(vm: dict, disk_name: str, mount_point: str) -> bool:
    if is_mounted(vm, mount_point):
        return True
        
    device_name = "rom-storage"
    for disk in get_attached_disks(vm):
        if disk["name"] == disk_name and disk["device_name"]:
            device_name = disk["device_name"]
            break
    cmd = f"""
DEVICE=/dev/disk/by-id/google-{device_name}
# Only wait if device not present
for i in $(seq 1 5); do
    [ -e "$DEVICE" ] && break
    sleep 0.5
done
if [ ! -e "$DEVICE" ]; then exit 1; fi
DEV_PATH=$(realpath "$DEVICE")
sudo mkdir -p '{mount_point}'
# Re-mount
if ! sudo blkid "$DEV_PATH" >/dev/null 2>&1; then
    sudo mkfs.ext4 -F -m 1 "$DEV_PATH"
fi
sudo mount -o discard,defaults,noatime,nofail "$DEV_PATH" '{mount_point}'
sudo chown -R $(whoami):$(whoami) '{mount_point}'
"""
    return vm_exec(vm, cmd, check=False).returncode == 0


def format_disk_ext4(vm: dict, disk_name: str) -> bool:
    if not is_vm_running(vm):
        return False
    cmd = f"sudo wipefs -af /dev/disk/by-id/google-rom-storage 2>/dev/null || true; sudo mkfs.ext4 -F /dev/disk/by-id/google-rom-storage"
    return vm_exec(vm, cmd, check=False).returncode == 0
