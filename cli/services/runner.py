"""
Build Dispatcher Backend Service (cli/services/runner.py)
Synchronizes guest scripts and dispatches compilation inside a remote tmux session.
"""

import json
from pathlib import Path
from cli.services.vm import (
    vm_exec, vm_exec_tty, is_vm_running, start_vm, run_gcloud
)
from cli.services.disk import (
    resolve_build_disk, attach_disk, mount_disk, format_disk_ext4,
    get_attached_disks
)
from cli.base import clear_screen, print_divider, print_header, GREEN, CYAN, NC

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def load_kernel_config() -> dict:
    config_path = REPO_ROOT / "configs" / "kernel.json"
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_build_slot(vm: dict) -> bool:
    """Ensure no compiler process is already running on the VM."""
    check_cmd = """
if [ -f /run/gcp-build-active ]; then
    echo "LOCKED"
    exit 0
fi
procs=$(pgrep -f '[s]oong_ui|[n]inja|[c]kati|scripts/build\\.py' | wc -l)
if [ "$procs" -gt 0 ]; then
    echo "ACTIVE"
else
    echo "IDLE"
fi
"""
    res = vm_exec(vm, check_cmd, check=False)
    status = res.stdout.strip()
    if status in ("LOCKED", "ACTIVE"):
        print(f"\n>>> Busy: A build is already running on {vm['instance']}.")
        print(">>> Please wait for it to complete or stop it via Monitoring -> Stop Build.")
        return False
    return True


def sync_build_tools_to_vm(vm: dict) -> bool:
    """Sync local scripts/ and configs/ to ~/.gcp_farm/ on the VM."""
    print(">>> [Status] Syncing build scripts and configs to VM...", end="", flush=True)
    vm_exec(vm, "mkdir -p ~/.gcp_farm", check=False)

    for folder in ["scripts", "configs"]:
        local_path = REPO_ROOT / folder
        if local_path.is_dir():
            args = [
                "compute", "scp", "--recurse",
                str(local_path),
                f"{vm['instance']}:~/.gcp_farm/",
                f"--zone={vm['zone']}"
            ]
            if vm.get("project"):
                args.append(f"--project={vm['project']}")
            run_gcloud(args, check=False)
    print(" Done.", flush=True)

    return True


def attach_tmux_session(vm: dict) -> int:
    """Attach to the remote live tmux session."""
    return vm_exec_tty(vm, "tmux attach-session -t rom-build")


