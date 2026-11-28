"""
ROM Build Menu (cli/builds/rom.py)
Android 16 / 17 ROM submenus for Evolution X, LineageOS and Lunaris.
"""

from cli.base import clear_screen, print_header, pause
from cli.helper import prompt_rom_device, prompt_build_variant, prompt_build_options
from cli.services.runner import dispatch_build


def _run_rom_build(vm: dict, rom_key: str, label: str, version: str) -> None:
    clear_screen()
    print_header(f"{label} - Android {version}")
    device = prompt_rom_device(label, rom_key, version)
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
        target_type="rom",
        name=rom_key,
        version=version,
        device=device,
        variant=variant,
        attach_live=True,
        **options,
    )
    if not started:
        pause()


def submenu_evolution(vm: dict, version: str = "16"):
    """Submenu for compiling Evolution X (Android 16 or 17)."""
    _run_rom_build(vm, "evolution", "Evolution X", version)


def submenu_lineage(vm: dict, version: str = "16"):
    """Submenu for compiling LineageOS (Android 16 or 17)."""
    _run_rom_build(vm, "lineage", "LineageOS", version)


def submenu_lunaris(vm: dict, version: str = "16"):
    """Submenu for compiling Lunaris AOSP (Android 16 or 17)."""
    _run_rom_build(vm, "lunaris", "Lunaris AOSP", version)


def submenu_android_builds(vm: dict, version: str = "16"):
    """Unified submenu for Android ROMs and Kernel of a specific version."""
    import json
    from pathlib import Path
    from cli.base import print_divider, read_input, GREEN, CYAN, YELLOW, MAGENTA, RED, BOLD, NC
    from cli.builds import kernel

    cfg_path = Path(__file__).resolve().parent.parent.parent / "configs" / "rom.json"
    rom_configs = {}
    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                rom_configs = json.load(f)
        except Exception:
            pass

    # Detect ROMs that have a configured target for this version
    available_roms = []
    for r_key, r_info in rom_configs.items():
        if version in r_info.get("versions", {}):
            available_roms.append((r_key, r_info.get("name", r_key)))

    while True:
        clear_screen()
        ver_color = GREEN if version == "16" else CYAN
        print_header(f"ANDROID {version} BUILDS (ROMS & KERNEL)")

        print(f" {BOLD}[ROMS]{NC}")
        idx = 1
        actions = {}
        for r_key, r_name in available_roms:
            print(f"   {ver_color}{idx}){NC} {r_name}")
            actions[str(idx)] = ("rom", r_key, r_name)
            actions[r_key.lower()] = ("rom", r_key, r_name)
            idx += 1

        kernel_idx = idx
        print()
        print(f" {BOLD}[KERNEL]{NC}")
        print(f"   {MAGENTA}{kernel_idx}){NC} Android {version} Kernel (boot.img)")
        actions[str(kernel_idx)] = ("kernel", "atoll", f"Android {version} Kernel")
        actions["k"] = ("kernel", "atoll", f"Android {version} Kernel")
        actions["kernel"] = ("kernel", "atoll", f"Android {version} Kernel")

        print()
        print(f"   {YELLOW}r){NC} Reload / Refresh")
        print(f"   {RED}0 / q){NC} Back to Main Menu")
        print_divider()

        max_idx = kernel_idx
        prompt_str = f" Select Android {version} target [1-{max_idx}, r, q, default: 0]: "
        choice = read_input(prompt_str).strip().lower()
        if choice in ("0", "q", "quit", "exit", "back"):
            return
        if choice in ("r", "reload", "refresh"):
            continue

        if choice in actions:
            kind, target_key, target_label = actions[choice]
            if kind == "rom":
                _run_rom_build(vm, target_key, target_label, version)
            elif kind == "kernel":
                kernel.submenu_atoll_kernel(vm, version)
        elif choice in ("evox", "evolution") and any(k == "evolution" for k, _ in available_roms):
            _run_rom_build(vm, "evolution", "Evolution X", version)
        elif choice in ("los", "lineage") and any(k == "lineage" for k, _ in available_roms):
            _run_rom_build(vm, "lineage", "LineageOS", version)
        elif choice in ("lunaris", "lunar") and any(k == "lunaris" for k, _ in available_roms):
            _run_rom_build(vm, "lunaris", "Lunaris AOSP", version)
        else:
            print(f"{RED}Invalid choice.{NC}")
            pause()
