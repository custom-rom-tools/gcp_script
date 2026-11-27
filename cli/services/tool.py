"""
VM Tools Service (cli/services/tool.py)
Credential provisioning (.env, SourceForge key), git identity, timezone,
host-environment setup and idle-watchdog deployment.
"""

from __future__ import annotations

import base64
import random
import shlex
from pathlib import Path

from cli.services.vm import (
    ensure_vm_online, is_vm_running, vm_exec, vm_exec_tty, vm_push,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TZ_ALIASES = {
    "ist": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "asia/calcutta": "Asia/Kolkata",
    "utc": "UTC",
    "gmt": "UTC",
    "est": "America/New_York",
    "edt": "America/New_York",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
}


def upload_env_file(vm: dict, local_env_path: str = ".env") -> bool:
    """Securely upload local .env configuration to ~/.build_env on the VM."""
    path = Path(local_env_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        root_env = REPO_ROOT / ".env"
        if root_env.is_file():
            path = root_env
        else:
            print(">>> Error: No local .env file found.")
            return False

    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    print(f"\n>>> Uploading {path} to {vm['instance']}:~/.build_env...")
    res = vm_push(vm, [path], "~/.build_env")
    if res.returncode == 0:
        vm_exec(vm, "chmod 600 ~/.build_env", check=False, timeout=20)
        print(">>> Success: Credentials saved.")
        return True

    print(f">>> Error: Upload failed: {(res.stderr or res.stdout).strip()}")
    return False


def upload_sf_key(vm: dict, key_path: str = "~/.ssh/crave_sf") -> bool:
    """Upload the SourceForge SSH private key and test authentication."""
    path = Path(key_path).expanduser().resolve()
    if not path.is_file():
        print(f">>> Error: Private key not found at {path}.")
        return False

    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    print(f"\n>>> Uploading SSH key to {vm['instance']}:~/.ssh/crave_sf...")
    vm_exec(vm, "mkdir -p ~/.ssh && chmod 700 ~/.ssh", check=False, timeout=20)
    res = vm_push(vm, [path], "~/.ssh/crave_sf")
    if res.returncode != 0:
        print(f">>> Error: Failed to upload key: {(res.stderr or res.stdout).strip()}")
        return False

    vm_exec(vm, "chmod 600 ~/.ssh/crave_sf", check=False, timeout=20)
    print(">>> Key uploaded. Testing SourceForge authentication...")
    test = vm_exec(
        vm,
        "ssh -i ~/.ssh/crave_sf -o StrictHostKeyChecking=no "
        "-o BatchMode=yes -T nikhilpal2705@frs.sourceforge.net 2>&1 | head -n 5 || true",
        check=False,
        timeout=45,
    )
    output = (test.stdout or test.stderr or "").strip()
    if output:
        print(output)
    print(">>> SourceForge key uploaded and verified.")
    return True


def get_git_identity(vm: dict) -> tuple[str, str]:
    """Return the current global git name/email on the VM."""
    if not is_vm_running(vm):
        return "", ""
    res = vm_exec(
        vm,
        "printf '%s\\n' \"$(git config --global user.name 2>/dev/null || true)\" "
        "\"$(git config --global user.email 2>/dev/null || true)\"",
        check=False,
        timeout=20,
    )
    lines = (res.stdout or "").replace("\r", "").splitlines()
    name = lines[0].strip() if lines else ""
    email = lines[1].strip() if len(lines) > 1 else ""
    return name, email


def configure_dummy_git(vm: dict, name: str = "", email: str = "") -> bool:
    """Configure a global git identity so repo sync/commit never aborts."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    if not name:
        name = "Android Builder"
    if not email:
        rand_id = f"{random.randint(1000, 9999)}"
        email = f"builder-{rand_id}@android-build.org"

    print(f"\n>>> Setting git identity on {vm['instance']}...")
    cmd = (
        f"git config --global user.name {shlex.quote(name)} && "
        f"git config --global user.email {shlex.quote(email)} && "
        "git config --global color.ui auto"
    )
    res = vm_exec(vm, cmd, check=False, timeout=30)
    if res.returncode != 0:
        print((res.stderr or res.stdout or "").strip())
        return False
    print(f">>> Git identity configured: {name} <{email}>")
    return True


def configure_gitcookies(vm: dict, cookie_data: str) -> bool:
    """Install Google Source authentication cookies (~/.gitcookies) to avoid 429 rate limits."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    cookie_data = cookie_data.strip()
    if not cookie_data:
        return False

    setup_cmd = (
        "touch ~/.gitcookies && chmod 0600 ~/.gitcookies && "
        "git config --global http.cookiefile ~/.gitcookies"
    )
    res = vm_exec(vm, setup_cmd, check=False, timeout=30)
    if res.returncode != 0:
        print(">>> Error configuring git cookiefile setting.")
        return False

    if ">>" in cookie_data or "tee" in cookie_data:
        res = vm_exec(vm, cookie_data, check=False, timeout=30)
    else:
        b64 = base64.b64encode(cookie_data.encode()).decode()
        res = vm_exec(vm, f"printf '%s' '{b64}' | base64 -d >> ~/.gitcookies", check=False, timeout=30)

    if res.returncode == 0:
        print(f"\n>>> Google Source .gitcookies successfully configured on {vm['instance']}!")
        return True
    else:
        print(f"\n>>> Failed to save .gitcookies: {res.stderr}")
        return False


def get_vm_timezone(vm: dict) -> tuple[str, str]:
    """Return the current VM timezone and local date string."""
    if not is_vm_running(vm):
        return "Unknown", "Unknown"
    res = vm_exec(
        vm,
        "printf '%s\\n' \"$(timedatectl show -p Timezone --value 2>/dev/null "
        "|| cat /etc/timezone 2>/dev/null || echo Unknown)\" "
        "\"$(date '+%Y-%m-%d %H:%M:%S %Z (%z)')\"",
        check=False,
        timeout=20,
    )
    lines = (res.stdout or "").replace("\r", "").splitlines()
    tz = lines[0].strip() if lines else "Unknown"
    now = lines[1].strip() if len(lines) > 1 else "Unknown"
    return tz or "Unknown", now or "Unknown"


def normalize_timezone(tz: str) -> str:
    """Map common shorthand timezone names to IANA identifiers."""
    value = (tz or "").strip()
    return TZ_ALIASES.get(value.lower(), value)


def set_vm_timezone(vm: dict, tz: str = "Asia/Kolkata") -> bool:
    """Change the VM system timezone with a zoneinfo fallback."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False

    target = normalize_timezone(tz)
    if not target:
        print(">>> Error: No timezone specified.")
        return False

    print(f"\n>>> Setting VM timezone to {target}...")
    quoted = shlex.quote(target)
    cmd = (
        f"if sudo timedatectl set-timezone {quoted} 2>/dev/null; then echo SUCCESS; "
        f"elif [ -f '/usr/share/zoneinfo/'{quoted} ]; then "
        f"sudo ln -sf '/usr/share/zoneinfo/'{quoted} /etc/localtime && "
        f"printf '%s\\n' {quoted} | sudo tee /etc/timezone >/dev/null && echo SUCCESS; "
        "else echo INVALID; fi"
    )
    res = vm_exec(vm, cmd, check=False, timeout=45)
    output = (res.stdout or "").replace("\r", "").strip()
    if "INVALID" in output:
        print(f">>> Error: Timezone '{target}' was not recognized on the VM.")
        print("    Use an IANA name such as Asia/Kolkata, UTC or Europe/London.")
        return False
    if res.returncode != 0:
        print((res.stderr or res.stdout or "").strip())
        return False

    new_tz, new_time = get_vm_timezone(vm)
    print(f">>> Timezone changed: {new_tz} ({new_time})")
    return True


def get_watchdog_status(vm: dict) -> str:
    """Return the watchdog status plus systemd unit state."""
    if not is_vm_running(vm):
        return "VM is offline."
    res = vm_exec(
        vm,
        "if [ -f /run/idle_watchdog.status ]; then echo -n 'Current Status: '; "
        "cat /run/idle_watchdog.status; fi; echo; echo 'Service Info:'; "
        "systemctl status idle-shutdown.service --no-pager 2>&1 | head -n 12 || true",
        check=False,
        timeout=30,
    )
    return (res.stdout or "").replace("\r", "").strip()


def deploy_watchdog(vm: dict) -> bool:
    """Deploy and restart the 30-minute idle shutdown watchdog."""
    if not is_vm_running(vm):
        print(">>> Error: Target VM is offline.")
        return False
    from cli.services.runner import deploy_watchdog as _deploy

    return _deploy(vm)


def _github_fallback_cmd() -> str:
    base = "https://raw.githubusercontent.com/custom-rom-tools/gcp_script/main/scripts"
    return (
        "T=$(date +%s); "
        f"curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' "
        f"\"{base}/setup_host.sh?t=$T\" -o ~/setup_host.sh && "
        f"curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' "
        f"\"{base}/upload.py?t=$T\" -o ~/upload.py && "
        f"curl -sLf -H 'Cache-Control: no-cache, no-store' -H 'Pragma: no-cache' "
        f"\"{base}/idle_watchdog.sh?t=$T\" -o ~/idle_watchdog.sh"
    )


def setup_environment(vm: dict) -> bool:
    """Deploy host scripts and run setup_host.sh on the VM."""
    if not ensure_vm_online(vm):
        print(">>> Error: Target VM is offline and could not be started.")
        return False

    scripts = [REPO_ROOT / "scripts" / name for name in ("setup_host.sh", "upload.py", "idle_watchdog.sh")]
    existing = [p for p in scripts if p.is_file()]
    if len(existing) == len(scripts):
        print(">>> Deploying host scripts to VM...")
        res = vm_push(vm, existing, "~/")
        if res.returncode != 0:
            print(f">>> Error: Failed to deploy scripts: {(res.stderr or res.stdout).strip()}")
            return False
    else:
        print(">>> Host scripts not found locally; downloading them from GitHub...")
        res = vm_exec(vm, _github_fallback_cmd(), check=False, timeout=120)
        if res.returncode != 0:
            print(f">>> Error: Failed to download host scripts: {(res.stderr or res.stdout).strip()}")
            return False

    print("\n>>> Running setup_host.sh on VM (packages, repo, udev rules)...")
    rc = vm_exec_tty(vm, "chmod +x ~/setup_host.sh ~/upload.py ~/idle_watchdog.sh && bash ~/setup_host.sh")
    if rc != 0:
        print(">>> setup_host.sh returned a non-zero exit code.")
        return False
    print(">>> Host environment setup complete.")
    return True
