"""
GCP Build Farm CLI Dispatcher & Dashboard Router (cli/main.py)
Orchestrates non-interactive CLI flags and drives the main interactive build dashboard.
"""

import sys
import argparse
import json
from pathlib import Path
from cli.base import (
    clear_screen, print_divider, CYAN, GREEN, YELLOW, RED, BLUE, BOLD, NC, pause,
    read_input, print_header, print_section, print_status
)
from cli.services.vm import (
    get_active_vm, start_vm, stop_vm, is_vm_running, get_vm_info,
    check_gcloud_auth, login_gcloud
)
from cli.cluster.vm import run_cluster_menu
from cli.builds import rom
from cli.builds import recovery
from cli.ops import download
from cli.ops import monitor
from cli.ops import link
from cli.storage import disk
from cli.storage import swap
from cli.tools import setup
from cli.tools import auth
from cli.tools import console
from cli.services.runner import dispatch_build

def run_build_dashboard(vm: dict):
    """Main Build & Operations Dashboard interactive loop."""
    while True:
        clear_screen()
        account = check_gcloud_auth() or "Not Authenticated"
        vm_info = get_vm_info(vm)
        vm_running = bool(vm_info and vm_info.get("status") == "RUNNING")
        status_color = GREEN if vm_running else RED
        status_text = "ONLINE" if vm_running else "OFFLINE"

        print_header("GCP BUILD MANAGER")
        print_status("Account", account, GREEN)
        print_status("VM", f"{vm['instance']}  {vm['zone']}", GREEN)
        print_status("Disk", vm['general_disk'], BLUE)
        print_status("Status", status_text, status_color)
        print_divider()

        print_section("ROM Builds")
        print(f" {GREEN}1){NC} Android 16  Evolution X")
        print(f" {GREEN}2){NC} Android 16  LineageOS")
        print(f" {GREEN}3){NC} Android 16  Lunaris")
        print(f" {CYAN}4){NC} Android 17  Evolution X")
        print(f" {CYAN}5){NC} Android 17  LineageOS")

        print_section("Recovery")
        print(f" {YELLOW}6){NC} OrangeFox")
        print(f" {YELLOW}7){NC} TWRP")
        print(f" {YELLOW}8){NC} All Recoveries")

        print_section("Downloads")
        print(f" {BLUE}9){NC} ROM")
        print(f" {BLUE}10){NC} Recovery")
        print(f" {BLUE}11){NC} Build Log")

        print_section("Monitor")
        print(f" {GREEN}12){NC} Live Build")
        print(f" {CYAN}13){NC} Build Status and Logs")
        print(f" {YELLOW}14){NC} Live System Monitor")
        print(f" {RED}15){NC} Stop Build")

        print_section("VM")
        print(f" {BOLD}16){NC} Status")
        print(f" {BOLD}17){NC} Start")
        print(f" {BOLD}18){NC} Stop")
        print(f" {BOLD}19){NC} Disks")
        print(f" {BOLD}20){NC} Swap")
        print(f" {BOLD}21){NC} Machine Type")

        print_section("Tools")
        print(f" {BOLD}22){NC} SSH")
        print(f" {BOLD}23){NC} Setup")
        print(f" {BOLD}24){NC} Upload Links")
        print(f" {YELLOW}25){NC} Switch VM")
        print(f" {RED}q){NC} Quit")
        print_divider()

        choice = read_input(" Select 1-25 or q: ").strip().lower()

        if choice in ("q", "quit", "exit"):
            sys.exit(0)

        # 1-3: Android 16 ROMs
        elif choice == "1":
            rom.submenu_evolution(vm, "16")
        elif choice == "2":
            rom.submenu_lineage(vm, "16")
        elif choice == "3":
            rom.submenu_lunaris(vm, "16")

        # 4-5: Android 17 ROMs
        elif choice == "4":
            rom.submenu_evolution(vm, "17")
        elif choice == "5":
            rom.submenu_lineage(vm, "17")

        # 6-8: Recoveries
        elif choice == "6":
            recovery.submenu_orangefox(vm)
        elif choice == "7":
            recovery.submenu_twrp(vm)
        elif choice == "8":
            recovery.submenu_batch_recoveries(vm)

        # 9-11: Downloads
        elif choice == "9":
            download.submenu_download_rom(vm)
        elif choice == "10":
            download.submenu_download_recovery(vm)
        elif choice == "11":
            download.submenu_download_log(vm)

        # 12-15: Monitoring & VM Info
        elif choice == "12":
            monitor.submenu_live_tmux(vm)
        elif choice == "13":
            monitor.submenu_build_status(vm)
        elif choice == "14":
            monitor.submenu_live_monitor(vm)
        elif choice == "15":
            monitor.submenu_stop_build(vm)
        elif choice == "16":
            monitor.submenu_vm_info(vm)

        # 17-18: VM Power
        elif choice == "17":
            start_vm(vm)
            pause()
        elif choice == "18":
            stop_vm(vm)
            pause()

        # 19-21: Storage & Machine Sizing
        elif choice == "19":
            disk.submenu_disk_manager(vm)
        elif choice == "20":
            swap.submenu_swap_manager(vm)
        elif choice == "21":
            setup.submenu_machine_type(vm)

        # 22-24: SSH Console, Setup, Upload Links
        elif choice == "22":
            console.submenu_ssh_console(vm)
        elif choice == "23":
            setup.run_tools_menu(vm)
        elif choice == "24":
            link.submenu_view_links(vm)

        # 25: Cluster
        elif choice == "25":
            vm = run_cluster_menu()


