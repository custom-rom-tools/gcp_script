"""
GCP Disk Backend Service (cli/services/disk.py)
Persistent-disk discovery, creation, attach/detach, mounting, formatting and
build-disk resolution.  Mirrors the proven behaviour of the original
``main`` shell menu: specific ROM disks are preferred, the shared general disk
is the fallback, and its device name is always ``rom-general``.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from pathlib import Path

from cli.base import CYAN, GREEN, YELLOW, RED, BLUE, BOLD, NC, read_input, print_divider
from cli.services.vm import (
    run_gcloud, vm_exec, is_vm_running, get_vm_info,
    start_vm_with_failover, ensure_vm_online,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRESET_CONFIG = REPO_ROOT / "configs" / "vm_preset.json"

GENERAL_DISK = "gcp-general-disk"
GENERAL_DEV_NAME = "rom-general"
BOOT_DISK_MARKERS = ("gcp-boot-ccache-disk",)

DISK_ROLES = {
    "rom": ("gcp-rom-disk", "rom-disk", "/mnt/rom", 500),
    "a16": ("gcp-rom-a16-disk", "rom-a16", "/mnt/rom", 500),
    "a17": ("gcp-rom-a17-disk", "rom-a17", "/mnt/rom", 500),
    "recovery": ("gcp-recovery-disk", "rom-recovery", "/mnt/recovery", 300),
    "rec": ("gcp-recovery-disk", "rom-recovery", "/mnt/recovery", 300),
    "general": (GENERAL_DISK, GENERAL_DEV_NAME, "/mnt/rom", 500),
    "shared": (GENERAL_DISK, GENERAL_DEV_NAME, "/mnt/rom", 500),
}


def load_disk_presets() -> dict:
    """Load disk presets from configs/vm_preset.json."""
    fallback = {
        "general": {"name": GENERAL_DISK, "size_gb": 500, "type": "pd-balanced",
                    "device_name": GENERAL_DEV_NAME, "default_mount": "/mnt/rom", "is_general": True},
        "rom": {"name": "gcp-rom-disk", "size_gb": 500, "type": "pd-balanced",
                "device_name": "rom-disk", "default_mount": "/mnt/rom", "is_general": False},
        "a16": {"name": "gcp-rom-a16-disk", "size_gb": 500, "type": "pd-balanced",
                "device_name": "rom-a16", "default_mount": "/mnt/rom", "is_general": False},
        "a17": {"name": "gcp-rom-a17-disk", "size_gb": 500, "type": "pd-balanced",
                "device_name": "rom-a17", "default_mount": "/mnt/rom", "is_general": False},
        "recovery": {"name": "gcp-recovery-disk", "size_gb": 300, "type": "pd-balanced",
                     "device_name": "rom-recovery", "default_mount": "/mnt/recovery", "is_general": False},
    }
    try:
        data = json.loads(PRESET_CONFIG.read_text(encoding="utf-8"))
        disks = data.get("disks", {})
        presets = {}
        for key, role_key, default_dev, default_mp, default_size in (
            ("general", "general", GENERAL_DEV_NAME, "/mnt/rom", 500),
            ("rom", "rom", "rom-disk", "/mnt/rom", 500),
            ("a16", "rom_a16", "rom-a16", "/mnt/rom", 500),
            ("a17", "rom_a17", "rom-a17", "/mnt/rom", 500),
            ("recovery", "recovery", "rom-recovery", "/mnt/recovery", 300),
        ):
            cfg = disks.get(role_key, {})
            presets[key] = {
                "name": cfg.get("name", fallback[key]["name"]),
                "size_gb": int(cfg.get("size_gb", default_size)),
                "type": cfg.get("type", "pd-balanced"),
                "device_name": cfg.get("device_name", default_dev),
                "default_mount": cfg.get("default_mount", default_mp),
                "is_general": key == "general",
            }
        return presets
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return fallback


DISK_PRESETS = load_disk_presets()


def normalize_disk_name(target_disk: str, vm: dict | None = None) -> str:
    """Map shortcuts and common aliases to real GCP disk names."""
    target = (target_disk or "").strip()
    lower = target.lower()
    if lower in ("a16", "16", "aosp-16"):
        return DISK_PRESETS["a16"]["name"]
    if lower in ("a17", "17", "aosp-17"):
        return DISK_PRESETS["a17"]["name"]
    if lower in ("recovery", "rec", "ofrp", "twrp"):
        return DISK_PRESETS["recovery"]["name"]
    if lower in ("general", "shared", "gcp-general"):
        return (vm or {}).get("general_disk") or GENERAL_DISK
    return target


def get_disk_meta(target_disk: str, vm: dict | None = None) -> dict:
    """Return normalized metadata for a ROM & Kernel or Recovery disk."""
    disk_name = normalize_disk_name(target_disk, vm)
    general_name = (vm or {}).get("general_disk") or GENERAL_DISK
    lower = disk_name.lower()

    # Recovery disk detection
    rec_preset = DISK_PRESETS.get("recovery", {})
    if lower == rec_preset.get("name", "gcp-recovery-disk").lower() or "recovery" in lower or lower in ("rec", "ofrp", "twrp"):
        return {
            "name": disk_name,
            "device_name": rec_preset.get("device_name", "rom-recovery"),
            "default_mount": "/mnt/recovery",
            "size_gb": rec_preset.get("size_gb", 300),
            "disk_type": rec_preset.get("type", "pd-balanced"),
            "description": "Dedicated 300GB storage for Recovery builds (/mnt/recovery)",
            "is_general": False,
            "role": "recovery",
        }

    # ROM & Kernel storage detection (all Android ROMs & kernels use /mnt/rom)
    gen_preset = DISK_PRESETS.get("general", {})
    if lower in ("gcp-general-disk", "gcp-rom-disk", "general", "rom", "shared", "kernel", general_name.lower()) or "rom" in lower or "kernel" in lower:
        return {
            "name": disk_name,
            "device_name": gen_preset.get("device_name", GENERAL_DEV_NAME),
            "default_mount": "/mnt/rom",
            "size_gb": gen_preset.get("size_gb", 500),
            "disk_type": gen_preset.get("type", "pd-balanced"),
            "description": "Dedicated 500GB storage for ROM & Kernel builds (/mnt/rom)",
            "is_general": True,
            "role": "rom",
        }

    # Custom disk: keep the caller's name and derive a stable device name.
    device_name = re.sub(r"[^a-z0-9-]+", "-", disk_name.lower()).strip("-") or disk_name
    return {
        "name": disk_name,
        "device_name": device_name,
        "default_mount": "",
        "size_gb": 100,
        "disk_type": "pd-balanced",
        "description": f"Custom secondary storage disk {disk_name}",
        "is_general": False,
        "role": "custom",
    }


def get_disk_role_label(target_disk: str, vm: dict | None = None) -> str:
    meta = get_disk_meta(target_disk, vm)
    role = meta.get("role")
    if role == "recovery" or meta.get("default_mount") == "/mnt/recovery":
        return "Recovery Storage -> /mnt/recovery"
    if role == "rom" or meta.get("default_mount") == "/mnt/rom":
        return "ROM & Kernel Storage -> /mnt/rom"
    return "Custom Secondary Disk"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
_DISKS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_MOUNT_CACHE: dict[str, tuple[float, dict]] = {}


def invalidate_disk_cache(vm: dict | None = None) -> None:
    """Invalidate disk listing and mount usage caches."""
    _DISKS_CACHE.clear()
    _MOUNT_CACHE.clear()


def _project_arg(vm: dict) -> list[str]:
    return [f"--project={vm['project']}"] if vm.get("project") else []


def list_disks(vm: dict, refresh: bool = False) -> list[dict]:
    zone = vm.get("zone", "")
    key = f"{vm.get('project', '')}:{zone}"
    now = time.time()
    if not refresh and key in _DISKS_CACHE:
        ts, cached = _DISKS_CACHE[key]
        if now - ts < 60.0:
            return cached

    args = ["compute", "disks", "list", f"--zones={zone}", "--format=json"] + _project_arg(vm)
    try:
        res = run_gcloud(args, check=False, timeout=60)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            _DISKS_CACHE[key] = (now, data)
            return data
    except Exception:
        pass
    if key in _DISKS_CACHE:
        return _DISKS_CACHE[key][1]
    return []


def disk_exists(vm: dict, disk_name: str) -> bool:
    zone = vm.get("zone", "")
    args = ["compute", "disks", "describe", disk_name]
    if zone:
        args.append(f"--zone={zone}")
    args.extend(_project_arg(vm))
    try:
        return run_gcloud(args, check=False, timeout=30).returncode == 0
    except Exception:
        return False


def get_disk_size(vm: dict, disk_name: str) -> int | None:
    zone = vm.get("zone", "")
    args = ["compute", "disks", "describe", disk_name]
    if zone:
        args.append(f"--zone={zone}")
    args.extend(["--format=value(sizeGb)"] + _project_arg(vm))
    try:
        res = run_gcloud(args, check=False, timeout=30)
        value = (res.stdout or "").strip()
        return int(float(value)) if value else None
    except (ValueError, TypeError, Exception):
        return None


def get_disk_users(vm: dict, disk_name: str) -> list[str]:
    zone = vm.get("zone", "")
    args = ["compute", "disks", "describe", disk_name]
    if zone:
        args.append(f"--zone={zone}")
    args.extend(["--format=value(users[].basename().list())"] + _project_arg(vm))
    try:
        res = run_gcloud(args, check=False, timeout=30)
        return [u.strip() for u in (res.stdout or "").split(",") if u.strip()]
    except Exception:
        return []


def get_attached_disks(vm: dict) -> list[dict]:
    info = get_vm_info(vm)
    if not info:
        return []
    result = []
    for disk in info.get("disks", []) or []:
        source = disk.get("source", "")
        result.append({
            "name": source.rstrip("/").split("/")[-1],
            "device_name": disk.get("deviceName", ""),
            "boot": bool(disk.get("boot", False)),
        })
    return result


def get_attached_device_name(vm: dict, target_disk: str, fallback_dev: str = "") -> str:
    """Resolve the actual GCP deviceName of an attached disk."""
    for disk in get_attached_disks(vm):
        if disk["name"] == target_disk and disk.get("device_name"):
            return disk["device_name"]
    return fallback_dev or get_disk_meta(target_disk, vm)["device_name"]


def _secondary_disks(vm: dict) -> list[str]:
    boot = {vm["instance"], *BOOT_DISK_MARKERS}
    return [d for d in get_attached_disks(vm) if d["name"] not in boot]


# ---------------------------------------------------------------------------
# Build disk resolution
# ---------------------------------------------------------------------------
def resolve_build_disk(vm: dict, target_category: str) -> dict:
    """Resolve which disk a ROM/recovery build should use."""
    cat = (target_category or "").lower()
    if cat in ("recovery", "rec", "ofrp", "twrp"):
        specific, mount_point, mode = DISK_PRESETS["recovery"]["name"], "/mnt/recovery", "specific"
    else:
        mount_point = "/mnt/rom"
        specific, mode = DISK_PRESETS.get("rom", {}).get("name", "gcp-rom-disk"), "rom"

    existing = {d.get("name") for d in list_disks(vm)}
    general = vm.get("general_disk") or GENERAL_DISK
    if specific in existing:
        return {"disk": specific, "mount_point": mount_point, "mode": mode}
    if general in existing:
        return {"disk": general, "mount_point": mount_point, "mode": "general"}

    # Fallback to any attached or existing ROM disk
    for r_name in ("gcp-rom-disk", "gcp-rom-a16-disk", "gcp-rom-a17-disk"):
        if r_name in existing:
            return {"disk": r_name, "mount_point": mount_point, "mode": "rom"}

    if cat in ("recovery", "rec", "ofrp", "twrp"):
        attached = [d["name"] for d in _secondary_disks(vm)]
        for r_name in ("gcp-rom-disk", "gcp-general-disk", "gcp-rom-a16-disk", "gcp-rom-a17-disk"):
            if r_name in attached:
                return {"disk": r_name, "mount_point": "/mnt/recovery", "mode": "shared-recovery"}

    return {"disk": general, "mount_point": mount_point, "mode": "general"}


# ---------------------------------------------------------------------------
# Device resolution helper
# ---------------------------------------------------------------------------
def build_vm_resolve_dev_cmd(target_disk: str, fallback_dev: str, actual_dev: str) -> str:
    return (
        "DEV=''; "
        f"for c in '/dev/disk/by-id/google-{actual_dev}' "
        f"'/dev/disk/by-id/google-{fallback_dev}' "
        f"'/dev/disk/by-id/google-{target_disk}'; do "
        "if [ -b \"$c\" ]; then DEV=\"$c\"; break; fi; done; "
        f"if [ -z \"$DEV\" ]; then echo 'Error: Block device for {target_disk} not detected on VM!'; exit 1; fi; "
        "REAL_DEV=$(readlink -f \"$DEV\")"
    )


# ---------------------------------------------------------------------------
# Attach / detach
# ---------------------------------------------------------------------------
def attach_disk(vm: dict, disk_name: str, device_name: str = "") -> bool:
    meta = get_disk_meta(disk_name, vm)
    dev_name = device_name or meta["device_name"]
    args = [
        "compute", "instances", "attach-disk", vm["instance"],
        f"--disk={meta['name']}",
        f"--device-name={dev_name}",
        f"--zone={vm['zone']}", "--mode=rw", "--quiet",
    ] + _project_arg(vm)
    res = run_gcloud(args, check=False, timeout=120)
    if res.returncode == 0:
        from cli.services.vm import invalidate_vm_cache
        invalidate_vm_cache(vm)
        return True
    print(f">>> Error: Failed to attach {meta['name']}: {(res.stderr or res.stdout).strip()}")
    return False


def detach_disk(vm: dict, disk_name: str) -> bool:
    args = [
        "compute", "instances", "detach-disk", vm["instance"],
        f"--disk={normalize_disk_name(disk_name, vm)}",
        f"--zone={vm['zone']}", "--quiet",
    ] + _project_arg(vm)
    res = run_gcloud(args, check=False, timeout=120)
    if res.returncode == 0:
        from cli.services.vm import invalidate_vm_cache
        invalidate_vm_cache(vm)
        return True
    print(f">>> Error: Failed to detach {disk_name}: {(res.stderr or res.stdout).strip()}")
    return False


def ensure_disk_attached(vm: dict, target_disk: str, size_gb: int | None = None) -> bool:
    """Ensure a disk exists, is attached to the VM, and is initialised."""
    disk_name = normalize_disk_name(target_disk, vm)
    meta = get_disk_meta(disk_name, vm)
    merged = dict(vm)
    merged.setdefault("general_disk", GENERAL_DISK)

    # Fast path: running VM already exposes the block device.
    if is_vm_running(vm):
        probe = vm_exec(
            vm,
            f"test -b '/dev/disk/by-id/google-{meta['device_name']}' || "
            f"test -b '/dev/disk/by-id/google-{disk_name}'",
            check=False, timeout=15,
        )
        if probe.returncode == 0:
            print(f">>> Disk '{disk_name}' is attached and recognized.")
            return True

    if not disk_exists(vm, disk_name):
        print(f">>> Target disk '{disk_name}' does not exist in GCP.")
        print(f">>> Auto-creating and initializing {size_gb or meta['size_gb']}GB disk...")
        return create_and_attach_disk(vm, disk_name, size_gb, meta["description"])

    if disk_name not in {d["name"] for d in _secondary_disks(vm)}:
        print(f">>> Attaching {disk_name} to {vm['instance']}...")
        if not attach_disk(vm, disk_name):
            return False
    else:
        print(f">>> Disk '{disk_name}' is attached to {vm['instance']}.")
    return True


# ---------------------------------------------------------------------------
# Mount / unmount
# ---------------------------------------------------------------------------
def mount_disk_on_vm(vm: dict, target_disk: str, mount_point: str) -> bool:
    """Format (if needed) and mount a disk at the requested mount point."""
    disk_name = normalize_disk_name(target_disk, vm)
    meta = get_disk_meta(disk_name, vm)
    actual_dev = get_attached_device_name(vm, disk_name, meta["device_name"])
    resolve_cmd = build_vm_resolve_dev_cmd(disk_name, meta["device_name"], actual_dev)
    mp = shlex.quote(mount_point)

    cmd = f"""{resolve_cmd}
