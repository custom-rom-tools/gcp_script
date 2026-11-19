#!/usr/bin/env python3
"""
Unified Android ROM, Recovery & Kernel Build Engine (scripts/build.py)

This is the remote-side counterpart of the original per-branch shell scripts.
It keeps the exact build flows used by the ``main`` GCP menu while being fully
parameterised through ``configs/*.json``:

  ROM      : repo init -> local manifest -> clean/sync -> lunch -> m <target>
  Recovery : OrangeFox/TWRP base repo -> local manifest -> clean/sync ->
             qcom deps -> lunch fallbacks -> m recoveryimage
  Kernel   : the Evolution X ROM workspace, lunch target and device manifests,
             then ``m bootimage`` (the kernel cannot be built standalone).

Typical invocation (from the remote tmux runner):

  python3 scripts/build.py --rom evolution --ver 16 --dev all --variant userdebug
  python3 scripts/build.py --recovery ofrp --dev sm7125 --variant eng
  python3 scripts/build.py --kernel atoll --ver 16 --dev RMX2170
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if SCRIPT_DIR.name == "tmp" or SCRIPT_DIR.parent == Path("/") or str(SCRIPT_DIR).startswith("/private/tmp"):
    gcp_farm = Path.home() / ".gcp_farm"
    if (gcp_farm / "configs").is_dir():
        REPO_ROOT = gcp_farm
        SCRIPT_DIR = gcp_farm / "scripts"
    else:
        REPO_ROOT = Path("/tmp")
else:
    REPO_ROOT = SCRIPT_DIR.parent

CONFIGS_DIR = REPO_ROOT / "configs"

DEVICE_ORDER = ["RMX2170", "RMX2061", "RMX3081"]
QCOM_COMMON_REPO = "https://github.com/TeamWin/android_device_qcom_common.git"
QCOM_TWRP_COMMON_REPO = "https://github.com/TeamWin/android_device_qcom_twrp-common.git"
QCOM_BRANCH = "android-12.1"


# ---------------------------------------------------------------------------
# Configuration / environment helpers
# ---------------------------------------------------------------------------

def load_env() -> None:
    """Load persistent variables from ``~/.build_env`` or a local ``.env``."""
    candidates = [
        Path.home() / ".build_env",
        REPO_ROOT / ".env",
        Path.cwd() / ".env",
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
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass


def load_json_config(filename: str) -> dict:
    candidates = [
        CONFIGS_DIR / filename,
        Path.home() / ".gcp_farm" / "configs" / filename,
        Path("/tmp/configs") / filename,
        SCRIPT_DIR.parent / "configs" / filename,
        Path("/configs") / filename,
    ]
    for path in candidates:
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
    raise FileNotFoundError(f"Configuration file not found: {filename} (searched: {[str(c) for c in candidates]})")


def env_truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def resolve_entry(data: dict, key: str, kind: str) -> str:
    """Resolve an alias to its canonical configuration key."""
    key_lower = (key or "").lower()
    for canonical, cfg in data.items():
        aliases = [str(a).lower() for a in cfg.get("aliases", [])]
        if key_lower == canonical.lower() or key_lower in aliases:
            return canonical
    raise KeyError(
        f"Unknown {kind} '{key}'. Available: {', '.join(data.keys())}"
    )


def resolve_device(devices: dict, device: str) -> str:
    """Resolve a device code case-insensitively (config keys are upper-case)."""
    wanted = (device or "").strip()
    for code in devices:
        if code.lower() == wanted.lower():
            return code
    raise KeyError(
        f"Unknown device '{device}'. Available: {', '.join(devices.keys())}"
    )


def ordered_devices(devices: dict) -> list[str]:
    """Return ``all`` targets in the exact order used by the old shell scripts."""
    known = [code for code in DEVICE_ORDER if code in devices]
    return known or list(devices.keys())


# ---------------------------------------------------------------------------
# Console & process helpers
# ---------------------------------------------------------------------------

def banner(title: str, char: str = "=") -> None:
    line = char * 60
    print(f"\n{line}\n {title}\n{line}", flush=True)


def step(number: int, total: int, title: str) -> None:
    print(f"\n[{number}/{total}] {title}", flush=True)


def run_command(
    command: str,
    cwd: Path | str | None = None,
    *,
    label: str | None = None,
    check: bool = True,
    pty: bool = False,
    env: dict | None = None,
) -> int:
    """Run a shell command, streaming output in real time."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    if label:
        print(f"\n>>> {label}", flush=True)
    elif len(command) <= 240:
        print(f"\n>>> [EXEC] {command}", flush=True)

    # ``script`` gives repo/ninja a real PTY so progress bars render.  When
    # build.py already runs under one (the tmux runner), run directly to avoid
    # nesting typescript wrappers.
    if pty and not os.isatty(2) and shutil.which("script"):
        argv = ["script", "-qefc", command, "/dev/null"]
        result = subprocess.run(argv, cwd=cwd, env=merged_env)
    else:
        result = subprocess.run(
            command, shell=True, cwd=cwd, env=merged_env, executable="/bin/bash"
        )

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.returncode


