"""Gmail API wrapper.

Each Gmail account has its own OAuth refresh token stored at
data/tokens/<label>.json. The shared OAuth client (client_secret.json)
is read from GMAIL_CLIENT_SECRET_JSON (env) — this lets us keep the
secret in GitHub Actions secrets while keeping per-account tokens
in repo files (refresh tokens, not client secrets).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger("gmail")

ROOT = Path(__file__).resolve().parent.parent
TOKEN_DIR = ROOT / "data" / "tokens"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailClient:
    def __init__(self, account_label: str) -> None:
        self.label = account_label
        self.creds = self._load_creds()
        self.service = build("gmail", "v1", credentials=self.creds, cache_discovery=False)

    def _load_creds(self) -> Credentials:
        token_path = TOKEN_DIR / f"{self.label}.json"
        if not token_path.exists():
            raise FileNotFoundError(
                f"missing token for account '{self.label}' at {token_path}. "
                f"run scripts/authorize.py {self.label} once locally."
            )
        client_secret = json.loads(os.environ["GMAIL_CLIENT_SECRET_JSON"])
        token_data = json.loads(token_path.read_text())

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data["refresh_token"],
            token_uri=client_secret["installed"]["token_uri"],
            client_id=client_secret["installed"]["client_id"],
            client_secret=client_secret["installed"]["client_secret"],
            scopes=SCOPES,
        )
        if not creds.valid:
            creds.refresh(Request())
            # Persist any rotated refresh token
            token_path.write_text(json.dumps({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            }, indent=2))
        return creds


# --- message fetching ---------------------------------------------------------

# Gmail search query: anything that smells like job-application activity.
# We're permissive on retrieval — Claude will filter precisely.
JOB_QUERY = (
    "newer_than:2d "
    "-from:me "
    "("
    "subject:(application OR applied OR interview OR position OR role OR opportunity "
    "OR rejection OR \"unfortunately\" OR \"thank you for applying\" "
    "OR \"we received your application\" OR \"next steps\" OR offer OR onsite "
    "OR \"moved forward\" OR \"not moving forward\") "
    "OR from:(noreply OR no-reply OR careers OR talent OR recruiting OR jobs OR "
    "greenhouse OR lever OR workday OR ashby OR smartrecruiters)"
    ")"
)


def fetch_recent_messages(
    client: GmailClient,
    *,
    hours: int = 36,
    exclude_ids: set[str] | None = None,
    max_results: int = 200,
) -> list[dict[str, Any]]:
    """Return a list of {id, account, subject, from, date, snippet, body} dicts."""
    exclude_ids = exclude_ids or set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    msg_ids: list[str] = []
    page_token: str | None = None
    while True:
        try:
            resp = client.service.users().messages().list(
                userId="me",
                q=JOB_QUERY,
                maxResults=min(100, max_results - len(msg_ids)),
                pageToken=page_token,
            ).execute()
        except HttpError as e:
            log.warning("list error on %s: %s", client.label, e)
            break
        for m in resp.get("messages", []):
            if m["id"] not in exclude_ids:
                msg_ids.append(m["id"])
        page_token = resp.get("nextPageToken")
        if not page_token or len(msg_ids) >= max_results:
            break

    out: list[dict[str, Any]] = []
    for mid in msg_ids:
        try:
            full = client.service.users().messages().get(
                userId="me", id=mid, format="full"
            ).execute()
        except HttpError as e:
            log.warning("get error on %s/%s: %s", client.label, mid, e)
            continue

        # Internal date (ms since epoch) — use this to enforce the hours cutoff
        internal_ts = int(full.get("internalDate", "0")) / 1000
        if datetime.fromtimestamp(internal_ts, tz=timezone.utc) < cutoff:
            continue

        headers = {h["name"].lower(): h["value"] for h in full["payload"].get("headers", [])}
        body = _extract_body(full["payload"])

        out.append({
            "id": mid,
            "account": client.label,
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "snippet": full.get("snippet", ""),
            # Trim aggressively — the visible part of an email almost always
            # has enough signal, and keeps token cost low.
            "body": body[:4000],
        })
        time.sleep(0.05)  # be polite to the API

    return out


def _extract_body(payload: dict) -> str:
    """Best-effort plain-text body extraction from a Gmail message payload."""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data")
                if data:
                    return _decode(data)
        # fall back to the first part recursively
        for part in payload["parts"]:
            inner = _extract_body(part)
            if inner:
                return inner
        return ""
    data = payload.get("body", {}).get("data")
    return _decode(data) if data else ""


def _decode(b64url: str) -> str:
    try:
        return base64.urlsafe_b64decode(b64url + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


def send_message(client: GmailClient, *, to: str, subject: str, html_body: str,
                 attachments: list[Path] | None = None) -> None:
    """Send an email from the authenticated account, optionally with attachments."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["to"] = to
    msg["subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    for path in attachments or []:
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    client.service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
