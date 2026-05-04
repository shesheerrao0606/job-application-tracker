"""Resolve account labels (jobs1, personal, ...) to actual email addresses.

We call Gmail's users.getProfile() once per label, cache the result in
data/account_emails.json, and use that everywhere we display the account.
This way the user never has to manually maintain a label→email mapping —
it's derived from the same OAuth tokens we already have.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("accounts")

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "account_emails.json"


def _load_cache() -> dict[str, str]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def get_email_for_label(label: str) -> str:
    """Return cached email for label, or empty string if not yet resolved."""
    return _load_cache().get(label, "")


def resolve_email_for_label(label: str, gmail_service) -> str:
    """Look up the email address via Gmail API and cache it.

    `gmail_service` is the googleapiclient discovery object (client.service).
    Falls back to the label itself if the API call fails.
    """
    cache = _load_cache()
    if label in cache and cache[label]:
        return cache[label]
    try:
        profile = gmail_service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "")
        if email:
            cache[label] = email
            _save_cache(cache)
            log.info("resolved %s -> %s", label, email)
            return email
    except Exception as e:
        log.warning("could not resolve email for %s: %s", label, e)
    return label  # graceful fallback


def display_name(label: str) -> str:
    """Return 'email@domain.com' if known, otherwise the label.

    Used in the digest + Stats sheet wherever we'd otherwise show the bare label.
    """
    email = get_email_for_label(label)
    return email or label