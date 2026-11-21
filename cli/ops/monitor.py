"""
Build Monitoring & Controls Menu (cli/ops/monitor.py)
Realtime build dashboard, live log follower, error scanner, process monitor,
build cancellation and VM status/specification screens.
"""

from cli.base import (
    clear_screen, print_header, print_divider, print_thin_divider,
    pause, read_input, print_kv,
    CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA, BOLD, NC,
)
from cli.services.monitor import (
    collect_build_status, follow_live_logs, view_log_snapshot,
    scan_log_for_errors, stop_active_build, get_running_build_processes,
    list_available_build_logs, get_metadata_build_status,
)
from cli.services.runner import attach_tmux_session, net_speed_setup_cmd
from cli.services.link import sync_links_from_vm
from cli.services.vm import (
    is_vm_running, vm_exec, vm_exec_tty, get_vm_info, get_vm_ip, start_vm,
)
from cli.services.download import download_artifacts


def require_vm_running(vm: dict, action: str = "") -> bool:
    """Return True when the VM is online, otherwise explain why not."""
    if is_vm_running(vm):
        return True
    suffix = f" to {action}" if action else ""
    print(f">>> VM '{vm['instance']}' is powered OFF{suffix}. Start it first.")
    return False


def submenu_live_tmux(vm: dict):
    """Attach to the live tmux build session."""
    if not require_vm_running(vm, "attach to the build session"):
        pause()
        return
    clear_screen()
    print_header("Live Build Session (tmux)")
    print(">>> Attaching to 'rom-build'... (Detach: Ctrl+B then D)\n")
    attach_tmux_session(vm)
    pause()


def _print_build_dashboard(vm: dict) -> dict:
    print(f" {CYAN}Fetching build status & logs from VM...{NC}", end="", flush=True)
    data = collect_build_status(vm)
    status = str(data.get("status", "UNKNOWN"))
    procs = int(data.get("proc_count", 0))
    active = procs > 0 or "RUNNING" in status.upper()

    clear_screen()
    print_header("BUILD STATUS & LOGS")

    if procs > 0:
        print(f" {BOLD}BUILD STATE   :{NC} {GREEN}* ACTIVE / COMPILING{NC} ({procs} compiler processes)")
        print(f" {BOLD}PROGRESS      :{NC} {GREEN}{data.get('progress', 'N/A')}{NC}")
    elif "SUCCESS" in status.upper():
        print(f" {BOLD}BUILD STATE   :{NC} {GREEN}SUCCESS{NC}")
        print(f" {BOLD}DETAILS       :{NC} Completed and uploaded successfully.")
        links = sync_links_from_vm(vm)
        if links:
            print(f" {BOLD}LATEST LINKS  :{NC}")
            for line in links[-3:]:
                print(f"   {line}")
    elif "FAILED" in status.upper() or any("ninja failed" in l.lower() for l in data.get("tail", [])):
        print(f" {BOLD}BUILD STATE   :{NC} {RED}FAILED{NC}")
        print(f" {BOLD}LAST PROGRESS :{NC} {YELLOW}{data.get('progress', 'N/A')}{NC}")
    elif "CANCELLED" in status.upper():
        print(f" {BOLD}BUILD STATE   :{NC} {YELLOW}CANCELLED{NC}")
    elif "RUNNING" in status.upper():
        print(f" {BOLD}BUILD STATE   :{NC} {YELLOW}RUNNING{NC} "
              "(no compiler process detected; between phases or runner detached)")
        print(f" {BOLD}LAST PROGRESS :{NC} {YELLOW}{data.get('progress', 'N/A')}{NC}")
    else:
        print(f" {BOLD}BUILD STATE   :{NC} {BLUE}IDLE{NC} ({status})")

    target = data.get("target") or "build"
    build_dir = data.get("build_dir") or ""
    if build_dir:
        target = f"{target} ({build_dir.rsplit('/', 1)[-1]})"
    print(f" {BOLD}TARGET        :{NC} {CYAN}{target}{NC}")
    print(f" {BOLD}NET SPEED     :{NC} {CYAN}{data.get('net_speed', 'N/A')}{NC}")
    if data.get("log_path"):
        print(f" {BOLD}LOG FILE      :{NC} {data['log_path']} ({data.get('log_size', 'N/A')})")
    else:
        print(f" {BOLD}LOG FILE      :{NC} {YELLOW}not found{NC}")
    if data.get("status_file"):
        print(f" {BOLD}STATUS FILE   :{NC} {data['status_file']}")
    print(f" {BOLD}IDLE WATCHDOG :{NC} {CYAN}{data.get('watchdog', 'INACTIVE')}{NC} (30m auto-poweroff)")
    print_divider()

    if data.get("errors"):
        print(f"{RED}{BOLD}>>> DETECTED ERROR SUMMARY:{NC}")
        for line in data["errors"]:
            print(line)
        print_thin_divider()

    print(f"{BOLD}>>> LAST LINES OF ACTIVE BUILD OUTPUT:{NC}")
    print_thin_divider()
    if data.get("tail"):
        for line in data["tail"]:
            print(line)
    else:
        print("No recent build output found.")
    print_thin_divider()

    data["active"] = active
    return data