if ! sudo blkid "$REAL_DEV" 2>/dev/null | grep -q 'TYPE="ext4"'; then
    sudo mkfs.ext4 -F -m 1 -E lazy_itable_init=0,lazy_journal_init=0 "$REAL_DEV"
fi
for omp in $(findmnt -rn -S "$REAL_DEV" -o TARGET 2>/dev/null || true); do
    if [ -n "$omp" ] && [ "$omp" != {mp} ]; then
        sudo swapoff "${{omp}}/swapfile" 2>/dev/null || true
        sudo umount "$omp" 2>/dev/null || sudo umount -l "$omp" 2>/dev/null || true
    fi
done
if [ -L {mp} ]; then sudo rm -f {mp}; fi
sudo mkdir -p {mp}
if mountpoint -q {mp}; then
    CUR_SRC=$(findmnt -rn -T {mp} -o SOURCE 2>/dev/null || true)
    CUR_REAL=$(readlink -f "$CUR_SRC" 2>/dev/null || echo "$CUR_SRC")
    if [ "$CUR_REAL" != "$REAL_DEV" ]; then
        sudo swapoff {mp}/swapfile 2>/dev/null || true
        sudo umount {mp} 2>/dev/null || sudo umount -l {mp} 2>/dev/null || true
    fi
