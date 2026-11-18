"""
Setup & Environment Tools Menu (cli/tools/setup.py)
Provides submenus for credential provisioning (.env, SSH keys), git config,
timezone settings, and machine type switching.
"""

from cli.base import clear_screen, print_divider, print_header, pause, read_input
from cli.services.tool import (
    upload_env_file, upload_sf_key, configure_dummy_git, set_vm_timezone,
    setup_environment, deploy_watchdog,
)
from cli.services.vm import set_machine_type
from cli.ops.link import submenu_view_links
from cli.tools.auth import submenu_gcloud_auth
from cli.tools.console import submenu_ssh_console


def submenu_upload_env(vm: dict):
    """Submenu to securely upload local .env to ~/.build_env on VM."""
    clear_screen()
    print_header("Upload .env")
    upload_env_file(vm)
    pause()


def submenu_upload_sf_key(vm: dict):
    """Submenu to upload SourceForge SSH key to ~/.ssh/crave_sf."""
    clear_screen()
    print_header("Upload SourceForge Key")
    upload_sf_key(vm)
    pause()


def submenu_dummy_git(vm: dict):
    """Submenu to configure dummy git identity to prevent repo sync aborts."""
    clear_screen()
    print_header("Configure Dummy Git Identity")
    configure_dummy_git(vm)
    pause()


def submenu_timezone(vm: dict):
    """Submenu to set VM system timezone."""
    clear_screen()
    print_header("Configure VM System Timezone")
    tz = read_input("Enter timezone: ").strip() or "Asia/Kolkata"
    set_vm_timezone(vm, tz)
    pause()


def submenu_machine_type(vm: dict):
    """Submenu to change VM machine type."""
    clear_screen()
    print_header("Switch VM Machine Type")
    print(" 1) c2d-standard-16")
    print(" 2) t2d-standard-16")
    print(" 3) c2-standard-16")
    print(" 0) Back")
    choice = read_input("\nSelect 0-3: ").strip()
    target = {
        "1": "c2d-standard-16",
        "2": "t2d-standard-16",
        "3": "c2-standard-16",
    }.get(choice)
    if target:
        set_machine_type(vm, target)
    pause()


def run_tools_menu(vm: dict):
    """Main Setup & Tools Submenu loop."""
    while True:
        clear_screen()
        print_header("SETUP & TOOLS", vm['instance'])
        print(" 1) Setup Build Environment")
        print(" 2) Upload SourceForge Key")
        print(" 3) Idle Shutdown Watchdog")
        print(" 4) Configure Git Identity")
        print(" 5) Change Timezone")
        print(" 6) Upload .env")
        print(" 7) Google Cloud Login")
        print(" 8) View Release Links")
        print(" 9) Open SSH Console")
        print("")
        print(" 0) Back")
        print_divider()

        choice = read_input("\nSelect 0-9: ").strip()
        if choice == "0":
            break
        elif choice == "1":
            clear_screen()
            setup_environment(vm)
            pause()
        elif choice == "2":
            submenu_upload_sf_key(vm)
        elif choice == "3":
            clear_screen()
            from cli.services.vm import vm_exec
            res = vm_exec(vm, "systemctl status idle-shutdown.service --no-pager 2>/dev/null || cat /run/idle_watchdog.status 2>/dev/null || true", check=False)
            print(res.stdout)
            if read_input("\nRedeploy watchdog? y/N: ").strip().lower() in ("y","yes"):
                deploy_watchdog(vm)
            pause()
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
