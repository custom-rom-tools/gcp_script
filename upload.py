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


def get_sf_key() -> Path | None:
    """
    Resolve SourceForge SSH private key:
    1. If SF_KEY is provided: decode base64 key and use it.
    2. Else if SF_UPLOAD is truthy: look in persistent SSH locations.
    3. Otherwise: skip SourceForge upload.
    """
    sf_key_b64 = os.environ.get("SF_KEY", "").strip()
    sf_upload = os.environ.get("SF_UPLOAD", "").strip().lower()

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

    if sf_upload in ("1", "true", "yes", "on"):
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

        print(">>> [WARN] SF_UPLOAD is enabled, but crave_sf key was not found. Skipping SourceForge.")
        return None

    return None


def get_build_sort_key(file_path: Path) -> tuple:
    """Sort key: 8-digit date (YYYYMMDD) then file modification time."""
    date_match = re.search(r'(20\d{6})', file_path.name)
    date_str = date_match.group(1) if date_match else "00000000"
    return (date_str, file_path.stat().st_mtime)


def find_companions(artifact: Path) -> list[Path]:
    """Find companion checksum/manifest/changelog files (.sha256sum, .md5sum, .json, .txt)."""
    companions: list[Path] = []
    parent, stem = artifact.parent, artifact.stem

    for cand in sorted(parent.iterdir()):
        if not cand.is_file() or cand.stat().st_size == 0 or cand == artifact:
            continue
        if not (cand.name.startswith(stem) or "changelog" in cand.name.lower()):
            continue

        match cand.suffix.lower():
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
    """Resolves an input item to a Path, checking direct path or out/target/product."""
    p = Path(item)
    if p.exists():
        return p

    base = Path("out/target/product")
    for cand in [base / item, base / item.lower(), base / item.upper()]:
        if cand.is_dir():
            return cand

    return p


def is_rom_zip(p: Path) -> bool:
    """Validate whether a zip file is a cooked ROM installer."""
    name = p.name.lower()
    stem = p.stem.lower()
    if any(ign in name for ign in ["target_files", "target-files", "symbols", "orangefox", "twrp"]):
        return False
    if any(ota in stem for ota in ["-ota", "_ota", ".ota"]) or stem.endswith("ota") or stem.startswith("ota"):
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

            zips = [
                z for z in all_zips
                if any(k in z.name.lower() for k in ["orangefox", "twrp", "recovery"])
                and not any(ign in z.name.lower() for ign in ["target_files", "target-files", "symbols", "ota_update"])
            ]
            if rec_flavor:
                flavored = [z for z in zips if rec_flavor in z.name.lower()]
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
                if any(k in i.name.lower() for k in ["orangefox", "twrp", "recovery"])
            ]
            if rec_flavor:
                flavored_imgs = [i for i in imgs if rec_flavor in i.name.lower()]
                if flavored_imgs:
                    imgs = flavored_imgs

            if imgs:
                branded = [i for i in imgs if any(k in i.name.lower() for k in ["orangefox", "twrp"])]
                chosen = branded if branded else imgs
                chosen.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                candidates.append(chosen[0])

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
                zips.sort(key=get_build_sort_key, reverse=True)
                candidates.append(zips[0])

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


def normalize_recovery_artifact(artifact: Path, remote_subdir: str = "", target_device: str = "") -> Path:
    """
    Ensure OFRP and TWRP recovery artifacts (.img, .zip) have an 8-digit timestamp
    (YYYYMMDD) and standard canonical naming before uploading.
    """
    if not artifact.is_file() or artifact.suffix.lower() not in [".img", ".zip"]:
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
    known_devs = ["RMX2170", "RMX2061", "RMX3081", "sm7125"]
    search_space = f"{remote_subdir} {target_device} {artifact.parent.name} {artifact.name}"
    dev = "sm7125"
    for d in known_devs:
        if re.search(rf"\b{re.escape(d)}\b", search_space, re.IGNORECASE):
            dev = "sm7125" if d.lower() == "sm7125" else d.upper()
            break

    # Determine timestamp: extract existing 8-digit date (YYYYMMDD) or generate from file mtime / today
    date_match = re.search(r'(20\d{6})', artifact.name)
    if date_match:
        ts = date_match.group(1)
    else:
        try:
            mtime = artifact.stat().st_mtime
            ts = time.strftime("%Y%m%d", time.localtime(mtime))
        except Exception:
            ts = time.strftime("%Y%m%d")

    suffix = artifact.suffix.lower()

    # Format canonical name with timestamp
    if flavor == "OrangeFox":
        ver_match = re.search(r"OrangeFox[_-]?(R\d+\.\d+)", artifact.name, re.IGNORECASE)
        version = ver_match.group(1).upper() if ver_match else "R12.0"
        target_name = f"OrangeFox-{version}-{ts}-Unofficial-{dev}{suffix}"
    else:
        ver_match = re.search(r"TWRP[_-]?(\d+\.\d+(?:\.\d+)?)", artifact.name, re.IGNORECASE)
        version = ver_match.group(1) if ver_match else "12.1"
        target_name = f"TWRP-{version}-{ts}-Unofficial-{dev}{suffix}"

    if target_name == artifact.name:
        return artifact

    new_path = artifact.with_name(target_name)
    print(f">>> [RECOVERY RENAME] Renaming {artifact.name} -> {target_name} (with timestamp)")

    parent = artifact.parent
    old_stem = artifact.stem
    old_name = artifact.name
    companions_to_rename = []
    for cand in parent.iterdir():
        if cand.is_file() and cand != artifact and (cand.name.startswith(old_stem) or cand.name.startswith(old_name)):
            comp_suffix = cand.name[len(old_name):] if cand.name.startswith(old_name) else cand.name[len(old_stem):]
            companions_to_rename.append((cand, parent / f"{new_path.stem}{comp_suffix}"))

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
                if is_rec:
                    cand = normalize_recovery_artifact(cand, remote_subdir=remote_subdir, target_device=target_dev)
                if cand not in targets:
                    targets.append(cand)
                    sha_comp = generate_sha256sum(cand)
                    if sha_comp and sha_comp not in targets:
                        targets.append(sha_comp)
                    for comp in find_companions(cand):
                        if comp not in targets:
                            targets.append(comp)
            continue

        if not p.is_file() or p.stat().st_size == 0 or p in targets:
            continue

        if is_rec:
            p = normalize_recovery_artifact(p, remote_subdir=remote_subdir, target_device=target_dev)

        if p not in targets:
            targets.append(p)
            sha_comp = generate_sha256sum(p)
            if sha_comp and sha_comp not in targets:
                targets.append(sha_comp)
            for comp in find_companions(p):
                if comp not in targets:
                    targets.append(comp)

    return targets


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


