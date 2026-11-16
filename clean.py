#!/usr/bin/env python3
"""
Clean and reset script for realme-atoll ROM builds.
- Kills orphaned git/repo network processes from aborted syncs.
- Cleans all stale git & repo lock files (*.lock, index.lock, HEAD.lock, etc.).
- Cleans 0-byte corrupted .git/index files.
- Resets patched build repositories to pristine HEAD state.

Can be executed directly via:
    curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-17/clean.py | python3
"""

import os
import sys
import subprocess


def kill_stale_git_processes():
    """Terminate lingering git network/fetch processes from aborted syncs."""
    try:
        subprocess.run(
            ["pkill", "-9", "-f", "git-remote-http|git-remote-https|git index-pack"],
            capture_output=True,
        )
    except Exception:
        pass


def clean_locks(root_dir="."):
    """Find and delete all stale git and repo lock files and zero-byte index files."""
    print("=== Cleaning Stale Git & Repo Lock Files ===")
    deleted = []

    # 1. Clean all .lock files inside .repo (safe: .repo only holds git/repo metadata)
    repo_dir = os.path.join(root_dir, ".repo")
    if os.path.isdir(repo_dir):
        try:
            res = subprocess.run(
                ["find", repo_dir, "-type", "f", "-name", "*.lock"],
                capture_output=True,
                text=True,
            )
            for path in res.stdout.splitlines():
                path = path.strip()
                if path:
                    try:
                        os.remove(path)
                        deleted.append(path)
                    except OSError:
                        pass
        except Exception:
            for r, _, files in os.walk(repo_dir):
                for f in files:
                    if f.endswith(".lock"):
                        fp = os.path.join(r, f)
                        try:
                            os.remove(fp)
                            deleted.append(fp)
                        except OSError:
                            pass

    # 2. Clean git lock files across the workspace (index.lock, HEAD.lock, config.lock, shallow.lock, packed-refs.lock)
    # Excludes legitimate source lockfiles such as Cargo.lock, yarn.lock, poetry.lock.
    try:
        res = subprocess.run(
            [
                "find",
                root_dir,
                "-type",
                "f",
                "(",
                "-name",
                "index.lock",
                "-o",
                "-name",
                "HEAD.lock",
                "-o",
                "-name",
                "config.lock",
                "-o",
                "-name",
                "shallow.lock",
                "-o",
                "-name",
                "packed-refs.lock",
                "-o",
                "-path",
                "*/.git/*",
                "-name",
                "*.lock",
                ")",
            ],
            capture_output=True,
            text=True,
        )
        for path in res.stdout.splitlines():
            path = path.strip()
            if path:
                try:
                    os.remove(path)
                    deleted.append(path)
                except OSError:
                    pass
    except Exception:
        git_locks = {
            "index.lock",
            "HEAD.lock",
            "config.lock",
            "shallow.lock",
            "packed-refs.lock",
        }
        for r, dirs, files in os.walk(root_dir):
            if ".repo" in r.split(os.sep):
                continue
            is_in_git = ".git" in r.split(os.sep)
            for f in files:
                if (is_in_git and f.endswith(".lock")) or (f in git_locks):
                    fp = os.path.join(r, f)
                    try:
                        os.remove(fp)
                        deleted.append(fp)
                    except OSError:
                        pass

    # 3. Clean corrupted zero-byte .git/index files
    try:
        res = subprocess.run(
            ["find", root_dir, "-type", "f", "-path", "*/.git/index", "-size", "0c"],
            capture_output=True,
            text=True,
        )
        for path in res.stdout.splitlines():
            path = path.strip()
            if path:
                try:
                    os.remove(path)
                    deleted.append(f"{path} (0-byte corrupted index)")
                except OSError:
                    pass
    except Exception:
        pass

    if deleted:
        print(f"Removed {len(deleted)} stale lock / corrupted index file(s):")
        for p in deleted[:15]:
            print(f"  - {p}")
        if len(deleted) > 15:
            print(f"  ... and {len(deleted) - 15} more.")
    else:
        print("No stale lock files found.")


def reset_patched_repos():
    """Reset tracked patched repositories to clean HEAD state."""
    print("=== Cleaning Patched Repositories ===")
    target_dirs = [
        ".repo/manifests",
        ".repo/repo",
        ".repo/local_manifests",
        "build/soong",
        "bootable/recovery",
    ]
    for gdir in target_dirs:
        if os.path.isdir(os.path.join(gdir, ".git")):
            for lock in ["index.lock", "HEAD.lock", "config.lock"]:
                lp = os.path.join(gdir, ".git", lock)
                if os.path.isfile(lp):
                    try:
                        os.remove(lp)
                    except OSError:
                        pass
            subprocess.run(
                ["git", "-C", gdir, "reset", "--hard", "HEAD"], capture_output=True
            )
            subprocess.run(
                ["git", "-C", gdir, "clean", "-fdx"], capture_output=True
            )
    print("=== Cleanup Complete ===")


def main():
    kill_stale_git_processes()
    clean_locks()
    reset_patched_repos()


if __name__ == "__main__":
    main()
