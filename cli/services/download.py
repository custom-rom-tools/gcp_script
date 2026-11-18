from pathlib import Path
from cli.services.vm import vm_exec, is_vm_running, run_gcloud


def download_artifacts(vm: dict, artifact_type: str = "rom", requested_log: str = None) -> bool:
    if not is_vm_running(vm):
        print(">>> VM is offline.")
        return False
    local_dir = Path.home() / "Downloads"
    local_dir.mkdir(parents=True, exist_ok=True)
    if artifact_type == "log":
        cmd = "ls -t /mnt/a16/build.log /mnt/a17/build.log /mnt/recovery/build.log 2>/dev/null | head -n 1"
        remote = vm_exec(vm, cmd, check=False).stdout.strip()
        if not remote:
            print(">>> No build log found.")
            return False
        local = local_dir / "build.log"
        args = ["compute", "scp", f"{vm['instance']}:{remote}", str(local), f"--zone={vm['zone']}"]
        if vm.get("project"):
            args.append(f"--project={vm['project']}")
        res = run_gcloud(args, check=False)
        if res.returncode == 0:
            print(f">>> Downloaded: {local}")
            return True
        return False
    print(">>> No artifact download implementation available.")
    return False
