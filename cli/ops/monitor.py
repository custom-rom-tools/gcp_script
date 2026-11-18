"""
Build Monitoring & Controls Menu (cli/ops/monitor.py)
Provides realtime log tailing, compilation error scanning, process stoppage, and VM info.
"""

from cli.base import clear_screen, print_header, print_divider, pause, read_input, GREEN, CYAN, YELLOW, RED, NC
from cli.services.runner import attach_tmux_session
from cli.services.monitor import (
    scan_log_for_errors, stop_active_build, get_active_log_path,
    follow_live_logs,
)
from cli.services.vm import get_vm_info


def submenu_live_tmux(vm: dict):
    """Submenu to attach to the live tmux build session."""
    clear_screen()
    print_header("Attach to Live Build Session (tmux)")
    attach_tmux_session(vm)


def submenu_scan_errors(vm: dict):
    """Submenu to scan remote build.log for FAILED targets and compiler errors."""
    clear_screen()
    print_header("Scan Active Build Log for Errors")
    scan_log_for_errors(vm)
    pause()


def submenu_build_status(vm: dict):
    """Show build status, active processes, and build log actions."""
    clear_screen()
    print_header("Build Status and Logs")
    log_path = get_active_log_path(vm)
    res = __import__("cli.services.vm", fromlist=["vm_exec"]).vm_exec(
        vm,
        """
STATUS="NO_BUILD_RECORDED"
for f in /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status; do
    if [ -f "$f" ]; then STATUS=$(cat "$f" 2>/dev/null); break; fi
done
echo "$STATUS"
echo "Processes:"
pgrep -af '[s]oong_ui|[n]inja|[c]kati|repo/main\\.py|scripts/build\\.py' 2>/dev/null | grep -v pgrep || echo "None"
""",
        check=False,
    )
    print(res.stdout)
    print_divider()
    print(f" {GREEN}1){NC} Follow live log")
    print(f" {CYAN}2){NC} Scan build log")
    print(f" {YELLOW}3){NC} Refresh")
    print(f" {RED}0){NC} Back")
    choice = read_input("\nSelect 0-3: ").strip()
    if choice == "1":
        follow_live_logs(vm)
    elif choice == "2":
        scan_log_for_errors(vm)
    if choice in ("1", "2"):
        pause()


def submenu_live_monitor(vm: dict):
    """Interactive htop, top, or network monitor."""
    while True:
        clear_screen()
        print_header("Live System Monitor")
        print(f" {GREEN}1){NC} htop")
        print(f" {CYAN}2){NC} top")
        print(f" {YELLOW}3){NC} Network speed")
        print(f" {RED}0){NC} Back")
        print_divider()
        choice = read_input("\nSelect 0-3: ").strip()
        if choice == "0":
            break
        if choice == "1":
            from cli.services.vm import vm_exec_tty
            vm_exec_tty(vm, "htop")
        elif choice == "2":
            from cli.services.vm import vm_exec_tty
            vm_exec_tty(vm, "top")
        elif choice == "3":
            from cli.services.vm import vm_exec_tty
            vm_exec_tty(vm, "while true; do clear; echo 'Network Speed'; /usr/local/bin/vm_net_speed.sh 2>/dev/null || true; awk '/:/ {gsub(/:/,\" \"); if ($1 != \"lo\") printf \"%s RX %.2f GB TX %.2f GB\\n\",$1,$2/1073741824,$10/1073741824}' /proc/net/dev; sleep 1; done")


def submenu_stop_build(vm: dict):
    """Submenu to cleanly kill all remote compilation processes and release locks."""
    clear_screen()
    print_header("Stop Active Build on VM")
    stop_active_build(vm)
    pause()


def submenu_vm_info(vm: dict):
    """Submenu to query and display compute instance properties."""
    clear_screen()
    print_header(f"VM Status & Info: {vm['instance']}")
    info = get_vm_info(vm)
    if info:
        print(f" Name        : {info.get('name')}")
        print(f" Status      : {info.get('status')}")
        print(f" Machine Type: {info.get('machineType', '').split('/')[-1]}")
        print(f" Zone        : {info.get('zone', '').split('/')[-1]}")
        print(f" CPU Platform: {info.get('cpuPlatform', 'Unknown')}")
    else:
        print(" Unable to retrieve instance information from GCP.")
    pause()
