#!/usr/bin/env python3
"""
Clean and reset script for Android ROM / Recovery source trees.
- Kills orphaned git network processes from aborted syncs.
- Cleans stale lock files (*.lock, index.lock, HEAD.lock) and 0-byte .git/index files.
- Removes unmanaged/manual clones (e.g. kernel/realme/sm7125 cloned via git origin).
- Resets dirty changes and detaches HEAD on repo-managed trees so repo sync succeeds.
- NOTE: NEVER touches out/ (intermediate build objects are 100% preserved).
"""

import argparse
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def kill_stale_processes() -> None:
    """Terminate lingering git network/fetch processes from aborted syncs."""
    try:
        subprocess.run(
            ["pkill", "-9", "-f", "git-remote-http|git-remote-https|git index-pack"],
            capture_output=True,
        )
    except Exception:
        pass


def clean_locks(repo_root: Path) -> None:
    """Find and remove stale git/repo lock files and zero-byte corrupted index files."""
    print(">>> Cleaning stale git & repo lock files...")

    # 1. Clean locks inside .repo
    repo_meta = repo_root / ".repo"
    if repo_meta.is_dir():
        subprocess.run(["find", str(repo_meta), "-name", "*.lock", "-delete"], capture_output=True)
        subprocess.run(["find", str(repo_meta), "-path", "*/.git/index", "-size", "0c", "-delete"], capture_output=True)

    # 2. Clean locks across source trees (excludes out/)
    for folder in ["device", "vendor", "kernel", "hardware", "build", "bootable"]:
        target_dir = repo_root / folder
        if target_dir.is_dir():
            subprocess.run(
                ["find", str(target_dir), "-name", "index.lock", "-o", "-name", "HEAD.lock", "-o", "-name", "config.lock", "-delete"],
                capture_output=True,
            )
            subprocess.run(
                ["find", str(target_dir), "-path", "*/.git/index", "-size", "0c", "-delete"],
                capture_output=True,
            )


def is_repo_managed(repo_dir: Path, repo_root: Path) -> bool:
    """Check if a project directory is managed by android repo tool (connected to .repo)."""
    git_entry = repo_dir / ".git"
    if not git_entry.exists() and not git_entry.is_symlink():
        return False

    # Symlink pointing into .repo/projects
    if git_entry.is_symlink():
        try:
            return ".repo" in os.readlink(str(git_entry))
        except OSError:
            return False

    # Gitfile (worktree pointer) containing gitdir: .../.repo/...
    if git_entry.is_file():
        try:
            return ".repo" in git_entry.read_text(errors="ignore")
        except Exception:
            return False

    # Standalone .git directory: check config or matching .repo/projects directory
    if git_entry.is_dir():
        cfg = git_entry / "config"
        if cfg.is_file():
            try:
                content = cfg.read_text(errors="ignore")
                if '[remote "m"]' in content or ".repo/projects" in content:
                    return True
            except Exception:
                pass
        rel = repo_dir.relative_to(repo_root)
        if (repo_root / ".repo" / "projects" / f"{rel}.git").is_dir():
            return True

    return False


def find_target_repos(repo_root: Path) -> list[Path]:
    """Find device, vendor, kernel, hardware repos and local_manifest paths."""
    found: set[Path] = set()

    # 1. Projects declared in .repo/local_manifests
    lm_dir = repo_root / ".repo" / "local_manifests"
    if lm_dir.is_dir():
        for xml_file in lm_dir.glob("*.xml"):
            try:
                tree = ET.parse(xml_file)
                for p in tree.getroot().findall("project"):
                    path = p.attrib.get("path") or p.attrib.get("name")
                    if path:
                        target = repo_root / path.strip("/")
                        if target.exists():
                            found.add(target)
            except Exception:
                pass

    # 2. Inspect kernel, device, vendor, hardware (up to depth 3)
    for category in ["kernel", "device", "vendor", "hardware"]:
        cat_dir = repo_root / category
        if not cat_dir.is_dir():
            continue
        for root, dirs, _ in os.walk(str(cat_dir)):
            rel_parts = Path(root).relative_to(repo_root).parts
            if len(rel_parts) > 3:
                dirs.clear()
                continue
            git_path = Path(root) / ".git"
            if git_path.exists() or git_path.is_symlink():
                found.add(Path(root))
                dirs.clear()

    return sorted(found)


