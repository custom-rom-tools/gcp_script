"""
Upload Links Menu (cli/ops/link.py)
Shows cached Gofile / SourceForge / Filebin links and can clear the history
from the local cache, the VM log and GCP instance metadata.
"""

from cli.base import (
    clear_screen, print_header, print_divider, pause, read_input,
    GREEN, CYAN, YELLOW, BOLD, NC,
)
from cli.services.link import (
    LOCAL_LINKS_FILE, sync_links_from_vm, clear_upload_links,
)


def submenu_view_links(vm: dict):
    """Display and synchronise every recorded upload link."""
    clear_screen()
    print_header("UPLOAD LINKS HISTORY", "Gofile / SourceForge / Filebin")
    print(f" Cache file: {CYAN}{LOCAL_LINKS_FILE}{NC} (works while the VM is stopped)")
    print_divider()

    print(">>> Syncing links (local cache + GCP metadata + VM log)...")
    links = sync_links_from_vm(vm)
    if not links:
        print(f" {YELLOW}No upload links recorded yet.{NC}")
    else:
        for idx, line in enumerate(links, 1):
            print(f" {idx:>2}) {line}")
    print_divider()

    answer = read_input(" Press Enter to return (or 'c' to clear links from local, VM and metadata): ")
    if answer.strip().lower() in ("c", "clear"):
        clear_upload_links(vm)
        print(">>> Link history cleared from local cache, VM log and cloud metadata.")
        pause()
