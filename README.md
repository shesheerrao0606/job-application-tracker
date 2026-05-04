# job-tracker

Daily scan of 4–5 Gmail inboxes for job-application activity. Classifies
each email with Claude, reconciles into a local `tracker.xlsx`, and emails
you a digest. Runs on GitHub Actions on a schedule.

## What it does

Each morning at 7am Central:

1. Pulls the last ~36h of mail from each Gmail account using a permissive
   Gmail search (`subject:application OR rejected OR ... OR from:greenhouse OR ...`).
2. Sends each email to Claude in batches of 10, gets back JSON like:
   `{ "category": "rejected", "company": "Acme", "role": "Backend Eng", ... }`
3. New "applied" classifications add a row to `data/tracker.xlsx`.
   "rejected" / "interview" / "offer" classifications find the matching row
   (by company + account, then by role) and update the Status.
4. Sends a digest email summarizing the day's activity, with the full
   spreadsheet attached.
5. Commits the updated `tracker.xlsx` back to the repo.

## One-time setup

### 1. Google Cloud OAuth

You need *one* OAuth client that all your Gmail accounts will authenticate
through.

1. Go to https://console.cloud.google.com/, create a project (or reuse one).
2. APIs & Services → Library → enable **Gmail API**.
3. APIs & Services → OAuth consent screen:
   - User type: **External**
   - App name: anything ("job-tracker")
   - Add yourself as a **test user** (under "Audience"). Add every Gmail
     address you'll connect — the consent screen rejects accounts that
     aren't on this list while the app is in "Testing" mode.
   - Scopes: add `gmail.readonly` and `gmail.send`.
4. APIs & Services → Credentials → Create Credentials → **OAuth client ID**:
   - Application type: **Desktop app**
   - Download the JSON, save it as `client_secret.json` in the repo root.

### 2. Authorize each Gmail account locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/authorize.py personal   # opens browser, sign in to account #1
python scripts/authorize.py jobs1      # opens browser, sign in to account #2
python scripts/authorize.py jobs2
python scripts/authorize.py jobs3
python scripts/authorize.py jobs4
```

Each run opens a browser, you log in, click "Allow", and a token file is
written to `data/tokens/<label>.json`. **These files contain refresh
tokens — treat them like passwords.** They're gitignored.

The labels (`personal`, `jobs1`, ...) are arbitrary; they just need to
match what you put in `GMAIL_ACCOUNTS` in the workflow file. Pick one of
them as the "sender" account that will email you the digest — that's what
`DIGEST_FROM_ACCOUNT` points at.

### 3. Sanity-check locally

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GMAIL_CLIENT_SECRET_JSON="$(cat client_secret.json)"
export GMAIL_ACCOUNTS="personal,jobs1,jobs2,jobs3,jobs4"
export DIGEST_TO="you@example.com"
export DIGEST_FROM_ACCOUNT="personal"

python src/main.py
```

You should see a log of fetches per account, classifications, the tracker
update, and a digest email arriving in `DIGEST_TO`.

Open `data/tracker.xlsx` and confirm the rows look right. If a lot of
recruiter cold-outreach is being marked as "applied", tweak the prompt in
`src/classifier.py` — the rules section accepts new exclusions easily.

### 4. GitHub Actions secrets

Push the repo (private!) to GitHub. Then in **Settings → Secrets and
variables → Actions**, add:

| Secret name                 | Value                                                    |
| --------------------------- | -------------------------------------------------------- |
| `ANTHROPIC_API_KEY`         | your Claude API key                                      |
| `GMAIL_CLIENT_SECRET_JSON`  | the entire contents of `client_secret.json`              |
| `DIGEST_TO`                 | the email address that should receive the daily digest   |
| `TOKEN_PERSONAL`            | `base64 < data/tokens/personal.json` (one line, no newline) |
| `TOKEN_JOBS1`               | `base64 < data/tokens/jobs1.json`                        |
| `TOKEN_JOBS2`               | `base64 < data/tokens/jobs2.json`                        |
| `TOKEN_JOBS3`               | `base64 < data/tokens/jobs3.json`                        |
| `TOKEN_JOBS4`               | `base64 < data/tokens/jobs4.json`                        |

On macOS / Linux:

```bash
base64 < data/tokens/personal.json | pbcopy   # macOS
base64 -w0 < data/tokens/personal.json        # Linux: print, then copy
```

### 5. Trigger a manual run

Actions tab → `daily-job-tracker` → "Run workflow". Watch the logs. If
everything works, the next scheduled run will happen tomorrow at 12:00 UTC.

## Tweaks you'll probably want

- **Schedule.** Change the cron in `.github/workflows/daily.yml`. GitHub
  Actions cron is UTC; College Station is Central, so 7am CT = 12:00 UTC
  (CDT) or 13:00 UTC (CST). I left it at 12:00.
- **Account labels.** If you don't have exactly 5 accounts, edit two
  places: the `GMAIL_ACCOUNTS` env var in the workflow, and the
  `Restore token files` step (one line per account).
- **Search query.** `JOB_QUERY` in `src/gmail_client.py` controls what
  Gmail returns before Claude sees it. Add ATS providers your target
  companies use, or specific subject phrases you keep getting.
- **What counts as "other".** The classifier currently drops everything
  that isn't applied/rejected/interview/offer. If you want recruiter
  outreach tracked separately, add a category there and a status mapping
  in `tracker.py`.



<img width="901" height="426" alt="image" src="https://github.com/user-attachments/assets/c71b74ba-48bd-430e-b39a-4eefa71f621a" />


<img width="901" height="632" alt="image" src="https://github.com/user-attachments/assets/68ff44b6-f3bf-4b1b-b792-b08080de6640" />




## Cost

At ~50 emails/day across 5 accounts, batched 10 per Claude call, you're
looking at roughly 5 API calls/day with maybe 6k input tokens and 1k
output tokens each. That's a few cents a day at Opus pricing, fractions
of a cent at Haiku. If cost matters, swap `CLAUDE_MODEL` to
`claude-haiku-4-5-20251001` in the workflow — the classification task
is well within Haiku's range.

## Troubleshooting

- **"missing token for account 'jobs2'"** — you didn't set the matching
  GH secret, or its name doesn't match the workflow.
- **Auth errors after a few weeks** — refresh tokens for "Testing"-mode
  OAuth apps expire after 7 days. Either publish the OAuth app (it's just
  you using it, no review needed for personal scopes) or re-run
  `scripts/authorize.py` periodically. The cleanest fix: in the OAuth
  consent screen, click **Publish App**.
- **Digest never arrives** — check the Action logs. Most common: the
  digest sender account doesn't have `gmail.send` in its token. Re-run
  `scripts/authorize.py` for that label.
- **Classifier puts cold-recruiter emails under "applied"** — add the
  pattern to the "Important rules" section of `SYSTEM_PROMPT` in
  `src/classifier.py`. Claude is responsive to specific examples there.