fi
if ! mountpoint -q {mp}; then
    sudo mount -o discard,defaults,noatime,nofail "$REAL_DEV" {mp}
fi
sudo resize2fs "$REAL_DEV" 2>/dev/null || true
sudo chown $USER:$USER {mp} 2>/dev/null || true
if [ -f {mp}/swapfile ] && ! grep -q "{mp}/swapfile" /proc/swaps 2>/dev/null; then
    sudo chmod 600 {mp}/swapfile 2>/dev/null || true
    sudo swapon {mp}/swapfile 2>/dev/null || {{ sudo mkswap {mp}/swapfile 2>/dev/null && sudo swapon {mp}/swapfile 2>/dev/null; }} || true
fi
[ -L {mp}/source ] && rm -f {mp}/source 2>/dev/null || true
"""
    res = vm_exec(vm, cmd, check=False, timeout=180)
    if res.returncode != 0:
        print(f">>> Error mounting {disk_name} at {mount_point}:")
        print((res.stderr or res.stdout or "").strip()[-800:])
        return False
    return True


def mount_disk(vm: dict, disk_name: str, mount_point: str) -> bool:
    """Compatibility alias used by older callers."""
    return mount_disk_on_vm(vm, disk_name, mount_point)


def unmount_disk_on_vm(vm: dict, target_disk: str) -> bool:
    disk_name = normalize_disk_name(target_disk, vm)
    meta = get_disk_meta(disk_name, vm)
    actual_dev = get_attached_device_name(vm, disk_name, meta["device_name"])
    fallback_mp = meta["default_mount"] or "/mnt/rom"
    cmd = f"""DEV=''
