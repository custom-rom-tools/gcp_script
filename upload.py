#!/usr/bin/env python3
"""
GCP Artifact Uploader (upload.py)
Uploads cooked ROM/recovery builds and companions (.md5sum, .sha256sum, .json, .txt)
to SourceForge (when enabled) and Gofile.io (always).

Usage:
  upload.py <remote_subdir> <device_or_path> [build_type] [extra_files...]
"""

import os
import sys
import re
import json
import time
import base64
import hashlib
import subprocess
from pathlib import Path

# SourceForge configuration
SF_PROJECT = "realme-atoll"
SF_USER = "nikhilpal2705"
SF_HOST = "frs.sourceforge.net"

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / "configs"


def load_config_devices() -> tuple[list[str], str]:
    """Load device codes and default recovery device from JSON configuration."""
    devices: list[str] = []
    default_recovery = ""
    for filename in ("rom.json", "recovery.json"):
        try:
            data = json.loads((CONFIG_DIR / filename).read_text(encoding="utf-8"))
            if filename == "rom.json":
                for rom_cfg in data.values():
                    for ver_cfg in rom_cfg.get("versions", {}).values():
                        for code in ver_cfg.get("devices", {}):
                            if code not in devices:
                                devices.append(code)
            else:
                for rec_cfg in data.values():
                    for code in rec_cfg.get("devices", {}):
                        if code not in devices:
                            devices.append(code)
                        if not default_recovery:
                            default_recovery = code
        except (OSError, json.JSONDecodeError):
            pass
    return devices, default_recovery


def get_sf_key() -> Path | None:
    """
    Resolve SourceForge SSH private key:
    1. If SF_KEY is provided in environment: decode base64 key to /tmp/sf_key.
    2. Else look in persistent SSH locations (~/.ssh/crave_sf, etc.).
    3. If no key found: return None.
    """
    sf_key_b64 = os.environ.get("SF_KEY", "").strip()

    if sf_key_b64:
        key_file = Path("/tmp/sf_key")
        try:
            key_file.write_bytes(base64.b64decode(sf_key_b64))
            key_file.chmod(0o600)
            print(">>> [SF KEY] Using SF_KEY from environment")
            return key_file
        except Exception as e:
            print(f">>> [WARN] Failed to decode base64 SF_KEY: {e}")
            return None

    candidate_keys = [
        Path.home() / ".ssh" / "crave_sf",
        Path.home() / ".ssh" / "sf_key",
        Path("/mnt/a16/crave_sf"),
        Path("/mnt/a17/crave_sf"),
        Path("/tmp/sf_key"),
    ]
    for key_path in candidate_keys:
        if key_path.is_file() and key_path.stat().st_size > 0:
            try:
                key_path.chmod(0o600)
            except Exception:
                pass
            print(f">>> [SF KEY] Using key: {key_path}")
            return key_path

    print(">>> [WARN] SourceForge upload enabled, but crave_sf key or SF_KEY was not found. Skipping SourceForge.")
    return None


def get_build_sort_key(file_path: Path) -> tuple:
    """Sort key: 8-digit date (YYYYMMDD) then file modification time."""
    date_match = re.search(r'(20\d{6})', file_path.name)
    date_str = date_match.group(1) if date_match else "00000000"
    return (date_str, file_path.stat().st_mtime)


