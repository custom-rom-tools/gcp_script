"""
Artifact Download Service (cli/services/download.py)
Pulls ROM zips, recovery images, kernel boot images and build logs from the
mounted GCP data disks into ``~/Downloads`` using the multiplexed SCP path.

Artifacts are discovered only in each workspace's
``out/target/product/<device>`` output tree and can be downloaded selectively.
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path

from cli.base import is_interactive, read_input
from cli.services.vm import is_vm_running, vm_exec, vm_pull
from cli.services.monitor import get_active_log_path

MOUNTS = ("/mnt/a16", "/mnt/a17", "/mnt/recovery")
DOWNLOAD_DIR = Path.home() / "Downloads"

PATTERNS = {
    "rom": ("*.zip",),
    "recovery": ("*OrangeFox*", "*twrp*", "recovery.img", "recovery.zip"),
    "kernel": ("boot.img",),
}
ARTIFACT_NAMES = {
    "rom": "ROM package",
    "recovery": "recovery package",
    "kernel": "kernel image",
}
RECOVERY_HINTS = re.compile(r"(orangefox|twrp|recovery)", re.IGNORECASE)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _artifact_origin(remote: str) -> str:
    """Return a short ``mount/workspace/device`` label for a remote path."""
    for mount in MOUNTS:
        if remote.startswith(mount + "/"):
            rel = remote[len(mount) + 1:]
            parts = rel.split("/")
            mount_name = mount.rsplit("/", 1)[-1]
            if "out" in parts:
                idx = parts.index("out")
                workspace = "/".join(parts[:idx])
                device = parts[idx + 3] if len(parts) > idx + 3 else ""
                return "/".join(filter(None, (mount_name, workspace, device)))
            return mount_name
    return "remote"


def _find_artifacts(vm: dict, patterns: tuple[str, ...]) -> list[dict]:
    """Find matching files under ``<workspace>/out/target/product/<device>``.

    Only the build output tree is scanned; unrelated zips or images elsewhere on
    the data disk are intentionally ignored.  Results are returned newest
    first and include the remote size and modification time.
    """
    name_expr = " -o ".join(f"-iname {shlex.quote(p)}" for p in patterns)
    files: dict[str, dict] = {}
    for mount in MOUNTS:
        q_mount = shlex.quote(mount)
        cmd = (
            f"for root in {q_mount}/out/target/product {q_mount}/*/out/target/product; do "
            f"[ -d \"$root\" ] || continue; "
            f"find \"$root\" -mindepth 2 -maxdepth 2 -type f \\( {name_expr} \\) "
            f"-printf '%T@\\t%s\\t%p\\n' 2>/dev/null; "
            f"done"
        )
        res = vm_exec(vm, cmd, check=False, timeout=90)
        for line in (res.stdout or "").replace("\r", "").splitlines():
            parts = line.strip().split("\t", 2)
            if len(parts) != 3:
                continue
            try:
                mtime = float(parts[0])
                size = int(parts[1])
            except ValueError:
                continue
            path = parts[2].strip()
            if path and path not in files:
                files[path] = {"path": path, "size": size, "mtime": mtime}
    return sorted(files.values(), key=lambda item: item["mtime"], reverse=True)


def _parse_selection(raw: str, count: int) -> list[int] | None:
    """Parse ``all``, ``1,3`` or ``1-3`` into zero-based indexes."""
    value = (raw or "").strip().lower()
    if value in ("", "0", "a", "all", "*"):
        return list(range(count))
    if value in ("q", "quit", "exit"):
        return []

    selected: set[int] = set()
    for token in re.split(r"[,\s]+", value):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            if start < 1 or end > count:
                return None
            selected.update(range(start - 1, end))
            continue
        if not token.isdigit():
            return None
        number = int(token)
        if number < 1 or number > count:
            return None
        selected.add(number - 1)
    return sorted(selected)


def _choose_artifacts(files: list[dict], artifact_type: str,
                      selection: str | list[int] | None = None) -> list[dict] | None:
    """Let the caller pick files, or return every file for unattended runs."""
    count = len(files)
    if isinstance(selection, (list, tuple)):
        chosen = [int(i) for i in selection if 0 <= int(i) < count]
        return [files[i] for i in chosen]

    if selection is not None:
        chosen = _parse_selection(str(selection), count)
        if chosen is None:
            print(f">>> Invalid selection '{selection}'.")
            return None
        return [files[i] for i in chosen]

    if not is_interactive():
        return files

    print(f"\n>>> Select {ARTIFACT_NAMES.get(artifact_type, 'artifact')} to download:")
    for index, item in enumerate(files, 1):
        folder = str(Path(item["path"]).parent)
        print(f"  {index:>2}) {Path(item['path']).name}  ({_human_size(item['size'])})")
        print(f"      {folder}")

    while True:
        raw = read_input(
            "\n Download which file(s)? [Enter=all, e.g. 1,3 or 1-3, q=cancel]: "
        )
        chosen = _parse_selection(raw, count)
        if chosen is not None:
            return [files[i] for i in chosen]
        print(f">>> Invalid selection '{raw.strip()}'. Use 1-{count}, ranges, or 'all'.")


def _download_one(vm: dict, remote: str, dest_dir: Path) -> bool:
    """Download a single remote file, avoiding silent overwrites."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(remote).name
    local = dest_dir / name
    if local.exists():
        prefix = _artifact_origin(remote).replace("/", "_")
        local = dest_dir / f"{prefix}_{name}"

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


def download_artifacts(vm: dict, artifact_type: str = "rom", log_path: str = "",
                       selection: str | list[int] | None = None) -> bool:
    """Download selected artifacts from the workspace output tree.

    ``selection`` may be ``None`` (prompt when interactive, otherwise all),
    ``"all"``/``"1,3"``/``"1-3"``, or a list of zero-based indexes.
    """
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

    print(">>> Scanning workspace out/target/product folders for "
          f"{artifact_type} artifacts...")
    files = _find_artifacts(vm, patterns)
    if artifact_type == "rom":
        # Recovery packages can share the same product directory; never treat
        # them as ROM zips.
        files = [
            item for item in files
            if not RECOVERY_HINTS.search(Path(item["path"]).name)
        ]
    if not files:
        print(">>> No "
              f"{artifact_type} artifacts found in workspace out/target/product "
              "folders on /mnt/a16, /mnt/a17 or /mnt/recovery.")
        return False

    chosen = _choose_artifacts(files, artifact_type, selection)
    if chosen is None:
        return False
    if not chosen:
        print(">>> Download cancelled.")
        return False

    print(f"\n>>> Downloading {len(chosen)} of {len(files)} {artifact_type} "
          f"artifact(s) to {DOWNLOAD_DIR}...")
    ok = 0
    for item in chosen:
        if _download_one(vm, item["path"], DOWNLOAD_DIR):
            ok += 1

    print()
    print(f">>> Downloaded {ok}/{len(chosen)} selected file(s) to {DOWNLOAD_DIR}")
    return ok == len(chosen)