def reset_manual_repos(repo_root: Path) -> None:
    """Delink unmanaged clones and reset dirty/branch conflicts on repos before repo sync."""
    target_repos = find_target_repos(repo_root)
    if not target_repos:
        return

    print(f">>> Inspecting {len(target_repos)} target repositories...")
    for repo_dir in target_repos:
        rel = repo_dir.relative_to(repo_root)

        # 1. Delink manual symlinks
        if repo_dir.is_symlink():
            print(f">>> Removing manual symlink: {rel}")
            repo_dir.unlink()
            continue

        # 2. If it's a standalone manual git clone (not managed by repo), remove it
        if not is_repo_managed(repo_dir, repo_root):
            print(f">>> Removing manual/unmanaged clone: {rel}")
            shutil.rmtree(repo_dir, ignore_errors=True)
            continue

        # 3. Clean locks inside repo
        for lock in ["index.lock", "HEAD.lock", "config.lock"]:
            lp = repo_dir / ".git" / lock
            if lp.is_file():
                try:
                    lp.unlink()
                except OSError:
                    pass

        # 4. Reset uncommitted changes, old patches, and detach HEAD
        subprocess.run(["git", "-C", str(repo_dir), "reset", "--hard", "HEAD"], capture_output=True)
        subprocess.run(["git", "-C", str(repo_dir), "clean", "-fdx"], capture_output=True)
        subprocess.run(["git", "-C", str(repo_dir), "checkout", "-f", "--detach"], capture_output=True)

    # Reset common patched core repositories if present
    for core in ["build/soong", "bootable/recovery"]:
        core_dir = repo_root / core
        if (core_dir / ".git").exists():
            subprocess.run(["git", "-C", str(core_dir), "reset", "--hard", "HEAD"], capture_output=True)
            subprocess.run(["git", "-C", str(core_dir), "clean", "-fdx"], capture_output=True)


def reset_all_dirty_repos(repo_root: Path) -> None:
    """Scan all repos across the source tree and reset any repo that has uncommitted/local changes."""
    repos: list[Path] = []

    plist = repo_root / ".repo" / "project.list"
    if plist.is_file():
        try:
            for line in plist.read_text(encoding="utf-8", errors="ignore").splitlines():
                proj = line.strip()
                if proj:
                    p = repo_root / proj
                    if (p / ".git").exists() or (p / ".git").is_file():
                        repos.append(p)
        except Exception:
            pass

    # Fallback/supplement with common core trees if project.list is absent
    if not repos:
        for prefix in ["frameworks/base", "build/make", "build/soong", "system/core", "bootable/recovery"]:
            p = repo_root / prefix
            if (p / ".git").exists() or (p / ".git").is_file():
                repos.append(p)

    if not repos:
        return

    def _check_and_reset(p: Path) -> str | None:
        try:
            r = subprocess.run(
                ["git", "-C", str(p), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                rel = str(p.relative_to(repo_root))
                subprocess.run(["git", "-C", str(p), "reset", "--hard", "HEAD"], capture_output=True, timeout=30)
                subprocess.run(["git", "-C", str(p), "clean", "-fd"], capture_output=True, timeout=30)
                return rel
        except Exception:
            pass
        return None

    workers = min(32, os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        reset_list = [r for r in executor.map(_check_and_reset, repos) if r]

    if reset_list:
        print(f">>> [CLEAN] Reset {len(reset_list)} repository/repositories with local changes:")
        for r in reset_list:
            print(f"    - {r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean repo locks and reset manual repos")
    parser.add_argument("path", nargs="?", default=".", help="Root directory of repo source tree")
    parser.add_argument("--clean-trees", action="store_true", help="Clean trees flag")
    parser.add_argument("--target", default="", help="Target type")
    parser.add_argument("--device", "--dev", dest="device", default="", help="Device name")
    args, _ = parser.parse_known_args()

    repo_root = Path(args.path).resolve()
    print(f"=== Starting Source Workspace Cleanup in {repo_root} ===")
    kill_stale_processes()
    clean_locks(repo_root)
    reset_manual_repos(repo_root)
    reset_all_dirty_repos(repo_root)
    print("=== Cleanup Complete (out/ directory preserved) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