def invoke_helper(script_name: str, args: list[str], cwd: Path) -> int:
    candidates = [
        SCRIPT_DIR / script_name,
        Path.home() / ".gcp_farm" / "scripts" / script_name,
        Path.home() / script_name,
        Path("/tmp/scripts") / script_name,
        Path("/tmp") / script_name,
    ]
    script = next((c for c in candidates if c.is_file()), None)
    if not script:
        print(f">>> [WARN] Helper {script_name} not found; skipping.", flush=True)
        return 0
    command = " ".join(
        ["python3", shlex.quote(str(script))]
        + [shlex.quote(str(a)) for a in args]
    )
    return run_command(command, cwd=cwd, check=False)


def shell_join(parts: list[str]) -> str:
    return " && ".join(parts)


# ---------------------------------------------------------------------------
# Shared source preparation
# ---------------------------------------------------------------------------

def clean_sources(work_dir: Path) -> None:
    invoke_helper("clean.py", [], work_dir)


def sync_sources(work_dir: Path) -> None:
    """Run ``repo sync`` with four attempts and a clean between failures."""
    nproc = os.cpu_count() or 16
    sync_cmd = (
        f"repo sync -c -j{nproc} --force-sync --no-clone-bundle --no-tags"
    )
    for attempt in range(1, 5):
        rc = run_command(sync_cmd, cwd=work_dir, check=False, pty=True)
        if rc == 0:
            return
        print(
            f">>> [WARN] repo sync failed (attempt {attempt}/4, possible HTTP "
            f"429 rate limit or stale lock); retrying in 60s...",
            flush=True,
        )
        time.sleep(60)
        clean_sources(work_dir)
    raise RuntimeError("repo sync failed after 4 attempts.")


def clone_local_manifest(work_dir: Path, url: str, branch: str) -> None:
    manifest_dir = work_dir / ".repo" / "local_manifests"
    if manifest_dir.exists():
        shutil.rmtree(manifest_dir, ignore_errors=True)
    run_command(
        f"git clone --progress -b {shlex.quote(branch)} {shlex.quote(url)} "
        ".repo/local_manifests",
        cwd=work_dir,
        label=f">>> Cloning local manifest {url} [{branch}]",
    )


def repo_init(work_dir: Path, url: str, branch: str, *, git_lfs: bool = False) -> None:
    lfs = " --git-lfs" if git_lfs else ""
    run_command(
        f"repo init -u {shlex.quote(url)} -b {shlex.quote(branch)}{lfs}",
        cwd=work_dir,
        label=f">>> Initializing repo {url} [{branch}]",
    )


def upload_artifacts(
    work_dir: Path,
    remote_subdir: str,
    device: str,
    build_type: str,
    *,
    sf_upload: bool,
    gofile_upload: bool,
) -> bool:
    candidates = [
        SCRIPT_DIR / "upload.py",
        Path.home() / ".gcp_farm" / "scripts" / "upload.py",
        Path.home() / "upload.py",
        Path("/tmp/scripts") / "upload.py",
        Path("/tmp/upload.py"),
    ]
    uploader = next((c for c in candidates if c.is_file()), None)
    if not uploader:
        print(">>> [WARN] upload.py is missing; artifacts were not uploaded.", flush=True)
        return False

    env = {
        "SF_UPLOAD": "true" if sf_upload else "false",
        "SKIP_GOFILE": "false" if gofile_upload else "true",
    }
    print(f"\n>>> Uploading artifacts to {remote_subdir}...", flush=True)
    rc = run_command(
        " ".join(
            [
                "python3",
                shlex.quote(str(uploader)),
                shlex.quote(remote_subdir),
                shlex.quote(device),
                shlex.quote(build_type),
            ]
        ),
        cwd=work_dir,
        check=False,
        env=env,
    )
    if rc != 0:
        print(f">>> [ERROR] Upload failed for {device} ({remote_subdir}).", flush=True)
    return rc == 0