def _submenu_alternate_logs(vm: dict):
    clear_screen()
    print_header("CHOOSE ALTERNATE BUILD LOG")
    logs = list_available_build_logs(vm)
    if not logs:
        print(">>> No build logs found on VM.")
        pause()
        return

    print(" Select a log to view or inspect:\n")
    for idx, p in enumerate(logs, 1):
        name = p.split("/")[-1]
        tag = ""
        if name == "build.log":
            tag = f" {GREEN}[Current / Active]{NC}"
        elif name == "build.log.failed":
            tag = f" {RED}[Last Failed Build Log]{NC}"
        elif name == "build.log.prev":
            tag = f" {YELLOW}[Previous Run Before Restart]{NC}"
        elif "failed" in name:
            tag = f" {RED}[Archived Failure]{NC}"
        print(f"  {idx}) {p}{tag}")
    print()
    print(f"  {RED}0 / q){NC} Back")
    print_divider()
    choice = read_input(f"Select log [1-{len(logs)}, 0/q]: ").strip().lower()
    if not choice or choice in ("0", "q", "quit", "exit"):
        return
    if choice.isdigit() and 1 <= int(choice) <= len(logs):
        chosen = logs[int(choice) - 1]
        print(f"\n Actions for: {chosen}")
        print("  1) View in Pager (less)")
        print("  2) Scan for Errors (grep FAILED)")
        print("  3) Download to Mac (~/Downloads)")
        sub_choice = read_input(" Select action [1-3, default: 1]: ").strip()
        if sub_choice == "2":
            clear_screen()
            print_header("SCANNING BUILD LOG FOR COMPILATION ERRORS")
            print(f" Log Target: {BOLD}{chosen}{NC}\n")
            scan_log_for_errors(vm, chosen)
            pause()
        elif sub_choice == "3":
            download_artifacts(vm, "log", chosen)
            pause()
        else:
            view_log_snapshot(vm, chosen)


