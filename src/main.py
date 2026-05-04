"""Daily job-tracker run.

Pulls last 24h from each Gmail account, classifies via Claude,
reconciles into tracker.xlsx, sends digest.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from accounts import resolve_email_for_label
from classifier import classify_emails
from digest import send_digest
from gmail_client import GmailClient, fetch_recent_messages
from tracker import reconcile_into_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TRACKER_PATH = DATA_DIR / "tracker.xlsx"
SEEN_PATH = DATA_DIR / "processed_message_ids.json"
DIGEST_TO = os.environ["DIGEST_TO"]
DIGEST_FROM_ACCOUNT = os.environ["DIGEST_FROM_ACCOUNT"]


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    return set(json.loads(SEEN_PATH.read_text()))


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen)))


def list_accounts() -> list[str]:
    raw = os.environ["GMAIL_ACCOUNTS"]
    return [a.strip() for a in raw.split(",") if a.strip()]


def run() -> None:
    log.info("=== job-tracker run @ %s ===", datetime.now(timezone.utc).isoformat())
    seen = load_seen()
    log.info("loaded %d previously-seen message ids", len(seen))

    all_classified: list[dict] = []
    # Only the message IDs that successfully made it through classification
    # get added to `seen` — anything that failed (API error, network issue,
    # credit problem) stays unseen so the next run will retry it.
    successfully_processed_ids: set[str] = set()

    for account in list_accounts():
        log.info("--- account: %s ---", account)
        client = GmailClient(account)
        resolve_email_for_label(account, client.service)
        messages = fetch_recent_messages(client, hours=36, exclude_ids=seen)
        log.info("  fetched %d candidate messages", len(messages))
        if not messages:
            continue

        classified = classify_emails(messages, account_label=account)
        log.info("  classified: %s", _summary(classified))
        all_classified.extend(classified)

        # Mark every input message ID as processed: even ones the classifier
        # decided were "other" (we genuinely don't want to reprocess those).
        # The classifier returns nothing for emails it failed on (API error),
        # so those won't appear here and will be retried next run.
        classified_ids = {c["id"] for c in classified}
        for m in messages:
            if m["id"] in classified_ids:
                successfully_processed_ids.add(m["id"])
                continue
            # The message was sent to Claude. If we got *any* classifications
            # back from this batch, then "missing from output" means Claude
            # decided it was 'other' — mark seen. If we got NOTHING back from
            # this batch, the whole batch failed; don't mark seen.
            if classified:
                successfully_processed_ids.add(m["id"])

    if not all_classified:
        log.info("nothing to record today; skipping tracker + digest")
        # Still persist any IDs that classified as 'other' so we don't keep
        # reprocessing them. (In practice, if all_classified is empty, none
        # were processed successfully either, so this is a no-op.)
        seen.update(successfully_processed_ids)
        save_seen(seen)
        return

    summary = reconcile_into_tracker(all_classified, TRACKER_PATH)
    log.info("tracker updated: %s", summary)

    send_digest(
        from_account=DIGEST_FROM_ACCOUNT,
        to_address=DIGEST_TO,
        summary=summary,
        classified=all_classified,
        tracker_path=TRACKER_PATH,
    )
    log.info("digest sent to %s", DIGEST_TO)

    seen.update(successfully_processed_ids)
    save_seen(seen)
    log.info("=== run complete ===")


def _summary(classified: list[dict]) -> str:
    counts: dict[str, int] = {}
    for c in classified:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"


if __name__ == "__main__":
    run()