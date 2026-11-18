#!/usr/bin/env python3
"""
Unified Android ROM & Recovery Build Engine (scripts/build.py)
Replaces all 25 fragmented shell scripts across git branches with a single,
parameterized, robust Python build engine.

Can be run directly on the GCP VM via:
  python3 scripts/build.py --rom evolution --ver 16 --dev <device> --sf
  python3 scripts/build.py --rom lineage --ver 17 --dev all --sf
  python3 scripts/build.py --recovery ofrp --dev <device> --sf
"""

import os
import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CONFIGS_DIR = REPO_ROOT / "configs"


def load_env():
    """Load persistent variables from ~/.build_env or .env."""
    candidates = [
        Path.home() / ".build_env",
        REPO_ROOT / ".env",
        Path.cwd() / ".env",
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
                        if k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass


def load_json_config(filename: str) -> dict:
    """Load JSON config file from configs directory."""
    path = CONFIGS_DIR / filename
    if not path.is_file():
        # Fallback if running standalone or curled
        alt_path = Path("/tmp/configs") / filename
        if alt_path.is_file():
            path = alt_path
        else:
            raise FileNotFoundError(f"Configuration file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_command(cmd, cwd=None, env=None, check=True, pty=False):
    """Run a shell command displaying output in realtime."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    if isinstance(cmd, list):
        cmd_str = " ".join(cmd)
    else:
        cmd_str = cmd

    print(f"\n>>> [EXEC] {cmd_str}")
    if cwd:
        print(f">>> [CWD]  {cwd}")

    # Use script or pty if available to preserve Ninja/Repo status bars
    if pty and shutil.which("script"):
        full_cmd = ["script", "-qefc", cmd_str, "/dev/null"]
        res = subprocess.run(full_cmd, cwd=cwd, env=merged_env)
    else:
        res = subprocess.run(cmd_str, shell=True, cwd=cwd, env=merged_env, executable="/bin/bash")

    if check and res.returncode != 0:
        raise subprocess.CalledProcessError(res.returncode, cmd_str)
    return res.returncode


def build_single_rom_device(rom_cfg, ver_cfg, dev_code, dev_info, variant, sf_upload, gofile_upload):
    """Build a ROM for a single device."""
    work_dir = Path(ver_cfg["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(work_dir)

    rom_name = rom_cfg["name"]
    upload_subdir = f"{dev_code}/{ver_cfg['upload_subdir_suffix']}"
    build_target = ver_cfg["build_target"]

    print("\n" + "=" * 60)
    print(f" Building {rom_name} for {dev_code} ({variant})")
    print(f" Workspace : {work_dir}")
    print(f" Manifest  : {ver_cfg['rom_manifest_url']} [{ver_cfg['rom_manifest_branch']}]")
    print(f" Device    : {dev_info['manifest_url']} [{dev_info['manifest_branch']}]")
    print("=" * 60)

    # 1. Clean previous device trees
    clean_py = SCRIPT_DIR / "clean.py"
    if clean_py.is_file():
        run_command(f"python3 {clean_py} --clean-trees --target rom --device {dev_code}", cwd=work_dir, check=False)
    else:
        print("[CLEAN] Clean hook not found; no cleanup actions performed.")

    # 2. Repo Init (shared base)
    print(f"\n[1/5] Initializing ROM Source ({rom_name})...")
    run_command(
        f"repo init -u '{ver_cfg['rom_manifest_url']}' -b '{ver_cfg['rom_manifest_branch']}' --git-lfs",
        cwd=work_dir
    )

    # 3. Setup device manifest
    print(f"\n[2/5] Setting up device manifest for {dev_code}...")
    local_manifests = work_dir / ".repo" / "local_manifests"
    if local_manifests.exists():
        shutil.rmtree(local_manifests, ignore_errors=True)
    run_command(
        f"git clone -b '{dev_info['manifest_branch']}' '{dev_info['manifest_url']}' .repo/local_manifests",
        cwd=work_dir
    )

    # 4. Clean & Sync source
    print("\n[3/5] Cleaning and syncing source repositories...")
    if clean_py.is_file():
        run_command(f"python3 {clean_py}", cwd=work_dir, check=False)

    nproc = os.cpu_count() or 16
    sync_ok = False
    for attempt in range(1, 5):
        rc = run_command(
            f"repo sync -c -j{nproc} --force-sync --no-clone-bundle --no-tags",
            cwd=work_dir,
            check=False,
            pty=True
        )
        if rc == 0:
            sync_ok = True
            break
        print(f">>> [WARN] repo sync failed (attempt {attempt}/4, possible rate limit). Retrying in 60s...")
        time.sleep(60)
        if clean_py.is_file():
            run_command(f"python3 {clean_py}", cwd=work_dir, check=False)

    if not sync_ok:
        raise RuntimeError("repo sync failed after 4 attempts.")

    # Apply patches
    patch_py = SCRIPT_DIR / "patch.py"
    if patch_py.is_file():
        target_tag = "17" if "17" in ver_cfg.get("upload_subdir_suffix", "") else "16"
        run_command(f"python3 {patch_py} --target {target_tag} --rom {rom_cfg['name']} --dev {dev_code}", cwd=work_dir, check=False)

    # 5. Lunch & Compile
    print(f"\n[4/5] Lunching and compiling {rom_name} ({build_target})...")
    lunch_suffix = ver_cfg.get("lunch_suffix", "")
    lunch_prefix = ver_cfg.get("lunch_prefix", "lineage")

    if lunch_suffix:
        lunch_target = f"{lunch_prefix}_{dev_code}-{lunch_suffix}-{variant}"
    else:
        lunch_target = f"{lunch_prefix}_{dev_code}-{variant}"

    fallback_str = ""
    if ver_cfg.get("fallback_lunch_without_suffix"):
        fallback_target = f"{lunch_prefix}_{dev_code}-{variant}"
        fallback_str = f" || lunch '{fallback_target}'"

    build_cmd = f"""
source build/envsetup.sh
export USE_CCACHE=1
export CCACHE_DIR="$HOME/.ccache"
export CCACHE_EXEC="$(which ccache)"
(lunch '{lunch_target}' {fallback_str}) && m installclean && m -j{nproc} '{build_target}'
"""
    run_command(build_cmd, cwd=work_dir, pty=True)

    # 6. Upload
    print("\n[5/5] Uploading artifacts...")
    upload_py = SCRIPT_DIR / "upload.py"
    if upload_py.is_file():
        upload_env = {
            "SF_UPLOAD": "true" if sf_upload else "false",
            "SKIP_GOFILE": "false" if gofile_upload else "true",
        }
        run_command(f"python3 {upload_py} '{upload_subdir}' '{dev_code}' 'rom'", cwd=work_dir, env=upload_env)


def build_single_recovery(rec_cfg, flavor_key, dev_code, variant, sf_upload, gofile_upload):
    """Build a Recovery (OrangeFox or TWRP)."""
    work_dir = Path(rec_cfg["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(work_dir)

    rec_name = rec_cfg["name"]
    upload_subdir = rec_cfg.get("upload_subdir", f"{dev_code.upper()}/Recovery/{rec_name}")
    build_target = rec_cfg.get("build_target", "recoveryimage")

    print("\n" + "=" * 60)
    print(f" Building {rec_name} for {dev_code} ({variant})")
    print(f" Workspace : {work_dir}")
    print(f" Base Repo : {rec_cfg['base_manifest_url']} [{rec_cfg['base_manifest_branch']}]")
    print(f" Device    : {rec_cfg['device_manifest_url']} [{rec_cfg['device_manifest_branch']}]")
    print("=" * 60)

    # Clean intermediate local manifests & recovery trees
    clean_py = SCRIPT_DIR / "clean.py"
    if clean_py.is_file():
        run_command(f"python3 {clean_py} --clean-trees --target recovery --device {dev_code}", cwd=work_dir, check=False)
    else:
        print("[CLEAN] Clean hook not found; no cleanup actions performed.")

    # 1. Base repo init
    print(f"\n[1/5] Initializing {rec_name} base repo...")
    run_command(f"repo init -u '{rec_cfg['base_manifest_url']}' -b '{rec_cfg['base_manifest_branch']}'", cwd=work_dir)

    # 2. Local manifest clone
    print(f"\n[2/5] Setting up recovery manifest...")
    run_command(f"git clone -b '{rec_cfg['device_manifest_branch']}' '{rec_cfg['device_manifest_url']}' .repo/local_manifests", cwd=work_dir)

    # 3. Clean & sync
    print("\n[3/5] Syncing recovery sources...")
    if clean_py.is_file():
        run_command(f"python3 {clean_py}", cwd=work_dir, check=False)

    nproc = os.cpu_count() or 16
    sync_ok = False
    for attempt in range(1, 5):
        rc = run_command(f"repo sync -c -j{nproc} --force-sync --no-clone-bundle --no-tags", cwd=work_dir, check=False, pty=True)
        if rc == 0:
            sync_ok = True
            break
        print(f">>> [WARN] repo sync failed (attempt {attempt}/4). Retrying in 60s...")
        time.sleep(60)

    if not sync_ok:
        raise RuntimeError("repo sync failed after 4 attempts.")

    patch_py = SCRIPT_DIR / "patch.py"
    if patch_py.is_file():
        run_command(f"python3 {patch_py} --target recovery --flavor {flavor_key} --dev {dev_code}", cwd=work_dir, check=False)

    # 4. Lunch & Compile
    print(f"\n[4/5] Lunching and compiling {rec_name}...")
    ofrp_release = rec_cfg.get("ofrp_release", "bp2a")
    lunch_pattern = rec_cfg.get("lunch_pattern", "twrp_{device}-{variant}")
    lunch_target = lunch_pattern.format(device=dev_code, ofrp_release=ofrp_release, variant=variant)

    rec_compile_cmd = f"""
source build/envsetup.sh
export USE_CCACHE=1
export CCACHE_DIR="$HOME/.ccache"
export CCACHE_EXEC="$(which ccache)"
lunch '{lunch_target}' && m -j{nproc} '{build_target}'
"""
    run_command(rec_compile_cmd, cwd=work_dir, pty=True)

    # 5. Upload
    print("\n[5/5] Uploading recovery artifacts...")
    upload_py = SCRIPT_DIR / "upload.py"
    if upload_py.is_file():
        upload_env = {
            "SF_UPLOAD": "true" if sf_upload else "false",
            "SKIP_GOFILE": "false" if gofile_upload else "true",
        }
        run_command(f"python3 {upload_py} '{upload_subdir}' '{dev_code}' 'recovery'", cwd=work_dir, env=upload_env)


def main():
    load_env()

    parser = argparse.ArgumentParser(description="Unified Android ROM & Recovery Build Runner")
    parser.add_argument("--rom", default=None, help="ROM name: evolution (evox), lineage (los), lunaris (lunar)")
    parser.add_argument("--ver", "--version", default=None, help="Android version")
    parser.add_argument("--recovery", "--flavor", default=None, help="Recovery flavor: ofrp, twrp, all")
    parser.add_argument("--dev", "--device", default=None, help="Target device code or all")
    parser.add_argument("--variant", "--type", default=None, help="Build variant: userdebug, user, eng")
    parser.add_argument("--sf", action="store_true", default=None, help="Upload release to SourceForge")
    parser.add_argument("--no-sf", action="store_true", help="Skip SourceForge upload")
    parser.add_argument("--gofile", action="store_true", default=None, help="Upload release to Gofile.io")
    parser.add_argument("--no-gofile", action="store_true", help="Skip Gofile.io upload")
    parser.add_argument("--dry-run", "--validate", action="store_true", help="Validate build configurations without compiling")

    args = parser.parse_args()

    # Resolve upload preferences from flags or .env
    sf_upload = False
    if args.sf:
        sf_upload = True
    elif args.no_sf:
        sf_upload = False
    else:
        sf_upload = os.environ.get("DEFAULT_SF_UPLOAD", "false").lower() in ("true", "1", "yes")

    gofile_upload = True
    if args.no_gofile:
        gofile_upload = False
    elif args.gofile:
        gofile_upload = True
    else:
        gofile_upload = os.environ.get("DEFAULT_GOFILE_UPLOAD", "true").lower() in ("true", "1", "yes")

    success_builds = []
    failed_builds = []

    # Case 1: Recovery Build
    if args.recovery:
        rec_data = load_json_config("recovery.json")
        flavors_to_build = []
        if args.recovery.lower() == "all":
            flavors_to_build = ["ofrp", "twrp"]
        else:
            flav = args.recovery.lower()
            if flav in ("orangefox", "fox"):
                flav = "ofrp"
            elif flav in ("teamwin",):
                flav = "twrp"
            if flav not in rec_data:
                sys.exit(f"Error: Unknown recovery flavor '{args.recovery}'. Options: {list(rec_data.keys())}")
            flavors_to_build = [flav]

        if not args.dev:
            sys.exit("Error: --dev is required for recovery builds.")
        target_dev = args.dev

        if args.dry_run:
            print("=== [DRY-RUN] Recovery Configuration Valid ===")
            for f in flavors_to_build:
                print(f" - {rec_data[f]['name']} for {target_dev} [{rec_data[f]['default_variant']}]")
            sys.exit(0)

        for flav in flavors_to_build:
            cfg = rec_data[flav]
            variant = args.variant or cfg.get("default_variant", "eng")
            try:
                build_single_recovery(cfg, flav, target_dev, variant, sf_upload, gofile_upload)
                success_builds.append(f"{cfg['name']} ({target_dev})")
            except Exception as e:
                print(f"\n>>> [ERROR] Failed to build {cfg['name']}: {e}")
                failed_builds.append(f"{cfg['name']} ({target_dev})")

    # Case 2: ROM Build
    elif args.rom:
        rom_data = load_json_config("rom.json")
        rom_key = args.rom.lower()
        # Aliases
        for k, v in rom_data.items():
            if rom_key == k or rom_key in v.get("aliases", []):
                rom_key = k
                break

        if rom_key not in rom_data:
            sys.exit(f"Error: Unknown ROM '{args.rom}'. Available: {list(rom_data.keys())}")

        rom_cfg = rom_data[rom_key]
        ver_str = str(args.ver) if args.ver else next(iter(rom_cfg["versions"]), "")
        if ver_str not in rom_cfg["versions"]:
            sys.exit(f"Error: Version '{ver_str}' not configured for {rom_cfg['name']}. Available: {list(rom_cfg['versions'].keys())}")

        ver_cfg = rom_cfg["versions"][ver_str]
        variant = args.variant or rom_cfg.get("default_variant", "userdebug")

        all_devs = ver_cfg["devices"]
        devices_to_build = []
        if not args.dev or args.dev.lower() == "all":
            devices_to_build = list(all_devs.keys())
        else:
            dev_upper = args.dev.upper()
            if dev_upper not in all_devs:
                sys.exit(f"Error: Device '{dev_upper}' not found for {rom_cfg['name']} {ver_str}. Available: {list(all_devs.keys())}")
            devices_to_build = [dev_upper]

        if args.dry_run:
            print(f"=== [DRY-RUN] ROM Configuration Valid: {rom_cfg['name']} (Android {ver_str}) ===")
            print(f" Target devices: {devices_to_build}")
            print(f" Variant       : {variant}")
            print(f" Work Dir      : {ver_cfg['work_dir']}")
            print(f" Source        : {ver_cfg['rom_manifest_url']} [{ver_cfg['rom_manifest_branch']}]")
            sys.exit(0)

        for dev_code in devices_to_build:
            dev_info = all_devs[dev_code]
            try:
                build_single_rom_device(rom_cfg, ver_cfg, dev_code, dev_info, variant, sf_upload, gofile_upload)
                success_builds.append(f"{rom_cfg['name']} {ver_str} - {dev_code}")
            except Exception as e:
                print(f"\n>>> [ERROR] Failed to build {rom_cfg['name']} for {dev_code}: {e}")
                failed_builds.append(f"{rom_cfg['name']} {ver_str} - {dev_code}")

    else:
        parser.print_help()
        sys.exit(1)

    # Summary Report
    print("\n" + "=" * 60)
    print("                   BUILD SUMMARY REPORT                     ")
    print("=" * 60)
    if success_builds:
        print(" Successful Builds:")
        for s in success_builds:
            print(f"  [✓] {s}")
    if failed_builds:
        print(" Failed Builds:")
        for f in failed_builds:
            print(f"  [✗] {f}")
    print("=" * 60)

    if failed_builds:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