# ---------------------------------------------------------------------------
# ROM builds (single device and batch)
# ---------------------------------------------------------------------------

def rom_lunch_targets(ver_cfg: dict, device: str, variant: str) -> list[str]:
    prefix = ver_cfg.get("lunch_prefix", "lineage")
    suffix = ver_cfg.get("lunch_suffix", "")
    if suffix:
        primary = f"{prefix}_{device}-{suffix}-{variant}"
    else:
        primary = f"{prefix}_{device}-{variant}"

    targets = [primary]
    if ver_cfg.get("fallback_lunch_without_suffix"):
        fallback = f"{prefix}_{device}-{variant}"
        if fallback not in targets:
            targets.append(fallback)
    return targets


def compile_rom(
    work_dir: Path,
    lunch_targets: list[str],
    build_target: str,
    *,
    installclean: bool = False,
) -> None:
    nproc = os.cpu_count() or 16
    lunch_chain = " || ".join(f"lunch {shlex.quote(t)}" for t in lunch_targets)
    commands = [
        "set -o pipefail",
        "source build/envsetup.sh",
        "export USE_CCACHE=1",
        'export CCACHE_DIR="$HOME/.ccache"',
        'export CCACHE_EXEC="$(which ccache)"',
        'export CCACHE_MAXSIZE="15G"',
        'readonly CCACHE_MAXSIZE="15G" 2>/dev/null || true',
        'ccache -M 15G >/dev/null 2>&1 || true',
        'ccache -o max_size=15G >/dev/null 2>&1 || true',
        "export SOONG_INCREMENTAL_ANALYSIS=true",
        f"{{ {lunch_chain}; }}",
    ]
    if installclean:
        commands.append("m installclean")
    commands.append(f"m -j{nproc} {shlex.quote(build_target)}")

    label = f">>> Compiling {build_target} ({lunch_targets[0]})"
    run_command(shell_join(commands), cwd=work_dir, label=label, pty=True)


def build_rom_device(
    work_dir: Path,
    ver_cfg: dict,
    device: str,
    dev_info: dict,
    variant: str,
    *,
    build_target: str | None = None,
    upload_type: str = "rom",
    upload_subdir: str | None = None,
    sf_upload: bool,
    gofile_upload: bool,
    batch: bool = False,
    label_prefix: str = "ROM",
    initialize_repo: bool = True,
) -> None:
    """Build one ROM/kernel target using the standard Evolution X pipeline."""
    work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(work_dir)

    rom_label = label_prefix
    target = build_target or ver_cfg["build_target"]
    remote_subdir = upload_subdir or f"{device}/{ver_cfg['upload_subdir_suffix']}"

    banner(
        f"Building {rom_label} for {device} ({variant})\n"
        f" Workspace : {work_dir}\n"
        f" Manifest  : {ver_cfg['rom_manifest_url']} "
        f"[{ver_cfg['rom_manifest_branch']}]\n"
        f" Device    : {dev_info['manifest_url']} "
        f"[{dev_info['manifest_branch']}]\n"
        f" Target    : {target}"
    )

    if batch:
        # Prevent module collisions when switching device trees in one tree.
        for pattern in ("device/realme/RMX*", "vendor/realme/RMX*"):
            for path in work_dir.glob(pattern):
                shutil.rmtree(path, ignore_errors=True)

    if initialize_repo:
        repo_init(
            work_dir,
            ver_cfg["rom_manifest_url"],
            ver_cfg["rom_manifest_branch"],
            git_lfs=True,
        )
    clone_local_manifest(
        work_dir, dev_info["manifest_url"], dev_info["manifest_branch"]
    )

    clean_sources(work_dir)
    sync_sources(work_dir)
    patch_target = str(ver_cfg.get("patch_target") or "")
    if not patch_target:
        subdir = ver_cfg.get("upload_subdir_suffix") or ver_cfg.get("upload_subdir", "")
        match = re.search(r"(?:/|^)(16|17)$", subdir)
        patch_target = match.group(1) if match else ""
    invoke_helper(
        "patch.py",
        ["--target", patch_target, "--rom", rom_label, "--dev", device],
        work_dir,
    )

    lunch_targets = rom_lunch_targets(ver_cfg, device, variant)
    compile_rom(
        work_dir, lunch_targets, target, installclean=batch
    )

    if not upload_artifacts(
        work_dir,
        remote_subdir,
        device,
        upload_type,
        sf_upload=sf_upload,
        gofile_upload=gofile_upload,
    ):
        raise RuntimeError(f"Upload failed for {device}")


