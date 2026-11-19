"""
Upload Links Menu (cli/ops/link.py)
Shows cached Gofile / SourceForge / Filebin links and can clear the history
from the local cache, the VM log and GCP instance metadata.
"""

from cli.base import (
    clear_screen, print_header, print_divider, pause, read_input,
    GREEN, CYAN, YELLOW, RED, BOLD, NC,
)
from cli.services.link import (
    LOCAL_LINKS_FILE, sync_links_from_vm, clear_upload_links,
)


def submenu_view_links(vm: dict):
    """Display and synchronise every recorded upload link."""
    while True:
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
        print(f"  {YELLOW}r){NC} Reload / Refresh Links")
        print(f"  {RED}c){NC} Clear Links (local cache, VM log and metadata)")
        print(f"  {RED}0 / q){NC} Back to Main Menu / Quit (or Enter)")
        print_divider()

        answer = read_input(" Select option [Enter, r, c, 0, q]: ").strip().lower()
        if answer in ("r", "reload", "refresh"):
            continue
        if answer in ("c", "clear"):
            clear_upload_links(vm)
            print(">>> Link history cleared from local cache, VM log and cloud metadata.")
            pause()
            continue
        return
