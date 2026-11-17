#!/usr/bin/env python3
"""
Clean, delink, and reset script for Android ROM / Recovery source trees.
- Kills orphaned git/repo network processes from aborted syncs.
- Cleans all stale git & repo lock files (*.lock, index.lock, HEAD.lock, etc.).
- Cleans 0-byte corrupted .git/index files.
- Delinks and resets manually changed repos, extracted folders (e.g. kernel/realme/sm7125),
  symlinks, standalone clones, and manual branches/remotes in device/kernel/vendor trees.
- Resets patched build repositories to pristine HEAD state.

Can be executed directly via:
    curl -sLf https://raw.githubusercontent.com/custom-rom-tools/gcp_script/aosp-16/clean.py | python3
"""

import os
import sys
import shutil
import subprocess
import xml.etree.ElementTree as ET


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


def find_repo_root(start_dir="."):
    """Find the root of the Android repo checkout by walking up from start_dir."""
    cur = os.path.abspath(start_dir)
    while True:
        if os.path.isdir(os.path.join(cur, ".repo")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(start_dir)


def parse_manifests(repo_root):
    """
    Parse project declarations and remotes from .repo/manifest.xml,
    .repo/manifests/*.xml, and .repo/local_manifests/*.xml.
    Returns:
        projects: dict mapping relative path -> {name, remote, revision}
        remotes: dict mapping remote name -> fetch base URL
        all_paths: set of relative paths
        local_projects: set of relative paths declared in local_manifests
    """
    repo_dir = os.path.join(repo_root, ".repo")
    projects = {}
    remotes = {}
    default_remote = None
    default_revision = None
    local_projects = set()

    manifest_files = []
    main_mf = os.path.join(repo_dir, "manifest.xml")
    if os.path.isfile(main_mf) or os.path.islink(main_mf):
        manifest_files.append(main_mf)

    mf_dir = os.path.join(repo_dir, "manifests")
    if os.path.isdir(mf_dir):
        try:
            for f in sorted(os.listdir(mf_dir)):
                if f.endswith(".xml"):
                    fp = os.path.join(mf_dir, f)
                    if fp not in manifest_files:
                        manifest_files.append(fp)
        except OSError:
            pass

    local_manifest_files = []
    lm_dir = os.path.join(repo_dir, "local_manifests")
    if os.path.isdir(lm_dir):
        try:
            for f in sorted(os.listdir(lm_dir)):
                if f.endswith(".xml"):
                    local_manifest_files.append(os.path.join(lm_dir, f))
        except OSError:
            pass
    lm_file = os.path.join(repo_dir, "local_manifest.xml")
    if os.path.isfile(lm_file):
        local_manifest_files.append(lm_file)

    manifest_files.extend(local_manifest_files)

    for mf in manifest_files:
        is_local = mf in local_manifest_files
        try:
            tree = ET.parse(mf)
            root = tree.getroot()

            for r in root.findall("remote"):
                r_name = r.attrib.get("name")
                r_fetch = r.attrib.get("fetch", "")
                if r_name:
                    remotes[r_name] = r_fetch

            def_elem = root.find("default")
            if def_elem is not None:
                if "remote" in def_elem.attrib:
                    default_remote = def_elem.attrib["remote"]
                if "revision" in def_elem.attrib:
                    default_revision = def_elem.attrib["revision"]

            for rp in root.findall("remove-project"):
                rp_name = rp.attrib.get("name")
                rp_path = rp.attrib.get("path")
                to_del = [
                    k
                    for k, v in projects.items()
                    if v.get("name") == rp_name or k == rp_path
                ]
                for k in to_del:
                    del projects[k]
                    local_projects.discard(k)

            for p in root.findall("project"):
                p_name = p.attrib.get("name")
                p_path = p.attrib.get("path") or p_name
                p_remote = p.attrib.get("remote") or default_remote
                p_revision = p.attrib.get("revision") or default_revision
                if p_path:
                    p_path = os.path.normpath(p_path)
                    projects[p_path] = {
                        "name": p_name,
                        "remote": p_remote,
                        "revision": p_revision,
                    }
                    if is_local:
                        local_projects.add(p_path)
        except Exception:
            pass

    all_paths = set(projects.keys())
    plist = os.path.join(repo_dir, "project.list")
    if os.path.isfile(plist):
        try:
            with open(plist, "r", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        all_paths.add(os.path.normpath(line))
        except Exception:
            pass

    return projects, remotes, all_paths, local_projects


def is_repo_managed_worktree(project_dir, repo_root):
    """
    Check if a directory with a .git entry is managed by repo (connected to .repo/projects).
    Returns True if it belongs to repo, False if it is a standalone manual clone or extracted folder.
    """
    git_entry = os.path.join(project_dir, ".git")
    if not os.path.exists(git_entry) and not os.path.islink(git_entry):
        return False

    # 1. Symlinked .git pointing to .repo/projects
    if os.path.islink(git_entry):
        try:
            target = os.readlink(git_entry)
            if ".repo/projects" in target:
                return True
        except OSError:
            pass

    # 2. Worktree pointer file (.git as file containing "gitdir: ...")
    if os.path.isfile(git_entry):
        try:
            with open(git_entry, "r", errors="ignore") as f:
                content = f.read()
                if ".repo/projects" in content:
                    return True
        except Exception:
            pass

    # 3. Regular directory .git
    if os.path.isdir(git_entry):
        cfg_path = os.path.join(git_entry, "config")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", errors="ignore") as f:
                    cfg = f.read()
                    if '[remote "m"]' in cfg or ".repo/projects" in cfg:
                        return True
            except Exception:
                pass

        obj_path = os.path.join(git_entry, "objects")
        if os.path.islink(obj_path):
            try:
                if ".repo" in os.readlink(obj_path):
                    return True
            except OSError:
                pass

        # Check if corresponding .git exists in .repo/projects
        rel_path = os.path.relpath(project_dir, repo_root)
        proj_meta = os.path.join(repo_root, ".repo", "projects", rel_path + ".git")
        if os.path.isdir(proj_meta):
            return True

    return False


def get_candidate_paths(repo_root, projects, local_projects):
    """
    Get candidate paths that could be manually changed, extracted, or overridden.
    Only inspects:
    1. Projects defined in .repo/local_manifests (device/vendor/kernel/hardware trees).
    2. Any directory or symlink under kernel/ (depth <= 3, e.g. kernel/realme/sm7125).
    3. Any directory or symlink under device/, vendor/, hardware/ matching device trees or OEMs.
    """
    candidates = set(local_projects)

    # 1. Inspect kernel/ for extracted folders, standalone clones, or symlinks
    kernel_root = os.path.join(repo_root, "kernel")
    if os.path.isdir(kernel_root):
        for r, dirs, files in os.walk(kernel_root):
            rel = os.path.relpath(r, repo_root)
            depth = len(rel.split(os.sep))
            if depth <= 3:
                if ".git" in dirs or not dirs or rel in projects:
                    candidates.add(os.path.normpath(rel))
            if depth >= 3 and ".git" in dirs:
                dirs.remove(".git")

    # 2. Inspect device/, vendor/, hardware/ for OEM subdirectories or symlinks
    for top in ["device", "vendor", "hardware"]:
        top_full = os.path.join(repo_root, top)
        if not os.path.isdir(top_full):
            continue
        try:
            for entry in os.scandir(top_full):
                if entry.is_symlink():
                    candidates.add(os.path.normpath(os.path.relpath(entry.path, repo_root)))
                elif entry.is_dir():
                    oem_dir = entry.path
                    for sub_entry in os.scandir(oem_dir):
                        if sub_entry.is_symlink() or sub_entry.is_dir():
                            rel_sub = os.path.normpath(os.path.relpath(sub_entry.path, repo_root))
                            candidates.add(rel_sub)
        except OSError:
            pass

    # 3. Add any project from manifest that starts with kernel/ or matches local_projects
    for p_path in projects:
        if p_path.startswith("kernel/") or p_path in local_projects:
            candidates.add(os.path.normpath(p_path))

    return candidates


def delink_manual_repos(root_dir="."):
    """
    Delink repositories with manually added URLs, manual branches, symlinks,
    or standalone manual git clones that conflict with repo manifest.
    Only resets repositories that are changed manually or extracted (e.g. kernel/realme/sm7125).
    """
    print("=== Delinking Manual Repositories & Non-Manifest URLs/Branches ===")
    repo_root = find_repo_root(root_dir)
    repo_dir = os.path.join(repo_root, ".repo")

    if not os.path.isdir(repo_dir):
        print("No .repo directory found; skipping repo delinking.")
        return

    projects, remotes, all_paths, local_projects = parse_manifests(repo_root)

    candidate_paths = get_candidate_paths(repo_root, projects, local_projects)
    delinked_count = 0

    for rel_path in sorted(candidate_paths):
        full_path = os.path.join(repo_root, rel_path)

        # 1. Delink symlinks (e.g. manual ln -s of kernel/device)
        if os.path.islink(full_path):
            try:
                target = os.readlink(full_path)
                print(f"Delinking manual symlink: {rel_path} -> {target}")
                os.unlink(full_path)
                delinked_count += 1
                continue
            except OSError as e:
                print(f"Error removing symlink {rel_path}: {e}")
                continue

        if not os.path.exists(full_path):
            continue

        p_info = projects.get(rel_path, {})
        manifest_proj_name = p_info.get("name")

        # 2. Check if this is an unmanaged directory or standalone manual clone (not connected to .repo)
        if os.path.isdir(full_path) and not is_repo_managed_worktree(full_path, repo_root):
            print(
                f"Delinking manual extracted folder / standalone clone: {rel_path}"
            )
            try:
                shutil.rmtree(full_path)
                delinked_count += 1
            except OSError as e:
                print(f"Error removing unmanaged folder {rel_path}: {e}")
            continue

        # 3. For repo-managed worktrees:
        # Clean stale locks in this repository
        for lock in ["index.lock", "HEAD.lock", "config.lock", "shallow.lock"]:
            lp = os.path.join(full_path, ".git", lock)
            if os.path.isfile(lp):
                try:
                    os.remove(lp)
                except OSError:
                    pass

        # Check if the repository name in manifest changed (different repo is used for this path)
        proj_cfg = os.path.join(repo_root, ".repo", "projects", rel_path + ".git", "config")
        current_proj_name = None
        if os.path.isfile(proj_cfg):
            try:
                with open(proj_cfg, "r", errors="ignore") as f:
                    for line in f:
                        if "projectname" in line and "=" in line:
                            current_proj_name = line.split("=", 1)[1].strip()
                            break
            except Exception:
                pass

        if current_proj_name and manifest_proj_name and current_proj_name != manifest_proj_name:
            print(
                f"Delinking old repository in {rel_path}: {current_proj_name} -> {manifest_proj_name}"
            )
            try:
                shutil.rmtree(full_path)
                proj_meta_dir = os.path.join(repo_root, ".repo", "projects", rel_path + ".git")
                if os.path.isdir(proj_meta_dir):
                    shutil.rmtree(proj_meta_dir)
                delinked_count += 1
                continue
            except OSError as e:
                print(f"Error removing old repo metadata {rel_path}: {e}")

        # A. Check and remove manual git remotes
        res = subprocess.run(
            ["git", "-C", full_path, "remote"],
            capture_output=True,
            text=True,
        )
        current_remotes = [
            r.strip() for r in res.stdout.splitlines() if r.strip()
        ]

        legitimate_remotes = {"m", "origin"}
        if p_info.get("remote"):
            legitimate_remotes.add(p_info["remote"])
        for r_name in remotes:
            legitimate_remotes.add(r_name)

        for cr in current_remotes:
            if cr not in legitimate_remotes:
                print(f"Delinking manual git remote in {rel_path}: {cr}")
                subprocess.run(
                    ["git", "-C", full_path, "remote", "remove", cr],
                    capture_output=True,
                )
                delinked_count += 1

        # B. Check for manual checked-out branch
        res_branch = subprocess.run(
            ["git", "-C", full_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        curr_branch = res_branch.stdout.strip()
        had_manual_branch = False

        if curr_branch and curr_branch != "HEAD":
            print(
                f"Delinking manual branch in {rel_path}: {curr_branch}"
            )
            subprocess.run(
                ["git", "-C", full_path, "checkout", "-f", "--detach"],
                capture_output=True,
            )
            had_manual_branch = True
            delinked_count += 1

        # Delete any local branch refs so repo can checkout manifest revision cleanly
        res_branches = subprocess.run(
            [
                "git",
                "-C",
                full_path,
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/",
            ],
            capture_output=True,
            text=True,
        )
        for b in res_branches.stdout.splitlines():
            b = b.strip()
            if b:
                subprocess.run(
                    ["git", "-C", full_path, "branch", "-D", b],
                    capture_output=True,
                )

        # C. Check if dirty (manual uncommitted changes, untracked files from extracted archives)
        res_status = subprocess.run(
            ["git", "-C", full_path, "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        is_dirty = bool(res_status.stdout.strip())

        if is_dirty or had_manual_branch:
            print(f"Resetting manual changes in {rel_path}")
            subprocess.run(
                ["git", "-C", full_path, "reset", "--hard", "HEAD"],
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", full_path, "clean", "-fdx"],
                capture_output=True,
            )
            delinked_count += 1

    if delinked_count > 0:
        print(f"Delinked / reset {delinked_count} manual repo/branch/remote/symlink items.")
    else:
        print("All target repositories are cleanly linked to manifest.")


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
    root_dir = (
        sys.argv[1]
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
        else "."
    )
    kill_stale_git_processes()
    clean_locks(root_dir)
    delink_manual_repos(root_dir)
    reset_patched_repos()


if __name__ == "__main__":
    main()