def find_companions(artifact: Path) -> list[Path]:
    """Find companion checksum/manifest/changelog files (.sha256sum, .md5sum, .json, .txt)."""
    companions: list[Path] = []
    parent = artifact.parent
    name = artifact.name
    stem = artifact.stem

    for cand in sorted(parent.iterdir()):
        if not cand.is_file() or cand.stat().st_size == 0 or cand == artifact:
            continue

        cand_suffix = cand.suffix.lower()
        if cand_suffix in [".img", ".zip", ".tar", ".gz", ".br", ".lz4"]:
            continue

        # Don't associate .img companions with .zip or vice versa
        if artifact.suffix.lower() == ".zip" and ".img." in cand.name.lower():
            continue
        if artifact.suffix.lower() == ".img" and ".zip." in cand.name.lower():
            continue

        is_match = False
        if "changelog" in cand.name.lower():
            is_match = True
        elif cand.name.startswith(name):
            is_match = True
        elif cand.stem == stem:
            is_match = True

        if not is_match:
            continue

        match cand_suffix:
            case ".sha256sum" | ".sha256" | ".md5sum" | ".md5" | ".json":
                if cand not in companions:
                    companions.append(cand)
            case ".txt" if "changelog" in cand.name.lower() or cand.stem == stem:
                if cand not in companions:
                    companions.append(cand)
            case _:
                pass

    return companions


def resolve_target_path(item: str) -> Path:
    """Resolves an input item to a Path, checking direct path, cwd, or out/target/product."""
    p = Path(item)
    if p.exists():
        return p

    # If user passed a device name while already inside that device's directory
    cwd = Path.cwd().resolve()
    if cwd.name.upper() == item.upper() or cwd.name.lower() == item.lower():
        return cwd

    # Check relative out/target/product
    base = Path("out/target/product")
    for cand in [base / item, base / item.lower(), base / item.upper()]:
        if cand.is_dir():
            return cand

    # Walk up parent directories to find out/target/product/<item>
    cur = cwd
    while cur != cur.parent:
        cand = cur / "out" / "target" / "product" / item
        if cand.is_dir():
            return cand
        cand = cur / item
        if cand.is_dir() and (cur / "out").is_dir():
            return cand
        cur = cur.parent

    # Check common VM build mount paths
    for mnt_base in [
        Path("/mnt/a17/evox"),
        Path("/mnt/a16/evox"),
        Path("/mnt/recovery/ofrp"),
        Path("/mnt/recovery/twrp"),
        Path("/mnt/rom"),
    ]:
        cand = mnt_base / "out" / "target" / "product" / item
        if cand.is_dir():
            return cand

    return p


def is_rom_zip(p: Path) -> bool:
    """Validate whether a zip file is a cooked ROM installer."""
    name = p.name.lower()
    stem = p.stem.lower()
    if any(ign in name for ign in ["target_files", "target-files", "symbols", "orangefox", "ofrp", "twrp"]):
        return False
    if any(ign in stem for ign in ["incremental", "ota_update", "ota-package"]):
        return False
    return True


