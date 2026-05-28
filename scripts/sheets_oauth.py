"""One-time OAuth bootstrap for Sheets sync. Bypasses gcloud entirely.

Reads `data/oauth-client.json` (the Desktop OAuth client you downloaded
from GCP Credentials), opens a browser for consent, then stores a long-
lived refresh token at `data/sheets_token.json`. After that, sheets_sync
loads the cached token automatically — no expiry handling needed.

Usage:
    .venv/bin/python scripts/sheets_oauth.py
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
CLIENT_JSON = Path("data/oauth-client.json")
TOKEN_JSON = Path("data/sheets_token.json")


def main() -> None:
    if not CLIENT_JSON.exists():
        raise SystemExit(f"Missing {CLIENT_JSON}. Download the Desktop OAuth client JSON from GCP Credentials.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_JSON), SCOPES)
    # Opens a browser, listens on localhost, captures the code automatically.
    creds = flow.run_local_server(port=0, prompt="consent")

    TOKEN_JSON.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_JSON.write_text(creds.to_json())
    print(f"\n✓ Token saved to {TOKEN_JSON}")
    print(f"  Account: {creds.client_id}")
    print(f"  Scopes:  {creds.scopes}")


if __name__ == "__main__":
    main()
