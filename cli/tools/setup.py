"""
Setup & Environment Tools Menu (cli/tools/setup.py)
Environment setup, SourceForge key, idle watchdog, git identity, timezone,
.env upload and machine-type switching.
"""

from __future__ import annotations

import random

from cli.base import (
    clear_screen, print_divider, print_header, pause, read_input,
    GREEN, CYAN, YELLOW, RED, BOLD, NC,
)
from cli.services.tool import (
    upload_env_file, upload_sf_key, configure_dummy_git, set_vm_timezone,
    setup_environment, deploy_watchdog,
    get_git_identity, get_vm_timezone, get_watchdog_status,
)
from cli.services.vm import (
    is_vm_running, get_instance_machine_type, get_vm_info, set_machine_type,
    MACHINE_TYPES,
)
from cli.ops.link import submenu_view_links
from cli.tools.auth import submenu_gcloud_auth
from cli.tools.console import submenu_ssh_console


def submenu_upload_env(vm: dict):
    """Upload a local .env file to ~/.build_env on the VM."""
    clear_screen()
    print_header("UPLOAD .ENV CREDENTIALS")
    upload_env_file(vm)
    pause()


def submenu_upload_sf_key(vm: dict):
    """Upload the SourceForge SSH key and test authentication."""
    clear_screen()
    print_header("UPLOAD SOURCEFORGE SSH KEY")
    upload_sf_key(vm)
    pause()


def submenu_watchdog(vm: dict):
    """Show watchdog status and optionally redeploy the service."""
    clear_screen()
    print_header("AUTO-IDLE SHUTDOWN WATCHDOG", "30-minute threshold")
    if not is_vm_running(vm):
        print(">>> VM is powered OFF. Start it to inspect the watchdog.")
        pause()
        return

    print(get_watchdog_status(vm))
    print()
    answer = read_input("Re-deploy / restart watchdog service now? [y/N, default: N]: ").strip().lower()
    if answer in ("y", "yes"):
        deploy_watchdog(vm)
    pause()


def submenu_dummy_git(vm: dict):
    """Configure a dummy git identity to avoid GitHub rate limits."""
    clear_screen()
    print_header("CONFIGURE GIT DUMMY IDENTITY")
    if not is_vm_running(vm):
        print(">>> VM is powered OFF. Start it first.")
        pause()
        return

    current_name, current_email = get_git_identity(vm)
    print(f" Current user.name : {YELLOW}{current_name or '[Not Set]'}{NC}")
    print(f" Current user.email: {YELLOW}{current_email or '[Not Set]'}{NC}\n")

    rand_id = f"{random.randint(1000, 9999)}"
    default_name = "Android Builder"
    default_email = f"builder-{rand_id}@android-build.org"
    print(" Recommended dummy identity:")
    print(f"   Name : {GREEN}{default_name}{NC}")
    print(f"   Email: {GREEN}{default_email}{NC}\n")

    choice = read_input("Use recommended dummy identity? [Y/n, default: Y] (or 'c' for custom): ").strip().lower()
    name, email = default_name, default_email
    if choice == "c":
        custom_name = read_input(f"Enter dummy user.name [default: {default_name}]: ").strip()
        custom_email = read_input(f"Enter dummy user.email [default: {default_email}]: ").strip()
        name = custom_name or default_name
        email = custom_email or default_email
    elif choice in ("n", "no"):
        print("Cancelled without changes.")
        pause()
        return

    configure_dummy_git(vm, name, email)
    pause()


def submenu_timezone(vm: dict):
    """Change the VM system timezone."""
    clear_screen()
    print_header("CHANGE VM TIMEZONE")
    if not is_vm_running(vm):
        print(">>> VM is powered OFF. Start it first.")
        pause()
        return

    current_tz, current_time = get_vm_timezone(vm)
    print(f" Current VM Timezone : {YELLOW}{current_tz}{NC}")
    print(f" Current VM Date/Time: {YELLOW}{current_time}{NC}\n")
    print(" Choose target timezone:")
    print(f"  {BOLD}1){NC} India Standard Time ({GREEN}Asia/Kolkata{NC}, UTC+5:30)")
    print(f"  {BOLD}2){NC} Universal Coordinated Time ({GREEN}UTC{NC})")
    print(f"  {BOLD}3){NC} US Eastern ({GREEN}America/New_York{NC})")
    print(f"  {BOLD}4){NC} US Pacific ({GREEN}America/Los_Angeles{NC})")
    print(f"  {BOLD}5){NC} Custom timezone (e.g. Europe/London, Asia/Dubai)")
    print(f"  {BOLD}0){NC} Cancel")
    choice = read_input("Select option [1-5, default: 1 (IST)]: ").strip() or "1"

    mapping = {
        "1": "Asia/Kolkata",
        "2": "UTC",
        "3": "America/New_York",
        "4": "America/Los_Angeles",
    }
    if choice == "0":
        print("Timezone change cancelled.")
        pause()
        return
    if choice == "5":
        target = read_input("Enter timezone name [default: Asia/Kolkata]: ").strip() or "Asia/Kolkata"
    else:
        target = mapping.get(choice, choice)

    if not target:
        print(">>> Error: No timezone specified.")
        pause()
        return
    set_vm_timezone(vm, target)
    pause()