for c in '/dev/disk/by-id/google-{actual_dev}' '/dev/disk/by-id/google-{meta['device_name']}' '/dev/disk/by-id/google-{disk_name}'; do
    if [ -b "$c" ]; then DEV="$c"; break; fi
done
if [ -n "$DEV" ]; then
    REAL_DEV=$(readlink -f "$DEV")
    for omp in $(findmnt -rn -S "$REAL_DEV" -o TARGET 2>/dev/null || true); do
        [ -z "$omp" ] && continue
        sudo swapoff "${{omp}}/swapfile" 2>/dev/null || true
        sudo umount "$omp" 2>/dev/null || sudo umount -l "$omp" 2>/dev/null || true
        sudo sed -i "\\|$omp|d" /etc/fstab 2>/dev/null || true
    done
elif mountpoint -q '{fallback_mp}' 2>/dev/null; then
    sudo swapoff '{fallback_mp}/swapfile' 2>/dev/null || true
    sudo umount -l '{fallback_mp}' 2>/dev/null || true
fi
"""
    return vm_exec(vm, cmd, check=False, timeout=120).returncode == 0


def is_mounted(vm: dict, mount_point: str) -> bool:
    return vm_exec(vm, f"mountpoint -q {shlex.quote(mount_point)}", check=False, timeout=15).returncode == 0


# ---------------------------------------------------------------------------
# Create / delete
# ---------------------------------------------------------------------------
def create_and_attach_disk(vm: dict, target_disk: str, size_gb: int | None = None,
                           description: str = "") -> bool:
    disk_name = normalize_disk_name(target_disk, vm)
    meta = get_disk_meta(disk_name, vm)
    size_gb = int(size_gb or meta["size_gb"])
    description = description or meta["description"]
    dev_name = meta["device_name"]

    print("=" * 60)
    print(f" Creating & Auto-Linking ROM Disk: {disk_name} ({size_gb}GB)")
    print("=" * 60)

    if disk_exists(vm, disk_name):
        current = get_disk_size(vm, disk_name)
        print(f" ✔ Disk '{disk_name}' already exists in GCP ({current or '?'}GB).")
        if current and current < size_gb:
            print(f">>> Growing '{disk_name}' from {current}GB to {size_gb}GB...")
            args = [
                "compute", "disks", "resize", disk_name,
                f"--zone={vm['zone']}", f"--size={size_gb}GB", "--quiet",
            ] + _project_arg(vm)
            res = run_gcloud(args, check=False, timeout=300)
            if res.returncode == 0:
                print(" ✔ Disk resized. The filesystem grows automatically when mounted.")
            else:
                print(f">>> Resize failed (regional SSD/DISK quota?): {(res.stderr or res.stdout).strip()}")
    else:
        disk_type = meta["disk_type"]
        args = [
            "compute", "disks", "create", disk_name,
            f"--zone={vm['zone']}", f"--size={size_gb}GB",
            f"--type={disk_type}", f"--description={description}", "--quiet",
        ] + _project_arg(vm)
        res = run_gcloud(args, check=False, timeout=300)
        if res.returncode != 0:
            print("\n>>> Notice: Regional SSD quota ('SSD_TOTAL_GB' = 500GB) may be exceeded.")
            answer = read_input("Create as pd-standard HDD instead? [Y/n, default: Y]: ").strip().lower()
            if answer in ("n", "no"):
                print(">>> Aborting disk creation.")
                return False
            args = [
                "compute", "disks", "create", disk_name,
                f"--zone={vm['zone']}", f"--size={size_gb}GB",
                "--type=pd-standard", f"--description={description}", "--quiet",
            ] + _project_arg(vm)
            res = run_gcloud(args, check=False, timeout=300)
            if res.returncode != 0:
                print(f">>> Failed to create disk {disk_name}: {(res.stderr or res.stdout).strip()}")
                return False

    attached = {d["name"] for d in _secondary_disks(vm)}
    if disk_name not in attached:
        print(f">>> Attaching {disk_name} to {vm['instance']} (device-name: {dev_name})...")
        if not attach_disk(vm, disk_name, dev_name):
            return False
    else:
        print(f" ✔ Disk '{disk_name}' is already attached to {vm['instance']}.")

    if not is_vm_running(vm):
        print(">>> Starting VM to initialize the filesystem...")
        if not start_vm_with_failover(vm):
            return False

    if meta["is_general"]:
        actual_dev = get_attached_device_name(vm, disk_name, dev_name)
        resolve_cmd = build_vm_resolve_dev_cmd(disk_name, dev_name, actual_dev)
        vm_exec(
            vm,
            f"""{resolve_cmd}