def build_rom_batch(
    work_dir: Path,
    rom_cfg: dict,
    ver_cfg: dict,
    devices: list[str],
    variant: str,
    *,
    sf_upload: bool,
    gofile_upload: bool,
) -> tuple[list[str], list[str]]:
    """Build several devices in one workspace, mirroring ``*_all.sh``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(work_dir)

    banner(
        f"Starting {rom_cfg['name']} Batch Build ({variant})\n"
        f" Devices   : {', '.join(devices)}\n"
        f" Workspace : {work_dir}\n"
        f" Manifest  : {ver_cfg['rom_manifest_url']} "
        f"[{ver_cfg['rom_manifest_branch']}]"
    )

    repo_init(
        work_dir,
        ver_cfg["rom_manifest_url"],
        ver_cfg["rom_manifest_branch"],
        git_lfs=True,
    )

    successes: list[str] = []
    failures: list[str] = []
    total = len(devices)

    for index, device in enumerate(devices, start=1):
        dev_info = ver_cfg["devices"][device]
        try:
            build_rom_device(
                work_dir,
                ver_cfg,
                device,
                dev_info,
                variant,
                sf_upload=sf_upload,
                gofile_upload=gofile_upload,
                batch=True,
                label_prefix=rom_cfg["name"],
                initialize_repo=False,
            )
            successes.append(device)
            print(f"\n>>> [SUCCESS] {device} build succeeded!", flush=True)
        except Exception as exc:  # keep building the remaining devices
            failures.append(f"{device} ({exc})")
            print(f"\n>>> [FAILED] {device}: {exc}", flush=True)
        print(f"\n>>> Device {index}/{total} finished.", flush=True)

    return successes, failures


# ---------------------------------------------------------------------------
# Recovery builds
# ---------------------------------------------------------------------------

def recovery_lunch_targets(
    rec_cfg: dict, flavor: str, device: str, variant: str
) -> list[str]:
    if flavor == "ofrp":
        release = rec_cfg.get("ofrp_release", "bp2a")
        return [
            f"twrp_{device}-{release}-{variant}",
            f"twrp_{device}-trunk_staging-{variant}",
            f"twrp_{device}-{variant}",
        ]
    pattern = rec_cfg.get("lunch_pattern", "twrp_{device}-{variant}")
    return [pattern.format(device=device, variant=variant)]


def ensure_qcom_dependencies(work_dir: Path) -> None:
    common = work_dir / "device" / "qcom" / "common"
    twrp_common = work_dir / "device" / "qcom" / "twrp-common"
    if not common.is_dir():
        common.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            f"git clone --depth=1 {QCOM_COMMON_REPO} -b {QCOM_BRANCH} "
            "device/qcom/common",
            cwd=work_dir,
            label=">>> Cloning device/qcom/common",
        )
    if not twrp_common.is_dir():
        twrp_common.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            f"git clone --depth=1 {QCOM_TWRP_COMMON_REPO} -b {QCOM_BRANCH} "
            "device/qcom/twrp-common",
            cwd=work_dir,
            label=">>> Cloning device/qcom/twrp-common",
        )


def ensure_boardconfig_stub(work_dir: Path, device: str) -> None:
    if device.lower() == "sm7125":
        return
    shared = work_dir / "device" / "realme" / "sm7125" / "BoardConfig.mk"
    target = work_dir / "device" / "realme" / device / "BoardConfig.mk"
    if shared.is_file() and not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "DEVICE_PATH := device/realme/sm7125\n"
            "include device/realme/sm7125/BoardConfig.mk\n",
            encoding="utf-8",
        )
        print(f">>> Created BoardConfig stub for {device}", flush=True)


def detect_twrp_version(work_dir: Path, out_dir: Path) -> str:
    for rel in ("bootable/recovery/variables.h", "bootable/recovery-twrp/variables.h"):
        path = work_dir / rel
        if not path.is_file():
            continue
        try:
            for line in path.read_text(errors="ignore").splitlines():
                if "TW_MAIN_VERSION_STR" in line and "#define" in line:
                    match = re.search(r'"([^"]+)"', line)
                    if match:
                        version = re.search(
                            r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", match.group(1)
                        )
                        if version:
                            return version.group(0)
        except OSError:
            pass

    for prop in (
        out_dir / "recovery" / "root" / "prop.default",
        out_dir / "recovery" / "root" / "default.prop",
    ):
        if not prop.is_file():
            continue
        try:
            for line in prop.read_text(errors="ignore").splitlines():
                if line.startswith("ro.twrp.version="):
                    version = re.search(
                        r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", line.split("=", 1)[1]
                    )
                    if version:
                        return version.group(0)
        except OSError:
            pass

    return "3.7.1"


def recovery_outputs(out_dir: Path, flavor: str) -> list[Path]:
    if not out_dir.is_dir():
        return []
    branded = "OrangeFox*" if flavor == "ofrp" else "TWRP*"
    found = [
        path
        for path in list(out_dir.glob(branded))
        if path.is_file() and path.stat().st_size > 0
    ]
    plain = out_dir / "recovery.img"
    if plain.is_file() and plain.stat().st_size > 0:
        found.append(plain)
    return found


def rename_recovery_artifacts(work_dir: Path, out_dir: Path, flavor: str, device: str) -> None:
    stamp = time.strftime("%Y%m%d")
    branded = "OrangeFox*" if flavor == "ofrp" else "TWRP*"
    existing = list(out_dir.glob(branded))

    if flavor == "ofrp":
        base_name = f"OrangeFox-R12.0-{stamp}-Unofficial-{device}"
    else:
        version = detect_twrp_version(work_dir, out_dir)
        base_name = f"TWRP-{version}_{stamp}-Unofficial-{device}"

    for suffix, plain_name in ((".img", "recovery.img"), (".zip", "recovery.zip")):
        plain = out_dir / plain_name
        if not plain.is_file() or plain.stat().st_size <= 0:
            continue
        if any(p.name.endswith(suffix) for p in existing):
            continue
        new_path = out_dir / f"{base_name}{suffix}"
        print(f">>> Renaming {plain.name} -> {new_path.name}", flush=True)
        try:
            if new_path.exists():
                new_path.unlink()
            plain.rename(new_path)
        except OSError as exc:
            print(f">>> [WARN] Could not rename {plain.name}: {exc}", flush=True)


def compile_recovery(
    work_dir: Path,
    flavor: str,
    device: str,
    variant: str,
    build_target: str,
    lunch_targets: list[str],
) -> None:
    nproc = os.cpu_count() or 16
    lunch_chain = " || ".join(f"lunch {shlex.quote(t)}" for t in lunch_targets)
    vendorsetup_candidates = [
        f"device/realme/{device}/vendorsetup.sh",
        "device/realme/sm7125/vendorsetup.sh",
    ]
    vendorsetup = ""
    for candidate in vendorsetup_candidates:
        if (work_dir / candidate).is_file():
            vendorsetup = f"source {shlex.quote(candidate)}"
            break

    env_lines = [
        "export ALLOW_MISSING_DEPENDENCIES=true",
        'export LC_ALL="C.UTF-8"',
    ]
    if flavor == "ofrp":
        env_lines.append("export FOX_USE_TWRP_RECOVERY_IMAGE_BUILDER=1")

    commands = [
        "set -o pipefail",
        *env_lines,
        "set +e; source build/envsetup.sh; set -e",
        "export USE_CCACHE=0",
        "export CCACHE_DISABLE=1",
        "unset CCACHE_EXEC CCACHE_DIR",
    ]
    if vendorsetup:
        commands.append(f"set +e; {vendorsetup}; set -e")
    commands.append(f"{{ {lunch_chain}; }}")
    commands.append(f"m -j{nproc} {shlex.quote(build_target)}")

    label = f">>> Compiling {build_target} ({lunch_targets[0]})"
    run_command(shell_join(commands), cwd=work_dir, label=label, pty=True)


def build_recovery_flavor(
    rec_cfg: dict,
    flavor: str,
    devices: list[str],
    *,
    sf_upload: bool,
    gofile_upload: bool,
) -> tuple[list[str], list[str]]:
    work_dir = Path(rec_cfg["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(work_dir)

    rec_name = rec_cfg["name"]
    build_target = rec_cfg.get("build_target", "recoveryimage")
    variant = os.environ.get("BUILD_VARIANT", rec_cfg.get("default_variant", "eng"))

    banner(
        f"Building {rec_name} for {', '.join(devices)}\n"
        f" Workspace : {work_dir}\n"
        f" Base Repo : {rec_cfg['base_manifest_url']} "
        f"[{rec_cfg['base_manifest_branch']}]\n"
        f" Device    : {rec_cfg['device_manifest_url']} "
        f"[{rec_cfg['device_manifest_branch']}]\n"
        f" Variant   : {variant}"
    )

    # Preserve out/ for incremental builds; refresh manifests & generated stubs.
    for rel in (".repo/local_manifests", "device/realme/sm7125"):
        shutil.rmtree(work_dir / rel, ignore_errors=True)
    for path in work_dir.glob("device/realme/RMX*"):
        shutil.rmtree(path, ignore_errors=True)

    repo_init(
        work_dir,
        rec_cfg["base_manifest_url"],
        rec_cfg["base_manifest_branch"],
    )
    clone_local_manifest(
        work_dir,
        rec_cfg["device_manifest_url"],
        rec_cfg["device_manifest_branch"],
    )

    clean_sources(work_dir)
    sync_sources(work_dir)
    ensure_qcom_dependencies(work_dir)
    invoke_helper(
        "patch.py",
        ["--target", "recovery", "--flavor", flavor, "--dev", "all"],
        work_dir,
    )

    successes: list[str] = []
    failures: list[str] = []
    for device in devices:
        try:
            ensure_boardconfig_stub(work_dir, device)
            out_dir = work_dir / "out" / "target" / "product" / device
            if out_dir.is_dir():
                for pattern in ("recovery.img", "OrangeFox*", "TWRP*"):
                    for stale in out_dir.glob(pattern):
                        if stale.is_file():
                            stale.unlink()

            lunch_targets = recovery_lunch_targets(
                rec_cfg, flavor, device, variant
            )
            compile_recovery(
                work_dir, flavor, device, variant, build_target, lunch_targets
            )

            outputs = recovery_outputs(out_dir, flavor)
            if not outputs:
                raise RuntimeError("no valid recovery image/zip was produced")

            rename_recovery_artifacts(work_dir, out_dir, flavor, device)
            remote_subdir = f"{device.upper()}/{rec_cfg['upload_subdir']}"
            if not upload_artifacts(
                work_dir,
                remote_subdir,
                device,
                "recovery",
                sf_upload=sf_upload,
                gofile_upload=gofile_upload,
            ):
                raise RuntimeError("upload failed")

            successes.append(device)
            print(f"\n>>> [SUCCESS] {rec_name} build succeeded for {device}!", flush=True)
        except Exception as exc:
            failures.append(f"{device} ({exc})")
            print(f"\n>>> [FAILED] {rec_name} for {device}: {exc}", flush=True)

    return successes, failures


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Android ROM, Recovery & Kernel Build Runner"
    )
    parser.add_argument("--rom", default=None,
                        help="ROM name: evolution (evox), lineage (los), lunaris (lunar)")
    parser.add_argument("--ver", "--version", default=None, help="Android version")
    parser.add_argument("--kernel", default=None, help="Kernel name (atoll)")
    parser.add_argument("--recovery", "--flavor", default=None,
                        help="Recovery flavor: ofrp, twrp, all")
    parser.add_argument("--dev", "--device", default=None,
                        help="Target device code or all")
    parser.add_argument("--variant", "--type", default=None,
                        help="Build variant: userdebug, user, eng")
    parser.add_argument("--sf", action="store_true",
                        help="Upload release to SourceForge")
    parser.add_argument("--no-sf", action="store_true",
                        help="Skip SourceForge upload")
    parser.add_argument("--gofile", action="store_true",
                        help="Upload release to Gofile.io")
    parser.add_argument("--no-gofile", action="store_true",
                        help="Skip Gofile.io upload")
    parser.add_argument("--no-shutdown", action="store_true",
                        help="Never power off the VM at the end")
    parser.add_argument("--dry-run", "--validate", action="store_true",
                        help="Validate build configurations without compiling")
    return parser.parse_args(argv)


def upload_preferences(args: argparse.Namespace) -> tuple[bool, bool]:
    if args.sf:
        sf_upload = True
    elif args.no_sf:
        sf_upload = False
    else:
        sf_upload = env_truthy("SF_UPLOAD", False)

    if args.no_gofile:
        gofile_upload = False
    elif args.gofile:
        gofile_upload = True
    else:
        gofile_upload = not env_truthy("SKIP_GOFILE", False)
        if gofile_upload:
            gofile_upload = env_truthy("DEFAULT_GOFILE_UPLOAD", True)

    return sf_upload, gofile_upload


def report_summary(successes: list[str], failures: list[str]) -> int:
    banner("BUILD SUMMARY REPORT")
    if successes:
        print(" Successful Builds:")
        for item in successes:
            print(f"  [OK] {item}")
    if failures:
        print(" Failed Builds:")
        for item in failures:
            print(f"  [FAIL] {item}")
    banner("")
    return 1 if failures else 0


def maybe_shutdown(failures: list[str], args: argparse.Namespace) -> None:
    if failures or args.no_shutdown:
        return
    if not env_truthy("AUTO_SHUTDOWN", True):
        return
    print(
        "\nAuto-shutdown enabled: Powering down VM in 10 seconds to stop billing...",
        flush=True,
    )
    time.sleep(10)
    subprocess.run(["sudo", "poweroff"], check=False)


def dry_run_rom(rom_cfg: dict, ver_cfg: dict, devices: list[str], variant: str) -> None:
    print(f"=== [DRY-RUN] ROM Configuration Valid: {rom_cfg['name']} ===")
    print(f" Target devices: {devices}")
    print(f" Variant       : {variant}")
    print(f" Work Dir      : {ver_cfg['work_dir']}")
    print(f" Source        : {ver_cfg['rom_manifest_url']} "
          f"[{ver_cfg['rom_manifest_branch']}]")
    print(f" Build Target  : {ver_cfg['build_target']}")
    for device in devices:
        print(f"  - {device}: {', '.join(rom_lunch_targets(ver_cfg, device, variant))}")


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    sf_upload, gofile_upload = upload_preferences(args)
    successes: list[str] = []
    failures: list[str] = []

    modes = [bool(args.rom), bool(args.recovery), bool(args.kernel)]
    if sum(modes) != 1:
        print("Error: specify exactly one of --rom, --recovery or --kernel.")
        return 2

    try:
        if args.recovery:
            rec_data = load_json_config("recovery.json")
            if args.recovery.lower() == "all":
                flavors = ["ofrp", "twrp"]
            else:
                flavors = [resolve_entry(rec_data, args.recovery, "recovery flavor")]

            if not args.dev:
                print("Error: --dev is required for recovery builds.")
                return 2

            for flavor in flavors:
                cfg = rec_data[flavor]
                if args.dev.lower() == "all":
                    devices = ordered_devices(cfg.get("devices", {}))
                else:
                    devices = [resolve_device(cfg.get("devices", {}), args.dev)]
                variant = (
                    args.variant
                    or os.environ.get("BUILD_VARIANT")
                    or cfg.get("default_variant", "eng")
                )

                if args.dry_run:
                    banner(f"=== [DRY-RUN] Recovery Configuration Valid: {cfg['name']} ===")
                    print(f" Devices : {devices}")
                    print(f" Variant : {variant}")
                    print(f" Work Dir: {cfg['work_dir']}")
                    print(f" Base    : {cfg['base_manifest_url']} "
                          f"[{cfg['base_manifest_branch']}]")
                    for device in devices:
                        print(f"  - {device}: "
                              f"{', '.join(recovery_lunch_targets(cfg, flavor, device, variant))}")
                    continue

                os.environ["BUILD_VARIANT"] = variant
                ok, failed = build_recovery_flavor(
                    cfg, flavor, devices,
                    sf_upload=sf_upload, gofile_upload=gofile_upload,
                )
                successes.extend(f"{cfg['name']} - {d}" for d in ok)
                failures.extend(f"{cfg['name']} - {d}" for d in failed)

        elif args.kernel:
            kern_data = load_json_config("kernel.json")
            kern_key = resolve_entry(kern_data, args.kernel, "kernel")
            kern_cfg = kern_data[kern_key]
            versions = kern_cfg.get("versions", {})
            if not versions:
                raise KeyError(f"{kern_cfg['name']} has no configured versions.")

            version = str(args.ver) if args.ver else next(iter(versions))
            if version not in versions:
                raise KeyError(
                    f"Version '{version}' not configured for {kern_cfg['name']}. "
                    f"Available: {', '.join(versions)}"
                )
            ver_cfg = versions[version]
            devices_cfg = ver_cfg.get("devices", {})
            if not args.dev or args.dev.lower() == "all":
                devices = ordered_devices(devices_cfg)
            else:
                devices = [resolve_device(devices_cfg, args.dev)]

            variant = (
                args.variant
                or os.environ.get("BUILD_VARIANT")
                or kern_cfg.get("default_variant", "userdebug")
            )
            build_target = kern_cfg.get("build_target", "bootimage")

            if args.dry_run:
                print(f"=== [DRY-RUN] Kernel Configuration Valid: {kern_cfg['name']} ===")
                print(f" Version : {version}")
                print(f" Devices : {devices}")
                print(f" Variant : {variant}")
                print(f" Work Dir: {ver_cfg['work_dir']}")
                print(f" Target  : {build_target}")
                for device in devices:
                    print(f"  - {device}: "
                          f"{', '.join(rom_lunch_targets(ver_cfg, device, variant))}")
                return report_summary([], [])

            for device in devices:
                dev_info = devices_cfg[device]
                try:
                    build_rom_device(
                        Path(ver_cfg["work_dir"]),
                        ver_cfg,
                        device,
                        dev_info,
                        variant,
                        build_target=build_target,
                        upload_type="kernel",
                        upload_subdir=f"{device.upper()}/{ver_cfg['upload_subdir']}",
                        sf_upload=sf_upload,
                        gofile_upload=gofile_upload,
                        label_prefix=f"{kern_cfg['name']} (Android {version})",
                    )
                    successes.append(f"{kern_cfg['name']} {version} - {device}")
                except Exception as exc:
                    failures.append(f"{kern_cfg['name']} {version} - {device} ({exc})")
                    print(f"\n>>> [ERROR] Failed to build kernel for {device}: {exc}",
                          flush=True)

        else:
            rom_data = load_json_config("rom.json")
            rom_key = resolve_entry(rom_data, args.rom, "ROM")
            rom_cfg = rom_data[rom_key]
            versions = rom_cfg.get("versions", {})
            if not versions:
                raise KeyError(f"{rom_cfg['name']} has no configured versions.")

            version = str(args.ver) if args.ver else next(iter(versions))
            if version not in versions:
                raise KeyError(
                    f"Version '{version}' not configured for {rom_cfg['name']}. "
                    f"Available: {', '.join(versions)}"
                )
            ver_cfg = versions[version]
            devices_cfg = ver_cfg.get("devices", {})
            if not args.dev or args.dev.lower() == "all":
                devices = ordered_devices(devices_cfg)
            else:
                devices = [resolve_device(devices_cfg, args.dev)]

            variant = (
                args.variant
                or os.environ.get("BUILD_VARIANT")
                or rom_cfg.get("default_variant", "userdebug")
            )
            build_target = ver_cfg.get("build_target", "bacon")

            if args.dry_run:
                dry_run_rom(rom_cfg, ver_cfg, devices, variant)
                return report_summary([], [])

            if len(devices) == 1:
                device = devices[0]
                try:
                    build_rom_device(
                        Path(ver_cfg["work_dir"]),
                        ver_cfg,
                        device,
                        devices_cfg[device],
                        variant,
                        sf_upload=sf_upload,
                        gofile_upload=gofile_upload,
                        label_prefix=rom_cfg["name"],
                    )
                    successes.append(f"{rom_cfg['name']} {version} - {device}")
                except Exception as exc:
                    failures.append(f"{rom_cfg['name']} {version} - {device} ({exc})")
                    print(f"\n>>> [ERROR] Failed to build {rom_cfg['name']} "
                          f"for {device}: {exc}", flush=True)
            else:
                ok, failed = build_rom_batch(
                    Path(ver_cfg["work_dir"]), rom_cfg, ver_cfg, devices, variant,
                    sf_upload=sf_upload, gofile_upload=gofile_upload,
                )
                successes.extend(f"{rom_cfg['name']} {version} - {d}" for d in ok)
                failures.extend(f"{rom_cfg['name']} {version} - {d}" for d in failed)

    except (KeyError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"\n>>> [ERROR] Command failed with exit code {exc.returncode}: {exc.cmd}")
        failures.append(str(exc.cmd))
    except Exception as exc:
        print(f"\n>>> [ERROR] {exc}")
        failures.append(str(exc))

    rc = report_summary(successes, failures)
    if not args.dry_run:
        maybe_shutdown(failures, args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
