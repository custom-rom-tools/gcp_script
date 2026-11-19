#!/usr/bin/env python3
"""
Custom Patch Hook for ROM / Recovery source trees.
Invoked before building. Add any custom cherry-picks, sed replacements,
or file patches here.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def apply_patch(repo_dir: Path, patch_file: Path) -> bool:
    """Apply a unified diff / patch file to a repository."""
    if not patch_file.is_file() or not repo_dir.is_dir():
        return False
    print(f">>> Applying patch: {patch_file.name} to {repo_dir.name}")
    res = subprocess.run(
        ["git", "-C", str(repo_dir), "apply", "--whitespace=nowarn", str(patch_file)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f">>> [WARN] Patch {patch_file.name} failed: {res.stderr.strip()}")
        return False
    return True


def cherry_pick(repo_dir: Path, commit_hash: str) -> bool:
    """Cherry pick a commit into a repository."""
    if not repo_dir.is_dir():
        return False
    print(f">>> Cherry-picking {commit_hash} in {repo_dir.name}")
    res = subprocess.run(
        ["git", "-C", str(repo_dir), "cherry-pick", commit_hash],
        capture_output=True,
        text=True,
    )
    return res.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ROM/Recovery source patcher")
    parser.add_argument("--target", default="", help="Target type (16, 17, recovery, kernel)")
    parser.add_argument("--rom", default="", help="ROM name (e.g. evox, lineage)")
    parser.add_argument("--flavor", default="", help="Recovery flavor (e.g. twrp, ofox)")
    parser.add_argument("--device", "--dev", dest="device", default="", help="Target device code")
    args = parser.parse_args()

    target_desc = f"Target: {args.target or 'all'} | ROM: {args.rom or 'none'} | Flavor: {args.flavor or 'none'} | Device: {args.device or 'all'}"
    print(f">>> [PATCH] Checking patch requirements ({target_desc})")

    # --- Add custom patch hooks below as needed ---
    # Example:
    # work_dir = Path.cwd()
    # if args.target == "recovery" and args.flavor == "twrp":
    #     cherry_pick(work_dir / "bootable" / "recovery", "<commit-hash>")

    print(">>> [PATCH] Source tree is ready for build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