if ! sudo blkid "$REAL_DEV" 2>/dev/null | grep -q 'TYPE="ext4"'; then
    sudo mkfs.ext4 -F -m 1 -E lazy_itable_init=0,lazy_journal_init=0 "$REAL_DEV"
    TMP_MP=$(mktemp -d /tmp/gcp_init.XXXXXX)
    sudo mount "$REAL_DEV" "$TMP_MP" && sudo chown $USER:$USER "$TMP_MP" && sudo umount "$TMP_MP"
    rmdir "$TMP_MP" 2>/dev/null || true
fi
""",
            check=False, timeout=240,
        )
        print(f">>> General disk '{disk_name}' is initialized and ready!")
        print("    It stays unmounted at boot and mounts on demand when a build starts.")
        return True

    mp = meta["default_mount"] or "/mnt/rom"
    if not mount_disk_on_vm(vm, disk_name, mp):
        return False
    print(f">>> Disk {disk_name} is formatted and ready at {mp}!")
    return True


def delete_disk_gcp(vm: dict, disk_name: str) -> bool:
    """Detach (if needed) and permanently delete a disk."""
    disk_name = normalize_disk_name(disk_name, vm)
    if disk_name in (vm["instance"], *BOOT_DISK_MARKERS):
        print(f">>> Error: '{disk_name}' is the VM boot disk and cannot be deleted.")
        return False
    if is_vm_running(vm):
        unmount_disk_on_vm(vm, disk_name)
    users = get_disk_users(vm, disk_name)
    if users:
        detach_disk(vm, disk_name)
    args = [
        "compute", "disks", "delete", disk_name,
        f"--zone={vm['zone']}", "--quiet",
    ] + _project_arg(vm)
    res = run_gcloud(args, check=False, timeout=180)
    return res.returncode == 0


def delete_disk_action(vm: dict, target_disk: str = "") -> bool:
    """Interactively detach and permanently delete a data disk."""
    disk_name = normalize_disk_name(target_disk, vm)
    if not disk_name:
        all_disks = [
            d.get("name", "") for d in list_disks(vm)
            if d.get("name") and d["name"] not in (vm["instance"], *BOOT_DISK_MARKERS)
        ]
        disk_name = _select_disk(vm, all_disks, "SELECT DISK TO DELETE PERMANENTLY", "delete")
        if not disk_name:
            return False

    if disk_name in (vm["instance"], *BOOT_DISK_MARKERS):
        print(f">>> Error: '{disk_name}' is the VM boot disk and cannot be deleted.")
        return False

    print("=" * 60)
    print(f" PERMANENTLY DELETE DISK: {disk_name}")
    print("=" * 60)
    print(">>> This detaches the disk (if attached) and deletes it from GCP.")
    print(">>> All build data on this disk is lost. Storage billing stops.")
    confirm = read_input(f"Type DELETE to permanently remove '{disk_name}': ").strip()
    if confirm != "DELETE":
        print(">>> Aborted. Disk was not deleted.")
        return False

    if delete_disk_gcp(vm, disk_name):
        print(f">>> Disk '{disk_name}' deleted successfully.")
        return True
    print(f">>> Failed to delete '{disk_name}'. Check the disk name and GCP quota.")
    return False


def _select_disk(vm: dict, disks: list[str], title: str, action: str) -> str:
    if not disks:
        print(f">>> No disks available to {action}.")
        return ""
    disks = sorted(disks)
    print("=" * 60)
    print(f" {title}")
    print("=" * 60)
    for idx, name in enumerate(disks, 1):
        size = get_disk_size(vm, name)
        print(f" {BOLD}{idx}){NC} {name} ({size or '?'} GB - {get_disk_role_label(name, vm)})")
    print()
    print(f" {RED}0 / q){NC} Cancel / Abort")
    choice = read_input(f"Select disk number to {action} [1-{len(disks)} or 0/q, default: 1]: ").strip().lower()
    if not choice:
        choice = "1"
    if choice in ("0", "q", "quit", "exit"):
        return ""
    if not choice.isdigit():
        return ""
    idx = int(choice)
    return disks[idx - 1] if 1 <= idx <= len(disks) else ""


def attach_disk_action(vm: dict, target_disk: str = "") -> bool:
    disk_name = normalize_disk_name(target_disk, vm)
    if not disk_name:
        attached = {d["name"] for d in _secondary_disks(vm)}
        all_disks = [
            d.get("name", "") for d in list_disks(vm)
            if d.get("name") and d["name"] not in (vm["instance"], *BOOT_DISK_MARKERS)
        ]
        disk_name = _select_disk(vm, [d for d in all_disks if d not in attached],
                                 "SELECT UNATTACHED DISK TO ATTACH", "attach")
        if not disk_name:
            return False

    print("=" * 60)
    print(f" Attaching ROM Disk ({disk_name}) to {vm['instance']}...")
    print("=" * 60)
    if not ensure_disk_attached(vm, disk_name):
        return False

    meta = get_disk_meta(disk_name, vm)
    if meta["is_general"]:
        print(f">>> General disk '{disk_name}' attached.")
        print("    Kept unmounted until a build starts.")
    elif is_vm_running(vm):
        if not mount_disk_on_vm(vm, disk_name, meta["default_mount"] or "/mnt/rom"):
            return False
    else:
        print(">>> Note: VM is currently stopped.")
        print(f">>> Disk is attached and will auto-mount to {meta['default_mount']} at boot.")
    return True


def detach_disk_action(vm: dict, target_disk: str = "") -> bool:
    disk_name = normalize_disk_name(target_disk, vm)
    if not disk_name:
        disks = [d["name"] for d in _secondary_disks(vm)]
        disk_name = _select_disk(vm, disks, "SELECT ATTACHED DISK TO DETACH", "detach")
        if not disk_name:
            return False

    print("=" * 60)
    print(f" Detaching ROM Disk ({disk_name}) from {vm['instance']}...")
    print("=" * 60)
    if is_vm_running(vm):
        unmount_disk_on_vm(vm, disk_name)
    if not detach_disk(vm, disk_name):
        return False
    print(f">>> Disk {disk_name} detached successfully!")
    return True


def mount_disk_action(vm: dict, target_disk: str = "", mount_point: str = "") -> bool:
    """Interactively mount an attached secondary disk to a specified mount point."""
    if not is_vm_running(vm):
        print(f"\n{YELLOW}>>> VM '{vm['instance']}' is powered OFF.{NC}")
        print("    Please start the VM first to mount disks.")
        return False

    attached = sorted(_secondary_disks(vm), key=lambda d: d.get("name", ""))
    if not attached:
        print(f"\n{YELLOW}>>> No attached secondary disks found on '{vm['instance']}'.{NC}")
        print("    Attach a disk first (Option 5).")
        return False

    usage = get_mount_info(vm)

    if not target_disk:
        print_divider()
        print(f"{BOLD}                   MOUNT ATTACHED DISK                           {NC}")
        print_divider()
        print(" Attached secondary disks:\n")
        for idx, d in enumerate(attached, 1):
            d_name = d["name"]
            dev = d.get("device_name", "")
            meta = get_disk_meta(d_name, vm)
            info = usage.get(dev, {})
            mp = info.get("mount_point", "")
            if mp and mp != "(unmounted)":
                status = f"{GREEN}Mounted at {mp}{NC} (Used: {info.get('used', '-')}, Free: {info.get('free', '-')})"
            else:
                sugg = meta.get("default_mount") or "/mnt/rom"
                status = f"{YELLOW}Not mounted{NC} (Suggested: {sugg})"
            print(f"   {GREEN}{idx}){NC} {BOLD}{d_name:<20}{NC} [{dev}] - {status}")

        print(f"\n   {RED}0 / q){NC} Cancel / Abort")
        print_divider()

        sel = read_input(f" Select disk 1-{len(attached)} or 0/q [default: 1]: ").strip().lower()
        if not sel:
            sel = "1"
        if sel in ("0", "q", "quit", "exit"):
            return False
        try:
            s_idx = int(sel)
            if 1 <= s_idx <= len(attached):
                target_disk = attached[s_idx - 1]["name"]
            else:
                print(f"{RED}Invalid selection.{NC}")
                return False
        except ValueError:
            return False

    meta = get_disk_meta(target_disk, vm)
    default_mp = meta.get("default_mount") or "/mnt/rom"
    if not mount_point:
        mp_input = read_input(f"\nEnter mount point [default: {default_mp}, q=abort]: ").strip()
        if mp_input.lower() in ("q", "quit", "exit"):
            print("Mount cancelled.")
            return False
        mount_point = mp_input or default_mp

    print(f"\n>>> Mounting {BOLD}{target_disk}{NC} to {BOLD}{mount_point}{NC} on VM '{vm['instance']}'...")
    if mount_disk_on_vm(vm, target_disk, mount_point):
        print(f"{GREEN}✔ Successfully mounted {target_disk} at {mount_point}!{NC}")
        return True
    else:
        print(f"{RED}✖ Failed to mount {target_disk} at {mount_point}.{NC}")
        return False


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------
def format_disk_action(vm: dict, target_disk: str = "", auto_yes: bool = False) -> bool:
    """Interactively or automatically format a secondary disk as clean ext4."""
    disk_name = normalize_disk_name(target_disk, vm)

    if not disk_name:
        all_disks = [
            d.get("name", "") for d in list_disks(vm)
            if d.get("name") and d["name"] not in (vm["instance"], *BOOT_DISK_MARKERS)
        ]
        disk_name = _select_disk(vm, all_disks, "SELECT ROM / GENERAL DISK TO FORMAT (EXT4)", "format")
        if not disk_name:
            return False

    if disk_name in (vm["instance"], *BOOT_DISK_MARKERS):
        print(f">>> Error: '{disk_name}' is the VM boot disk and cannot be formatted.")
        return False
    if not disk_exists(vm, disk_name):
        print(f">>> Error: Disk '{disk_name}' does not exist in GCP.")
        return False

    if not auto_yes:
        print("=" * 60)
        print(f" ⚠️  CONFIRM DISK FORMAT: {disk_name}")
        print("=" * 60)
        print(" 1. Unmount the disk and disable swap on it.")
        print(" 2. PERMANENTLY ERASE ALL source trees, out/ artifacts and files.")
        print(" 3. Create a fresh clean ext4 filesystem.")
        print("\nNote: the VM boot disk (OS, ccache, SF keys) is NOT affected.\n")
        if read_input(f"Type 'FORMAT' to confirm formatting {disk_name}: ").strip() != "FORMAT":
            print(">>> Aborted. No changes were made.")
            return False

    meta = get_disk_meta(disk_name, vm)
    if not ensure_disk_attached(vm, disk_name):
        return False
    if not is_vm_running(vm):
        if not start_vm_with_failover(vm):
            return False

    actual_dev = get_attached_device_name(vm, disk_name, meta["device_name"])
    resolve_cmd = build_vm_resolve_dev_cmd(disk_name, meta["device_name"], actual_dev)
    cmd = f"""{resolve_cmd}
