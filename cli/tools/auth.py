"""
Authentication Menu (cli/tools/auth.py)
Provides Google Cloud account sign-in and account switching.
"""

from cli.base import clear_screen, print_header, pause
from cli.services.vm import login_gcloud


def submenu_gcloud_auth():
    """Submenu to sign in or switch active Google Cloud account."""
    clear_screen()
    print_header("Google Cloud Account Authentication")
    login_gcloud()
    pause()
