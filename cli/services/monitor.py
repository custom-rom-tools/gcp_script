from cli.services.vm import vm_exec, is_vm_running


def get_active_log_path(vm: dict) -> str:
    cmd = "ls -t /mnt/a16/build.log /mnt/a17/build.log /mnt/recovery/build.log 2>/dev/null | head -n 1"
    res = vm_exec(vm, cmd, check=False)
    return res.stdout.strip()


def follow_live_logs(vm: dict, lines: int = 400):
    if not is_vm_running(vm):
        print(">>> VM is offline.")
        return
    path = get_active_log_path(vm)
    if not path:
        print(">>> No build log found.")
        return
    from cli.services.vm import vm_exec_tty
    vm_exec_tty(vm, f"tail -n {lines} -f '{path}'")


def scan_log_for_errors(vm: dict):
    if not is_vm_running(vm):
        print(">>> VM is offline.")
        return
    path = get_active_log_path(vm)
    if not path:
        print(">>> No build log found.")
        return
    res = vm_exec(vm, f"grep -nE -B2 -A8 'FAILED:|\\berror:' '{path}' | tail -n 100", check=False)
    print(res.stdout or "No explicit errors found.")


def stop_active_build(vm: dict) -> bool:
    if not is_vm_running(vm):
        return True
    cmd = "sudo rm -f /run/gcp-build-active; tmux kill-session -t rom-build 2>/dev/null || true; for f in /mnt/a16/build.status /mnt/a17/build.status /mnt/recovery/build.status; do [ -f \"$f\" ] && echo 'BUILD_STATUS: CANCELLED' > \"$f\"; done"
    return vm_exec(vm, cmd, check=False).returncode == 0
