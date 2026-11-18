"""
Common Menu Prompts & Helpers (cli/helper.py)
Encapsulates reusable interactive prompts for devices, build variants,
cloud upload targets, and formatting choices directly from configs.
"""

import json
from pathlib import Path
from cli.base import CYAN, GREEN, YELLOW, RED, BOLD, NC, read_input


def load_rom_devices(rom_key: str = "", version: str = "") -> dict:
    """Load target devices directly from configs/rom.json for the given ROM and version."""
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "rom.json"
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # If specific rom and version requested, get that device dict
            if rom_key and rom_key in data:
                ver_dict = data[rom_key].get("versions", {}).get(str(version), {})
                if "devices" in ver_dict:
                    return ver_dict["devices"]

            # Otherwise aggregate devices across all roms and versions
            all_devices = {}
            for r_key, r_info in data.items():
                for v_key, v_info in r_info.get("versions", {}).items():
                    for dev_code, dev_info in v_info.get("devices", {}).items():
                        all_devices[dev_code] = dev_info
            if all_devices:
                return all_devices
        except Exception:
            pass

    return {}


def load_recovery_devices(recovery_key: str) -> dict:
    """Load recovery target devices directly from configs/recovery.json."""
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "recovery.json"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        devices = data.get(recovery_key, {}).get("devices")
        if devices:
            return devices
    except Exception:
        pass
    return {}


def prompt_device_selection(rom_name: str, version: str, rom_key: str = "") -> str:
    """Prompt user to select a target device code or all devices."""
    devices = (
        load_recovery_devices(rom_key)
        if rom_key in ("ofrp", "twrp")
        else load_rom_devices(rom_key, version)
    )

    print(f"\nSelect device:")
    idx_map = {}
    for idx, (code, dev_data) in enumerate(devices.items(), 1):
        name = dev_data.get("name", code)
        print(f"   {GREEN}{idx}){NC} {name} ({code})")
        idx_map[str(idx)] = code

    all_idx = str(len(devices) + 1)
    print(f"   {GREEN}{all_idx}){NC} All Devices")
    print(f"   {RED}0){NC} Cancel")
    idx_map[all_idx] = "all"

    choice = read_input(f"\nSelect device 1-{all_idx} or 0: ").strip()
    return idx_map.get(choice, "")


def load_kernel_devices(kernel_key: str, version: str) -> dict:
    """Load kernel target devices directly from configs/kernel.json for the version."""
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "kernel.json"
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(kernel_key, {}).get("versions", {}).get(str(version), {}).get("devices", {})
    except Exception:
        pass
    return {}


def prompt_kernel_device_selection(kernel_name: str, kernel_key: str, version: str) -> str:
    """Prompt user to select a kernel target device code."""
    devices = load_kernel_devices(kernel_key, version)

    print(f"\nSelect device:")
    idx_map = {}
    for idx, (code, name) in enumerate(devices.items(), 1):
        print(f"   {GREEN}{idx}){NC} {name} ({code})")
        idx_map[str(idx)] = code

    print(f"   {RED}0){NC} Cancel")

    choice = read_input(f"\nSelect device 1-{len(devices)} or 0: ").strip()
    if choice == "0":
        return ""
    return idx_map.get(choice, "")



def prompt_build_variant(default: str = "userdebug") -> str:
    """Prompt user to choose a build variant."""
    print(f"\nSelect build variant:")
    print(f"   {GREEN}1){NC} userdebug")
    print(f"   {GREEN}2){NC} user")
    print(f"   {GREEN}3){NC} eng")
    choice = read_input("Select variant 1-3: ").strip()
    mapping = {"1": "userdebug", "2": "user", "3": "eng"}
    return mapping.get(choice, default)


def prompt_build_options() -> dict:
    """Prompt user for upload targets, auto-shutdown, and disk format options."""
    print("\nBuild options:")
    sf_in = read_input(" Upload release to SourceForge? y/N: ").strip().lower()
    sf = sf_in in ("y", "yes")

    gofile_in = read_input(" Upload release to Gofile.io? Y/n: ").strip().lower()
    gofile = gofile_in not in ("n", "no")

    shut_in = read_input(" Power off VM after build? y/N: ").strip().lower()
    shut = shut_in in ("y", "yes")

    fmt_in = read_input(" Format disk before build? y/N: ").strip().lower()
    fmt = fmt_in in ("y", "yes")

    return {
        "sf_upload": sf,
        "gofile_upload": gofile,
        "auto_shutdown": shut,
        "format_disk": fmt
    }


def confirm_action(prompt_text: str, default_yes: bool = False) -> bool:
    """Safely prompt user for yes/no confirmation."""
    suffix = "Y/n" if default_yes else "y/N"
    ans = read_input(f"{prompt_text} {suffix}: ").strip().lower()
    if not ans:
        return default_yes
    return ans in ("y", "yes")
