"""
Artifact Download Service (cli/services/download.py)
Pulls ROM zips, recovery images, kernel boot images and build logs from the
mounted GCP data disks into ``~/Downloads`` using the multiplexed SCP path.
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path

from cli.services.vm import is_vm_running, vm_exec, vm_pull
from cli.services.monitor import get_active_log_path

MOUNTS = ("/mnt/a16", "/mnt/a17", "/mnt/recovery")
DOWNLOAD_DIR = Path.home() / "Downloads"

PATTERNS = {
    "rom": ("*.zip",),
    "recovery": ("*OrangeFox*", "*twrp*", "*TWRP*", "recovery.img"),
    "kernel": ("boot.img",),
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _find_artifacts(vm: dict, patterns: tuple[str, ...]) -> list[str]:
    """Find matching files on every mounted build disk."""
    name_expr = " -o ".join(f"-name {shlex.quote(p)}" for p in patterns)
    files: list[str] = []
    for mount in MOUNTS:
        cmd = (
            f"mountpoint -q {shlex.quote(mount)} || exit 0; "
            f"find {shlex.quote(mount)} -maxdepth 7 -type f \\( {name_expr} \\) "
            f"2>/dev/null"
        )
        res = vm_exec(vm, cmd, check=False, timeout=90)
        for line in (res.stdout or "").replace("\r", "").splitlines():
            path = line.strip()
            if path and path not in files:
                files.append(path)
    return files


def _download_one(vm: dict, remote: str, dest_dir: Path) -> bool:
    """Download a single remote file, avoiding silent overwrites."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(remote).name
    local = dest_dir / name
    if local.exists():
        mount_label = "unknown"
        for mount in MOUNTS:
            if remote.startswith(mount + "/"):
                mount_label = mount.rsplit("/", 1)[-1]
                break
        local = dest_dir / f"{mount_label}_{name}"

    print(f">>> Downloading {remote} -> {local} ...", end="", flush=True)
    res = vm_pull(vm, [remote], str(dest_dir))
    produced = dest_dir / name
    if res.returncode != 0 or not produced.exists():
        print(" FAILED")
        message = (res.stderr or res.stdout or "").strip()
        if message:
            print(f"    {message.splitlines()[-1]}")
        return False

    if local != produced:
        try:
            produced.replace(local)
        except OSError:
            pass
    size = local.stat().st_size if local.exists() else produced.stat().st_size
    print(f" Done ({_human_size(size)}).")
    return True


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _download_log(vm: dict, log_path: str = "") -> bool:
    remote = log_path or get_active_log_path(vm)
    if not remote:
        print(">>> No active build.log found on VM (/mnt/a16, /mnt/a17, or /mnt/recovery).")
        return False

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    label = Path(remote).parent.name or "build"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    local = DOWNLOAD_DIR / f"build_{label}_{stamp}.log"

    size_res = vm_exec(vm, f"stat -c %s {shlex.quote(remote)} 2>/dev/null || echo 0",
                       check=False, timeout=20)
    try:
        remote_size = int((size_res.stdout or "0").replace("\r", "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        remote_size = 0

    print(f">>> Downloading {remote} -> {local} ...", end="", flush=True)
    res = vm_pull(vm, [remote], str(DOWNLOAD_DIR))
    produced = DOWNLOAD_DIR / Path(remote).name
    if res.returncode != 0 or not produced.exists():
        print(" FAILED")
        message = (res.stderr or res.stdout or "").strip()
        if message:
            print(f"    {message.splitlines()[-1]}")
        return False
    if produced != local:
        try:
            produced.replace(local)
        except OSError:
            pass
    print(" Done.")

    local_size = local.stat().st_size
    print()
    print("=" * 60)
    print(" Full build log downloaded successfully!")
    print(f"    Source   : {remote} ({remote_size} bytes)")
    print(f"    Saved to : {local} ({_human_size(local_size)}, {local_size} bytes)")
    if remote_size and remote_size != local_size:
        print("    WARNING: byte count differs - the log may still be growing.")

    clean = local.with_name(local.stem + "_clean.log")
    try:
        raw = local.read_text(encoding="utf-8", errors="replace")
        raw = ANSI_RE.sub("", raw).replace("\r", "\n")
        clean.write_text(raw, encoding="utf-8")
        if clean.stat().st_size:
            print(f"    Clean log: {clean} (ANSI/CR removed)")
    except OSError:
        pass
    print("=" * 60)
    return True


def download_artifacts(vm: dict, artifact_type: str = "rom", log_path: str = "") -> bool:
    """Download every artifact of ``artifact_type`` or the selected build log."""
    artifact_type = (artifact_type or "rom").lower()
    if not is_vm_running(vm):
        print(">>> VM is offline. Power it on to download artifacts.")
        return False

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if artifact_type == "log":
        return _download_log(vm, log_path)

    patterns = PATTERNS.get(artifact_type)
    if not patterns:
        print(f">>> Unknown artifact type '{artifact_type}'.")
        return False

    print(f">>> Scanning mounted build disks for {artifact_type} artifacts...")
    files = _find_artifacts(vm, patterns)
    if not files:
        print(f">>> No {artifact_type} artifacts found on /mnt/a16, /mnt/a17 or /mnt/recovery.")
        return False

    print(f">>> Found {len(files)} file(s):")
    for path in files:
        print(f"    {path}")
    print()

    ok = 0
    for path in files:
        if _download_one(vm, path, DOWNLOAD_DIR):
            ok += 1

    print()
    print(f">>> Downloaded {ok}/{len(files)} file(s) to {DOWNLOAD_DIR}")
    return ok == len(files)
