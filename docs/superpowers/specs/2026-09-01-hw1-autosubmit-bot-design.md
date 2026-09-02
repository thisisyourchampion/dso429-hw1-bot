# DSO 429 HW1 auto-submit bot — design

## Purpose
DSO 429 (USC Marshall) HW1 releases 5 MC business-math questions every hour for
168 rounds (7 days). Each round has a 65-minute deadline. Grade = 60% on-time
submission rate + 40% accuracy (accuracy computed over all rounds, so a skip
counts against you twice). Manually watching for and answering 168 hourly
rounds isn't feasible, so this is a scheduled bot.

## Resources (confirmed by direct inspection, 2026-09-01)
- Questions feed: `https://raw.githubusercontent.com/FaisalXL/dso429-hw1-questions/main/questions.csv`
  - CDN-cached 5 min. Requires a `User-Agent` header or returns 403.
  - Columns: `round_id, q_index, released_at, deadline_at, question_id, question_text, option_a..d`.
- Submission target: Google Form at
  `https://docs.google.com/forms/d/e/1FAIpQLSc0fSvaiv3kHXv9uU-N4kaFc0K4H2NBavBFgZj4tG8WwM1GnA/`
  - No login required. The form's *content* (question text/choices shown) is
    rewritten to match the current round each hour, but the actual answer
    values for each MC question are literally the letters `A`/`B`/`C`/`D`.
  - Submits via a plain POST to its `formResponse` endpoint using
    `entry.<id>` fields for email, round_id, and Q1-Q5. Because we can't
    prove the entry IDs are stable across rounds, the bot re-fetches the
    form and re-parses IDs fresh before every submission.
- Receipt/verification: `https://dso429-hw1-tick.dso429hw1.workers.dev/receipt?email=...`
  - Also needs a `User-Agent` header. Returns `registered`, `rounds_submitted`,
    and the latest submission — this is the only trustworthy signal of what
    was actually recorded (the form's own "recorded" message is not).

## Approach
Deliberately **stateless**: the bot carries no local memory of what it has
already answered. On every run it:
1. Fetches the CSV and the receipt for the registered email.
2. Computes `missing = {round_ids in CSV} - {round_ids in receipt}`.
3. For each missing round (oldest first), asks Gemini (free tier) to answer
   all 5 questions in one call, then submits to the form, then re-checks the
   receipt to confirm it landed.

This choice was made over hardcoding entry IDs / keeping a local "already
answered" file because:
- It self-heals: if a run is skipped (Actions delay, outage), the next run
  still finds and submits the missed round — as a **late** submission, which
  the grading rules say still earns accuracy credit (just not uptime credit).
  A skipped round otherwise costs accuracy *and* uptime, so this is a
  meaningful safety net, not just convenience.
- The receipt endpoint is the actual source of truth for grading, so there's
  no risk of local state drifting from what was really recorded.

**LLM choice**: every question is answered by an LLM call (Gemini 2.5 Flash,
free tier via Google AI Studio, no card required) rather than a deterministic
math solver, per explicit preference — this assignment is partly about
exploring an LLM-based mechanic, not just about maximizing the score by the
easiest possible means. Reliability trade-off accepted: free-tier LLMs can
occasionally slip on arithmetic; mitigated with a step-by-step prompt, low
temperature, and one retry with backoff on failure.

**Uptime protection**: if Gemini fails after retries, the bot still submits a
fallback random guess rather than skipping the round — under this grading
scheme a wrong-but-submitted answer is strictly better than a skip (skip =
lose both uptime and accuracy credit for that round).

## Hosting
GitHub Actions scheduled workflow (`cron`), `*/10 * * * *`, on a **private**
repo. Chosen over running the script on the student's own laptop because a
single missed hour (sleep, closed lid, dropped WiFi) loses that round —
GitHub Actions has none of those failure modes and is free for this volume
(~1000 short runs over 7 days, well under the 2,000 free private-repo
minutes/month). 10-minute granularity leaves multiple retry attempts inside
every 65-minute window even accounting for the CSV's 5-minute cache lag and
occasional Actions scheduler jitter.

## Components
- `solve_and_submit.py` — the whole bot: fetch CSV, fetch receipt, diff,
  call Gemini, parse form, submit, verify. Single file, stdlib +
  `requests` only.
- `.github/workflows/answer.yml` — the cron trigger + job that runs the
  script with `GEMINI_API_KEY` and `STUDENT_EMAIL` from repo secrets.
- `README.md` — setup checklist for the student (repo creation, secret,
  pasting the two files in via GitHub's web UI — no git/gh CLI needed,
  since this terminal's `gh` session is authenticated as a different
  GitHub account than the student's).

## Out of scope
- No deterministic math solver (explicit choice, see above).
- No notifications beyond what GitHub already sends by default on workflow
  failure — kept out to stay within the ~2 hour setup budget.
- No dashboard/UI — the receipt URL and the Actions log are the interface.