set -e
tmux kill-session -t rom-build 2>/dev/null || true
ALL_MOUNTS=$(lsblk -rn -o MOUNTPOINT "$REAL_DEV" 2>/dev/null | grep -v '^$' || true)
if [ -z "$ALL_MOUNTS" ]; then ALL_MOUNTS=$(findmnt -rn -S "$REAL_DEV" -o TARGET 2>/dev/null || true); fi
for omp in $ALL_MOUNTS; do
    [ -z "$omp" ] && continue
    sudo swapoff "${{omp}}/swapfile" 2>/dev/null || true
    sudo fuser -km "$omp" 2>/dev/null || true
    sudo umount -f "$omp" 2>/dev/null || sudo umount -l "$omp" 2>/dev/null || true
done
sudo fuser -km "$REAL_DEV" 2>/dev/null || true
sleep 1
sudo wipefs -af "$REAL_DEV" 2>/dev/null || true
sudo mkfs.ext4 -F -m 1 -E lazy_itable_init=0,lazy_journal_init=0 "$REAL_DEV"
TMP_MP=$(mktemp -d /tmp/gcp_fmt.XXXXXX)
sudo mount "$REAL_DEV" "$TMP_MP"
sudo chown $USER:$USER "$TMP_MP"
sudo chmod 755 "$TMP_MP"
rm -f "$TMP_MP/.gcp_build_target" "$TMP_MP/build.status" "$TMP_MP/build.log" 2>/dev/null || true
sudo umount "$TMP_MP"
rmdir "$TMP_MP" 2>/dev/null || true
sudo blkid "$REAL_DEV" | grep -q 'TYPE="ext4"'
"""
    res = vm_exec(vm, cmd, check=False, timeout=600)
    if res.returncode != 0:
        print(f">>> Error: Failed to format {disk_name}:")
        print((res.stderr or res.stdout or "").strip()[-1000:])
        return False

    if meta.get("default_mount"):
        if not mount_disk_on_vm(vm, disk_name, meta["default_mount"]):
            return False
        print(f">>> Disk {disk_name} formatted cleanly (ext4) and mounted at {meta['default_mount']}.")
    else:
        print(f">>> Disk {disk_name} formatted cleanly (ext4)!")
    return True


# ---------------------------------------------------------------------------
# Dashboard tables
# ---------------------------------------------------------------------------
def get_mount_info(vm: dict, refresh: bool = False) -> dict:
    """Return device_name -> {mount_point, used, free, pct}."""
    if not is_vm_running(vm):
        return {}
    key = f"{vm.get('instance', '')}:{vm.get('zone', '')}"
    now = time.time()
    if not refresh and key in _MOUNT_CACHE:
        ts, data = _MOUNT_CACHE[key]
        if now - ts < 30.0:
            return data
    cmd = r"""