def submenu_machine_type(vm: dict):
    """Machine-type and failover manager."""
    while True:
        clear_screen()
        current_mt = get_instance_machine_type(vm)
        info = get_vm_info(vm)
        current_status = (info or {}).get("status", "UNKNOWN")

        print_header("MACHINE TYPE & FAILOVER MANAGER")
        print(f" Current Machine: {GREEN}{BOLD}{current_mt}{NC} | "
              f"Status: {GREEN}{BOLD}{current_status}{NC}\n")
        print(f"{BOLD}16-Core 64GB RAM Machine Options:{NC}")
        print("  -----------------------------------------------------------------")
        print("  #   Machine Type       Processor / Architecture       Hourly Cost")
        print("  -----------------------------------------------------------------")
        descriptions = {
            "c2d-standard-16": "AMD EPYC Milan (Compute-Opt)   ~$0.726/hr  (Fastest)",
            "t2d-standard-16": "AMD EPYC Milan (1:1 Cores)     ~$0.675/hr  (Cheapest)",
            "c2-standard-16": "Intel Cascade Lake (Comp-Opt)  ~$0.835/hr  (Intel Fallback)",
        }
        for idx, machine in enumerate(MACHINE_TYPES, 1):
            marker = f"{GREEN}* {NC}" if machine == current_mt else "  "
            print(f"  {marker}{idx:>2}) {machine:<18} {descriptions.get(machine, '')}")
        print("  -----------------------------------------------------------------")
        print(f"  {GREEN}A){NC} {BOLD}Set Fastest (c2d-standard-16){NC}")
        print(f"  {GREEN}B){NC} {BOLD}Set Cheapest (t2d-standard-16){NC}")
        print(f"  {BOLD}0){NC} Back to Main Menu")
        print_divider()

        choice = read_input(
            f"Select machine type [1-{len(MACHINE_TYPES)}, A, B, or 0]: "
        ).strip().lower()
        if choice in ("0", "q", ""):
            return
        target_mt = ""
        if choice.isdigit() and 1 <= int(choice) <= len(MACHINE_TYPES):
            target_mt = MACHINE_TYPES[int(choice) - 1]
        elif choice == "a":
            target_mt = "c2d-standard-16"
        elif choice == "b":
            target_mt = "t2d-standard-16"
        else:
            print(f"{RED}Invalid selection.{NC}")
            pause()
            continue

        if target_mt == current_mt:
            print(f"{YELLOW}VM is already using {target_mt}.{NC}")
            pause()
            continue

        set_machine_type(vm, target_mt)
        pause()


def run_tools_menu(vm: dict):
    """Setup & tools submenu loop."""
    while True:
        clear_screen()
        print_header("VM SETUP & TOOLS", vm["instance"])
        print(f" {BOLD}1){NC} Run Environment Setup (packages, repo, ccache)")
        print(f" {BOLD}2){NC} Upload SourceForge SSH Key (~/.ssh/crave_sf)")
        print(f" {BOLD}3){NC} Deploy 30m Auto-Idle Shutdown Watchdog")
        print(f" {BOLD}4){NC} Configure Git Dummy Identity (avoid 429 rate limits)")
        print(f" {BOLD}5){NC} Change VM Timezone (IST or custom)")
        print(f" {BOLD}6){NC} Upload .env Credentials")
        print(f" {BOLD}7){NC} Google Cloud Login / Switch Account")
        print(f" {BOLD}8){NC} View Upload Links")
        print(f" {BOLD}9){NC} Open SSH Console")
        print(f" {BOLD}0){NC} Back to Main Menu")
        print_divider()

        choice = read_input("Select option [0-9]: ").strip().lower()
        if choice in ("0", "q", ""):
            return
        if choice == "1":
            clear_screen()
            print_header("SETTING UP BUILD ENVIRONMENT")
            if setup_environment(vm):
                upload_sf_key(vm)
            pause()
        elif choice == "2":
            submenu_upload_sf_key(vm)
        elif choice == "3":
            submenu_watchdog(vm)
        elif choice == "4":
            submenu_dummy_git(vm)
        elif choice == "5":
            submenu_timezone(vm)
        elif choice == "6":
            submenu_upload_env(vm)
        elif choice == "7":
            submenu_gcloud_auth()
        elif choice == "8":
            submenu_view_links(vm)
        elif choice == "9":
            submenu_ssh_console(vm)