def main():
    """Main CLI entrypoint handling interactive menu or direct headless flags."""
    if len(sys.argv) == 1:
        # Step 1: Show Cluster & VM Selector screen
        active_vm = run_cluster_menu()
        # Step 2: Proceed into main dashboard with active VM
        run_build_dashboard(active_vm)
        return

    if sys.argv[1].lower() in ("-h", "--help", "help"):
        print("Usage:")
        print("  ./gcp_menu.sh")
        print("  ./gcp_menu.sh build")
        print("  ./gcp_menu.sh recovery")
        print("  ./gcp_menu.sh start")
        print("  ./gcp_menu.sh stop")
        print("  ./gcp_menu.sh status")
        print("  ./gcp_menu.sh tmux")
        print("  ./gcp_menu.sh format-disk <disk_name>")
        return

    command = sys.argv[1].lower()
    vm = get_active_vm()

    if command in ("cluster", "menu-cluster"):
        run_cluster_menu()

    elif command in ("build", "build-rom"):
        parser = argparse.ArgumentParser(prog="./gcp_menu.sh build")
        parser.add_argument("--rom", default=None, help="ROM name")
        parser.add_argument("--ver", "--version", default=None, help="Android version")
        parser.add_argument("--dev", "--device", default=None, help="Target device code or all")
        parser.add_argument("--type", "--variant", default=None, help="Build variant")
        parser.add_argument("--sf", action="store_true", help="Upload to SourceForge")
        parser.add_argument("--no-sf", action="store_true", help="Skip SourceForge upload")
        parser.add_argument("--gofile", action="store_true", default=True, help="Upload to Gofile.io")
        parser.add_argument("--no-gofile", action="store_true", help="Skip Gofile.io upload")
        parser.add_argument("--shutdown", default="true", help="Auto shutdown VM on success (true/false)")
        parser.add_argument("--keep-alive", action="store_true", help="Keep VM running after build")
        parser.add_argument("--format", action="store_true", help="Format disk clean ext4 before build")
        args = parser.parse_args(sys.argv[2:])

        shut = False if args.keep_alive else (args.shutdown.lower() != "false")
        sf = False if args.no_sf else args.sf
        gofile = False if args.no_gofile else args.gofile

        rom_alias = {"evox": "evolution", "los": "lineage", "lunar": "lunaris"}
        rom_name = rom_alias.get((args.rom or "").lower(), (args.rom or "").lower())
        rom_data = json.loads((Path(__file__).resolve().parent.parent / "configs" / "rom.json").read_text(encoding="utf-8"))
        if not rom_name:
            rom_name = next(iter(rom_data), "")
        ver_str = str(args.ver) if args.ver else ""
        if not ver_str:
            ver_str = next(iter(rom_data[rom_name]["versions"]), "")
        device = args.dev or "all"
        variant = args.type or rom_data[rom_name].get("default_variant", "")

        dispatch_build(
            vm=vm,
            target_type="rom",
            name=rom_name,
            version=ver_str,
            device=device,
            variant=variant,
            sf_upload=sf,
            gofile_upload=gofile,
            auto_shutdown=shut,
            format_disk=args.format
        )

    elif command in ("recovery", "build-recovery"):
        parser = argparse.ArgumentParser(prog="./gcp_menu.sh recovery")
        parser.add_argument("--flavor", "--recovery", default=None, help="Recovery flavor")
        parser.add_argument("--dev", "--device", default=None, help="Target device code")
        parser.add_argument("--type", "--variant", default=None, help="Build variant")
        parser.add_argument("--sf", action="store_true", help="Upload to SourceForge")
        parser.add_argument("--no-sf", action="store_true", help="Skip SourceForge upload")
        parser.add_argument("--gofile", action="store_true", default=True, help="Upload to Gofile.io")
        parser.add_argument("--no-gofile", action="store_true", help="Skip Gofile.io upload")
        parser.add_argument("--shutdown", default="true", help="Auto shutdown VM on success (true/false)")
        parser.add_argument("--keep-alive", action="store_true", help="Keep VM running after build")
        parser.add_argument("--format", action="store_true", help="Format disk clean ext4 before build")
        args = parser.parse_args(sys.argv[2:])

        shut = False if args.keep_alive else (args.shutdown.lower() != "false")
        sf = False if args.no_sf else args.sf
        gofile = False if args.no_gofile else args.gofile
        recovery_data = json.loads((Path(__file__).resolve().parent.parent / "configs" / "recovery.json").read_text(encoding="utf-8"))
        flavor = args.flavor or next(iter(recovery_data), "")
        cfg = recovery_data.get(flavor, {})
        if not cfg:
            sys.exit(f"Unknown recovery flavor '{flavor}'.")
        if not args.dev:
            sys.exit("A recovery device is required.")
        variant = args.type or cfg.get("default_variant", "")

        dispatch_build(
            vm=vm,
            target_type="recovery",
            name=flavor.lower(),
            device=args.dev,
            variant=variant,
            sf_upload=sf,
            gofile_upload=gofile,
            auto_shutdown=shut,
            format_disk=args.format
        )

    elif command == "start":
        start_vm(vm)
    elif command == "stop":
        stop_vm(vm)
    elif command == "status":
        from cli.services.vm import get_vm_info
        info = get_vm_info(vm)
        if info:
            print(f"VM Name  : {info.get('name')}")
            print(f"Status   : {info.get('status')}")
            print(f"Machine  : {info.get('machineType', '').split('/')[-1]}")
            print(f"Zone     : {info.get('zone', '').split('/')[-1]}")
        else:
            print(f"Could not query VM status for {vm['instance']}.")

    elif command in ("login", "auth"):
        login_gcloud()

    elif command in ("vms", "switch-vm", "select-vm"):
        chosen = run_cluster_menu()
        print(f">>> Active VM: {chosen['instance']} {chosen['zone']}")

    elif command in ("upload-env", "env"):
        from cli.services.tool import upload_env_file
        env_file = sys.argv[2] if len(sys.argv) > 2 else ".env"
        upload_env_file(vm, env_file)

    elif command in ("upload-key", "key"):
        from cli.services.tool import upload_sf_key
        key_file = sys.argv[2] if len(sys.argv) > 2 else "~/.ssh/crave_sf"
        upload_sf_key(vm, key_file)

    elif command in ("git-config", "dummy-git"):
        from cli.services.tool import configure_dummy_git
        configure_dummy_git(vm)

    elif command in ("timezone", "tz"):
        from cli.services.tool import set_vm_timezone
        tz = sys.argv[2] if len(sys.argv) > 2 else "Asia/Kolkata"
        set_vm_timezone(vm, tz)

    elif command == "format-disk":
        from cli.services.disk import format_disk_ext4
        if len(sys.argv) < 3:
            sys.exit("Usage: ./gcp_menu.sh format-disk <disk_name>")
        format_disk_ext4(vm, sys.argv[2])

    elif command in ("links", "urls"):
        from cli.services.link import sync_links_from_vm
        links = sync_links_from_vm(vm)
        for l in links:
            print(l)

    elif command in ("tmux", "attach"):
        from cli.services.runner import attach_tmux_session
        attach_tmux_session(vm)

    elif command in ("log-errors", "errors", "scan"):
        from cli.services.monitor import scan_log_for_errors
        scan_log_for_errors(vm)

    elif command in ("stop-build", "kill"):
        from cli.services.monitor import stop_active_build
        stop_active_build(vm)

    elif command in ("download-rom",):
        download.submenu_download_rom(vm)

    elif command in ("download-recovery",):
        download.submenu_download_recovery(vm)

    elif command in ("download-log",):
        download.submenu_download_log(vm)

    else:
        print(f"Unknown command '{command}'.")
        print("Run './gcp_menu.sh' to enter the menu, or use 'build', 'recovery', 'start', 'stop', 'status'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
