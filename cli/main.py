"""
GCP Build Farm CLI Dispatcher & Dashboard Router (cli/main.py)
Drives both the interactive build dashboard and the non-interactive CLI flags.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from cli import base, services, builds, ops, storage, tools, cluster
from cli.base import (
    clear_screen, print_divider, print_section, print_status,
    is_interactive,
    CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA, BOLD, NC,
    read_input, print_header,
)
from cli.services.vm import (
    get_active_vm, start_vm, stop_vm, get_vm_info, get_vm_state,
    check_gcloud_auth, login_gcloud,
)
from cli.cluster.vm import run_cluster_menu
from cli.builds import rom, recovery, kernel
from cli.ops import download, monitor, link
from cli.storage import disk, swap
from cli.tools import setup, auth, console
from cli.services.runner import dispatch_build

REPO_ROOT = Path(__file__).resolve().parent.parent


def reload_all():
    """Reload the CLI modules so edits are reflected without restarting."""
    for module in (
        base,
        services.vm, services.runner, services.disk, services.download,
        services.tool, services.monitor, services.link, services.swap,
        builds.rom, builds.recovery, builds.kernel,
        ops.download, ops.monitor, ops.link,
        storage.disk, storage.swap,
        tools.setup, tools.auth, tools.console,
        cluster.vm,
    ):
        importlib.reload(module)


def run_build_dashboard(vm: dict):
    """Main Build & Operations Dashboard interactive loop."""
    while True:
        clear_screen()
        account = check_gcloud_auth() or "Not Authenticated"
        vm_info = get_vm_info(vm)
        vm_running = bool(vm_info and vm_info.get("status") == "RUNNING")
        status_color = GREEN if vm_running else RED
        status_text = "ONLINE" if vm_running else "OFFLINE"

        print_header("GCP BUILD & DOWNLOAD MANAGER")
        print_status("Account", account, GREEN)
        print_status("VM", f"{vm['instance']}  {vm['zone']}", GREEN)
        print_status("Disk", vm.get("general_disk", "gcp-general-disk"), BLUE)
        print_status("Status", status_text, status_color)
        print_divider()

        print_section("[ANDROID 16 ROM]")
        print(f"   {GREEN}1){NC} Evolution X 16")
        print(f"   {GREEN}2){NC} LineageOS 16")
        print(f"   {GREEN}3){NC} Lunaris AOSP 16")
        print()
        print_section("[ANDROID 17 ROM]")
        print(f"   {CYAN}4){NC} Evolution X 17")
        print(f"   {CYAN}5){NC} LineageOS 17")
        print()
        print_section("[RECOVERY]")
        print(f"   {YELLOW}6){NC} OrangeFox Recovery")
        print(f"   {YELLOW}7){NC} TWRP Recovery")
        print(f"   {YELLOW}8){NC} All Recoveries Batch (OFRP + TWRP)")
        print()
        print_section("[KERNEL]")
        print(f"   {MAGENTA}9){NC} Android 16 Kernel (boot.img)")
        print(f"  {MAGENTA}10){NC} Android 17 Kernel (boot.img)")
        print()
        print_section("[DOWNLOADS TO MAC]")
        print(f"  {BLUE}11){NC} Download ROM (.zip)")
        print(f"  {BLUE}12){NC} Download Recovery (.img/.zip)")
        print(f"  {BLUE}13){NC} Download Kernel (.img)")
        print(f"  {BLUE}14){NC} {CYAN}Download Build Log (.log){NC}")
        print()
        print_section("[MONITOR]")
        print(f"  {BOLD}15){NC} {GREEN}Live Build Progress (tmux){NC}")
        print(f"  {BOLD}16){NC} {CYAN}Build Status & Error Logs{NC}")
        print(f"  {BOLD}17){NC} {GREEN}Live System Monitor (htop/top/net){NC}")
        print(f"  {BOLD}18){NC} {RED}Stop Active Build{NC}")
        print()
        print_section("[VM]")
        print(f"  {BOLD}19){NC} VM Status & Info")
        print(f"  {BOLD}20){NC} Start VM")
        print(f"  {BOLD}21){NC} Stop VM")
        print(f"  {BOLD}22){NC} Disk Manager")
        print(f"  {BOLD}23){NC} Swap Manager")
        print(f"  {BOLD}24){NC} {CYAN}Machine Type Switcher{NC}")
        print(f"  {BOLD}25){NC} SSH Console")
        print()
        print_section("[TOOLS]")
        print(f"  {BOLD}26){NC} Setup & Tools")
        print(f"  {BOLD}27){NC} {CYAN}Upload Links (works while VM stopped){NC}")
        print(f"  {BOLD}28){NC} Switch / Manage VMs")
        print()
        print(f"   {YELLOW}r){NC} Refresh Menu")
        print(f"   {RED}q){NC} Quit")
        print_divider()

        choice = read_input(" Select option [1-28, r, q]: ").strip().lower()

        if choice in ("q", "quit", "exit"):
            print("Goodbye!")
            sys.exit(0)
        if choice in ("r", ""):
            reload_all()
            continue

        if choice == "1":
            rom.submenu_evolution(vm, "16")
        elif choice == "2":
            rom.submenu_lineage(vm, "16")
        elif choice == "3":
            rom.submenu_lunaris(vm, "16")
        elif choice == "4":
            rom.submenu_evolution(vm, "17")
        elif choice == "5":
            rom.submenu_lineage(vm, "17")
        elif choice == "6":
            recovery.submenu_orangefox(vm)
        elif choice == "7":
            recovery.submenu_twrp(vm)
        elif choice == "8":
            recovery.submenu_batch_recoveries(vm)
        elif choice == "9":
            kernel.submenu_atoll_kernel(vm, "16")
        elif choice == "10":
            kernel.submenu_atoll_kernel(vm, "17")
        elif choice == "11":
            download.submenu_download_rom(vm)
        elif choice == "12":
            download.submenu_download_recovery(vm)
        elif choice == "13":
            download.submenu_download_kernel(vm)
        elif choice == "14":
            download.submenu_download_log(vm)
        elif choice == "15":
            monitor.submenu_live_tmux(vm)
        elif choice == "16":
            monitor.submenu_build_status(vm)
        elif choice == "17":
            monitor.submenu_live_monitor(vm)
        elif choice == "18":
            monitor.submenu_stop_build(vm)
        elif choice == "19":
            monitor.submenu_vm_info(vm)
        elif choice == "20":
            start_vm(vm)
            base.pause()
        elif choice == "21":
            stop_vm(vm)
            base.pause()
        elif choice == "22":
            disk.submenu_disk_manager(vm)
        elif choice == "23":
            swap.submenu_swap_manager(vm)
        elif choice == "24":
            setup.submenu_machine_type(vm)
        elif choice == "25":
            console.submenu_ssh_console(vm)
        elif choice == "26":
            setup.run_tools_menu(vm)
        elif choice == "27":
            link.submenu_view_links(vm)
        elif choice == "28":
            vm = run_cluster_menu()
        else:
            print(f"{RED}Invalid choice.{NC}")
            base.pause()


def _load_config(name: str) -> dict:
    try:
        return json.loads((REPO_ROOT / "configs" / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"Could not read configs/{name}: {exc}")


def _add_upload_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sf", action="store_true", help="Upload to SourceForge")
    parser.add_argument("--no-sf", action="store_true", help="Skip SourceForge upload")
    parser.add_argument("--gofile", action="store_true", default=True,
                        help="Upload to Gofile.io (default)")
    parser.add_argument("--no-gofile", action="store_true", help="Skip Gofile.io upload")
    parser.add_argument("--shutdown", nargs="?", const="true", default="true",
                        help="Auto shutdown VM on completion (true/false)")
    parser.add_argument("--keep-alive", action="store_true",
                        help="Keep the VM running after the build")
    parser.add_argument("--format", action="store_true",
                        help="Format the target disk clean ext4 before building")
    parser.add_argument("--no-attach", action="store_true",
                        help="Do not attach to the tmux session after launch")


def _dispatch_options(args) -> dict:
    return {
        "sf_upload": False if args.no_sf else bool(args.sf),
        "gofile_upload": False if args.no_gofile else bool(args.gofile),
        "auto_shutdown": False if args.keep_alive else (str(args.shutdown).lower() != "false"),
        "format_disk": bool(args.format),
        "attach_live": False if args.no_attach else is_interactive(),
    }


def _cli_build(vm: dict):
    parser = argparse.ArgumentParser(prog="./gcp_menu.sh build")
    parser.add_argument("--rom", default="evox", help="ROM name (evox, lineage, lunaris)")
    parser.add_argument("--ver", "--version", default="16", help="Android version")
    parser.add_argument("--dev", "--device", default="RMX2170", help="Device code or 'all'")
    parser.add_argument("--type", "--variant", default=None, help="Build variant")
    _add_upload_args(parser)
    args = parser.parse_args(sys.argv[2:])

    rom_data = _load_config("rom.json")
    alias = {"evox": "evolution", "los": "lineage", "lunar": "lunaris"}
    rom_name = alias.get(str(args.rom).lower(), str(args.rom).lower())
    if rom_name not in rom_data:
        sys.exit(f"Unknown ROM '{args.rom}'. Choose from: {', '.join(rom_data)}")
    versions = rom_data[rom_name].get("versions", {})
    version = str(args.ver)
    if version not in versions:
        sys.exit(f"Unknown version '{version}' for {rom_name}. Choose from: {', '.join(versions)}")
    variant = args.type or rom_data[rom_name].get("default_variant", "userdebug")

    dispatch_build(
        vm=vm, target_type="rom", name=rom_name, version=version,
        device=args.dev, variant=variant, **_dispatch_options(args),
    )


def _cli_recovery(vm: dict):
    parser = argparse.ArgumentParser(prog="./gcp_menu.sh recovery")
    parser.add_argument("--flavor", "--recovery", default="ofrp",
                        help="Recovery flavor: ofrp, twrp or all")
    parser.add_argument("--dev", "--device", default="sm7125", help="Device code or 'all'")
    parser.add_argument("--type", "--variant", default=None, help="Build variant")
    _add_upload_args(parser)
    args = parser.parse_args(sys.argv[2:])

    recovery_data = _load_config("recovery.json")
    flavor = str(args.flavor).lower()
    flavor = {"orangefox": "ofrp", "fox": "ofrp", "teamwin": "twrp"}.get(flavor, flavor)
    if flavor not in ("ofrp", "twrp", "all"):
        sys.exit(f"Unknown recovery flavor '{args.flavor}'. Choose from: ofrp, twrp, all")
    cfg = recovery_data.get(flavor, recovery_data.get("ofrp", {}))
    if not cfg:
        sys.exit("No recovery configuration found.")
    variant = args.type or cfg.get("default_variant", "eng")

    dispatch_build(
        vm=vm, target_type="recovery", name=flavor, device=args.dev,
        variant=variant, **_dispatch_options(args),
    )


def _cli_kernel(vm: dict):
    parser = argparse.ArgumentParser(prog="./gcp_menu.sh kernel")
    parser.add_argument("--ver", "--version", default="16", help="Android version (16 or 17)")
    parser.add_argument("--dev", "--device", default="RMX2170", help="Device code")
    parser.add_argument("--type", "--variant", default="userdebug", help="Build variant")
    _add_upload_args(parser)
    args = parser.parse_args(sys.argv[2:])

    kernel_data = _load_config("kernel.json")
    kernel_cfg = kernel_data.get("atoll", {})
    versions = kernel_cfg.get("versions", {})
    version = str(args.ver)
    if version not in versions:
        sys.exit(f"Unknown kernel version '{version}'. Choose from: {', '.join(versions)}")
    variant = args.type or kernel_cfg.get("default_variant", "userdebug")

    dispatch_build(
        vm=vm, target_type="kernel", name="atoll", version=version,
        device=args.dev, variant=variant, **_dispatch_options(args),
    )


def _print_help():
    print("Usage:")
    print("  ./gcp_menu.sh                         Interactive dashboard")
    print("  ./gcp_menu.sh build                   Build a ROM (defaults: evox/16/RMX2170)")
    print("  ./gcp_menu.sh recovery                Build a recovery (defaults: ofrp/sm7125)")
    print("  ./gcp_menu.sh kernel                  Build a kernel (defaults: A16/RMX2170)")
    print("  ./gcp_menu.sh start | stop | status")
    print("  ./gcp_menu.sh ssh                     Open an SSH shell on the VM")
    print("  ./gcp_menu.sh tmux                    Attach to the live build session")
    print("  ./gcp_menu.sh htop | top | net        Live process/network monitor")
    print("  ./gcp_menu.sh vms                     Switch active VM")
    print("  ./gcp_menu.sh download-rom|recovery|kernel|log [--all | --pick 1,3]")
    print("  ./gcp_menu.sh create-disk [name] [size]")
    print("  ./gcp_menu.sh delete-disk [name]")
    print("  ./gcp_menu.sh attach-disk | detach-disk [name]")
    print("  ./gcp_menu.sh swap | swap-create [size] [path] | swap-off")
    print("  ./gcp_menu.sh links                   Show upload links")
    print("  ./gcp_menu.sh setup                   Run VM environment setup")
    print("  ./gcp_menu.sh machine-type [type]")
    print("  ./gcp_menu.sh format-disk <disk_name>")
    print("  ./gcp_menu.sh info                    Farm specifications")
    print("  ./gcp_menu.sh stop-build | log-errors")


def main():
    """Entrypoint for both the interactive menu and headless CLI flags."""
    if len(sys.argv) == 1:
        active_vm = run_cluster_menu()
        run_build_dashboard(active_vm)
        return

    if sys.argv[1].lower() in ("-h", "--help", "help"):
        _print_help()
        return

    command = sys.argv[1].lower()
    vm = get_active_vm()

    if command in ("cluster", "menu-cluster", "vms", "switch-vm", "select-vm"):
        chosen = run_cluster_menu()
        print(f">>> Active VM: {chosen['instance']} {chosen['zone']}")

    elif command in ("build", "build-rom"):
        _cli_build(vm)

    elif command in ("recovery", "build-recovery"):
        _cli_recovery(vm)

    elif command in ("kernel", "build-kernel"):
        _cli_kernel(vm)

    elif command == "start":
        start_vm(vm)
    elif command == "stop":
        stop_vm(vm)
    elif command == "status":
        info = get_vm_info(vm, refresh=True)
        status, ip = get_vm_state(vm)
        if info:
            print(f"Name         : {info.get('name')}")
            print(f"Status       : {info.get('status')}")
            print(f"External IP  : {ip or 'none'}")
            print(f"Machine Type : {(info.get('machineType', '') or '').rstrip('/').split('/')[-1]}")
            print(f"Zone         : {(info.get('zone', '') or '').rstrip('/').split('/')[-1]}")
            print(f"Project      : {vm.get('project', '')}")
        else:
            print(f"Could not query VM status for {vm['instance']} (state: {status}).")

    elif command in ("login", "auth"):
        login_gcloud()

    elif command in ("info", "specs"):
        from cli.ops.monitor import show_farm_specs
        show_farm_specs(vm)

    elif command == "ssh":
        from cli.ops.monitor import require_vm_running
        if require_vm_running(vm, "open an SSH console"):
            from cli.services.vm import vm_exec_tty
            vm_exec_tty(vm, "bash -l")

    elif command in ("htop", "top", "net", "netspeed", "net-speed"):
        from cli.ops.monitor import require_vm_running, _net_speed_cmd
        if require_vm_running(vm, "monitor processes"):
            from cli.services.vm import vm_exec_tty
            if command == "htop":
                vm_exec_tty(vm, "htop")
            elif command == "top":
                vm_exec_tty(vm, "top")
            else:
                vm_exec_tty(vm, _net_speed_cmd())

    elif command in ("monitor",):
        monitor.submenu_live_monitor(vm)

    elif command in ("logs", "log", "status-build", "build-status"):
        monitor.submenu_build_status(vm)

    elif command == "create-disk":
        from cli.services.disk import create_and_attach_disk
        disk_name = sys.argv[2] if len(sys.argv) > 2 else "general"
        size = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else None
        description = sys.argv[4] if len(sys.argv) > 4 else ""
        create_and_attach_disk(vm, disk_name, size, description)

    elif command == "delete-disk":
        from cli.services.disk import delete_disk_action
        delete_disk_action(vm, sys.argv[2] if len(sys.argv) > 2 else "")

    elif command in ("attach-disk", "detach-disk"):
        from cli.services.disk import attach_disk_action, detach_disk_action
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        if command == "attach-disk":
            attach_disk_action(vm, target)
        else:
            detach_disk_action(vm, target)

    elif command in ("swap", "swap-menu"):
        swap.submenu_swap_manager(vm)

    elif command == "swap-create":
        from cli.services.swap import create_swap
        size = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 16
        path = sys.argv[3] if len(sys.argv) > 3 else "/mnt/a16/swapfile"
        mount = path.rsplit("/", 1)[0] or "/"
        create_swap(vm, mount, size)

    elif command in ("swap-off", "swapoff"):
        from cli.services.swap import disable_all_swap
        disable_all_swap(vm)

    elif command == "setup":
        from cli.services.tool import setup_environment
        setup_environment(vm)

    elif command in ("watchdog", "deploy-watchdog"):
        from cli.services.tool import deploy_watchdog
        deploy_watchdog(vm)

    elif command in ("upload-env", "env"):
        from cli.services.tool import upload_env_file
        upload_env_file(vm, sys.argv[2] if len(sys.argv) > 2 else ".env")

    elif command in ("upload-key", "key"):
        from cli.services.tool import upload_sf_key
        upload_sf_key(vm, sys.argv[2] if len(sys.argv) > 2 else "~/.ssh/crave_sf")

    elif command in ("git-config", "dummy-git"):
        from cli.services.tool import configure_dummy_git
        configure_dummy_git(vm)

    elif command in ("timezone", "tz"):
        from cli.services.tool import set_vm_timezone
        set_vm_timezone(vm, sys.argv[2] if len(sys.argv) > 2 else "Asia/Kolkata")

    elif command in ("machine-type", "type"):
        from cli.tools.setup import submenu_machine_type
        from cli.services.vm import set_machine_type
        if len(sys.argv) > 2:
            target = sys.argv[2]
            if target.lower() == "cheapest":
                target = "t2d-standard-16"
            elif target.lower() == "fastest":
                target = "c2d-standard-16"
            set_machine_type(vm, target)
        else:
            submenu_machine_type(vm)

    elif command == "format-disk":
        from cli.services.disk import format_disk_action
        if len(sys.argv) < 3:
            sys.exit("Usage: ./gcp_menu.sh format-disk <disk_name>")
        format_disk_action(vm, sys.argv[2], auto_yes=True)

    elif command in ("links", "urls", "upload-links"):
        from cli.services.link import sync_links_from_vm
        links = sync_links_from_vm(vm)
        if not links:
            print("No upload links recorded yet.")
        for line in links:
            print(line)

    elif command in ("tmux", "attach"):
        from cli.services.runner import attach_tmux_session
        attach_tmux_session(vm)

    elif command in ("log-errors", "errors", "scan"):
        from cli.services.monitor import scan_log_for_errors
        scan_log_for_errors(vm)

    elif command in ("stop-build", "kill", "cancel-build"):
        from cli.services.monitor import stop_active_build
        stop_active_build(vm)

    elif command in ("download-rom", "download-recovery", "download-kernel", "download-log"):
        from cli.services.download import download_artifacts
        artifact = command.rsplit("-", 1)[-1]
        selection = None
        rest = sys.argv[2:]
        for index, arg in enumerate(rest):
            if arg == "--all":
                selection = "all"
            elif arg.startswith("--pick="):
                selection = arg.split("=", 1)[1]
            elif arg == "--pick":
                if index + 1 >= len(rest):
                    sys.exit("Usage: ./gcp_menu.sh download-rom [--all | --pick 1,3]")
                selection = rest[index + 1]
        download_artifacts(vm, artifact, selection=selection)

    else:
        print(f"Unknown command '{command}'.")
        print("Run './gcp_menu.sh --help' or './gcp_menu.sh' for the interactive menu.")
        sys.exit(1)


if __name__ == "__main__":
    main()