def dispatch_build(
    vm: dict,
    target_type: str,            # "rom", "recovery", or "kernel"
    name: str,                   # "evolution", "lineage", "ofrp", "atoll", etc.
    version: str = "16",
    device: str = "all",
    variant: str = "userdebug",
    sf_upload: bool = False,
    gofile_upload: bool = True,
    auto_shutdown: bool = True,
    format_disk: bool = False,
    attach_live: bool = False
) -> bool:
    """Deploy and launch the build pipeline on the VM inside a resilient tmux session."""

    # 1. Ensure VM is online
    if not is_vm_running(vm):
        if not start_vm(vm):
            return False

    # 2. Check build slot
    if not check_build_slot(vm):
        return False

    # 3. Resolve target storage disk
    if target_type == "kernel":
        # Kernel uses version-specific ROM folder
        kernel_cfg = load_kernel_config().get(name, {})
        ver_cfg = kernel_cfg.get("versions", {}).get(version, {})
        # Fallback to general disk if no specific kernel disk (as requested: mount general)
        disk_name = "gcp-general-disk" 
        mp = kernel_cfg.get("disk_mount", "/mnt/kernel")
        target_disk = disk_name
        defconfig = ver_cfg.get("defconfig", "vendor/atoll_defconfig")
    elif target_type == "recovery":
        disk_target_tag = "recovery"
        resolved = resolve_build_disk(vm, disk_target_tag)
        target_disk = resolved["disk"]
        mp = resolved["mount_point"]
        defconfig = ""
    else:
        disk_target_tag = version
        resolved = resolve_build_disk(vm, disk_target_tag)
        target_disk = resolved["disk"]
        mp = resolved["mount_point"]
        defconfig = ""

    clear_screen()
    print_header(f"{name.upper()} {target_type.upper()} Build")
    print(f"{CYAN}Device{NC}  {device}")
    print(f"{CYAN}Disk{NC}    {target_disk}")
    print(f"{CYAN}Mount{NC}   {mp}")
    print(f"{CYAN}VM{NC}      {vm['instance']}  {vm['zone']}")
    if target_type == "kernel":
        print(f"{CYAN}Defconfig{NC} {defconfig}")
    else:
        print(f"{CYAN}Variant{NC} {variant}")
    print_divider()

    # 4. Attach & Mount disk
    attached_names = {d["name"] for d in get_attached_disks(vm)}
    if target_disk not in attached_names:
        print(f">>> [Status] Attaching disk {target_disk}...", end="", flush=True)
        if not attach_disk(vm, target_disk):
            print(" Failed.", flush=True)
            print(f">>> Error: Failed to attach {target_disk}")
            return False
        print(" Done.", flush=True)

    if format_disk:
        print(f">>> [Status] Formatting disk {target_disk}...", end="", flush=True)
        if not format_disk_ext4(vm, target_disk):
            print(" Failed.", flush=True)
            return False
        print(" Done.", flush=True)

    print(f">>> [Status] Mounting disk {target_disk} at {mp}...", end="", flush=True)
    if not mount_disk(vm, target_disk, mp):
        print(" Failed.", flush=True)
        print(f">>> Error: Failed to mount {target_disk} at {mp}")
        return False
    print(" Done.", flush=True)

    # 5. Sync scripts & configs to VM
    sync_build_tools_to_vm(vm)

    # 6. Build the runner command
    runner_args = []
    if target_type == "kernel":
        runner_args.extend(["--kernel", name])
        if device:
            runner_args.extend(["--dev", device])
        if sf_upload:
            runner_args.append("--sf")
        if not gofile_upload:
            runner_args.append("--no-gofile")
    elif target_type == "recovery":
        runner_args.extend(["--recovery", name])
        if device:
            runner_args.extend(["--dev", device])
        if variant:
            runner_args.extend(["--variant", variant])
        if sf_upload:
            runner_args.append("--sf")
        if not gofile_upload:
            runner_args.append("--no-gofile")
    else:
        runner_args.extend(["--rom", name, "--ver", version])
        if device:
            runner_args.extend(["--dev", device])
        if variant:
            runner_args.extend(["--variant", variant])
        if sf_upload:
            runner_args.append("--sf")
        if not gofile_upload:
            runner_args.append("--no-gofile")

    args_str = " ".join(runner_args)

    # Kernel gets its own status file; ROM/Recovery share build.status
    status_file = f"{mp}/kernel-build.status" if target_type == "kernel" else f"{mp}/build.status"
    log_file = f"{mp}/kernel-build.log" if target_type == "kernel" else f"{mp}/build.log"
    build_label = f"KERNEL {name.upper()} ({device})" if target_type == "kernel" else variant

    tmux_runner_cmd = f"""
sudo touch /run/gcp-build-active
sudo rm -f /run/gcp-build-shutdown-pending
echo "BUILD_STATUS: RUNNING ({build_label}) - $(date)" > '{status_file}'

tmux kill-session -t rom-build 2>/dev/null || true
tmux new-session -d -s rom-build '
    set -o pipefail
    sudo touch /run/gcp-build-active
    stdbuf -oL -eL python3 ~/.gcp_farm/scripts/build.py {args_str} 2>&1 | tee "{log_file}"
    BUILD_EXIT=$?
    sudo rm -f /run/gcp-build-active

    if [ $BUILD_EXIT -eq 0 ]; then
        echo "BUILD_STATUS: SUCCESS - $(date)" > "{status_file}"
        echo -e "\\n\\033[1;32mBuild and upload completed successfully!\\033[0m"
        if [ "{str(auto_shutdown).lower()}" = "true" ]; then
            echo "Auto-shutdown enabled: Powering off VM in 15 seconds (press any key to cancel)..."
            read -t 15 -n 1 -r || sudo poweroff
        fi
    else
        echo "BUILD_STATUS: FAILED ($BUILD_EXIT) - $(date)" > "{status_file}"
        echo -e "\\n\\033[1;31mBuild failed with exit code $BUILD_EXIT\\033[0m"
        echo "Build session remains open. Logs are still visible above."
        exec bash
    fi
'
"""
    print(f"\n>>> Launching build in tmux session 'rom-build' on {vm['instance']}...")
    res = vm_exec(vm, tmux_runner_cmd, check=False)
    if res.returncode == 0:
        clear_screen()
        print_header("Build Started")
        print(f"{GREEN}Build{NC}      {name.upper()} {target_type.upper()}")
        print(f"{GREEN}Device{NC}    {device}")
        print(f"{GREEN}Session{NC}   rom-build")
        print(f"{GREEN}VM{NC}        {vm['instance']}")
        print(f"{GREEN}Mount{NC}     {mp}")
        print_divider()
        if attach_live:
            print("\nConnecting to live tmux session...")
            vm_exec_tty(vm, "tmux attach-session -t rom-build")
        return True

    print(f">>> Error: Failed to launch build: {res.stderr}")
    return False
