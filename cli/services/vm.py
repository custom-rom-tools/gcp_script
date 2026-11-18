"""
VM Backend Service (cli/services/vm.py)
Handles Google Cloud Compute Engine instance lifecycle, discovery,
authentication, SSH execution, and persistence.
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from cli.base import read_input

ACTIVE_VM_FILE = Path.home() / ".gcp_active_vm"
_VM_INFO_CACHE = {}
_VM_INFO_CACHE_TIME = {}
_AUTH_CACHE = {"value": None, "checked": 0.0}
_DISCOVER_CACHE = {}
_DISCOVER_CACHE_TIME = {}


def load_local_env():
    """Load local .env or ~/.build_env into os.environ if present."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path.home() / ".build_env",
    ]
    for p in candidates:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ and v:
                            os.environ[k] = v
            except Exception:
                pass


def run_gcloud(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """Run a gcloud command returning CompletedProcess."""
    cmd = ["gcloud"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def check_gcloud_auth() -> str | None:
    """Return active Google Cloud account email, or None if unauthenticated."""
    now = time.time()
    if now - _AUTH_CACHE["checked"] < 30:
        return _AUTH_CACHE["value"]
    try:
        res = run_gcloud(["auth", "list", "--format=json"], check=False)
        if res.returncode == 0 and res.stdout.strip():
            accounts = json.loads(res.stdout)
            for acc in accounts:
                if acc.get("status") == "ACTIVE":
                    _AUTH_CACHE["value"] = acc.get("account")
                    _AUTH_CACHE["checked"] = now
                    return _AUTH_CACHE["value"]
            if accounts and accounts[0].get("account"):
                _AUTH_CACHE["value"] = accounts[0].get("account")
                _AUTH_CACHE["checked"] = now
                return _AUTH_CACHE["value"]
    except Exception:
        pass
    _AUTH_CACHE["value"] = None
    _AUTH_CACHE["checked"] = now
    return None


def login_gcloud() -> bool:
    """Prompt interactive Google Cloud login."""
    print("\n>>> Launching Google Cloud authentication (gcloud auth login)...")
    res = subprocess.run(["gcloud", "auth", "login"])
    if res.returncode == 0:
        _AUTH_CACHE["checked"] = 0.0
        account = check_gcloud_auth()
        print(f"\n>>> Successfully authenticated as: {account}")
        return True
    print("\n>>> Error: Authentication failed or was cancelled.")
    return False


def ensure_gcloud_authenticated() -> bool:
    """Verify active gcloud authentication, prompting login if needed."""
    account = check_gcloud_auth()
    if account:
        return True

    print("\n" + "=" * 65)
    print(" ⚠️  No active Google Cloud account detected.")
    print("=" * 65)
    choice = read_input("Sign in to Google Cloud now? Y/n: ").strip().lower()
    if choice not in ("n", "no"):
        return login_gcloud()
    return False


def get_default_project() -> str:
    """Resolve active GCP project ID from .env or gcloud config."""
    load_local_env()
    proj = os.environ.get("GCP_PROJECT", "").strip()
    if proj:
        return proj

    try:
        res = run_gcloud(["config", "get-value", "project"], check=False)
        val = res.stdout.strip()
        if val and "(unset)" not in val:
            return val
    except Exception:
        pass
    return ""


def discover_instances(project: str = "", refresh: bool = False) -> list[dict]:
    """Dynamically list all compute instances across all zones for the project."""
    target_project = project or get_default_project()
    now = time.time()
    if not refresh and now - _DISCOVER_CACHE_TIME.get(target_project, 0) < 10:
        return _DISCOVER_CACHE.get(target_project, [])
    args = [
        "compute", "instances", "list",
        "--format=json(name,zone.basename(),status,machineType.basename(),networkInterfaces[0].accessConfigs[0].natIP,disks[].source.basename())"
    ]
    if target_project:
        args.append(f"--project={target_project}")

    try:
        res = run_gcloud(args, check=False)
        if res.returncode == 0 and res.stdout.strip():
            raw_vms = json.loads(res.stdout)
            discovered = []
            for v in raw_vms:
                name = v.get("name", "")
                zone = v.get("zone", "")
                status = v.get("status", "")
                mt = v.get("machineType", "")
                ip = "None"
                net_ifs = v.get("networkInterfaces", [])
                if net_ifs:
                    access = net_ifs[0].get("accessConfigs", [])
                    if access:
                        ip = access[0].get("natIP", "None")

                disks = []
                for d in v.get("disks", []):
                    if isinstance(d, dict):
                        src = d.get("source", "")
                    else:
                        src = str(d)
                    if src:
                        disks.append(src.split("/")[-1])

                gen_disk = next((d for d in disks if "general" in d.lower()), "gcp-general-disk")

                discovered.append({
                    "name": name,
                    "zone": zone,
                    "status": status,
                    "machine_type": mt,
                    "ip": ip,
                    "disks": disks,
                    "general_disk": gen_disk,
                    "project": target_project
                })
            _DISCOVER_CACHE[target_project] = discovered
            _DISCOVER_CACHE_TIME[target_project] = now
            return discovered
    except Exception:
        pass
    return []


def get_active_vm() -> dict:
    """Resolve active target VM from .env, ~/.gcp_active_vm, or dynamic discovery."""
    load_local_env()
    project = get_default_project()

    env_inst = os.environ.get("GCP_INSTANCE", "").strip()
    env_zone = os.environ.get("GCP_ZONE", "").strip()
    env_disk = os.environ.get("GCP_GENERAL_DISK", "").strip()

    if env_inst and env_zone:
        return {
            "instance": env_inst,
            "zone": env_zone,
            "project": project,
            "general_disk": env_disk or "gcp-general-disk"
        }

    saved_data = {}
    if ACTIVE_VM_FILE.is_file():
        try:
            content = ACTIVE_VM_FILE.read_text(encoding="utf-8").strip()
            for line in content.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k in ("instance", "zone", "project", "general_disk") and v:
                        saved_data[k] = v
        except Exception:
            pass

    if saved_data.get("instance") and saved_data.get("zone"):
        return {
            "instance": saved_data["instance"],
            "zone": saved_data["zone"],
            "project": saved_data.get("project") or project,
            "general_disk": saved_data.get("general_disk") or "gcp-general-disk"
        }

    instances = discover_instances(project)
    if instances:
        running_vms = [v for v in instances if v["status"] == "RUNNING"]
        chosen = running_vms[0] if running_vms else instances[0]
        save_active_vm(
            instance=chosen["name"],
            zone=chosen["zone"],
            project=chosen["project"],
            general_disk=chosen["general_disk"]
        )
        return {
            "instance": chosen["name"],
            "zone": chosen["zone"],
            "project": chosen["project"],
            "general_disk": chosen["general_disk"]
        }

    return {
        "instance": "rom-build-farm",
        "zone": "us-central1-a",
        "project": project,
        "general_disk": "gcp-general-disk"
    }


def save_active_vm(instance: str, zone: str, project: str, general_disk: str = "gcp-general-disk"):
    """Persist active target VM to ~/.gcp_active_vm."""
    content = f"INSTANCE='{instance}'\nZONE='{zone}'\nPROJECT='{project}'\nGENERAL_DISK='{general_disk}'\n"
    ACTIVE_VM_FILE.write_text(content, encoding="utf-8")


def get_vm_info(vm: dict) -> dict | None:
    """Fetch JSON description of the active instance."""
    key = f"{vm.get('project','')}:{vm['zone']}:{vm['instance']}"
    now = time.time()
    if now - _VM_INFO_CACHE_TIME.get(key, 0) < 5:
        return _VM_INFO_CACHE.get(key)
    args = [
        "compute", "instances", "describe", vm["instance"],
        f"--zone={vm['zone']}",
        "--format=json"
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    try:
        res = run_gcloud(args, check=False)
        if res.returncode == 0 and res.stdout:
            info = json.loads(res.stdout)
            _VM_INFO_CACHE[key] = info
            _VM_INFO_CACHE_TIME[key] = now
            return info
    except Exception:
        pass
    _VM_INFO_CACHE[key] = None
    _VM_INFO_CACHE_TIME[key] = now
    return None


def is_vm_running(vm: dict) -> bool:
    """Check if the target instance is online and running."""
    info = get_vm_info(vm)
    return bool(info and info.get("status") == "RUNNING")


def get_vm_external_ip(vm: dict) -> str:
    """Fetch external NAT IP of the instance."""
    info = get_vm_info(vm)
    if info:
        for iface in info.get("networkInterfaces", []):
            for access in iface.get("accessConfigs", []):
                ip = access.get("natIP")
                if ip:
                    return ip
    return ""


def vm_exec(vm: dict, cmd_str: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run non-interactive remote command via gcloud compute ssh."""
    args = [
        "compute", "ssh", vm["instance"],
        f"--zone={vm['zone']}",
        "--command", cmd_str
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    return run_gcloud(args, check=check)


def vm_exec_tty(vm: dict, cmd_str: str = "bash -l") -> int:
    """Attach interactive PTY SSH terminal to the remote instance."""
    args = [
        "gcloud", "compute", "ssh", vm["instance"],
        f"--zone={vm['zone']}",
        "--", "-t", cmd_str
    ]
    if vm.get("project"):
        args.insert(4, f"--project={vm['project']}")
    res = subprocess.run(args)
    return res.returncode


def start_vm(vm: dict) -> bool:
    """Power on instance."""
    print(f"\n>>> Starting VM: {vm['instance']} ({vm['zone']})...")
    args = ["compute", "instances", "start", vm["instance"], f"--zone={vm['zone']}"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    res = run_gcloud(args, check=False)
    if res.returncode == 0:
        key = f"{vm.get('project','')}:{vm['zone']}:{vm['instance']}"
        _VM_INFO_CACHE.pop(key, None)
        _VM_INFO_CACHE_TIME.pop(key, None)
        print(">>> VM started successfully.")
        return True
    print(f">>> Error: Failed to start VM: {res.stderr}")
    return False


def stop_vm(vm: dict) -> bool:
    """Power off instance to pause compute billing."""
    print(f"\n>>> Stopping VM: {vm['instance']} ({vm['zone']})...")
    args = ["compute", "instances", "stop", vm["instance"], f"--zone={vm['zone']}"]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    res = run_gcloud(args, check=False)
    if res.returncode == 0:
        key = f"{vm.get('project','')}:{vm['zone']}:{vm['instance']}"
        _VM_INFO_CACHE.pop(key, None)
        _VM_INFO_CACHE_TIME.pop(key, None)
        print(">>> VM stopped successfully. Compute billing paused.")
        return True
    print(f">>> Error: Failed to stop VM: {res.stderr}")
    return False


def set_machine_type(vm: dict, new_type: str) -> bool:
    """Change instance machine type."""
    print(f"\n>>> Switching {vm['instance']} to machine type: {new_type}...")
    info = get_vm_info(vm)
    current_type = ""
    if info:
        current_type = info.get("machineType", "").rstrip("/").split("/")[-1]
    if current_type == new_type:
        print(f">>> VM is already using {new_type}.")
        return True
    was_running = is_vm_running(vm)
    if was_running:
        print(">>> Stopping VM before changing machine type...")
        if not stop_vm(vm):
            return False

    args = [
        "compute", "instances", "set-machine-type", vm["instance"],
        f"--machine-type={new_type}",
        f"--zone={vm['zone']}"
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    res = run_gcloud(args, check=False)
    if res.returncode == 0:
        print(">>> Machine type updated.")
        if was_running:
            print(">>> Restarting VM...")
            if not start_vm(vm):
                return False
        _VM_INFO_CACHE.pop(f"{vm.get('project','')}:{vm['zone']}:{vm['instance']}", None)
        _VM_INFO_CACHE_TIME.pop(f"{vm.get('project','')}:{vm['zone']}:{vm['instance']}", None)
        return True
    print(f">>> Error: Failed to set machine type: {res.stderr}")
    return False


def create_new_vm(name: str, zone: str, machine_type: str, data_disk: str = "", project: str = "") -> dict | None:
    """Create a new compute instance with 30GB free standard boot disk and attached data disk."""
    target_project = project or get_default_project()

    # 1. Create data disk if specified and doesn't exist
    if data_disk:
        disk_args = ["compute", "disks", "describe", data_disk, f"--zone={zone}"]
        if target_project:
            disk_args.append(f"--project={target_project}")
        res_d = run_gcloud(disk_args, check=False)
        if res_d.returncode != 0:
            print(f"\n>>> Creating 500GB pd-balanced data disk '{data_disk}' in {zone}...")
            c_args = [
                "compute", "disks", "create", data_disk,
                f"--zone={zone}",
                "--size=500GB",
                "--type=pd-balanced",
                f"--description=Build storage disk for {name}",
                "--quiet"
            ]
            if target_project:
                c_args.append(f"--project={target_project}")
            created_disk = run_gcloud(c_args, check=False)
            if created_disk.returncode != 0:
                print(f"\n>>> [ERROR] Failed to create data disk '{data_disk}': {created_disk.stderr}")
                return None

    # 2. Create the VM instance
    print(f"\n>>> Creating VM '{name}' in {zone} with 30GB pd-standard boot disk...")
    create_args = [
        "compute", "instances", "create", name,
        f"--zone={zone}",
        f"--machine-type={machine_type}",
        "--boot-disk-size=30GB",
        "--boot-disk-type=pd-standard",
        "--image-family=ubuntu-2204-lts",
        "--image-project=ubuntu-os-cloud",
        "--metadata=enable-oslogin=TRUE",
        "--scopes=cloud-platform",
        "--tags=rom-builder",
        "--quiet"
    ]
    if data_disk:
        create_args.extend(["--disk", f"name={data_disk},device-name=rom-general,mode=rw,boot=no"])
    if target_project:
        create_args.append(f"--project={target_project}")

    res_vm = run_gcloud(create_args, check=False)
    if res_vm.returncode != 0:
        print(f"\n>>> Error: Failed to create VM: {res_vm.stderr}")
        return None

    gen_disk_val = data_disk or "gcp-general-disk"
    save_active_vm(name, zone, target_project, gen_disk_val)
    return {
        "instance": name,
        "zone": zone,
        "project": target_project,
        "general_disk": gen_disk_val
    }


def delete_vm_instance(name: str, zone: str, delete_disks: list[str] = None, project: str = "") -> bool:
    """Delete an instance and optionally its secondary data disks."""
    target_project = project or get_default_project()
    print(f"\n>>> Deleting instance '{name}' in zone '{zone}'...")
    del_args = ["compute", "instances", "delete", name, f"--zone={zone}", "--quiet"]
    if target_project:
        del_args.append(f"--project={target_project}")
    res = run_gcloud(del_args, check=False)
    if res.returncode != 0:
        print(f">>> Error: Failed to delete VM: {res.stderr}")
        return False

    if delete_disks:
        for d in delete_disks:
            print(f">>> Deleting data disk '{d}' in zone '{zone}'...")
            dd_args = ["compute", "disks", "delete", d, f"--zone={zone}", "--quiet"]
            if target_project:
                dd_args.append(f"--project={target_project}")
            run_gcloud(dd_args, check=False)

    return True
