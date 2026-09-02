# DSO 429 HW1 auto-submit bot

Answers and submits every round of the DSO 429 HW1 quiz automatically, 24/7,
for the full 7-day campaign. See `docs/superpowers/specs/2026-09-01-hw1-autosubmit-bot-design.md`
for how and why this is built the way it is.

It's already been tested end-to-end against the live assignment (rounds 1-6,
all practice) — this README is only about getting it *deployed* so it keeps
running without your laptop.

## What it does, every 10 minutes, forever (via GitHub Actions)
1. Downloads the current questions CSV.
2. Checks your receipt to see which round_ids are already recorded.
3. For anything missing, asks Gemini (free tier) to answer all 5 questions,
   submits to the Google Form, and re-checks the receipt to confirm it landed.

## One-time setup (do this in your browser — ~15 minutes)

1. **Create a GitHub account** (skip if you have one already) at
   [github.com/signup](https://github.com/signup) — use your own email, not
   anything already logged into a terminal on this machine.

2. **Create a new private repository**, e.g. named `dso429-hw1-bot`:
   [github.com/new](https://github.com/new) → Private → Create repository.

3. **Get a free Gemini API key** (no credit card) at
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — sign in
   with any Google account, "Create API key". Keep this tab open, you'll need
   it in step 5.

4. **Add the two files below to your new repo**, using GitHub's web editor
   (no git/CLI needed):
   - In your repo, click **Add file → Create new file**.
   - For the filename, type the *full path* `solve_and_submit.py` and paste
     in the contents of [`solve_and_submit.py`](solve_and_submit.py) from
     this folder. Commit.
   - Click **Add file → Create new file** again. This time type the full
     path `.github/workflows/answer.yml` (GitHub will create the folders
     automatically) and paste in the contents of
     [`.github/workflows/answer.yml`](.github/workflows/answer.yml). Commit.
   - One more: filename `requirements.txt`, paste in the contents of
     [`requirements.txt`](requirements.txt). Commit.

5. **Add two repository secrets**: in your repo, go to
   **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `STUDENT_EMAIL`, value: the exact email you registered for the
     course with (`carnaugh1893@gmail.com` — confirmed registered).
   - Name: `GEMINI_API_KEY`, value: the key from step 3.

6. **Check it's enabled**: go to the **Actions** tab of your repo. You
   should see a workflow called "DSO429 HW1 auto-submit". If GitHub shows a
   banner asking to enable Actions/workflows, click enable.

7. **Run it once by hand to confirm it works**: on the Actions tab, click
   the "DSO429 HW1 auto-submit" workflow → **Run workflow** button → Run
   workflow. After ~30 seconds, click into the run and check the log —
   it should either say "nothing to do" (if everything's already submitted)
   or show it answering and confirming a round. Then check your receipt
   directly: `https://dso429-hw1-tick.dso429hw1.workers.dev/receipt?email=carnaugh1893@gmail.com`

That's it — from here it runs itself every 10 minutes for the full 7 days.
You don't need to do anything else unless something breaks.

## Checking on it during the week
- **Actions tab** of your repo: every run's log, whether it succeeded or
  failed. GitHub emails you automatically if a scheduled run fails.
- **Receipt URL**: the actual source of truth for what was recorded —
  `https://dso429-hw1-tick.dso429hw1.workers.dev/receipt?email=carnaugh1893@gmail.com`

## If something looks wrong
- A single failed run isn't a big deal — the bot is stateless and self-heals:
  the next run (10 minutes later) will find and submit anything still
  missing, even past its deadline (late = accuracy credit, no uptime credit,
  per the assignment's own rules — still much better than a skip).
- If runs are failing repeatedly, open the failing run's log in the Actions
  tab — the error will be in the last few lines.
