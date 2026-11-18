"""
Artifact Downloads Menu (cli/ops/download.py)
Transfers compiled ROM .zip files, recovery images, and build logs to ~/Downloads.
"""

from cli.base import clear_screen, print_header, pause
from cli.services.download import download_artifacts


def submenu_download_rom(vm: dict):
    """Submenu to search and pull latest compiled ROM zip."""
    clear_screen()
    print_header("Download ROM")
    download_artifacts(vm, artifact_type="rom")
    pause()


def submenu_download_recovery(vm: dict):
    """Submenu to search and pull latest recovery image or zip."""
    clear_screen()
    print_header("Download Recovery")
    download_artifacts(vm, artifact_type="recovery")
    pause()


def submenu_download_log(vm: dict):
    """Submenu to download raw build.log and generate clean ANSI-stripped copy."""
    clear_screen()
    print_header("Download Build Log")
    download_artifacts(vm, artifact_type="log")
    pause()