def submenu_build_status(vm: dict):
    """Interactive build status page with log actions."""
    if not is_vm_running(vm):
        from pathlib import Path
        import subprocess
        clear_screen()
        print_header("BUILD STATUS & ERROR LOGS (OFFLINE)")
        print(f" {YELLOW}>>> VM '{vm['instance']}' is currently powered OFF (Compute billing paused).{NC}\n")
        meta = get_metadata_build_status(vm)
        status = meta.get("last-build-status") or "NO_BUILD_RECORDED"
        stamp = meta.get("last-build-timestamp") or ""
        error = meta.get("last-build-error") or ""

        status_color = RED if "FAIL" in status.upper() else (GREEN if "SUCCESS" in status.upper() else YELLOW)
        print(f" {BOLD}LAST RECORDED STATUS :{NC} {status_color}{status}{NC}")
        if stamp:
            print(f" {BOLD}TIMESTAMP            :{NC} {CYAN}{stamp}{NC}")
        if error:
            print(f"\n {RED}{BOLD}>>> LAST DETECTED COMPILATION ERROR (saved in GCP metadata):{NC}")
            print_thin_divider()
            for line in error.splitlines():
                print(f"   {line}")
            print_thin_divider()

        local_logs = sorted(Path.home().glob("Downloads/build_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if local_logs:
            print(f"\n {BOLD}LOCAL LOGS FOUND IN ~/Downloads:{NC}")
            for l in local_logs[:3]:
                print(f"   * {l.name} ({l.stat().st_size // 1024} KB)")

        print()
        print(f" {GREEN}1){NC} Power on VM to view full interactive dashboard & live logs")
        if local_logs:
            print(f" {CYAN}2){NC} View most recent local build log in terminal ({local_logs[0].name})")
        print(f" {RED}0 / q){NC} Back to Main Menu")
        print_divider()
        choice = read_input(" Select option [default: 1]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return
        if choice == "2" and local_logs:
            clear_screen()
            print_header(f"LOCAL LOG: {local_logs[0].name}")
            subprocess.run(["less", "-R", str(local_logs[0])])
            return
        if choice in ("1", "", "y", "yes"):
            print(f">>> Starting VM '{vm['instance']}'...")
            if not start_vm(vm) or not is_vm_running(vm):
                pause()
                return

    while True:
        data = _print_build_dashboard(vm)
        active = data.get("active", False)
        print()
        if active:
            print(f" {GREEN}1){NC} Follow Live Build Log (realtime, Ctrl+C to stop)")
        else:
            print(f" {CYAN}1){NC} View Full Build Log (less)")
        print(f" {CYAN}2){NC} Download Build Log (.log) to Downloads")
        print(f" {YELLOW}3){NC} Scan Log for Errors (grep FAILED with context)")
        print(f" {CYAN}4){NC} Choose / View Alternate Build Log (failed, prev, archives)")
        print(f" {RED}5){NC} Stop Active Build")
        print()
        print(f" {YELLOW}r){NC} Reload / Refresh Build Status")
        print(f" {RED}0 / q){NC} Back to Main Menu / Quit")
        print_divider()

        choice = read_input(" Select option [0-5, r, q, default: r]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return
        if choice in ("r", "reload", "refresh", ""):
            continue
        if choice == "1":
            if active:
                follow_live_logs(vm, 400, data.get("log_path", ""))
            else:
                view_log_snapshot(vm, data.get("log_path", ""))
        elif choice == "2":
            download_artifacts(vm, "log", data.get("log_path", ""))
            pause()
        elif choice == "3":
            clear_screen()
            print_header("SCANNING BUILD LOG FOR COMPILATION ERRORS")
            print(f" Log Target: {BOLD}{data.get('log_path') or 'newest build.log'}{NC}\n")
            scan_log_for_errors(vm, data.get("log_path", ""))
            pause()
        elif choice == "4":
            _submenu_alternate_logs(vm)
        elif choice == "5":
            submenu_stop_build(vm)


def _net_speed_cmd() -> str:
    return (
        net_speed_setup_cmd()
        + "\nwhile true; do clear; "
        "echo '====================================================='; "
        "echo '       LIVE VM NETWORK SPEED (1s refresh)            '; "
        "echo '====================================================='; "
        "printf '  Speed : %s\\n' \"$(/usr/local/bin/vm_net_speed.sh 2>/dev/null || echo 'DL -- | UL --')\"; "
        "awk '/:/ { gsub(/:/, \" \"); if ($1 != \"lo\" && $1 !~ /^(veth|docker|br-|virbr)/) "
        "{ printf \"  %-8s RX: %.2f GB | TX: %.2f GB\\n\", $1, $2/1073741824, $10/1073741824 } }' "
        "/proc/net/dev; "
        "echo '-----------------------------------------------------'; "
        "echo 'Press Ctrl+C to return...'; sleep 1; done"
    )


def submenu_live_monitor(vm: dict):
    """Interactive htop, top, or network monitor."""
    while True:
        clear_screen()
        print_header("LIVE SYSTEM MONITOR")
        print(f" {BOLD}1){NC} {GREEN}htop{NC}      - Interactive colour CPU & RAM monitor")
        print(f" {BOLD}2){NC} {CYAN}top{NC}       - Standard UNIX process monitor")
        print(f" {BOLD}3){NC} {YELLOW}net speed{NC} - Live VM Download / Upload speed (MB/s)")
        print()
        print(f" {YELLOW}r){NC} Reload / Refresh")
        print(f" {RED}0 / q){NC} Back to Main Menu / Quit")
        print_divider()

        choice = read_input(" Select monitor [0-3, r, q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return
        if choice in ("r", "reload", "refresh"):
            continue
        if choice not in ("1", "2", "3"):
            continue
        if not require_vm_running(vm, "monitor processes"):
            pause()
            continue
        if choice == "1":
            print(f">>> Launching htop on {vm['instance']}... (Press 'q' or F10 to exit)\n")
            vm_exec_tty(vm, "htop")
        elif choice == "2":
            print(f">>> Launching top on {vm['instance']}... (Press 'q' to exit)\n")
            vm_exec_tty(vm, "top")
        elif choice == "3":
            print(f">>> Monitoring VM network speed on {vm['instance']}... (Ctrl+C to exit)\n")
            vm_exec_tty(vm, _net_speed_cmd())


def submenu_stop_build(vm: dict):
    """Confirm and cancel the running build."""
    clear_screen()
    print_header("STOP / CANCEL RUNNING BUILD", vm["instance"])
    if not require_vm_running(vm, "stop build"):
        pause()
        return

    print(f"{YELLOW}>>> Active build & compilation processes:{NC}")
    processes = get_running_build_processes(vm)
    print(processes or "None (No active compiler processes).")
    print()

    answer = read_input("Are you sure you want to STOP and cancel the current build? [y/N, default: N, q=abort]: ")
    if answer.strip().lower() in ("q", "quit", "exit") or answer.strip().lower() not in ("y", "yes"):
        print(">>> Aborted. Build continues running.")
        pause()
        return

    print(">>> Terminating compiler processes and closing tmux build session...")
    if stop_active_build(vm):
        print(f"{GREEN}>>> Build has been terminated.{NC}")
    else:
        print(f"{RED}>>> Failed to stop the build cleanly; check the VM.{NC}")
    pause()


def submenu_vm_info(vm: dict):
    """Show GCP instance properties plus the farm specification sheet."""
    while True:
        clear_screen()
        print_header("VM STATUS & INFO", vm["instance"])
        info = get_vm_info(vm, refresh=True)
        ip = get_vm_ip(vm)
        if info:
            machine = (info.get("machineType", "") or "").rstrip("/").split("/")[-1]
            zone = (info.get("zone", "") or "").rstrip("/").split("/")[-1]
            print_kv("Name", info.get("name", vm["instance"]))
            print_kv("Status", info.get("status", "UNKNOWN"),
                     GREEN if info.get("status") == "RUNNING" else RED)
            print_kv("External IP", ip or "none")
            print_kv("Machine Type", machine)
            print_kv("Zone", zone)
            print_kv("CPU Platform", info.get("cpuPlatform", "Unknown"))
        else:
            print(" Unable to retrieve instance information from GCP.")
        print()
        show_farm_specs(vm)
        print()
        print(f"  {YELLOW}r){NC} Reload / Refresh Status")
        print(f"  {RED}0 / q){NC} Back to Main Menu / Quit")
        print_divider()
        choice = read_input(" Select [r, 0/q, default: 0]: ").strip().lower()
        if choice in ("0", "q", "quit", "exit", ""):
            return
        if choice in ("r", "reload", "refresh"):
            continue


def show_farm_specs(vm: dict) -> None:
    """Print the static farm architecture / cost summary."""
    info = get_vm_info(vm)
    machine = "unknown"
    if info:
        machine = (info.get("machineType", "") or "").rstrip("/").split("/")[-1]
    zone = vm.get("zone", "unknown")
    general_disk = vm.get("general_disk", "gcp-general-disk")

    print_divider()
    print(f"{BOLD} GCP Build Farm Specifications & Architecture{NC}")
    print_divider()
    print(f" Machine Type : {BOLD}{machine}{NC} (16 vCPUs, 64 GB RAM, compute-optimised)")
    print(" Failover     : Auto-cascades c2d -> t2d -> c2 when a pool is exhausted")
    print(f" RAM          : {BOLD}64 GB physical RAM{NC} (pure RAM mode by default)")
    print(f" Boot Disk    : {BOLD}50 GB standard disk{NC} (free-tier eligible)")
    print("              - OS, build packages and persistent ccache (15 GB cap)")
    print(" Storage      : Simplified 2-mount architecture (/mnt/rom and /mnt/recovery)")
    print("              - gcp-rom-disk (/mnt/rom) for all ROM & Kernel builds")
    print("              - gcp-recovery-disk (/mnt/recovery) for Recovery builds")
    print(f"              - General shared {general_disk} is also supported")
    print("              - Format anytime via Disk Manager to reuse across ROMs")
    print("              - Delete on demand to pause storage billing")
    print(" Swap         : Optional on demand via Swap Manager")
    print(f" Zone         : {BOLD}{zone}{NC} (lowest compute/storage rates globally)")
    print(" Running Cost : ~$0.67-$0.72 / hr (only billed while RUNNING)")
    print(f" Auto-Shutdown: {GREEN}Enabled (30-minute idle watchdog){NC}")
    print("              - Powers off when no builds and no SSH users for 30 min")
    print(" Storage Cost : ~$1.65 / day (500GB SSD; delete when done)")
    print_divider()