for link in /dev/disk/by-id/google-*; do
  [ -e "$link" ] || continue
  case "$link" in *-part*) continue ;; esac
  devname="${link##*/google-}"
  blk=$(readlink -f "$link" 2>/dev/null || true)
  [ -b "$blk" ] || continue
  mp=$(lsblk -rn -o MOUNTPOINT "$blk" 2>/dev/null | grep -v "^$" | grep -v "^\[SWAP\]$" | awk '$0 !~ /^\/boot/ { print; found=1; exit } END { if (!found) exit 0 }')
  if [ -n "$mp" ]; then
    df -hP "$mp" 2>/dev/null | awk -v d="$devname" -v m="$mp" 'NR==2{ printf "%s|%s|%s|%s|%s\n", d, $3, $4, $5, m }'
    continue
  fi
  fs=$(lsblk -rn -o FSTYPE "$blk" 2>/dev/null | head -n 1)
  if [ "$fs" = "ext4" ] || [ "$fs" = "ext3" ] || [ "$fs" = "ext2" ]; then
    out=$(sudo -n dumpe2fs -h "$blk" 2>/dev/null | awk -v d="$devname" '
      function h(bytes) {
        if (bytes >= 1073741824) return sprintf("%.0fG", bytes / 1073741824);
        if (bytes >= 1048576) return sprintf("%.0fM", bytes / 1048576);
        return sprintf("%.0fK", bytes / 1024);
      }
      /^Block count:/ { bc = $NF + 0 }
      /^Free blocks:/ { fb = $NF + 0 }
      /^Block size:/ { bs = $NF + 0 }
      END {
        if (bc > 0 && bs > 0) {
          used = (bc - fb) * bs; free = fb * bs;
          pct = (bc > 0) ? sprintf("%.0f%%", ((bc - fb) * 100.0) / bc) : "-";
          printf "%s|%s|%s|%s|(unmounted)\n", d, h(used), h(free), pct;
        }
      }')
    [ -n "$out" ] && echo "$out" && continue
  fi
  echo "${devname}|-|-|-|(unmounted)"
done
"""
    res = vm_exec(vm, cmd, check=False, timeout=60)
    mounts: dict[str, dict] = {}
    for line in (res.stdout or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) == 5:
            mounts[parts[0]] = {"used": parts[1], "free": parts[2], "pct": parts[3],
                                "mount_point": parts[4]}
    _MOUNT_CACHE[key] = (now, mounts)
    return mounts


def print_disk_tables(vm: dict, refresh: bool = False) -> None:
    disks = list_disks(vm, refresh=refresh)
    attached = get_attached_disks(vm)
    usage = get_mount_info(vm, refresh=refresh)
    dev_for_disk = {d["name"]: d.get("device_name", "") for d in attached}

    print(f"{BOLD}Disks in Project:{NC}")
    header = f"{'NAME':<18} {'ZONE':<14} {'SIZE':<7} {'USED':<7} {'FREE':<7} {'USE%':<6} {'TYPE':<12} {'MEDIA':<5} STATUS"
    print(header)
    if not disks:
        print("  (none)")
    for disk in disks:
        name = disk.get("name", "")
        zone = (disk.get("zone", "") or "").rstrip("/").split("/")[-1]
        size = f"{disk.get('sizeGb', '?')}G"
        dtype = (disk.get("type", "") or "").rstrip("/").split("/")[-1]
        status = disk.get("status", "")
        dev = dev_for_disk.get(name, "")
        info = usage.get(dev, {})
        media = "HDD" if dtype == "pd-standard" else (
            "SSD" if re.match(r"^(pd-ssd|pd-balanced|pd-extreme|hyperdisk|local-ssd)", dtype) else "?")
        print(f"{name:<18} {zone:<14} {size:<7} {info.get('used', '-'):<7} "
              f"{info.get('free', '-'):<7} {info.get('pct', '-'):<6} {dtype:<12} {media:<5} {status}")

    print()
    vm_off = "" if usage or not is_vm_running(vm) else " (VM OFF)"
    print(f"{BOLD}Attached to VM:{NC}{vm_off}")
    print(f"{'ATTACHED_DISK':<18} {'DEVICE_NAME':<22} {'BOOT':<6} {'MOUNT':<15} {'USED':<7} {'FREE':<7} USE%")
    if not attached:
        print("  (none or VM unreachable)")
    for disk in attached:
        dev = disk.get("device_name", "")
        info = usage.get(dev, {})
        default_mp = "(VM OFF)" if not is_vm_running(vm) else "-"
        print(f"{disk['name']:<18} {dev:<22} {str(disk.get('boot', False)):<6} "
              f"{info.get('mount_point', default_mp):<15} {info.get('used', '-'):<7} "
              f"{info.get('free', '-'):<7} {info.get('pct', '-')}")
