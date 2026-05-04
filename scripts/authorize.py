"""One-time OAuth bootstrap, run locally.

Usage:
    python scripts/authorize.py <account_label>

e.g.:
    python scripts/authorize.py personal
    python scripts/authorize.py jobs1
    python scripts/authorize.py jobs2

Prereq:
    - A Google Cloud project with the Gmail API enabled.
    - An OAuth client of type "Desktop" downloaded as `client_secret.json`
      placed in the repo root.
    - For each Gmail account, you'll do the consent flow once. The resulting
      refresh-token file is written to data/tokens/<label>.json — this is
      what GitHub Actions will use.

Important: the *client secret* is private. The per-account *token* files
contain refresh tokens scoped to gmail.readonly + gmail.send. Decide whether
you're comfortable committing them to a private repo (they ARE bearer
credentials — anyone with read access to the repo can read your mail) or
whether you'd rather paste them as repo secrets too.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent.parent
TOKEN_DIR = ROOT / "data" / "tokens"
CLIENT_SECRET = ROOT / "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/authorize.py <account_label>", file=sys.stderr)
        sys.exit(2)
    label = sys.argv[1]

    if not CLIENT_SECRET.exists():
        print(f"ERROR: place your OAuth client at {CLIENT_SECRET}", file=sys.stderr)
        sys.exit(1)

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    # Forces a refresh_token to be returned even on subsequent runs
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    out = TOKEN_DIR / f"{label}.json"
    out.write_text(json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }, indent=2))
    print(f"✓ wrote {out}")
    print(f"  refresh_token (first 12 chars): {creds.refresh_token[:12]}...")


if __name__ == "__main__":
    main()
