"""
Release Links Menu (cli/ops/link.py)
Displays and synchronizes SourceForge and Gofile release upload links.
"""

from cli.base import clear_screen, print_header, pause
from cli.services.link import sync_links_from_vm


def submenu_view_links(vm: dict):
    """Submenu to view and synchronize SourceForge / Gofile release links."""
    clear_screen()
    print_header("Release Links")
    links = sync_links_from_vm(vm)
    if not links:
        print(" No uploaded release links found yet.")
    else:
        for line in links:
            print(f" {line}")
    pause()
