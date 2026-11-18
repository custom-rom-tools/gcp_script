"""
Host Tools Backend Service (cli/services/tools.py)
Handles credential provisioning (.env, SSH keys), git config, and timezone setting.
"""

from pathlib import Path
from cli.services.vm import vm_exec, vm_exec_tty, is_vm_running, run_gcloud


def upload_env_file(vm: dict, local_env_path: str = ".env") -> bool:
    """Securely upload local .env configuration to ~/.build_env on the VM."""
    path = Path(local_env_path).expanduser().resolve()
    if not path.is_file():
        root_env = Path(__file__).resolve().parent.parent.parent / ".env"
        if root_env.is_file():
            path = root_env
        else:
            print(">>> Error: No local .env file found.")
            return False

    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    print(f"\n>>> Uploading {path} to {vm['instance']}:~/.build_env...")
    args = [
        "compute", "scp", str(path),
        f"{vm['instance']}:~/.build_env",
        f"--zone={vm['zone']}"
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")

    res = run_gcloud(args, check=False)
    if res.returncode == 0:
        vm_exec(vm, "chmod 600 ~/.build_env", check=False)
        print(">>> Success: Credentials saved.")
        return True

    print(f">>> Error: Upload failed: {res.stderr}")
    return False


def upload_sf_key(vm: dict, key_path: str = "~/.ssh/crave_sf") -> bool:
    """Upload SourceForge SSH private key to the VM."""
    path = Path(key_path).expanduser().resolve()
    if not path.is_file():
        print(">>> Error: Private key not found.")
        return False

    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    print(f"\n>>> Uploading SSH key to {vm['instance']}:~/.ssh/crave_sf...")
    vm_exec(vm, "mkdir -p ~/.ssh && chmod 700 ~/.ssh", check=False)
    args = [
        "compute", "scp", str(path),
        f"{vm['instance']}:~/.ssh/crave_sf",
        f"--zone={vm['zone']}"
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")

    res = run_gcloud(args, check=False)
    if res.returncode == 0:
        vm_exec(vm, "chmod 600 ~/.ssh/crave_sf", check=False)
        print(">>> Success: SourceForge key configured.")
        return True

    print(f">>> Error: Failed to upload key: {res.stderr}")
    return False


def configure_dummy_git(vm: dict) -> bool:
    """Configure dummy git identity on the VM to prevent repo sync/commit aborts."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    print(f"\n>>> Setting dummy git identity on {vm['instance']}...")
    cmd = """
git config --global user.name "GCP Build Bot"
git config --global user.email "buildbot@gcp.internal"
git config --global color.ui auto
"""
    res = vm_exec(vm, cmd, check=False)
    return res.returncode == 0


def set_vm_timezone(vm: dict, tz: str = "Asia/Kolkata") -> bool:
    """Change VM system timezone."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    print(f"\n>>> Setting VM timezone to {tz}...")
    cmd = f"sudo timedatectl set-timezone '{tz}' && timedatectl"
    res = vm_exec(vm, cmd, check=False)
    return res.returncode == 0


def deploy_watchdog(vm: dict) -> bool:
    """Deploy and restart the 30-minute idle shutdown watchdog."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False
    repo_root = Path(__file__).resolve().parent.parent.parent
    script = repo_root / "system" / "startup.sh"
    if not script.is_file():
        print(">>> Error: Watchdog script not found.")
        return False
    args = [
        "compute", "scp", str(script),
        f"{vm['instance']}:~/startup.sh",
        f"--zone={vm['zone']}",
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    uploaded = run_gcloud(args, check=False)
    if uploaded.returncode != 0:
        print(">>> Error: Failed to deploy watchdog.")
        return False
    res = vm_exec(
        vm,
        "chmod +x ~/startup.sh && bash ~/startup.sh",
        check=False,
    )
    if res.returncode == 0:
        print(">>> Watchdog deployed.")
        return True
    print(">>> Error: Failed to restart watchdog.")
    return False


def setup_environment(vm: dict) -> bool:
    """Deploy and run the VM host build environment setup."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False
    repo_root = Path(__file__).resolve().parent.parent.parent
    script = repo_root / "system" / "setup_host.sh"
    if not script.is_file():
        print(">>> Error: setup_host.sh not found.")
        return False
    args = [
        "compute", "scp", str(script),
        f"{vm['instance']}:~/setup_host.sh",
        f"--zone={vm['zone']}",
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    uploaded = run_gcloud(args, check=False)
    if uploaded.returncode != 0:
        print(">>> Error: Failed to deploy setup script.")
        return False
    res = vm_exec_tty(vm, "chmod +x ~/setup_host.sh && bash ~/setup_host.sh")
    return res == 0
