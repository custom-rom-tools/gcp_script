"""
Upload Links Service (cli/services/link.py)
Merges the local link cache, the VM's ``~/.upload_links.log`` and the
``upload-links`` GCP instance metadata entry, then de-duplicates by URL.
The GCP metadata copy works even while the VM is stopped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cli.services.vm import is_vm_running, run_gcloud, vm_exec

LOCAL_LINKS_FILE = Path.home() / ".gcp_upload_links.log"

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
URL_RE = re.compile(r"^(.*?)\s*->\s*(https?://\S+)")
OUTPUT_RE = re.compile(r"^(?:\[[^\]]+\]\s*){1,6}(.+?)\s*->\s*(https?://\S+)")

LINK_CACHE_VERSION = 2


def _prefer_summary(existing: str, candidate: str) -> bool:
    """Prefer the batch summary line when the same URL appears twice."""
    existing_summary = "files)" in existing or "(All" in existing
    candidate_summary = "files)" in candidate or "(All" in candidate
    return candidate_summary and not existing_summary


def dedupe_links(lines: list[str]) -> list[str]:
    """Deduplicate link lines by URL, preserving order and summaries."""
    by_url: dict[str, str] = {}
    for raw in lines:
        line = ANSI_RE.sub("", raw).strip()
        if not line:
            continue
        match = URL_RE.match(line)
        if not match:
            continue
        url = match.group(2)
        if url not in by_url:
            by_url[url] = line
        elif _prefer_summary(by_url[url], line):
            by_url[url] = line
    return list(by_url.values())


def _metadata_links(vm: dict) -> list[str]:
    if not vm or not vm.get("instance") or not vm.get("zone"):
        return []
    args = [
        "compute", "instances", "describe", vm["instance"],
        f"--zone={vm['zone']}",
        "--format=json",
    ]
    if vm.get("project"):
        args.append(f"--project={vm['project']}")
    res = run_gcloud(args, check=False, timeout=15)
    if res.returncode != 0 or not res.stdout:
        return []
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError:
        return []
    for item in info.get("metadata", {}).get("items", []) or []:
        if item.get("key") == "upload-links":
            return (item.get("value") or "").splitlines()
    return []


def _write_metadata_links(vm: dict, lines: list[str]) -> bool:
    """Save upload links to GCP instance metadata so they survive shutdowns."""
    if not vm or not vm.get("instance") or not vm.get("zone"):
        return False
    import tempfile
    content = "\n".join(lines) + ("\n" if lines else "")
    tf_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
            tf.write(content)
            tf_path = tf.name
        args = [
            "compute", "instances", "add-metadata", vm["instance"],
            f"--zone={vm['zone']}",
            f"--metadata-from-file=upload-links={tf_path}",
        ]
        if vm.get("project"):
            args.append(f"--project={vm['project']}")
        res = run_gcloud(args, check=False, timeout=15)
        return res.returncode == 0
    except Exception:
        return False
    finally:
        if tf_path:
            try:
                Path(tf_path).unlink(missing_ok=True)
            except OSError:
                pass


def _write_local_links(lines: list[str]) -> None:
    try:
        LOCAL_LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_LINKS_FILE.write_text("\n".join(lines) + ("\n" if lines else ""),
                                    encoding="utf-8")
    except OSError:
        pass


def sync_links_from_vm(vm: dict) -> list[str]:
    """Synchronise and return every known upload link across local, cloud metadata, and VM."""
    merged: list[str] = []
    try:
        merged.extend(LOCAL_LINKS_FILE.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        pass

    try:
        merged.extend(_metadata_links(vm))
    except Exception:
        pass

    if is_vm_running(vm):
        try:
            cmd = (
                "cat ~/.upload_links.log "
                "/mnt/rom/.upload_links.log "
                "/mnt/recovery/.upload_links.log "
                "/tmp/.upload_links.log 2>/dev/null || true"
            )
            res = vm_exec(vm, cmd, check=False, timeout=10)
            if res.returncode == 0 or res.stdout:
                merged.extend((res.stdout or "").replace("\r", "").splitlines())
        except Exception:
            pass

    links = dedupe_links(merged)
    _write_local_links(links)
    try:
        _write_metadata_links(vm, links)
    except Exception:
        pass
    return links


def cache_upload_links_from_output(output_file: str | Path) -> list[str]:
    """Extract link lines from a captured uploader output/log file."""
    path = Path(output_file)
    if not path.is_file():
        return []
    merged: list[str] = []
    try:
        merged.extend(LOCAL_LINKS_FILE.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        pass
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = ANSI_RE.sub("", raw).strip()
        if URL_RE.match(line):
            merged.append(line)
    links = dedupe_links(merged)
    _write_local_links(links)
    return links


def clear_upload_links(vm: dict, clear_metadata: bool = True) -> bool:
    """Remove the local cache, the VM log and optional GCP metadata history."""
    try:
        LOCAL_LINKS_FILE.unlink()
    except OSError:
        pass

    if is_vm_running(vm):
        vm_exec(vm, "> ~/.upload_links.log 2>/dev/null || true",
                check=False, timeout=20)

    if clear_metadata:
        args = [
            "compute", "instances", "remove-metadata", vm["instance"],
            f"--zone={vm['zone']}", "--keys=upload-links",
        ]
        if vm.get("project"):
            args.append(f"--project={vm['project']}")
        run_gcloud(args, check=False, timeout=45)
    return True
