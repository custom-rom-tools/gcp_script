"""
VM Backend Service (cli/services/vm.py)
Google Cloud Compute lifecycle, discovery, authentication, persistence and a
low-latency SSH/SCP layer with connection multiplexing.

The interactive menu must feel instant: every routine that runs on the VM uses
an SSH ControlMaster connection, and GCP API results are cached for a few
seconds.  ``gcloud compute ssh`` is only a fallback for the first connection or
when the local Google key is missing.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable

from cli.base import restore_local_terminal

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ACTIVE_VM_FILE = Path.home() / ".gcp_active_vm"
SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"
SSH_SOCK = "/tmp/gcp_rom_ssh_%h_%p_%r"
CACHE_DIR = Path(tempfile.gettempdir())

MACHINE_TYPES = ["c2d-standard-16", "t2d-standard-16", "c2-standard-16"]
DEFAULT_MACHINE_TYPE = "c2d-standard-16"
DEFAULT_ZONE = "us-central1-a"
DEFAULT_GENERAL_DISK = "gcp-general-disk"

_VM_INFO_CACHE: dict[str, tuple[float, dict | None]] = {}
_AUTH_CACHE = {"value": None, "checked": 0.0}
_DISCOVER_CACHE: dict[str, tuple[float, list[dict]]] = {}
_PROJECT_CACHE = {"value": "", "checked": 0.0}


# ---------------------------------------------------------------------------
# Environment / local configuration
# ---------------------------------------------------------------------------
def load_local_env() -> None:
    """Load local .env or ~/.build_env into os.environ if present."""
    candidates = [
        Path.cwd() / ".env",
        REPO_ROOT / ".env",
        Path.home() / ".build_env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass


def load_vm_preset() -> dict:
    try:
        return json.loads((REPO_ROOT / "configs" / "vm_preset.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# gcloud helpers
# ---------------------------------------------------------------------------
def run_gcloud(args: list[str], check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a gcloud command and return its CompletedProcess."""
    return subprocess.run(
        ["gcloud"] + args,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def check_gcloud_auth(refresh: bool = False) -> str | None:
    """Return the active Google Cloud account email, or None."""
    now = time.time()
    ttl = 300.0 if _AUTH_CACHE["value"] else 15.0
    if not refresh and (now - _AUTH_CACHE["checked"] < ttl):
        return _AUTH_CACHE["value"]
    try:
        res = run_gcloud(["auth", "list", "--format=json"], check=False, timeout=30)
        accounts = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else []
        active = next((a.get("account") for a in accounts if a.get("status") == "ACTIVE"), None)
        if not active and accounts:
            active = accounts[0].get("account")
        _AUTH_CACHE["value"] = active
    except Exception:
        _AUTH_CACHE["value"] = None
    _AUTH_CACHE["checked"] = now
    return _AUTH_CACHE["value"]


def login_gcloud() -> bool:
    """Launch interactive ``gcloud auth login``."""
    print("\n>>> Launching Google Cloud authentication (gcloud auth login)...")
    try:
        res = subprocess.run(["gcloud", "auth", "login"])
    except FileNotFoundError:
        print(">>> Error: gcloud is not installed or not on PATH.")
        return False
    _AUTH_CACHE["checked"] = 0.0
    _PROJECT_CACHE["checked"] = 0.0
    if res.returncode == 0:
        account = check_gcloud_auth()
        print(f"\n>>> Successfully authenticated as: {account or 'unknown'}")
        return True
    print("\n>>> Error: Authentication failed or was cancelled.")
    return False


def ensure_gcloud_authenticated() -> bool:
    if check_gcloud_auth():
        return True
    print("\n" + "=" * 65)
    print(" ⚠️  No active Google Cloud account detected.")
    print("=" * 65)
    from cli.base import read_input

    choice = read_input("Sign in to Google Cloud now? [Y/n, default: Y]: ").strip().lower()
    if choice not in ("n", "no"):
        return login_gcloud()
    return False


def get_default_project() -> str:
    """Resolve the active GCP project ID from .env or gcloud config."""
    load_local_env()
    project = os.environ.get("GCP_PROJECT", "").strip()
    if project:
        return project
    now = time.time()
    if now - _PROJECT_CACHE["checked"] < 30:
        return _PROJECT_CACHE["value"]
    try:
        res = run_gcloud(["config", "get-value", "project"], check=False, timeout=15)
        value = res.stdout.strip()
        if value and "(unset)" not in value:
            _PROJECT_CACHE["value"] = value
    except Exception:
        pass
    _PROJECT_CACHE["checked"] = now
    return _PROJECT_CACHE["value"]


def _gcloud_scope(vm: dict) -> list[str]:
    args: list[str] = []
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    return args


# ---------------------------------------------------------------------------
# Per-VM cache files
# ---------------------------------------------------------------------------
def _vm_key(vm: dict) -> str:
    raw = f"{vm.get('project', '')}:{vm.get('zone', '')}:{vm.get('instance', '')}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def _ip_cache_path(vm: dict) -> Path:
    return CACHE_DIR / f"gcp_rom_vm_ip_{_vm_key(vm)}"


def _state_cache_path(vm: dict) -> Path:
    return CACHE_DIR / f"gcp_rom_vm_state_{_vm_key(vm)}"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def _write_json(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def invalidate_vm_cache(vm: dict) -> None:
    key = f"{vm.get('project', '')}:{vm.get('zone', '')}:{vm.get('instance', '')}"
    _VM_INFO_CACHE.pop(key, None)
    for path in (_ip_cache_path(vm), _state_cache_path(vm)):
        try:
            path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Discovery / active VM
# ---------------------------------------------------------------------------
def discover_instances(project: str = "", refresh: bool = False) -> list[dict]:
    """List all compute instances across zones (cached for 10 seconds)."""
    target_project = project or get_default_project()
    now = time.time()
    cached = _DISCOVER_CACHE.get(target_project)
    if not refresh and cached and now - cached[0] < 10:
        return cached[1]

    args = [
        "compute", "instances", "list",
        "--format=json(name,zone.basename(),status,machineType.basename(),"
        "networkInterfaces[0].accessConfigs[0].natIP,disks[].source.basename())",
    ]
    if target_project:
        args.append(f"--project={target_project}")
    try:
        res = run_gcloud(args, check=False, timeout=90)
        raw = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else []
    except Exception:
        return cached[1] if cached else []

    discovered: list[dict] = []
    for item in raw:
        name = item.get("name", "")
        zone = item.get("zone", "")
        if "/" in zone:
            zone = zone.rstrip("/").split("/")[-1]
        status = item.get("status", "")
        machine_type = item.get("machineType", "")
        if "/" in machine_type:
            machine_type = machine_type.rstrip("/").split("/")[-1]
        ip = "None"
        for iface in item.get("networkInterfaces", []) or []:
            for access in iface.get("accessConfigs", []) or []:
                if access.get("natIP"):
                    ip = access["natIP"]
                    break
            if ip != "None":
                break
        disks: list[str] = []
        for disk in item.get("disks", []) or []:
            src = disk.get("source", "") if isinstance(disk, dict) else str(disk)
            if src:
                disks.append(src.rstrip("/").split("/")[-1])
        general = next((d for d in disks if "general" in d.lower()), DEFAULT_GENERAL_DISK)
        discovered.append({
            "name": name,
            "instance": name,
            "zone": zone,
            "status": status,
            "machine_type": machine_type,
            "ip": ip,
            "disks": disks,
            "general_disk": general,
            "project": target_project,
        })

    discovered.sort(key=lambda item: item["name"])
    _DISCOVER_CACHE[target_project] = (now, discovered)
    return discovered


def get_active_vm() -> dict:
    """Resolve the active target VM from ~/.gcp_active_vm, discovery, or env fallback."""
    load_local_env()
    project = get_default_project()
    instances = discover_instances(project)

    saved: dict[str, str] = {}
    if ACTIVE_VM_FILE.is_file():
        try:
            for line in ACTIVE_VM_FILE.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip().lower()
                    value = value.strip().strip("'\"")
                    if key in ("instance", "zone", "project", "general_disk", "machine_type") and value:
                        saved[key] = value
        except OSError:
            pass

    # If user selected an active VM via Switch/Manage VMs, respect that selection
    if instances and saved.get("instance"):
        match = next((v for v in instances if v["name"] == saved["instance"]), None)
        if match:
            return {
                "instance": match["name"],
                "zone": match["zone"],
                "project": match.get("project") or project,
                "general_disk": match.get("general_disk") or DEFAULT_GENERAL_DISK,
                "machine_type": match.get("machine_type", ""),
            }

    # Fallback to first RUNNING instance, or first instance
    if instances:
        chosen = next((v for v in instances if v["status"] == "RUNNING"), instances[0])
        save_active_vm(chosen["name"], chosen["zone"], chosen["project"], chosen["general_disk"],
                       chosen.get("machine_type", ""))
        return {
            "instance": chosen["name"],
            "zone": chosen["zone"],
            "project": chosen["project"],
            "general_disk": chosen["general_disk"],
            "machine_type": chosen.get("machine_type", ""),
        }

    # No instances found in project: clean up stale cache
    if ACTIVE_VM_FILE.is_file():
        try:
            ACTIVE_VM_FILE.unlink()
        except OSError:
            pass

    return {
        "instance": "",
        "zone": DEFAULT_ZONE,
        "project": project,
        "general_disk": DEFAULT_GENERAL_DISK,
        "machine_type": DEFAULT_MACHINE_TYPE,
    }


def save_active_vm(instance: str, zone: str, project: str,
                   general_disk: str = DEFAULT_GENERAL_DISK,
                   machine_type: str = "") -> None:
    """Persist the active target VM to ~/.gcp_active_vm."""
    if not instance:
        if ACTIVE_VM_FILE.is_file():
            try:
                ACTIVE_VM_FILE.unlink()
            except OSError:
                pass
        return

    lines = [
        f"INSTANCE='{instance}'",
        f"ZONE='{zone}'",
        f"PROJECT='{project}'",
        f"GENERAL_DISK='{general_disk}'",
    ]
    if machine_type:
        lines.append(f"MACHINE_TYPE='{machine_type}'")
    try:
        ACTIVE_VM_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f">>> Warning: could not save active VM: {exc}")


# ---------------------------------------------------------------------------
# VM state / info
# ---------------------------------------------------------------------------
def check_ssh_port(ip: str, timeout: float = 1.0) -> bool:
    if not ip:
        return False
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except OSError:
        return False


def get_vm_state(vm: dict, refresh: bool = False) -> tuple[str, str]:
    """Return ``(status, external_ip)`` using a 15-second cache and fast SSH probe."""
    now = time.time()
    state_path = _state_cache_path(vm)
    if not refresh:
        cached = _read_json(state_path, None)
        if cached and now - float(cached.get("ts", 0)) < 15:
            return str(cached.get("status", "UNKNOWN")), str(cached.get("ip", ""))

    ip_path = _ip_cache_path(vm)
    try:
        cached_ip = ip_path.read_text(encoding="utf-8").strip()
    except OSError:
        cached_ip = ""
    if cached_ip and check_ssh_port(cached_ip):
        _write_json(state_path, {"status": "RUNNING", "ip": cached_ip, "ts": now})
        return "RUNNING", cached_ip

    args = [
        "compute", "instances", "describe", vm["instance"],
        f"--zone={vm['zone']}",
        "--format=csv[no-heading](status,networkInterfaces[0].accessConfigs[0].natIP)",
    ] + _gcloud_scope(vm)
    status, ip = "UNKNOWN", ""
    try:
        res = run_gcloud(args, check=False, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            first = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            status = parts[0] or "UNKNOWN"
            ip = parts[1] if len(parts) > 1 else ""
    except Exception:
        pass

    if status == "RUNNING" and ip:
        try:
            ip_path.write_text(ip, encoding="utf-8")
        except OSError:
            pass
    else:
        try:
            ip_path.unlink()
        except OSError:
            pass
    _write_json(state_path, {"status": status, "ip": ip, "ts": now})
    return status, ip


def is_vm_running(vm: dict) -> bool:
    return get_vm_state(vm)[0] == "RUNNING"


def get_vm_ip(vm: dict) -> str:
    status, ip = get_vm_state(vm)
    if status == "RUNNING" and ip:
        return ip
    try:
        cached_ip = _ip_cache_path(vm).read_text(encoding="utf-8").strip()
    except OSError:
        cached_ip = ""
    if cached_ip and check_ssh_port(cached_ip):
        return cached_ip
    return ""


def get_vm_info(vm: dict, refresh: bool = False) -> dict | None:
    """Fetch the JSON description of an instance (cached for 30 seconds)."""
    key = f"{vm.get('project', '')}:{vm.get('zone', '')}:{vm.get('instance', '')}"
    now = time.time()
    cached = _VM_INFO_CACHE.get(key)
    if not refresh and cached and now - cached[0] < 30:
        return cached[1]
    args = [
        "compute", "instances", "describe", vm["instance"],
        f"--zone={vm['zone']}",
        "--format=json",
    ] + _gcloud_scope(vm)
    info = None
    try:
        res = run_gcloud(args, check=False, timeout=45)
        if res.returncode == 0 and res.stdout:
            info = json.loads(res.stdout)
    except Exception:
        info = None
    _VM_INFO_CACHE[key] = (now, info)
    return info


def get_attached_disk_names(vm: dict) -> list[str]:
    info = get_vm_info(vm)
    if not info:
        return []
    names: list[str] = []
    for disk in info.get("disks", []) or []:
        src = disk.get("source", "")
        if src:
            names.append(src.rstrip("/").split("/")[-1])
    return names


def get_instance_machine_type(vm: dict) -> str:
    info = get_vm_info(vm)
    if info:
        return (info.get("machineType", "") or "").rstrip("/").split("/")[-1]
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# SSH / SCP
# ---------------------------------------------------------------------------
_SSH_USER_CACHE: dict[str, str] = {}


def _ssh_identity_available() -> bool:
    return SSH_KEY.is_file()


def _ssh_user(vm: dict | None = None) -> str:
    """Determine the remote SSH user on the GCP VM."""
    vm_name = vm.get("instance", "") if vm else ""
    if vm_name and vm_name in _SSH_USER_CACHE:
        return _SSH_USER_CACHE[vm_name]

    # 1. Try gcloud compute ssh --dry-run to discover the exact SSH user and host configuration
    if vm and vm.get("instance") and vm.get("zone"):
        try:
            res = run_gcloud(
                ["compute", "ssh", vm["instance"], f"--zone={vm['zone']}", "--dry-run"] + _gcloud_scope(vm),
                check=False, timeout=8,
            )
            if res.returncode == 0 and res.stdout:
                match = re.search(r"\b([a-zA-Z0-9_\.\-]+)@[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", res.stdout)
                if match:
                    detected = match.group(1)
                    _SSH_USER_CACHE[vm_name] = detected
                    return detected
        except Exception:
            pass

    # 2. Check active gcloud account
    account = check_gcloud_auth() or (vm.get("account") if vm else "")
    if account and "@" in account:
        detected = account.replace("@", "_").replace(".", "_")
        if vm_name:
            _SSH_USER_CACHE[vm_name] = detected
        return detected

    # 3. Fallback to local username
    fallback = os.environ.get("USER") or getpass.getuser()
    if vm_name:
        _SSH_USER_CACHE[vm_name] = fallback
    return fallback


def _ssh_opts(batch_mode: bool = True) -> list[str]:
    opts = [
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={SSH_SOCK}",
        "-o", "ControlPersist=15m",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=3",
        "-i", str(SSH_KEY),
    ]
    if batch_mode:
        opts += ["-o", "BatchMode=yes"]
    return opts


def vm_exec(vm: dict, cmd_str: str, check: bool = True,
            timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a non-interactive command on the VM with automatic gcloud fallback."""
    ip = get_vm_ip(vm)
    user = _ssh_user(vm)
    if ip and _ssh_identity_available():
        cmd = ["ssh", *_ssh_opts(), f"{user}@{ip}", cmd_str]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
            if res.returncode == 0:
                return res
            err_out = (res.stderr or "").lower()
            if "permission denied" in err_out or "connection refused" in err_out or res.returncode == 255:
                pass  # Fall through to gcloud compute ssh
            else:
                if check and res.returncode != 0:
                    raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)
                return res
        except subprocess.TimeoutExpired:
            if not check:
                pass
            else:
                raise

    gcloud_cmd = [
        "gcloud", "compute", "ssh", vm["instance"],
        f"--zone={vm['zone']}",
        f"--command={cmd_str}",
    ] + _gcloud_scope(vm)
    try:
        return subprocess.run(gcloud_cmd, capture_output=True, text=True, check=check, timeout=timeout)
    except subprocess.TimeoutExpired:
        if not check:
            return subprocess.CompletedProcess(args=gcloud_cmd, returncode=124, stdout="",
                                              stderr=f"Command timed out after {timeout}s")
        raise


def vm_exec_tty(vm: dict, cmd_str: str = "bash -l") -> int:
    """Run an interactive PTY command on the VM and restore the local terminal."""
    ip = get_vm_ip(vm)
    user = _ssh_user(vm)
    try:
        if ip and _ssh_identity_available():
            cmd = ["ssh", "-t", *_ssh_opts(batch_mode=False), f"{user}@{ip}", cmd_str]
            res = subprocess.run(cmd)
            if res.returncode == 0:
                return 0
            if res.returncode == 255:
                pass  # Fall through to gcloud compute ssh
            else:
                return res.returncode
        gcloud_cmd = [
            "gcloud", "compute", "ssh", vm["instance"],
            f"--zone={vm['zone']}",
        ] + _gcloud_scope(vm) + ["--", "-t", cmd_str]
        res = subprocess.run(gcloud_cmd)
        return res.returncode
    finally:
        restore_local_terminal()


def vm_push(vm: dict, local_paths: Iterable[str | Path], remote_dir: str,
            recursive: bool = False) -> subprocess.CompletedProcess:
    """Copy local file(s) to the VM with automatic gcloud fallback."""
    paths = [str(p) for p in local_paths]
    ip = get_vm_ip(vm)
    user = _ssh_user(vm)
    if ip and _ssh_identity_available():
        cmd = ["scp", *([] if not recursive else ["-r"]), *_ssh_opts(),
               *paths, f"{user}@{ip}:{remote_dir}"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return res
    gcloud_cmd = ["gcloud", "compute", "scp"]
    if recursive:
        gcloud_cmd.append("--recurse")
    gcloud_cmd += paths + [f"{vm['instance']}:{remote_dir}"] + _gcloud_scope(vm) + [f"--zone={vm['zone']}"]
    return subprocess.run(gcloud_cmd, capture_output=True, text=True, check=False)


def vm_pull(vm: dict, remote_paths: Iterable[str], local_dir: str,
            recursive: bool = False) -> subprocess.CompletedProcess:
    """Copy remote file(s) (globs allowed) to a local directory with automatic gcloud fallback."""
    paths = list(remote_paths)
    ip = get_vm_ip(vm)
    user = _ssh_user(vm)
    if ip and _ssh_identity_available():
        sources = [f"{user}@{ip}:{p}" for p in paths]
        cmd = ["scp", *([] if not recursive else ["-r"]), *_ssh_opts(), *sources, local_dir]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return res
    sources = [f"{vm['instance']}:{p}" for p in paths]
    gcloud_cmd = ["gcloud", "compute", "scp"]
    if recursive:
        gcloud_cmd.append("--recurse")
    gcloud_cmd += sources + [local_dir, f"--zone={vm['zone']}"] + _gcloud_scope(vm)
    return subprocess.run(gcloud_cmd, capture_output=True, text=True, check=False)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def vm_scp(vm: dict, src: str | Path, dst: str, recursive: bool = False) -> subprocess.CompletedProcess:
    """Compatibility wrapper: copy one local path to a remote destination."""
    return vm_push(vm, [src], dst, recursive=recursive)


# ---------------------------------------------------------------------------
# SSH readiness
# ---------------------------------------------------------------------------
def diagnose_ssh_failure(vm: dict, elapsed: int) -> bool:
    status, ip = get_vm_state(vm)
    print("\n" + "=" * 65)
    print(f">>> SSH did not become ready after {elapsed}s")
    print(f"    Instance state : {status or 'UNKNOWN'}")
    print(f"    Instance IP    : {ip or '(none)'}")
    if status != "RUNNING":
        print("    The VM is not RUNNING, so sshd cannot accept connections.")
        print("=" * 65)
        return False
    if not ip:
        print("    The VM has no external IP yet. Wait a few seconds and retry.")
        print("=" * 65)
        return False
    if not check_ssh_port(ip):
        print("    TCP port 22    : closed")
        print("    sshd is not listening yet (still booting) or port 22 is blocked.")
        print("    Inspect the boot log:")
        print(f"      gcloud compute instances get-serial-port-output {vm['instance']} "
              f"--zone={vm['zone']} --project={vm.get('project', '')} | tail -40")
        print("=" * 65)
        return False

    probe = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "LogLevel=ERROR", "-i", str(SSH_KEY), f"{_ssh_user(vm)}@{ip}", "true"],
        capture_output=True, text=True,
    )
    print(f"    TCP port 22    : open")
    print(f"    SSH result     : {(probe.stderr or probe.stdout or 'unknown').strip()} (exit {probe.returncode})")
    if "Permission denied" in (probe.stderr or ""):
        print("    The guest rejected the SSH key.")
        print("    Most common cause: the boot disk is full, so cloud-init cannot")
        print("    write ~/.ssh/authorized_keys (the metadata script fails too).")
    print("=" * 65)
    return False


def wait_for_ssh(vm: dict, timeout: int = 180) -> bool:
    """Wait until the VM accepts SSH, reporting progress instead of hanging."""
    print(f">>> Waiting for SSH service to become ready (up to {timeout}s)...")
    invalidate_vm_cache(vm)
    start = time.monotonic()
    while True:
        elapsed = int(time.monotonic() - start)
        status, ip = get_vm_state(vm)
        if status == "RUNNING" and ip:
            try:
                probe = vm_exec(vm, "true", check=False, timeout=8)
                if probe.returncode == 0:
                    print(f"\n>>> SSH connection established ({elapsed}s).")
                    _post_ssh_health_check(vm)
                    return True
            except subprocess.TimeoutExpired:
                pass
        if elapsed >= timeout:
            print()
            return diagnose_ssh_failure(vm, timeout)
        port_state = "open" if ip and check_ssh_port(ip) else "waiting"
        print(f"\r    ... {elapsed:3d}s  VM:{status or 'UNKNOWN':<10}  SSH:{port_state}", end="", flush=True)
        time.sleep(2)


def _post_ssh_health_check(vm: dict) -> None:
    """Best-effort watchdog enable and boot-disk space warning."""
    try:
        vm_exec(
            vm,
            "sudo systemctl is-active --quiet idle-shutdown.service || "
            "(sudo systemctl daemon-reload && sudo systemctl enable --now idle-shutdown.service "
            "&& sudo systemctl restart idle-shutdown.service) 2>/dev/null || true",
            check=False,
            timeout=20,
        )
        res = vm_exec(vm, "df -Pm / | awk 'NR==2{print $4}'", check=False, timeout=10)
        digits = re.sub(r"[^0-9]", "", res.stdout or "")
        if digits and int(digits) < 4096:
            print(f">>> WARNING: only {digits}MB free on the VM boot disk.")
            print("    Run 'ccache -C' or grow the boot disk before building.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Power lifecycle
# ---------------------------------------------------------------------------
def start_vm_with_failover(vm: dict) -> bool:
    """Power on the VM, cascading through machine types when capacity is exhausted."""
    if is_vm_running(vm):
        return True

    info = get_vm_info(vm, refresh=True)
    status = (info or {}).get("status", "UNKNOWN")
    current_mt = ((info or {}).get("machineType", "") or "").rstrip("/").split("/")[-1]
    if status == "RUNNING":
        print(f">>> VM {vm['instance']} is already RUNNING in {vm['zone']}.")
        return True
    current_mt = current_mt or DEFAULT_MACHINE_TYPE

    print(f">>> VM {vm['instance']} is currently {status} ({current_mt}). Powering on...")
    args = ["compute", "instances", "start", vm["instance"], f"--zone={vm['zone']}"] + _gcloud_scope(vm)
    res = run_gcloud(args, check=False)
    if res.returncode == 0:
        print(f">>> VM {vm['instance']} started successfully with {current_mt}!")
        invalidate_vm_cache(vm)
        return wait_for_ssh(vm)

    err = f"{res.stdout}\n{res.stderr}"
    print(f">>> Start failed with {current_mt}:\n{err.strip()}\n")
    exhausted = any(token in err for token in (
        "RESOURCE_POOL_EXHAUSTED", "ZONE_RESOURCE_POOL_EXHAUSTED",
        "not have enough resources", "does not have enough resources",
    ))
    if exhausted:
        print(f">>> Capacity exhausted for {current_mt} in {vm['zone']}.")
        print(">>> Cascading through 16-core machine types (c2d -> t2d -> c2)...")
        for machine_type in MACHINE_TYPES:
            if machine_type == current_mt:
                continue
            print(f"\n>>> Switching in-place to candidate: {machine_type}...")
            run_gcloud(
                ["compute", "instances", "set-machine-type", vm["instance"],
                 f"--machine-type={machine_type}", f"--zone={vm['zone']}"] + _gcloud_scope(vm) + ["--quiet"],
                check=False,
            )
            print(f">>> Attempting to power on with {machine_type}...")
            retry = run_gcloud(
                ["compute", "instances", "start", vm["instance"], f"--zone={vm['zone']}"]
                + _gcloud_scope(vm),
                check=False,
            )
            if retry.returncode == 0:
                print(f">>> Resources acquired! VM {vm['instance']} is RUNNING with {machine_type}!")
                invalidate_vm_cache(vm)
                return wait_for_ssh(vm)
            print(f">>> {machine_type} also out of capacity in {vm['zone']}.")
        print(f">>> All 16-vCPU 64GB machine types are temporarily exhausted in {vm['zone']}.")
        return False

    print(">>> Retrying start once...")
    run_gcloud(["compute", "instances", "start", vm["instance"], f"--zone={vm['zone']}"]
               + _gcloud_scope(vm), check=False)
    invalidate_vm_cache(vm)
    return wait_for_ssh(vm)


def ensure_vm_online(vm: dict) -> bool:
    if is_vm_running(vm):
        return True
    return start_vm_with_failover(vm)


def ensure_running(vm: dict, target_disk: str, mount_point: str) -> bool:
    """Make sure the VM is online and the requested build disk is mounted."""
    from cli.services.disk import (
        get_disk_meta, get_attached_device_name, ensure_disk_attached,
        mount_disk_on_vm,
    )

    meta = get_disk_meta(target_disk)
    if is_vm_running(vm):
        actual_dev = get_attached_device_name(vm, target_disk, meta["device_name"])
        check_cmd = (
            f"DEV=''\n"
            f"for c in '/dev/disk/by-id/google-{actual_dev}' "
            f"'/dev/disk/by-id/google-{meta['device_name']}' "
            f"'/dev/disk/by-id/google-{target_disk}'; do\n"
            f"  if [ -b \"$c\" ]; then DEV=\"$c\"; break; fi\n"
            f"done\n"
            f"[ -z \"$DEV\" ] && echo 'NO_DEV' && exit 0\n"
            f"REAL_DEV=$(readlink -f \"$DEV\")\n"
            f"CUR_MPS=$(findmnt -rn -S \"$REAL_DEV\" -o TARGET 2>/dev/null || true)\n"
            f"if [ \"$CUR_MPS\" = '{mount_point}' ]; then echo 'MOUNTED_EXACT'; "
            f"else echo 'NEEDS_REMOUNT'; fi"
        )
        try:
            res = vm_exec(vm, check_cmd, check=False, timeout=30)
            state = (res.stdout or "").strip()
        except subprocess.TimeoutExpired:
            state = "NEED_FULL"
        if state == "MOUNTED_EXACT":
            # Auto-activate existing swapfile in background if inactive
            _sw_cmd = (
                f"( for sw in {shlex.quote(mount_point)}/swapfile /swapfile; do "
                "if [ -f \"$sw\" ] && ! grep -q \"$sw\" /proc/swaps 2>/dev/null; then "
                "sudo chmod 600 \"$sw\" 2>/dev/null || true; "
                "sudo swapon \"$sw\" 2>/dev/null || { sudo mkswap \"$sw\" 2>/dev/null && sudo swapon \"$sw\" 2>/dev/null; } || true; "
                "fi; done ) >/dev/null 2>&1 &"
            )
            vm_exec(vm, _sw_cmd, check=False, timeout=10)
            return True
        if state == "NEEDS_REMOUNT":
            print(f">>> Mounting {target_disk} at {mount_point} (unmounting previous mount if any)...")
            return mount_disk_on_vm(vm, target_disk, mount_point)

    print(">>> Initializing VM and storage for build...")
    if not ensure_disk_attached(vm, target_disk):
        return False
    if not start_vm_with_failover(vm):
        return False
    return mount_disk_on_vm(vm, target_disk, mount_point)


def start_vm(vm: dict) -> bool:
    """Interactive start flow used by the VM menu."""
    from cli.base import clear_screen
    clear_screen()
    print("=" * 60)
    print("             GCP INSTANCE & DISK STARTUP")
    print("=" * 60)
    print(">>> Step 1/3: Checking GCP Instance State...")
    status, ip = get_vm_state(vm)
    if status == "RUNNING":
        print(f" ✔ VM '{vm['instance']}' is already powered ON.")
        if ip:
            print(f"    External IP: {ip}")
    else:
        print(f">>> VM '{vm['instance']}' is currently powered down.")
        if not start_vm_with_failover(vm):
            print(f" ✖ Could not power on VM '{vm['instance']}' (no capacity in {vm['zone']}).")
            return False
        status, ip = get_vm_state(vm)

    print("\n>>> Step 2/3: Inspecting Attached Secondary Disks...")
    attached = get_attached_disk_names(vm)
    secondary = [d for d in attached if d not in (vm["instance"], "gcp-boot-ccache-disk")]
    if secondary:
        print(f" ✔ Attached secondary disk(s): {', '.join(secondary)}")
        print("    (Specific disks auto-mount at boot; general disk mounts at build time.)")
    else:
        print(" ℹ No secondary ROM disk currently attached (use Disk Manager or start a build).")

    print("\n>>> Step 3/3: Verifying SSH access, boot mounts & RAM on VM...")
    if not wait_for_ssh(vm):
        ip = get_vm_ip(vm)
        print("=" * 60)
        print(f" ✖ VM '{vm['instance']}' is RUNNING but SSH is NOT ready!")
        print(f"    External IP: {ip or 'unknown'} | Status: RUNNING | SSH: FAILED")
        print("    sshd may still be booting, or the firewall/SSH key is not ready.")
        print(f"    gcloud compute ssh {vm['instance']} --zone={vm['zone']}")
        print("=" * 60)
        return False

    ip = get_vm_ip(vm)
    res = vm_exec(vm, 'for d in /mnt/rom /mnt/recovery; do mountpoint -q "$d" && echo "$d"; done',
                  check=False, timeout=15)
    mounts = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
    print("")
    print("=" * 60)
    print(f" ✅ VM '{vm['instance']}' is ONLINE and READY!")
    print(f"    External IP: {ip} | Status: RUNNING | SSH: OK")
    if mounts:
        print(f"    Mounted build storage: {' '.join(mounts)}")
    else:
        print("    ℹ No build disk mounted yet.")
        print("    It is mounted automatically when a build starts (Disk Manager / build menu).")
    print("=" * 60)
    print("")
    res = vm_exec(vm, "df -h / /mnt/rom /mnt/recovery 2>/dev/null; free -h",
                  check=False, timeout=15)
    if res.stdout:
        print(res.stdout)
    return True


def stop_vm(vm: dict) -> bool:
    """Stop the instance to pause compute billing."""
    from cli.base import clear_screen
    clear_screen()
    print("=" * 60)
    print("                 STOPPING GCP INSTANCE")
    print("=" * 60)
    status, _ = get_vm_state(vm)
    if status != "RUNNING":
        print(f" ✔ VM '{vm['instance']}' is already STOPPED ({status}).")
        print("    Compute billing is paused. No action needed.")
        return True

    print(f">>> Stopping VM '{vm['instance']}' to pause compute charges (~$0.726/hr)...")
    res = run_gcloud(
        ["compute", "instances", "stop", vm["instance"], f"--zone={vm['zone']}"] + _gcloud_scope(vm),
        check=False,
    )
    invalidate_vm_cache(vm)
    if res.returncode == 0:
        print("=" * 60)
        print(f" ✅ VM '{vm['instance']}' has been STOPPED successfully!")
        print("    Compute billing has been PAUSED immediately.")
        print("=" * 60)
        return True
    print(f" ✖ Failed to stop VM: {(res.stderr or res.stdout).strip()}")
    return False


def restart_vm(vm: dict) -> bool:
    """Restart the GCP instance (graceful OS reboot with gcloud reset fallback)."""
    from cli.base import clear_screen
    clear_screen()
    print("=" * 60)
    print("                 RESTARTING GCP INSTANCE")
    print("=" * 60)
    status, ip = get_vm_state(vm)
    if status != "RUNNING":
        print(f">>> VM '{vm['instance']}' is currently powered OFF ({status}).")
        print(">>> Starting VM...")
        return start_vm(vm)

    print(f">>> VM '{vm['instance']}' is currently RUNNING (IP: {ip}).")
    print(">>> Sending reboot signal to VM...")

    # Attempt graceful OS reboot via SSH first
    rebooted = False
    try:
        res = vm_exec(vm, "sudo systemctl reboot || sudo reboot", check=False, timeout=10)
        if res.returncode == 0:
            rebooted = True
    except Exception:
        pass

    if not rebooted:
        print(">>> SSH reboot did not respond; performing GCP instance reset...")
        run_gcloud(
            ["compute", "instances", "reset", vm["instance"], f"--zone={vm['zone']}"] + _gcloud_scope(vm),
            check=False,
        )

    invalidate_vm_cache(vm)
    print(">>> Reboot triggered. Waiting for VM to reboot and SSH to become ready...")
    time.sleep(6)

    if not wait_for_ssh(vm, timeout=120):
        print("=" * 60)
        print(f" ✖ VM '{vm['instance']}' rebooted but SSH is not reachable yet.")
        print("=" * 60)
        return False

    new_ip = get_vm_ip(vm)
    print("=" * 60)
    print(f" ✅ VM '{vm['instance']}' has been RESTARTED successfully!")
    print(f"    External IP: {new_ip} | Status: RUNNING | SSH: OK")
    print("=" * 60)
    return True


def set_machine_type(vm: dict, new_type: str) -> bool:
    """Change the instance machine type, preserving its power state."""
    print(f"\n>>> Switching {vm['instance']} to machine type: {new_type}...")
    current_type = get_instance_machine_type(vm)
    if current_type == new_type:
        print(f">>> VM is already using {new_type}.")
        return True
    was_running = is_vm_running(vm)
    if was_running:
        print(">>> Stopping VM before changing machine type...")
        res = run_gcloud(
            ["compute", "instances", "stop", vm["instance"], f"--zone={vm['zone']}"] + _gcloud_scope(vm),
            check=False,
        )
        if res.returncode != 0:
            print(f">>> Error: failed to stop VM: {(res.stderr or res.stdout).strip()}")
            return False
        invalidate_vm_cache(vm)

    res = run_gcloud(
        ["compute", "instances", "set-machine-type", vm["instance"],
         f"--machine-type={new_type}", f"--zone={vm['zone']}"] + _gcloud_scope(vm) + ["--quiet"],
        check=False,
    )
    if res.returncode != 0:
        print(f">>> Error: Failed to set machine type: {(res.stderr or res.stdout).strip()}")
        if was_running:
            start_vm_with_failover(vm)
        return False
    print(">>> Machine type updated.")
    invalidate_vm_cache(vm)
    if was_running:
        print(">>> Restarting VM...")
        return start_vm_with_failover(vm)
    return True


# ---------------------------------------------------------------------------
# VM creation / deletion
# ---------------------------------------------------------------------------
def create_new_vm(name: str, zone: str, machine_type: str, data_disk: str = "",
                  project: str = "") -> dict | None:
    """Create a VM with a 30GB standard boot disk and an optional data disk."""
    target_project = project or get_default_project()

    if data_disk:
        disk_args = ["compute", "disks", "describe", data_disk, f"--zone={zone}"]
        if target_project:
            disk_args.append(f"--project={target_project}")
        existing = run_gcloud(disk_args, check=False)
        if existing.returncode != 0:
            print(f"\n>>> Creating 500GB pd-balanced data disk '{data_disk}' in {zone}...")
            create_args = [
                "compute", "disks", "create", data_disk,
                f"--zone={zone}",
                "--size=500GB",
                "--type=pd-balanced",
                f"--description=Build storage disk for {name}",
                "--quiet",
            ]
            if target_project:
                create_args.append(f"--project={target_project}")
            created = run_gcloud(create_args, check=False)
            if created.returncode != 0:
                print(f"\n>>> [ERROR] Failed to create data disk '{data_disk}': "
                      f"{(created.stderr or created.stdout).strip()}")
                return None

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
        "--quiet",
    ]
    if data_disk:
        create_args.extend(["--disk", f"name={data_disk},device-name=rom-general,mode=rw,boot=no"])
    if target_project:
        create_args.append(f"--project={target_project}")

    res = run_gcloud(create_args, check=False)
    if res.returncode != 0:
        print(f"\n>>> Error: Failed to create VM: {(res.stderr or res.stdout).strip()}")
        return None

    general_disk = data_disk or DEFAULT_GENERAL_DISK
    save_active_vm(name, zone, target_project, general_disk, machine_type)
    discover_instances(target_project, refresh=True)
    return {
        "instance": name,
        "zone": zone,
        "project": target_project,
        "general_disk": general_disk,
        "machine_type": machine_type,
    }


def get_region_from_zone(zone: str) -> str:
    """Extract region from zone (e.g. 'us-central1-a' -> 'us-central1')."""
    z = zone.rstrip("/").split("/")[-1] if "/" in zone else zone
    if "-" in z:
        return z.rsplit("-", 1)[0]
    return z


def get_vm_boot_disk(name: str, zone: str, project: str = "") -> str:
    """Find the boot disk name for a VM."""
    target_project = project or get_default_project()
    args = ["compute", "instances", "describe", name, f"--zone={zone}", "--format=json(disks)"]
    if target_project:
        args.append(f"--project={target_project}")
    try:
        res = run_gcloud(args, check=False, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            for d in data.get("disks", []):
                if d.get("boot", False):
                    src = d.get("source", "")
                    if src:
                        return src.rstrip("/").split("/")[-1]
    except Exception:
        pass
    return name


def list_disks_in_region(region: str, project: str = "") -> list[dict]:
    """List all persistent disks in a given region (attached and unattached)."""
    target_project = project or get_default_project()
    args = [
        "compute", "disks", "list",
        "--format=json(name,zone.basename(),region.basename(),sizeGb,type.basename(),users[].basename().list(),status)"
    ]
    if target_project:
        args.append(f"--project={target_project}")
    try:
        res = run_gcloud(args, check=False, timeout=60)
        if res.returncode == 0 and res.stdout.strip():
            raw = json.loads(res.stdout)
            result = []
            for d in raw:
                d_zone = d.get("zone", "")
                if "/" in d_zone:
                    d_zone = d_zone.rstrip("/").split("/")[-1]
                d_reg = d.get("region", "")
                if "/" in d_reg:
                    d_reg = d_reg.rstrip("/").split("/")[-1]
                calc_reg = get_region_from_zone(d_zone) if d_zone else d_reg
                if calc_reg == region or d_reg == region:
                    raw_users = d.get("users", []) or []
                    users = [u.rstrip("/").split("/")[-1] for u in raw_users if u]
                    result.append({
                        "name": d.get("name", ""),
                        "zone": d_zone,
                        "region": d_reg or calc_reg,
                        "sizeGb": d.get("sizeGb", ""),
                        "type": d.get("type", "").rstrip("/").split("/")[-1],
                        "users": users,
                        "status": d.get("status", ""),
                    })
            return result
    except Exception:
        pass
    return []


def delete_vm_instance(name: str, zone: str,
                       boot_disk: str = "",
                       delete_disks: list[dict | str] | None = None,
                       project: str = "") -> bool:
    """Delete an instance, its boot disk, and optionally secondary data/regional disks."""
    target_project = project or get_default_project()
    proj_args = [f"--project={target_project}"] if target_project else []

    print(f"\n>>> Deleting instance '{name}' in zone '{zone}' (along with boot disk)...")
    # Using --delete-disks=boot ensures GCP deletes the attached boot disk automatically
    args = ["compute", "instances", "delete", name, f"--zone={zone}", "--delete-disks=boot", "--quiet"] + proj_args
    res = run_gcloud(args, check=False)
    if res.returncode != 0:
        print(f">>> Error: Failed to delete VM: {(res.stderr or res.stdout).strip()}")
        return False

    print(f" ✔ VM instance '{name}' deleted.")

    # Explicitly check if the boot disk is still present in GCP disks, and delete if found
    target_boot = boot_disk or name
    chk_boot = run_gcloud(["compute", "disks", "describe", target_boot, f"--zone={zone}"] + proj_args, check=False)
    if chk_boot.returncode == 0:
        print(f">>> Deleting boot disk '{target_boot}' in zone '{zone}'...")
        res_b = run_gcloud(["compute", "disks", "delete", target_boot, f"--zone={zone}", "--quiet"] + proj_args, check=False)
        if res_b.returncode == 0:
            print(f" ✔ Boot disk '{target_boot}' deleted.")
        else:
            print(f" ✖ Could not delete boot disk '{target_boot}': {(res_b.stderr or res_b.stdout).strip()}")
    else:
        print(f" ✔ Boot disk '{target_boot}' was deleted with the instance.")

    # Delete any additional data or regional disks confirmed by the user
    for item in delete_disks or []:
        if isinstance(item, dict):
            d_name = item.get("name", "")
            d_zone = item.get("zone") or zone
            d_region = item.get("region", "")
            users = item.get("users", [])
        else:
            d_name = str(item)
            d_zone = zone
            d_region = ""
            users = []

        if not d_name or d_name == target_boot:
            continue

        print(f">>> Deleting disk '{d_name}'...")
        # Detach if attached to any other running/stopped instance
        for u in users:
            if u != name:
                print(f">>> Detaching '{d_name}' from '{u}'...")
                run_gcloud(["compute", "instances", "detach-disk", u, f"--disk={d_name}", f"--zone={d_zone}", "--quiet"] + proj_args, check=False)

        del_args = ["compute", "disks", "delete", d_name, "--quiet"] + proj_args
        if d_zone:
            del_args.append(f"--zone={d_zone}")
        elif d_region:
            del_args.append(f"--region={d_region}")

        res_d = run_gcloud(del_args, check=False)
        if res_d.returncode == 0:
            print(f" ✔ Disk '{d_name}' deleted.")
        else:
            print(f" ✖ Could not delete disk '{d_name}': {(res_d.stderr or res_d.stdout).strip()}")

    # Clear caches
    _VM_INFO_CACHE.clear()
    discover_instances(target_project, refresh=True)
    return True