def discover_candidates(search_dir: Path, build_type: str, remote_subdir: str = "") -> list[Path]:
    """Discovers target artifacts in product directory based on build type."""
    candidates: list[Path] = []
    sub_lower = remote_subdir.lower()

    match build_type.lower():
        case "recovery":
            all_zips = list(search_dir.glob("*.zip"))
            if not all_zips and search_dir.name == "product":
                all_zips = list(search_dir.glob("*/*.zip"))

            rec_flavor = ""
            if any(k in sub_lower for k in ["orangefox", "ofrp"]):
                rec_flavor = "orangefox"
            elif any(k in sub_lower for k in ["twrp"]):
                rec_flavor = "twrp"

            flavor_keys = ["orangefox", "ofrp"] if rec_flavor == "orangefox" else ["twrp"]

            zips = [
                z for z in all_zips
                if any(k in z.name.lower() for k in ["orangefox", "ofrp", "twrp", "recovery"])
                and not any(ign in z.name.lower() for ign in ["target_files", "target-files", "symbols", "ota_update"])
            ]
            if rec_flavor:
                flavored = [z for z in zips if any(fk in z.name.lower() for fk in flavor_keys)]
                if flavored:
                    zips = flavored

            if zips:
                zips.sort(key=get_build_sort_key, reverse=True)
                candidates.append(zips[0])

            all_imgs = list(search_dir.glob("*.img"))
            if not all_imgs and search_dir.name == "product":
                all_imgs = list(search_dir.glob("*/*.img"))
            imgs = [
                i for i in all_imgs
                if any(k in i.name.lower() for k in ["orangefox", "ofrp", "twrp", "recovery"])
            ]
            if rec_flavor:
                flavored_imgs = [i for i in imgs if any(fk in i.name.lower() for fk in flavor_keys)]
                if flavored_imgs:
                    imgs = flavored_imgs

            if imgs:
                branded = [i for i in imgs if any(k in i.name.lower() for k in ["orangefox", "ofrp", "twrp"])]
                chosen = branded if branded else imgs
                chosen.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                candidates.append(chosen[0])

        case "kernel":
            all_imgs = list(search_dir.glob("*.img"))
            if not all_imgs and search_dir.name == "product":
                all_imgs = list(search_dir.glob("*/*.img"))
            imgs = [
                i for i in all_imgs
                if i.name.lower() == "boot.img"
                or i.stem.lower().startswith("boot-")
                or i.stem.lower().startswith("boot_")
            ]
            if not imgs:
                imgs = [
                    i for i in all_imgs
                    if not any(
                        skip in i.name.lower()
                        for skip in ["recovery", "dtbo", "logo", "vbmeta", "super",
                                     "vendor_boot", "init_boot"]
                    )
                ]
            if imgs:
                imgs.sort(key=get_build_sort_key, reverse=True)
                candidates.append(imgs[0])

        case "rom" | _:
            all_zips = list(search_dir.glob("*.zip"))
            if not all_zips and search_dir.name == "product":
                all_zips = list(search_dir.glob("*/*.zip"))
            zips = [z for z in all_zips if is_rom_zip(z)]

            rom_flavor = ""
            for r in ["evolution", "lineage", "lunaris"]:
                if r in sub_lower:
                    rom_flavor = r
                    break
            if rom_flavor:
                flavored = [z for z in zips if rom_flavor in z.name.lower()]
                if flavored:
                    zips = flavored

            if zips:
                branded = [z for z in zips if not any(ota in z.stem.lower() for ota in ["-ota", "_ota"])]
                chosen = branded if branded else zips
                chosen.sort(key=get_build_sort_key, reverse=True)
                candidates.append(chosen[0])

    return candidates


def generate_sha256sum(file_path: Path) -> Path | None:
    """Generate <file_path>.sha256sum companion if not already present."""
    if not file_path.is_file() or file_path.stat().st_size == 0:
        return None
    if file_path.suffix.lower() in [".sha256sum", ".sha256", ".md5sum", ".md5", ".json", ".txt"]:
        return None

    sum_file = file_path.with_name(f"{file_path.name}.sha256sum")
    if sum_file.exists() and sum_file.stat().st_size > 0:
        return sum_file

    try:
        h = hashlib.sha256()
        with file_path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        digest = h.hexdigest()
        sum_file.write_text(f"{digest}  {file_path.name}\n", encoding="utf-8")
        return sum_file
    except Exception as e:
        print(f">>> [WARN] Could not generate sha256sum for {file_path.name}: {e}")
        return None


def generate_md5sum(file_path: Path) -> Path | None:
    """Generate <file_path>.md5sum companion if not already present."""
    if not file_path.is_file() or file_path.stat().st_size == 0:
        return None
    if file_path.suffix.lower() in [".sha256sum", ".sha256", ".md5sum", ".md5", ".json", ".txt"]:
        return None

    for ext in [".md5sum", ".md5"]:
        existing = file_path.with_name(f"{file_path.name}{ext}")
        if existing.exists() and existing.stat().st_size > 0:
            return existing

    sum_file = file_path.with_name(f"{file_path.name}.md5sum")
    try:
        h = hashlib.md5()
        with file_path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        digest = h.hexdigest()
        sum_file.write_text(f"{digest}  {file_path.name}\n", encoding="utf-8")
        return sum_file
    except Exception as e:
        print(f">>> [WARN] Could not generate md5sum for {file_path.name}: {e}")
        return None


