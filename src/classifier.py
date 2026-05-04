"""Email classifier using the Claude API.

We batch ~10 emails per request and ask Claude to return a strict JSON
array. Strict JSON is enforced via a "respond ONLY with JSON" instruction
plus a regex-based extraction in case Claude wraps the output.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from anthropic import Anthropic

log = logging.getLogger("classifier")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")
BATCH_SIZE = 10

CATEGORIES = ["applied", "rejected", "interview", "offer", "other"]

SYSTEM_PROMPT = """You classify emails for a job-application tracker.

For EACH input email, return one JSON object with these fields:
- "id": copy of the input id
- "category": one of [applied, rejected, interview, offer, other]
    - "applied" = automated confirmation that the user just submitted an application
    - "rejected" = company is declining / not moving forward / position filled
    - "interview" = invitation to interview, scheduling request, assessment, or next-round
    - "offer" = job offer extended
    - "other" = anything else (recruiter cold outreach, newsletters, job alerts,
                LinkedIn notifications, generic recruiter follow-ups, etc.)
- "company": best guess at the hiring company name. Use the company the user is
    applying to, NOT the ATS provider (Greenhouse, Lever, Workday, Ashby).
    Empty string if you genuinely cannot tell.
- "role": role/position title if stated. Empty string if not.
- "confidence": float 0.0 to 1.0
- "reason": one short sentence explaining the classification

Important rules:
- Recruiter cold outreach ("we have an opening you might like") is "other",
  NOT "applied". Only mark "applied" when the email confirms a submission the
  user actively made.
- Job alerts and saved-search digests are "other".
- LinkedIn / Indeed notifications about job views are "other".
- If the email looks transactional but ambiguous, prefer "other" with low confidence.

Respond with a JSON array only. No prose, no markdown fences."""


def classify_emails(messages: list[dict], *, account_label: str) -> list[dict]:
    """Returns enriched message dicts with classifier fields merged in.

    Skips anything classified as 'other'.
    """
    if not messages:
        return []

    client = Anthropic()  # uses ANTHROPIC_API_KEY env var
    out: list[dict] = []

    for i in range(0, len(messages), BATCH_SIZE):
        batch = messages[i : i + BATCH_SIZE]
        prompt_payload = [
            {
                "id": m["id"],
                "subject": m["subject"],
                "from": m["from"],
                "snippet": m["snippet"],
                "body": m["body"],
            }
            for m in batch
        ]

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Classify the following emails. Return a JSON array with one "
                            "object per email, in the same order.\n\n"
                            + json.dumps(prompt_payload, ensure_ascii=False)
                        ),
                    }
                ],
            )
        except Exception as e:
            log.error("Claude API error on batch %d: %s", i // BATCH_SIZE, e)
            continue

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        parsed = _parse_json_array(text)
        if parsed is None:
            log.error("could not parse classifier output: %r", text[:500])
            continue

        by_id = {p.get("id"): p for p in parsed if isinstance(p, dict)}
        for m in batch:
            tag = by_id.get(m["id"])
            if not tag:
                continue
            cat = tag.get("category", "other")
            if cat not in CATEGORIES:
                cat = "other"
            if cat == "other":
                continue
            out.append({
                **m,
                "category": cat,
                "company": (tag.get("company") or "").strip(),
                "role": (tag.get("role") or "").strip(),
                "confidence": float(tag.get("confidence", 0.0)),
                "reason": (tag.get("reason") or "").strip(),
            })

    return out


def _parse_json_array(text: str) -> list[Any] | None:
    """Extract a JSON array from Claude's response, tolerating fences/prose."""
    text = text.strip()
    # Strip ```json ... ``` fences if present
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # Find the first [...] block
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
