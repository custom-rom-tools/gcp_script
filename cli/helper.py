"""
Common Menu Prompts & Helpers (cli/helper.py)
Reusable interactive prompts for ROM / recovery / kernel devices, build
variants and upload options.  Device ordering mirrors the original shell menu
(RMX2170, RMX2061, RMX3081) while still reading the JSON configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli.base import CYAN, GREEN, YELLOW, RED, BOLD, NC, read_input

REPO_ROOT = Path(__file__).resolve().parent.parent
PREFERRED_DEVICE_ORDER = ("RMX2170", "RMX2061", "RMX3081")


def _load_json(name: str) -> dict:
    try:
        return json.loads((REPO_ROOT / "configs" / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _ordered_devices(devices: dict) -> list[tuple[str, dict]]:
    """Order known devices first, then any extra entries from the config."""
    entries: list[tuple[str, dict]] = []
    for code in PREFERRED_DEVICE_ORDER:
        if code in devices:
            entries.append((code, devices[code]))
    for code, info in devices.items():
        if code not in PREFERRED_DEVICE_ORDER:
            entries.append((code, info))
    return entries


def load_rom_devices(rom_key: str = "", version: str = "") -> dict:
    """Load target devices from configs/rom.json for the given ROM/version."""
    data = _load_json("rom.json")
    if rom_key and rom_key in data:
        version_cfg = data[rom_key].get("versions", {}).get(str(version), {})
        if version_cfg.get("devices"):
            return version_cfg["devices"]

    all_devices: dict = {}
    for rom_info in data.values():
        for version_info in rom_info.get("versions", {}).values():
            all_devices.update(version_info.get("devices", {}))
    return all_devices


def load_recovery_devices(recovery_key: str) -> dict:
    """Load recovery target devices from configs/recovery.json."""
    data = _load_json("recovery.json")
    return data.get(recovery_key, {}).get("devices", {})


def load_kernel_devices(kernel_key: str, version: str) -> dict:
    """Load kernel target devices from configs/kernel.json for a version."""
    data = _load_json("kernel.json")
    return data.get(kernel_key, {}).get("versions", {}).get(str(version), {}).get("devices", {})


def _device_label(code: str, info: dict) -> str:
    name = info.get("name", code) if isinstance(info, dict) else str(info)
    if name and name != code:
        return f"{name} ({code})"
    return code


def prompt_rom_device(rom_label: str, rom_key: str, version: str) -> str:
    """Prompt for a ROM target device; returns a code, ``all`` or ````."""
    devices = load_rom_devices(rom_key, version)
    entries = _ordered_devices(devices)

    print(f"\n{BOLD}  {rom_label} - Select Target Device{NC}")
    print(f"{CYAN}{'-' * 65}{NC}")
    index: dict[str, str] = {}
    for idx, (code, info) in enumerate(entries, 1):
        print(f"   {GREEN}{idx}){NC} {_device_label(code, info)}")
        index[str(idx)] = code
    all_idx = str(len(entries) + 1)
    print(f"   {GREEN}{all_idx}){NC} All Devices Batch "
          f"({', '.join(code for code, _ in entries)})")
    index[all_idx] = "all"
    print(f"\n   {RED}0 / q){NC} Back to Main Menu / Cancel")
    print(f"{CYAN}{'-' * 65}{NC}")

    def_code = entries[0][0] if entries else ""
    choice = read_input(f" Select device [0-{all_idx}, q, default: 1 ({def_code})]: ").strip().lower()
    if not choice:
        choice = "1"
    if choice in ("0", "q", "quit", "exit"):
        return ""
    return index.get(choice, "")


def prompt_recovery_device(recovery_label: str, recovery_key: str) -> str:
    """Prompt for a recovery target (unified sm7125 first), or ``all``."""
    devices = load_recovery_devices(recovery_key)
    ordered: list[tuple[str, dict]] = []
    if "sm7125" in devices:
        ordered.append(("sm7125", devices["sm7125"]))
    for code, info in _ordered_devices(devices):
        if code != "sm7125":
            ordered.append((code, info))

    print(f"\n{BOLD}  {recovery_label} - Select Target Device{NC}")
    print(f"{CYAN}{'-' * 65}{NC}")
    index: dict[str, str] = {}
    for idx, (code, info) in enumerate(ordered, 1):
        label = _device_label(code, info)
        if code == "sm7125":
            label = f"Unified sm7125 ({', '.join(PREFERRED_DEVICE_ORDER)})"
        print(f"   {YELLOW}{idx}){NC} {label}")
        index[str(idx)] = code
    all_idx = str(len(ordered) + 1)
    print(f"   {YELLOW}{all_idx}){NC} All Devices Batch "
          f"({', '.join(PREFERRED_DEVICE_ORDER)})")
    index[all_idx] = "all"
    print(f"\n   {RED}0 / q){NC} Back to Main Menu / Cancel")
    print(f"{CYAN}{'-' * 65}{NC}")

    def_rec = ordered[0][0] if ordered else "sm7125"
    choice = read_input(f" Select target [0-{all_idx}, q, default: 1 ({def_rec})]: ").strip().lower()
    if not choice:
        choice = "1"
    if choice in ("0", "q", "quit", "exit"):
        return ""
    return index.get(choice, "")


def prompt_kernel_device(version: str, kernel_key: str = "atoll") -> str:
    """Prompt for a kernel target device."""
    devices = load_kernel_devices(kernel_key, version)
    entries = _ordered_devices(devices)

    print(f"\n{BOLD}  Realme Atoll Kernel (Android {version}) - Select Target Device{NC}")
    print(f"{CYAN}{'-' * 65}{NC}")
    index: dict[str, str] = {}
    for idx, (code, info) in enumerate(entries, 1):
        print(f"   {GREEN}{idx}){NC} {_device_label(code, info)}")
        index[str(idx)] = code
    print(f"\n   {RED}0 / q){NC} Back to Main Menu / Cancel")
    print(f"{CYAN}{'-' * 65}{NC}")

    def_kern = entries[0][0] if entries else ""
    choice = read_input(f" Select device [0-{len(entries)}, q, default: 1 ({def_kern})]: ").strip().lower()
    if not choice:
        choice = "1"
    if choice in ("0", "q", "quit", "exit"):
        return ""
    return index.get(choice, "")


def prompt_device_selection(rom_name: str, version: str, rom_key: str = "") -> str:
    """Backward-compatible dispatcher used by the build submenus."""
    if rom_key in ("ofrp", "twrp"):
        return prompt_recovery_device(rom_name, rom_key)
    return prompt_rom_device(rom_name, rom_key, version)


def prompt_kernel_device_selection(kernel_name: str, kernel_key: str, version: str) -> str:
    """Backward-compatible kernel device prompt."""
    return prompt_kernel_device(version, kernel_key)


def prompt_build_variant(default: str = "userdebug") -> str:
    """Prompt for a build variant, ordering the recommended one first."""
    recovery = default == "eng"
    print(f"\n{BOLD}Select Build Variant:{NC}")
    if recovery:
        print(f"  {GREEN}1){NC} eng        (Recommended)")
        print(f"  {CYAN}2){NC} userdebug  (Debug)")
        print(f"  {YELLOW}3){NC} user       (Release)")
        print()
        print(f"  {RED}0 / q){NC} Cancel / Abort")
        mapping = {"1": "eng", "2": "userdebug", "3": "user"}
        prompt = " Select variant [1-3, 0/q, default: 1 (eng)]: "
    else:
        print(f"  {GREEN}1){NC} userdebug  (Recommended)")
        print(f"  {CYAN}2){NC} user       (Official Release)")
        print(f"  {YELLOW}3){NC} eng        (Debug)")
        print()
        print(f"  {RED}0 / q){NC} Cancel / Abort")
        mapping = {"1": "userdebug", "2": "user", "3": "eng"}
        prompt = " Select variant [1-3, 0/q, default: 1 (userdebug)]: "

    raw = read_input(prompt).strip().lower()
    if raw in ("0", "q", "quit", "exit"):
        return ""
    if raw in ("", "1"):
        return default if raw == "" else mapping["1"]
    return mapping.get(raw, raw if raw in ("userdebug", "user", "eng") else default)


def prompt_build_options(default_shutdown: bool = False) -> dict | None:
    """Prompt for upload targets and shutdown behaviour (no disk format)."""
    print(f"\n{BOLD}Build options (or 'q' to abort):{NC}")

    sf_in = read_input(" Upload to SourceForge too? [y/N, default: N, q=abort]: ").strip().lower()
    if sf_in in ("q", "quit", "exit"):
        return None
    sf_upload = sf_in in ("y", "yes")

    gofile_in = read_input(" Upload to Gofile.io too? [Y/n, default: Y, q=abort]: ").strip().lower()
    if gofile_in in ("q", "quit", "exit"):
        return None
    gofile_upload = gofile_in not in ("n", "no")

    if default_shutdown:
        shut_in = read_input(" Power off immediately when build ends? [Y/n, default: Y, q=abort]: ").strip().lower()
        if shut_in in ("q", "quit", "exit"):
            return None
        auto_shutdown = shut_in not in ("n", "no")
    else:
        shut_in = read_input(
            " Power off immediately when build ends? [y/N, default: N, q=abort]: "
        ).strip().lower()
        if shut_in in ("q", "quit", "exit"):
            return None
        auto_shutdown = shut_in in ("y", "yes")

    return {
        "sf_upload": sf_upload,
        "gofile_upload": gofile_upload,
        "auto_shutdown": auto_shutdown,
    }


def confirm_action(prompt_text: str, default_yes: bool = False) -> bool:
    """Safely prompt the user for a yes/no confirmation."""
    suffix = "Y/n, q=abort" if default_yes else "y/N, q=abort"
    ans = read_input(f"{prompt_text} [{suffix}]: ").strip().lower()
    if ans in ("q", "quit", "exit"):
        return False
    if not ans:
        return default_yes
    return ans in ("y", "yes")