def detect_twrp_version() -> str:
    """Detect TWRP version dynamically from recovery source or compiled ramdisk props."""
    search_dirs = [
        Path("."),
        Path("/mnt/recovery/twrp"),
        Path("/mnt/recovery"),
        Path.home() / "twrp",
    ]
    for base in search_dirs:
        for vh in [
            base / "bootable" / "recovery" / "variables.h",
            base / "bootable" / "recovery-twrp" / "variables.h",
        ]:
            if vh.is_file():
                try:
                    for line in vh.read_text(errors="ignore").splitlines():
                        if "TW_MAIN_VERSION_STR" in line and "#define" in line:
                            m = re.search(r'"([^"]+)"', line)
                            if m:
                                vm = re.search(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?)", m.group(1))
                                if vm:
                                    return vm.group(1)
                except Exception:
                    pass

        # Check compiled prop.default / default.prop in out/
        for prop in base.glob("out/target/product/*/recovery/root/*prop*"):
            if prop.is_file():
                try:
                    for line in prop.read_text(errors="ignore").splitlines():
                        if line.startswith("ro.twrp.version="):
                            vm = re.search(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?)", line.split("=")[1])
                            if vm:
                                return vm.group(1)
                except Exception:
                    pass

    return "3.7.1"


def normalize_recovery_artifact(artifact: Path, remote_subdir: str = "", target_device: str = "") -> Path:
    """
    Ensure OFRP and TWRP recovery artifacts (.img, .zip) have an 8-digit timestamp (YYYYMMDD).
    If the file already has a timestamp, it is left untouched.
    """
    if not artifact.is_file() or artifact.suffix.lower() not in [".img", ".zip"]:
        return artifact

    # If the recovery file already has an 8-digit timestamp and is not mislabeled as 12.1, leave as-is
    if re.search(r'(20\d{6})', artifact.name) and "TWRP-12.1" not in artifact.name:
        return artifact

    sub_lower = remote_subdir.lower()
    name_lower = artifact.name.lower()

    # Determine recovery flavor: OrangeFox or TWRP
    flavor = ""
    if any(k in sub_lower or k in name_lower for k in ["orangefox", "ofrp"]):
        flavor = "OrangeFox"
    elif any(k in sub_lower or k in name_lower for k in ["twrp"]):
        flavor = "TWRP"
    else:
        return artifact

    # Determine device
    known_devs, default_recovery = load_config_devices()
    search_space = f"{remote_subdir} {target_device} {artifact.parent.name} {artifact.name}"
    dev = default_recovery
    for d in known_devs:
        if re.search(rf"\b{re.escape(d)}\b", search_space, re.IGNORECASE):
            dev = d
            break

    # Generate timestamp from file mtime or today
    try:
        mtime = artifact.stat().st_mtime
        ts = time.strftime("%Y%m%d", time.localtime(mtime))
    except Exception:
        ts = time.strftime("%Y%m%d")

    suffix = artifact.suffix.lower()

    # Format canonical name with timestamp
    if flavor == "OrangeFox":
        ver_match = re.search(r"(?:OrangeFox|OFRP)[_-]?(R\d+\.\d+)", artifact.name, re.IGNORECASE)
        version = ver_match.group(1).upper() if ver_match else "R12.0"
        target_name = f"OrangeFox-{version}-{ts}-Unofficial-{dev}{suffix}"
    else:
        ver_match = re.search(r"TWRP[_-]?(\d+\.\d+(?:\.\d+)?)", artifact.name, re.IGNORECASE)
        version = ver_match.group(1) if ver_match else ""
        if not version or version == "12.1":
            version = detect_twrp_version()
        target_name = f"TWRP-{version}_{ts}-Unofficial-{dev}{suffix}"

    new_path = artifact.with_name(target_name)
    if new_path == artifact:
        return artifact

    print(f">>> [RECOVERY RENAME] Renaming {artifact.name} -> {target_name} (added timestamp)")

    parent = artifact.parent
    old_stem = artifact.stem
    old_name = artifact.name
    companions_to_rename = []
    for cand in parent.iterdir():
        if not cand.is_file() or cand == artifact:
            continue
        if cand.suffix.lower() in [".img", ".zip"]:
            continue
        if artifact.suffix.lower() == ".zip" and ".img." in cand.name.lower():
            continue
        if artifact.suffix.lower() == ".img" and ".zip." in cand.name.lower():
            continue

        if cand.name.startswith(old_name):
            comp_suffix = cand.name[len(old_name):]
            companions_to_rename.append((cand, parent / f"{new_path.name}{comp_suffix}"))
        elif cand.stem == old_stem:
            companions_to_rename.append((cand, parent / f"{new_path.stem}{cand.suffix}"))

    try:
        if new_path.exists() and new_path != artifact:
            new_path.unlink()
        artifact.rename(new_path)

        for old_comp, new_comp in companions_to_rename:
            try:
                if new_comp.exists() and new_comp != old_comp:
                    new_comp.unlink()
                old_comp.rename(new_comp)
                print(f">>> [COMPANION RENAME] Renamed {old_comp.name} -> {new_comp.name}")
            except Exception:
                pass

        return new_path
    except Exception as e:
        print(f">>> [WARN] Could not rename {artifact.name} to {target_name}: {e}")
        return artifact


