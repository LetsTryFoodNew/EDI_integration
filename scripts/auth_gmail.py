"""
One-time CLI script to authorise Gmail access and produce token.json.

Usage:
    python scripts/auth_gmail.py

Prerequisites:
    1. Download OAuth 2.0 credentials from Google Cloud Console:
         APIs & Services → Credentials → Create Credentials → OAuth client ID
         Application type: Desktop app
         Download as JSON and save to the path configured in GMAIL_CREDENTIALS_PATH
         (default: ./credentials/gmail_credentials.json)
    2. Enable the Gmail API in the same Google Cloud project.
    3. Add tech@letstryfoods.com as a test user if the app is in "Testing" mode.

After running this script, token.json is written to GMAIL_TOKEN_PATH
(default: ./credentials/gmail_token.json). The token auto-refreshes; you only
need to run this script once per deployment or when the token is revoked.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root without installing package
sys.path.insert(0, str(Path(__file__).parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    settings = get_settings()
    credentials_path = Path(settings.gmail_credentials_path)
    token_path = Path(settings.gmail_token_path)

    if not credentials_path.exists():
        print(f"ERROR: credentials file not found at {credentials_path}")
        print("Download from Google Cloud Console → APIs & Services → Credentials.")
        sys.exit(1)

    print(f"Launching OAuth flow with credentials: {credentials_path}")
    print("A browser window will open. Authorise with tech@letstryfoods.com.")

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())

    print(f"\nSuccess! Token saved to {token_path}")
    print("The scheduler and workers will use this token automatically.")
    print("Token auto-refreshes; re-run this script only if access is revoked.")


if __name__ == "__main__":
    main()