def upload_fallback(files: list[Path]) -> list[tuple[str, str, str]]:
    """Uploads cooked artifacts to Gofile.io with Filebin fallback, returning (provider, filename, url) entries."""
    print("\n>>> [GOFILE] Generating direct download links via Gofile.io...")
    gf_server = get_gofile_server()
    results: list[tuple[str, str, str]] = []

    for f in files:
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f">>> Uploading {f.name} ({size_mb:.2f} MB)...")
        link = None

        for attempt in range(1, 3):
            tmp_resp = Path(f"/tmp/gofile_resp_{int(time.time())}_{f.name}.json")
            try:
                cmd = [
                    "curl",
                    "-F", f"file=@{f}",
                    f"https://{gf_server}.gofile.io/contents/uploadfile",
                    "-o", str(tmp_resp),
                ]
                res = subprocess.run(cmd, timeout=1200)
                if res.returncode == 0 and tmp_resp.exists():
                    resp = json.loads(tmp_resp.read_text())
                    if resp.get("status") == "ok":
                        link = resp.get("data", {}).get("downloadPage")
                        if link:
                            print(f"\n✅ Download link ({f.name}) [Gofile]: {link}")
                            results.append(("Gofile", f.name, link))
                            break
            except Exception as e:
                if attempt == 2:
                    print(f"\n>>> [WARN] Gofile upload exception: {e}")
            finally:
                tmp_resp.unlink(missing_ok=True)

        if not link:
            print(f"\n>>> [WARN] Gofile failed for {f.name}. Attempting Filebin fallback...")
            tmp_fb = Path(f"/tmp/filebin_resp_{int(time.time())}_{f.name}.json")
            try:
                bin_id = f"realme-atoll-{int(time.time())}"
                cmd = [
                    "curl",
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
                        link = f"https://filebin.net/{bin_id}/{f.name}"
                        print(f"\n✅ Download link ({f.name}) [Filebin]: {link}")
                        results.append(("Filebin", f.name, link))
            except Exception as e:
                print(f"\n>>> [WARN] Filebin fallback exception: {e}")
            finally:
                tmp_fb.unlink(missing_ok=True)

    return results


def record_links(entries: list[tuple[str, str, str]], remote_subdir: str) -> None:
    """Persist upload links on the VM; the local build menu captures the summary."""
    if not entries:
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [f"[{ts}] [{remote_subdir}] [{prov}] {name} -> {url}" for prov, name, url in entries]

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
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <remote_subdir> <device_or_path> [build_type] [extra_files...]")
        sys.exit(1)

    remote_subdir = sys.argv[1].strip("/")
    target_arg = sys.argv[2]

    explicit_type = ""
    extra_start = 3
    if len(sys.argv) > 3 and sys.argv[3].lower() in ["rom", "recovery"]:
        explicit_type = sys.argv[3].lower()
        extra_start = 4

    build_type = explicit_type or ("recovery" if any(k in remote_subdir.lower() for k in ["recovery", "twrp", "orangefox", "ofrp"]) else "rom")
    raw_inputs = [target_arg] + sys.argv[extra_start:]

    files = resolve_artifacts(raw_inputs, build_type=build_type, remote_subdir=remote_subdir)
    if not files:
        print(f">>> [ERROR] No valid build artifacts found for target '{target_arg}' (build_type: {build_type}).")
        sys.exit(1)

    print(f"\nArtifacts to upload ({len(files)}):")
    for f in files:
        print(f" - {f.name} ({f.stat().st_size / (1024 * 1024):.2f} MB)")

    uploaded_links: list[tuple[str, str, str]] = []

    key_file = get_sf_key()
    if key_file:
        sf_url = upload_sf(files, remote_subdir, key_file)
        if sf_url:
            uploaded_links.append(("SourceForge", remote_subdir, sf_url))

    skip_gofile = os.environ.get("SKIP_GOFILE", "").strip().lower() in ("1", "true", "yes")
    if not skip_gofile:
        uploaded_links.extend(upload_fallback(files))

    record_links(uploaded_links, remote_subdir)

    print("\nUpload complete.")


if __name__ == "__main__":
    main()