def resolve_artifacts(raw_inputs: list[str], build_type: str = "rom", remote_subdir: str = "") -> list[Path]:
    """Resolves cooked artifacts and companions from device names, directories, or direct file paths."""
    targets: list[Path] = []
    target_dev = raw_inputs[0] if raw_inputs else ""
    is_rec = (
        build_type.lower() == "recovery"
        or any(k in remote_subdir.lower() for k in ["recovery", "orangefox", "ofrp", "twrp"])
    )

    for item in raw_inputs:
        if not item:
            continue

        p = resolve_target_path(item)

        if p.is_dir():
            for cand in discover_candidates(p, build_type, remote_subdir=remote_subdir):
                if not cand.is_file():
                    continue
                if is_rec:
                    cand = normalize_recovery_artifact(cand, remote_subdir=remote_subdir, target_device=target_dev)
                if cand.is_file() and cand not in targets:
                    targets.append(cand)
                    sha_comp = generate_sha256sum(cand)
                    if sha_comp and sha_comp.is_file() and sha_comp not in targets:
                        targets.append(sha_comp)
                    md5_comp = generate_md5sum(cand)
                    if md5_comp and md5_comp.is_file() and md5_comp not in targets:
                        targets.append(md5_comp)
                    for comp in find_companions(cand):
                        if comp.is_file() and comp not in targets:
                            targets.append(comp)
            continue

        if not p.is_file() or p.stat().st_size == 0 or p in targets:
            continue

        if is_rec:
            p = normalize_recovery_artifact(p, remote_subdir=remote_subdir, target_device=target_dev)

        if p.is_file() and p not in targets:
            targets.append(p)
            sha_comp = generate_sha256sum(p)
            if sha_comp and sha_comp.is_file() and sha_comp not in targets:
                targets.append(sha_comp)
            md5_comp = generate_md5sum(p)
            if md5_comp and md5_comp.is_file() and md5_comp not in targets:
                targets.append(md5_comp)
            for comp in find_companions(p):
                if comp.is_file() and comp not in targets:
                    targets.append(comp)

    # Sanitize targets: only keep existing, non-empty files
    valid_targets: list[Path] = []
    for t in targets:
        try:
            if t.is_file() and t.stat().st_size > 0 and t not in valid_targets:
                valid_targets.append(t)
        except Exception:
            pass

    return valid_targets


