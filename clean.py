#!/usr/bin/env python3
"""
Clean and reset script for realme-atoll ROM builds.
Can be executed directly via:
    curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-17/clean.py | python3
"""

import os
import subprocess

def clean():
    print("=== Cleaning Patched Repositories ===")
    # Reset patched build repositories and clean dirty repo tools/manifests
    for gdir in [".repo/manifests", ".repo/repo", ".repo/local_manifests", "build/soong"]:
        if os.path.isdir(os.path.join(gdir, ".git")):
            subprocess.run(["git", "-C", gdir, "reset", "--hard", "HEAD"], capture_output=True)
            subprocess.run(["git", "-C", gdir, "clean", "-fdx"], capture_output=True)
    print("=== Cleanup Complete ===")

if __name__ == "__main__":
    clean()
