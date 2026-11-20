#!/usr/bin/env python3
"""
Custom Patch Engine for ROM / Recovery source trees.
Automatically discovers and applies .patch / .diff files from patches/<target>/
before building.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def find_patches_dir() -> Path | None:
    """Find the root patches/ directory across standard local and VM paths."""
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir.parent / "patches",             # REPO_ROOT/patches
        script_dir / "patches",                    # scripts/patches
        Path.home() / ".gcp_farm" / "patches",     # ~/.gcp_farm/patches on VM
        Path("/tmp/patches"),                      # /tmp/patches on VM
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def parse_patch_target_files(patch_file: Path) -> list[str]:
    """Extract list of relative file paths modified in a patch file."""
    files: list[str] = []
    try:
        content = patch_file.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            m = re.match(r"^(?:---|\+\+\+)\s+[ab]/(.+)$", line)
            if m:
                path_str = m.group(1).strip()
                if path_str != "dev/null" and path_str not in files:
                    files.append(path_str)
    except Exception:
        pass
    return files


def find_target_repo(work_dir: Path, rel_file_path: str) -> tuple[Path, int]:
    """
    Find which git repository in work_dir owns the file path,
    and return (repo_path, strip_level).
    """
    # 1. Fast path: check .repo/project.list if present in Android tree
    plist = work_dir / ".repo" / "project.list"
    if plist.is_file():
        try:
            for line in plist.read_text(encoding="utf-8", errors="ignore").splitlines():
                proj = line.strip()
                if not proj:
                    continue
                # Case A: rel_file_path was generated inside the subrepo (e.g. packages/SystemUI/... inside frameworks/base)
                if (work_dir / proj / rel_file_path).is_file():
                    return work_dir / proj, 1
                # Case B: rel_file_path includes repo prefix (e.g. frameworks/base/packages/SystemUI/...)
                if rel_file_path.startswith(proj + "/"):
                    return work_dir / proj, len(Path(proj).parts) + 1
        except Exception:
            pass

    # 2. Check common Android framework repository prefixes
    for prefix in ["frameworks/base", "packages/apps/Settings", "system/core", "system/sepolicy", "build/make"]:
        if (work_dir / prefix / rel_file_path).is_file():
            return work_dir / prefix, 1
        if rel_file_path.startswith(prefix + "/"):
            return work_dir / prefix, len(Path(prefix).parts) + 1

    # 3. Direct file existence from work_dir
    if (work_dir / rel_file_path).is_file():
        curr = (work_dir / rel_file_path).parent
        while curr != work_dir.parent:
            if (curr / ".git").exists():
                return curr, 1
            curr = curr.parent
        return work_dir, 1

    # 4. Search by filename across top-level subtrees (pruning .repo and out)
    fname = Path(rel_file_path).name
    for top in ["frameworks", "packages", "system", "hardware", "vendor", "device"]:
        top_dir = work_dir / top
        if not top_dir.is_dir():
            continue
        for match in top_dir.rglob(fname):
            if str(match).endswith(rel_file_path):
                curr = match.parent
                while curr != work_dir.parent:
                    if (curr / ".git").exists():
                        return curr, 1
                    curr = curr.parent

    # Fallback to work_dir itself
    return work_dir, 1


def apply_patch_file(work_dir: Path, patch_file: Path) -> bool:
    """
    Safely apply a unified diff / patch file to the appropriate repository.
    Handles strip levels, checks for existing application, and falls back to -3.
    """
    if not patch_file.is_file():
        return False

    patch_name = patch_file.name
    rel_files = parse_patch_target_files(patch_file)

    target_repo = work_dir
    default_p = 1
    if rel_files:
        target_repo, default_p = find_target_repo(work_dir, rel_files[0])

    p_levels = [f"-p{default_p}", "-p1", "-p2", "-p0"]
    # De-duplicate p_levels preserving order
    seen_levels: set[str] = set()
    test_levels = [p for p in p_levels if not (p in seen_levels or seen_levels.add(p))]

    # 1. Test if patch is already applied (reverse check succeeds)
    for p_level in test_levels:
        for extra in [["-C1"], []]:
            cmd = ["git", "-C", str(target_repo), "apply", "--reverse", "--check"] + extra + [p_level, str(patch_file)]
            res_rev = subprocess.run(cmd, capture_output=True, text=True)
            if res_rev.returncode == 0:
                print(f">>> [PATCH] Already applied: {patch_name} (in {target_repo.name})")
                return True

    # 2. Try applying cleanly
    first_err = ""
    for p_level in test_levels:
        for extra in [["-C1"], []]:
            cmd_chk = ["git", "-C", str(target_repo), "apply", "--check"] + extra + [p_level, str(patch_file)]
            res_chk = subprocess.run(cmd_chk, capture_output=True, text=True)
            if not first_err and res_chk.stderr:
                first_err = res_chk.stderr.strip()
            if res_chk.returncode == 0:
                cmd_apply = ["git", "-C", str(target_repo), "apply", "--whitespace=nowarn"] + extra + [p_level, str(patch_file)]
                res_apply = subprocess.run(cmd_apply, capture_output=True, text=True)
                if res_apply.returncode == 0:
                    print(f">>> [PATCH] Successfully applied: {patch_name} (to {target_repo.name} {p_level})")
                    return True

    # 3. Try with 3-way merge fallback
    for p_level in test_levels:
        cmd_3way = ["git", "-C", str(target_repo), "apply", "-3", "--whitespace=nowarn", p_level, str(patch_file)]
        res_3way = subprocess.run(cmd_3way, capture_output=True, text=True)
        if res_3way.returncode == 0:
            print(f">>> [PATCH] Successfully applied (3-way): {patch_name} (to {target_repo.name})")
            return True

    # 4. If target_repo was a subrepo, also attempt from work_dir root
    if target_repo != work_dir:
        for p_level in ["-p1", "-p2"]:
            res_root = subprocess.run(
                ["git", "-C", str(work_dir), "apply", "--whitespace=nowarn", p_level, str(patch_file)],
                capture_output=True,
                text=True,
            )
            if res_root.returncode == 0:
                print(f">>> [PATCH] Successfully applied: {patch_name} (at root {p_level})")
                return True

    print(f">>> [WARN] Could not apply {patch_name} cleanly: {first_err or 'patch mismatch'}")
    return False


def fallback_text_interpolator_fix(work_dir: Path) -> bool:
    """Safe inline regex fallback for TextInterpolator if patch application had line drift."""
    patched = False
    import re
    candidates = list(work_dir.glob("frameworks/base/**/TextInterpolator.kt"))
    for file_path in candidates:
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if "if (baseGlyphId != targetGlyphId)" in content:
                print(f">>> [PATCH] TextInterpolator already patched with bypass in {file_path.name}")
                return True
            pattern = re.compile(
                r'require\s*\(\s*baseGlyphId\s*==\s*targetGlyphId\s*\)\s*\{[^}]*\}',
                re.MULTILINE,
            )
            if pattern.search(content):
                content = pattern.sub('if (baseGlyphId != targetGlyphId) {\n                return\n            }', content)
                file_path.write_text(content, encoding="utf-8")
                print(f">>> [PATCH] Fallback: regex patched TextInterpolator in {file_path.name}")
                patched = True
        except Exception as e:
            print(f">>> [WARN] Fallback exception on {file_path.name}: {e}")
    return patched


def collect_patch_files(patches_root: Path, target: str, rom: str = "", flavor: str = "") -> list[Path]:
    """Collect all relevant .patch and .diff files for the target build."""
    files: list[Path] = []
    seen: set[str] = set()

    def add_from_dir(d: Path) -> None:
        if not d.is_dir():
            return
        for p in sorted(d.glob("*.patch")) + sorted(d.glob("*.diff")):
            if p.is_file() and p.name not in seen:
                seen.add(p.name)
                files.append(p)

    def add_file(f: Path) -> None:
        if f.is_file() and f.name not in seen:
            seen.add(f.name)
            files.append(f)

    # 1. Global / Common patches
    add_from_dir(patches_root / "common")
    add_file(patches_root / "common.patch")

    # 2. Target specific (e.g. a16, a17, recovery)
    norm_target = target.lower().strip()
    target_names = [norm_target]
    if norm_target in ["16", "a16", "16.0"]:
        target_names = ["a16", "16"]
    elif norm_target in ["17", "a17", "17.0"]:
        target_names = ["a17", "17"]

    for t_name in target_names:
        add_from_dir(patches_root / t_name)
        add_file(patches_root / f"{t_name}.patch")

        if rom:
            # Handle e.g. "Evolution X" -> "evox" or "evolution x"
            clean_rom = rom.lower().replace(" ", "").replace("-", "")
            for candidate in [rom.lower(), clean_rom, "evox" if "evox" in clean_rom or "evolution" in clean_rom else ""]:
                if candidate:
                    add_from_dir(patches_root / t_name / candidate)
        if flavor:
            add_from_dir(patches_root / t_name / flavor.lower())

    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="ROM/Recovery source patch engine")
    parser.add_argument("--target", default="", help="Target type (16, 17, recovery, kernel)")
    parser.add_argument("--rom", default="", help="ROM name (e.g. evox, lineage)")
    parser.add_argument("--flavor", default="", help="Recovery flavor (e.g. twrp, ofox)")
    parser.add_argument("--device", "--dev", dest="device", default="", help="Target device code")
    args = parser.parse_args()

    target_desc = f"Target: {args.target or 'all'} | ROM: {args.rom or 'none'} | Flavor: {args.flavor or 'none'} | Device: {args.device or 'all'}"
    print(f">>> [PATCH] Checking patch requirements ({target_desc})")

    work_dir = Path.cwd()
    patches_root = find_patches_dir()

    if not patches_root:
        print(">>> [PATCH] No patches/ directory found. Source tree ready.")
        return 0

    patch_files = collect_patch_files(patches_root, args.target, args.rom, args.flavor)
    if not patch_files:
        print(">>> [PATCH] No matching .patch files found for target. Source tree ready.")
        return 0

    print(f">>> [PATCH] Found {len(patch_files)} patch(es) to process from {patches_root.name}:")
    for pf in patch_files:
        ok = apply_patch_file(work_dir, pf)
        # Extra resilience: if TextInterpolator patch failed to apply via git, invoke fallback
        if not ok and "textinterpolator" in pf.name.lower():
            fallback_text_interpolator_fix(work_dir)

    print(">>> [PATCH] All patches processed. Source tree is ready for build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