def upload_sf(files: list[Path], remote_subdir: str, key_file: Path | None) -> str | None:
    """Uploads artifacts to SourceForge via rsync over SSH. Returns URL on success."""
    if not key_file:
        print(">>> [INFO] SourceForge SSH key not available. Skipping SourceForge upload.")
        return None

    dest = f"/home/frs/project/{SF_PROJECT}/{remote_subdir.strip('/')}/"
    print(f"\n>>> [SF UPLOAD] Uploading to {dest}...")

    try:
        dirs = remote_subdir.strip("/").split("/")
        curr = f"/home/frs/project/{SF_PROJECT}"
        sftp_cmds = "\n".join([f"mkdir {curr}/{'/'.join(dirs[:i+1])}" for i in range(len(dirs))]) + "\n"
        subprocess.run(
            ["sftp", "-i", str(key_file), "-o", "StrictHostKeyChecking=no", f"{SF_USER}@{SF_HOST}"],
            input=sftp_cmds,
            text=True,
            capture_output=True,
            timeout=60,
        )

        ssh_cmd = f"ssh -q -i {key_file} -o StrictHostKeyChecking=no"
        cmd = [
            "rsync", "-avz", "--partial", "--progress",
            "-e", ssh_cmd,
            *[str(f) for f in files],
            f"{SF_USER}@{SF_HOST}:{dest}",
        ]
        res = subprocess.run(cmd)
        if res.returncode == 0:
            url = f"https://sourceforge.net/projects/{SF_PROJECT}/files/{remote_subdir.strip('/')}/"
            print(f">>> [SF UPLOAD] Success! View at: {url}")
            return url
        else:
            print(f">>> [WARN] rsync exited with returncode {res.returncode}")
            return None
    except Exception as e:
        print(f">>> [WARN] SourceForge upload failed: {e}")
        return None


