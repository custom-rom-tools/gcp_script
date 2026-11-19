"""
Authentication Menu (cli/tools/auth.py)
Google Cloud account sign-in and account switching.
"""

from cli.base import clear_screen, print_header, pause
from cli.services.vm import check_gcloud_auth, login_gcloud


def submenu_gcloud_auth():
    """Sign in or switch the active Google Cloud account."""
    clear_screen()
    print_header("GOOGLE CLOUD ACCOUNT AUTHENTICATION")
    account = check_gcloud_auth()
    print(f" Current account: {account or 'Not Authenticated'}\n")
    login_gcloud()
    pause()