def get_gofile_server() -> str:
    """Fetch best active Gofile server."""
    try:
        res = subprocess.run(
            ["curl", "-s", "https://api.gofile.io/servers"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(res.stdout)
        if data.get("status") == "ok":
            servers = data.get("data", {}).get("servers", [])
            if servers:
                return servers[0].get("name", "store3")
    except Exception:
        pass
    return "store3"


def upload_fallback(files: list[Path], remote_subdir: str = "") -> list[tuple[str, str, str]]:
    """
    Uploads cooked artifacts to Gofile.io with Filebin fallback.
    All files from the build are uploaded into a single shared folder on Gofile
    (or single bin on Filebin), producing a single link containing all artifacts
    (ROM/recovery zip, img, sha256, md5, etc.).
    """
    if not files:
        return []

    print("\n>>> [GOFILE] Generating direct download links via Gofile.io (Single Folder Mode)...")
    gf_server = get_gofile_server()
    results: list[tuple[str, str, str]] = []

    # Identify primary artifact for naming the folder
    main_art = next((f for f in files if f.suffix.lower() in [".zip", ".img"]), files[0])
    folder_name = main_art.stem

    gofile_folder_id: str | None = None
    gofile_folder_link: str | None = None
    gofile_token: str | None = os.environ.get("GOFILE_TOKEN", "").strip() or None
    gofile_uploaded: list[Path] = []
    failed_files: list[Path] = []

    for f in files:
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f">>> Uploading {f.name} ({size_mb:.2f} MB)...")
        file_uploaded = False

        for attempt in range(1, 3):
            tmp_resp = Path(f"/tmp/gofile_resp_{int(time.time())}_{f.name}.json")
            try:
                cmd = [
                    "curl",
                    "-s",
                    "-F", f"file=@{f}",
                ]
                if gofile_folder_id:
                    cmd.extend(["-F", f"folderId={gofile_folder_id}"])
                if gofile_token:
                    cmd.extend(["-H", f"Authorization: Bearer {gofile_token}"])

                cmd.extend([
                    f"https://{gf_server}.gofile.io/contents/uploadfile",
                    "-o", str(tmp_resp),
                ])

                res = subprocess.run(cmd, timeout=1200)
                if res.returncode == 0 and tmp_resp.exists():
                    resp = json.loads(tmp_resp.read_text())
                    if resp.get("status") == "ok":
                        data = resp.get("data", {})
                        if not gofile_folder_id:
                            gofile_folder_id = data.get("parentFolder")
                            gofile_folder_link = data.get("downloadPage")
                            if not gofile_token:
                                gofile_token = data.get("guestToken")
                            # Set descriptive folder name on Gofile
                            if gofile_folder_id and gofile_token:
                                try:
                                    body = json.dumps({"attribute": "name", "attributeValue": folder_name})
                                    subprocess.run([
                                        "curl", "-s", "-X", "PUT",
                                        f"https://api.gofile.io/contents/{gofile_folder_id}/update",
                                        "-H", f"Authorization: Bearer {gofile_token}",
                                        "-H", "Content-Type: application/json",
                                        "-d", body,
                                    ], capture_output=True, timeout=15)
                                except Exception:
                                    pass

                        file_uploaded = True
                        gofile_uploaded.append(f)
                        print(f"    ✔ Uploaded {f.name} into Gofile folder")
                        break
                    else:
                        print(f">>> [WARN] Gofile API returned status: {resp.get('status')}")
            except Exception as e:
                if attempt == 2:
                    print(f">>> [WARN] Gofile upload exception for {f.name}: {e}")
            finally:
                tmp_resp.unlink(missing_ok=True)

        if not file_uploaded:
            failed_files.append(f)

    if gofile_uploaded and gofile_folder_link:
        print("\n" + "=" * 64)
        print(f"✅ [GOFILE SINGLE LINK] All build files uploaded to:")
        print(f"   {gofile_folder_link}")
        print(f"   Folder: {folder_name} ({len(gofile_uploaded)} files)")
        for uf in gofile_uploaded:
            sz = uf.stat().st_size / (1024 * 1024)
            print(f"     - {uf.name} ({sz:.2f} MB)")
        print("=" * 64)
        batch_label = f"{folder_name} ({len(gofile_uploaded)} files)" if len(gofile_uploaded) > 1 else folder_name
        results.append(("Gofile", batch_label, gofile_folder_link))

    # Filebin fallback for any files that failed Gofile upload
    if failed_files:
        print(f"\n>>> [WARN] {len(failed_files)} file(s) failed Gofile upload. Attempting Filebin fallback...")
        bin_id = f"realme-atoll-{folder_name[:24]}-{int(time.time())}".lower().replace("_", "-").replace(" ", "-")
        fb_uploaded: list[Path] = []

        for f in failed_files:
            tmp_fb = Path(f"/tmp/filebin_resp_{int(time.time())}_{f.name}.json")
            try:
                cmd = [
                    "curl",
                    "-s",
                    "-X", "POST",
                    "--data-binary", f"@{f}",
                    "-H", f"filename: {f.name}",
                    f"https://filebin.net/{bin_id}/{f.name}",
                    "-o", str(tmp_fb),
                ]
                res = subprocess.run(cmd, timeout=1200)
                if res.returncode == 0 and tmp_fb.exists():
                    fb_data = json.loads(tmp_fb.read_text())
                    if "file" in fb_data:
                        fb_uploaded.append(f)
                        print(f"    ✔ Uploaded {f.name} to Filebin bin {bin_id}")
            except Exception as e:
                print(f">>> [WARN] Filebin fallback exception for {f.name}: {e}")
            finally:
                tmp_fb.unlink(missing_ok=True)

        if fb_uploaded:
            bin_link = f"https://filebin.net/{bin_id}"
            print("\n" + "=" * 64)
            print(f"✅ [FILEBIN SINGLE LINK] Files uploaded to:")
            print(f"   {bin_link}")
            print("=" * 64)
            batch_label = f"{folder_name} ({len(fb_uploaded)} files)" if len(fb_uploaded) > 1 else folder_name
            results.append(("Filebin", batch_label, bin_link))

    return results


def record_links(entries: list[tuple[str, str, str]], remote_subdir: str) -> None:
    """Persist upload links on the VM; the local build menu captures the summary."""
    if not entries:
        return

    # Deduplicate entries by URL so each batch link is listed exactly once
    seen_urls = set()
    deduped_entries: list[tuple[str, str, str]] = []
    for prov, name, url in entries:
        if url not in seen_urls:
            seen_urls.add(url)
            deduped_entries.append((prov, name, url))

    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [f"[{ts}] [{remote_subdir}] [{prov}] {name} -> {url}" for prov, name, url in deduped_entries]

    print("\n=== UPLOAD LINKS SUMMARY ===")
    for line in lines:
        print(f" {line}")
    print("============================")

    # Keep a copy on the VM so the menu can also sync it over SSH while running.
    vm_log = Path.home() / ".upload_links.log"
    try:
        with vm_log.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f">>> Upload links saved to {vm_log}.")
    except Exception as e:
        print(f">>> [WARN] Failed to write {vm_log}: {e}")

def main():
    args = sys.argv[1:]
    do_sf = None
    do_gofile = None

    filtered_args = []
    for arg in args:
        if arg == "--sf":
            do_sf = True
        elif arg == "--no-sf":
            do_sf = False
        elif arg == "--gofile":
            do_gofile = True
        elif arg == "--no-gofile":
            do_gofile = False
        else:
            filtered_args.append(arg)

    if len(filtered_args) < 2:
        print(f"Usage: {sys.argv[0]} <remote_subdir> <device_or_path> [build_type] [extra_files...] [--sf|--no-sf] [--gofile|--no-gofile]")
        sys.exit(1)

    remote_subdir = filtered_args[0].strip("/")
    target_arg = filtered_args[1]

    explicit_type = ""
    extra_start = 2
    if len(filtered_args) > 2 and filtered_args[2].lower() in ["rom", "recovery", "kernel"]:
        explicit_type = filtered_args[2].lower()
        extra_start = 3

    build_type = explicit_type or ("recovery" if any(k in remote_subdir.lower() for k in ["recovery", "twrp", "orangefox", "ofrp"]) else "rom")
    raw_inputs = [target_arg] + filtered_args[extra_start:]

    files = resolve_artifacts(raw_inputs, build_type=build_type, remote_subdir=remote_subdir)
    if not files:
        print(f">>> [ERROR] No valid build artifacts found for target '{target_arg}' (build_type: {build_type}).")
        sys.exit(1)

    print(f"\nArtifacts to upload ({len(files)}):")
    for f in files:
        try:
            size_mb = f.stat().st_size / (1024 * 1024)
            print(f" - {f.name} ({size_mb:.2f} MB)")
        except Exception as e:
            print(f" - {f.name} (error reading size: {e})")

    # Determine SourceForge upload toggle
    if do_sf is None:
        sf_env = os.environ.get("SF_UPLOAD", "").strip().lower()
        do_sf = sf_env in ("1", "true", "yes", "on")

    # Determine Gofile upload toggle
    if do_gofile is None:
        skip_gofile = os.environ.get("SKIP_GOFILE", "").strip().lower() in ("1", "true", "yes", "on")
        do_gofile = not skip_gofile

    uploaded_links: list[tuple[str, str, str]] = []

    if do_sf:
        key_file = get_sf_key()
        if key_file:
            sf_url = upload_sf(files, remote_subdir, key_file)
            if sf_url:
                uploaded_links.append(("SourceForge", remote_subdir, sf_url))
    else:
        print("\n>>> [SOURCEFORGE] Skipped (not enabled for this build).")

    if do_gofile:
        uploaded_links.extend(upload_fallback(files, remote_subdir=remote_subdir))
    else:
        print("\n>>> [GOFILE] Skipped (not enabled for this build).")

    record_links(uploaded_links, remote_subdir)

    print("\nUpload complete.")


if __name__ == "__main__":
    main()
